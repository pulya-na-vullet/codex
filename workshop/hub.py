"""HUB integration helpers (HMAC + outbound stubs)."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any
from urllib import error, request as urlrequest

from workshop.models import HubConnectionSettings, ModelingBrief

logger = logging.getLogger(__name__)


def sign_body(secret: str, timestamp: str, body: bytes) -> str:
    msg = timestamp.encode("utf-8") + b"\n" + body
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def verify_signature(secret: str, timestamp: str, body: bytes, signature: str, *, max_skew: int = 300) -> bool:
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs(int(time.time()) - ts) > max_skew:
        return False
    expected = sign_body(secret, timestamp, body)
    return hmac.compare_digest(expected, (signature or "").strip())


def _headers(cfg: HubConnectionSettings, body: bytes) -> dict[str, str]:
    ts = str(int(time.time()))
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {cfg.site_token}",
        "X-Site-Id": cfg.site_id,
        "X-Timestamp": ts,
        "X-Signature": sign_body(cfg.site_secret, ts, body),
    }


def _hub_configured(cfg: HubConnectionSettings | None = None) -> tuple[HubConnectionSettings | None, str]:
    cfg = cfg or HubConnectionSettings.get_solo()
    if not cfg.enabled or not cfg.hub_base_url or not cfg.site_token or not cfg.site_secret:
        return None, "Интеграция с хабом не настроена (admin → HUB)"
    return cfg, ""


def fetch_brief_from_hub(hub_brief_id: str, *, cfg: HubConnectionSettings | None = None) -> tuple[bool, str, dict[str, Any]]:
    """GET /api/v1/briefs/{id} from HUB (HMAC over empty body)."""
    cfg, err = _hub_configured(cfg)
    if not cfg:
        return False, err, {}
    hub_brief_id = (hub_brief_id or "").strip()
    if not hub_brief_id:
        return False, "нет hub_brief_id", {}

    body = b""
    url = f"{cfg.hub_base_url.rstrip('/')}/api/v1/briefs/{hub_brief_id}"
    req = urlrequest.Request(url, data=None, headers=_headers(cfg, body), method="GET")
    try:
        with urlrequest.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode("utf-8") or "{}"
            data = json.loads(raw)
            if not isinstance(data, dict):
                return False, "invalid HUB JSON", {}
            # Unwrap common envelopes from HUB serializers.
            for key in ("brief", "data", "result"):
                nested = data.get(key)
                if isinstance(nested, dict) and ("status" in nested or "brief_id" in nested or "id" in nested):
                    data = nested
                    break
            return True, "ok", data
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        logger.warning("HUB fetch brief failed HTTP %s: %s", exc.code, detail)
        return False, f"HUB HTTP {exc.code}: {detail}", {}
    except Exception as exc:
        logger.warning("HUB fetch brief failed: %s", exc)
        return False, str(exc)[:500], {}


def _map_hub_status(raw: str) -> str | None:
    from workshop.models import ModelingBriefStatus

    value = (raw or "").strip().lower()
    aliases = {
        "completed": ModelingBriefStatus.DONE,
        "complete": ModelingBriefStatus.DONE,
        "closed": ModelingBriefStatus.DONE,
        "finished": ModelingBriefStatus.DONE,
        "готово": ModelingBriefStatus.DONE,
        "выполнен": ModelingBriefStatus.DONE,
        "закрыт": ModelingBriefStatus.DONE,
        "taken_in_work": ModelingBriefStatus.ASSIGNED,
        "clarification": ModelingBriefStatus.NEEDS_CLARIFICATION,
    }
    if value in aliases:
        return aliases[value]
    allowed = {c.value for c in ModelingBriefStatus}
    return value if value in allowed else None


def apply_hub_brief_snapshot(
    brief: ModelingBrief,
    data: dict[str, Any],
    *,
    notify: bool = True,
) -> tuple[bool, str]:
    """Apply HUB brief JSON onto local ModelingBrief. Notifies Max only on status change."""
    from django.utils import timezone

    from workshop.models import ModelingBriefStatus

    old_status = brief.status
    new_status = _map_hub_status(str(data.get("status") or ""))
    changed = False

    hub_id = str(data.get("brief_id") or data.get("id") or data.get("hub_brief_id") or data.get("public_id") or "").strip()
    if hub_id and brief.hub_brief_id != hub_id:
        brief.hub_brief_id = hub_id
        changed = True

    designer_name = str(
        data.get("designer_name")
        or (data.get("designer") or {}).get("full_name")
        or (data.get("designer") or {}).get("name")
        or ""
    ).strip()
    designer_id = str(
        data.get("designer_id")
        or (data.get("designer") or {}).get("max_user_id")
        or (data.get("designer") or {}).get("id")
        or ""
    ).strip()
    eta = str(data.get("eta") or "").strip()
    message = str(data.get("message") or data.get("last_message") or data.get("text") or "").strip()

    if designer_name and brief.designer_name != designer_name:
        brief.designer_name = designer_name
        changed = True
    if designer_id and brief.designer_id != str(designer_id):
        brief.designer_id = str(designer_id)
        changed = True
    if eta and brief.eta != eta:
        brief.eta = eta
        changed = True
    if message and brief.last_hub_message != message:
        brief.last_hub_message = message
        changed = True

    status_changed = bool(new_status and new_status != old_status)
    if status_changed:
        brief.status = new_status
        changed = True
        if new_status == ModelingBriefStatus.DONE:
            brief.manager_alert = True
            if not brief.done_at:
                brief.done_at = timezone.now()
            brief.mark_rating_pending_if_needed()
        elif new_status == ModelingBriefStatus.NEEDS_CLARIFICATION:
            brief.manager_alert = True
        elif new_status in {
            ModelingBriefStatus.ASSIGNED,
            ModelingBriefStatus.IN_PROGRESS,
            ModelingBriefStatus.QUEUED,
            ModelingBriefStatus.CLARIFICATION_PROVIDED,
        }:
            # Clear alert only when moving into active work from draft/queue.
            if old_status in {ModelingBriefStatus.DRAFT, ModelingBriefStatus.QUEUED}:
                brief.manager_alert = False

    if changed:
        brief.save()

    if notify and status_changed:
        if new_status in {ModelingBriefStatus.ASSIGNED, ModelingBriefStatus.IN_PROGRESS}:
            notify_staff_max(
                text=(
                    f"3D {brief.brief_number}: статус с HUB — {brief.get_status_display()}.\n"
                    f"Дизайнер: {brief.designer_name or '—'} · срок: {brief.eta or '—'}"
                )
            )
        elif new_status == ModelingBriefStatus.NEEDS_CLARIFICATION:
            notify_client_max(
                brief.client,
                message
                or (
                    f"Здравствуйте! По заявке на 3D-моделирование {brief.brief_number} "
                    "нужны уточнения. Пожалуйста, свяжитесь с мастерской."
                ),
            )
            notify_staff_max(
                text=f"3D {brief.brief_number}: требуется переуточнение (синхронизация с HUB)."
            )
        elif new_status == ModelingBriefStatus.DONE:
            notify_client_max(
                brief.client,
                message
                or (
                    f"Здравствуйте! 3D-модель по заявке {brief.brief_number} готова. "
                    "Можно забирать в мастерской."
                ),
            )
            notify_staff_max(
                text=(
                    f"3D {brief.brief_number}: выполнено (синхронизация с HUB).\n"
                    f"Клиент: {brief.client.name}. Сумма: {brief.agreed_price}"
                )
            )
        elif new_status == ModelingBriefStatus.CANCELLED:
            notify_staff_max(text=f"3D {brief.brief_number}: отменено на HUB.")

    if status_changed:
        return True, f"status:{old_status}->{new_status}"
    if changed:
        return True, "updated"
    return True, "unchanged"


def sync_brief_from_hub(brief: ModelingBrief, *, notify: bool = True) -> tuple[bool, str]:
    """Pull current brief state from HUB and apply locally."""
    if not (brief.hub_brief_id or "").strip():
        return False, "Заявка ещё не связана с HUB (нет hub_brief_id)"
    ok, detail, data = fetch_brief_from_hub(brief.hub_brief_id)
    if not ok:
        return False, detail
    return apply_hub_brief_snapshot(brief, data, notify=notify)


def sync_open_briefs_from_hub(*, limit: int = 30) -> tuple[int, int, list[str]]:
    """Sync non-terminal briefs that already have hub_brief_id. Returns (ok, fail, details)."""
    from workshop.models import ModelingBrief, ModelingBriefStatus

    qs = (
        ModelingBrief.objects.exclude(hub_brief_id="")
        .exclude(status__in=[ModelingBriefStatus.DONE, ModelingBriefStatus.CANCELLED])
        .order_by("-id")[:limit]
    )
    ok_n = fail_n = 0
    details: list[str] = []
    for brief in qs:
        ok, detail = sync_brief_from_hub(brief, notify=True)
        if ok:
            ok_n += 1
            if detail.startswith("status:"):
                details.append(f"{brief.brief_number}: {detail}")
        else:
            fail_n += 1
            details.append(f"{brief.brief_number}: {detail}")
    return ok_n, fail_n, details


def rating_event_id(brief: ModelingBrief, *, version: int = 1) -> str:
    cfg = HubConnectionSettings.get_solo()
    site = (cfg.site_id or "site").strip() or "site"
    return f"rating-{site}-{brief.id}-{version}"


def push_brief_rating_to_hub(
    brief: ModelingBrief,
    *,
    score: int,
    comment: str = "",
    rated_by: str = "менеджер",
) -> tuple[bool, str, dict[str, Any]]:
    """POST /api/v1/briefs/{id}/ratings — manager star rating after done."""
    cfg, err = _hub_configured()
    if not cfg:
        return False, err, {}
    hub_brief_id = (brief.hub_brief_id or "").strip()
    if not hub_brief_id:
        return False, "Заявка ещё не связана с HUB (нет hub_brief_id)", {}
    try:
        score_i = int(score)
    except (TypeError, ValueError):
        return False, "Оценка должна быть числом от 1 до 5", {}
    if score_i < 1 or score_i > 5:
        return False, "Оценка должна быть от 1 до 5", {}

    event_id = (brief.rating_event_id or "").strip() or rating_event_id(brief, version=1)
    payload = {
        "event_id": event_id,
        "score": score_i,
        "comment": (comment or "").strip(),
        "rated_by": (rated_by or "менеджер").strip() or "менеджер",
        "local_brief_id": brief.id,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    url = f"{cfg.hub_base_url.rstrip('/')}/api/v1/briefs/{hub_brief_id}/ratings"
    req = urlrequest.Request(url, data=body, headers=_headers(cfg, body), method="POST")
    try:
        with urlrequest.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8") or "{}"
            data = json.loads(raw) if raw else {}
            if not isinstance(data, dict):
                data = {}
            data.setdefault("event_id", event_id)
            return True, str(data.get("status") or "ok"), data
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        logger.warning("HUB rating failed HTTP %s: %s", exc.code, detail)
        if exc.code == 400:
            return False, detail or "У брифа нет назначенного дизайнера (HUB 400)", {}
        return False, f"HUB HTTP {exc.code}: {detail}", {}
    except Exception as exc:
        logger.warning("HUB rating failed: %s", exc)
        return False, str(exc)[:500], {}


def submit_brief_rating(
    brief: ModelingBrief,
    *,
    score: int,
    comment: str = "",
    rated_by: str = "менеджер",
) -> tuple[bool, str]:
    """Persist local rating and push to HUB. Returns (ok, detail for UI)."""
    from decimal import Decimal, InvalidOperation

    from django.utils import timezone

    ok, detail, data = push_brief_rating_to_hub(
        brief, score=score, comment=comment, rated_by=rated_by
    )
    if not ok:
        return False, detail

    brief.rating_score = int(score)
    brief.rating_comment = (comment or "").strip()
    brief.rating_event_id = str(data.get("event_id") or brief.rating_event_id or rating_event_id(brief, version=1))
    brief.rating_sent_at = timezone.now()
    brief.rating_pending = False
    avg = data.get("avg_rating")
    count = data.get("ratings_count")
    if avg is not None and avg != "":
        try:
            brief.rating_hub_avg = Decimal(str(avg)).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError, TypeError):
            pass
    if count is not None and count != "":
        try:
            brief.rating_hub_count = int(count)
        except (TypeError, ValueError):
            pass
    brief.save()
    status = str(data.get("status") or detail or "created")
    if status == "duplicate":
        return True, "Оценка уже была принята HUB (duplicate)"
    avg_s = f", avg={brief.rating_hub_avg}" if brief.rating_hub_avg is not None else ""
    return True, f"Оценка {brief.rating_score}/5 отправлена в HUB{avg_s}"


def brief_payload(brief: ModelingBrief) -> dict[str, Any]:
    return {
        "local_brief_id": brief.id,
        "brief_number": brief.brief_number,
        "client_ref": str(brief.client_id),
        "model_url": brief.model_url or "",
        "description": brief.description or "",
        "agreed_price": str(brief.agreed_price),
        "designer_share_amount": str(brief.designer_share_amount),
        "site_share_amount": str(brief.site_share_amount),
        "has_stl": bool(brief.stl_file),
        "screenshots_count": brief.screenshots.count(),
    }


def push_brief_to_hub(brief: ModelingBrief) -> tuple[bool, str, dict[str, Any]]:
    """Create or update brief on HUB. Returns (ok, detail, response_json)."""
    cfg = HubConnectionSettings.get_solo()
    if not cfg.enabled or not cfg.hub_base_url or not cfg.site_token or not cfg.site_secret:
        return False, "Интеграция с хабом не настроена (admin → HUB)", {}

    payload = brief_payload(brief)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    base = cfg.hub_base_url.rstrip("/")
    if brief.hub_brief_id:
        url = f"{base}/api/v1/briefs/{brief.hub_brief_id}"
        method = "POST"  # update/resubmit
    else:
        url = f"{base}/api/v1/briefs"
        method = "POST"

    req = urlrequest.Request(url, data=body, headers=_headers(cfg, body), method=method)
    try:
        with urlrequest.urlopen(req, timeout=25) as resp:
            raw = resp.read().decode("utf-8") or "{}"
            data = json.loads(raw)
            return True, "ok", data if isinstance(data, dict) else {}
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        logger.warning("HUB push failed HTTP %s: %s", exc.code, detail)
        return False, f"HUB HTTP {exc.code}: {detail}", {}
    except Exception as exc:
        logger.exception("HUB push failed")
        return False, str(exc)[:500], {}


def notify_staff_max(*, text: str, max_user_id: str = "") -> tuple[bool, str]:
    """Send Max message to manager/admin (always logged to SmsLog + console)."""
    from workshop.messaging import send_message
    from workshop.models import SmsKind, SmsSettings, YandexAiSettings

    cfg = SmsSettings.get_solo()
    uid = (max_user_id or "").strip()
    if not uid:
        ai = YandexAiSettings.get_solo()
        uid = (ai.admin_max_user_id or "").strip()
    if not uid:
        detail = "Не указан Max user_id менеджера/админа"
        from workshop.messaging import _log

        _log(
            kind=SmsKind.SYSTEM,
            phone="",
            text=text,
            success=False,
            provider=cfg.provider,
            response=detail,
            username="hub-staff",
        )
        return False, detail
    result = send_message(
        phone=f"max:{uid}",
        text=text,
        kind=SmsKind.SYSTEM,
        username="hub-staff",
        max_user_id=uid,
        force=True,
    )
    return result.success, result.response


def notify_client_max(client, text: str) -> tuple[bool, str]:
    """Send Max message to client (always logged to SmsLog + console)."""
    from workshop.messaging import send_message
    from workshop.models import SmsKind

    result = send_message(
        phone=getattr(client, "phone", "") or "",
        text=text,
        kind=SmsKind.SYSTEM,
        client=client,
        username="hub-client",
        force=True,
    )
    return result.success, result.response


def apply_hub_brief_event(payload: dict[str, Any]) -> tuple[bool, str]:
    """Apply inbound HUB webhook for a modeling brief. Idempotent by event_id."""
    from django.utils import timezone

    from workshop.models import HubWebhookEvent, ModelingBrief, ModelingBriefStatus

    event_id = str(payload.get("event_id") or "").strip()
    if not event_id:
        return False, "event_id required"
    if HubWebhookEvent.objects.filter(event_id=event_id).exists():
        return True, "duplicate"

    event = str(payload.get("event") or payload.get("type") or "").strip().lower()
    local_id = payload.get("local_brief_id")
    hub_brief_id = str(payload.get("brief_id") or payload.get("hub_brief_id") or "").strip()

    brief = None
    if local_id:
        brief = ModelingBrief.objects.filter(pk=local_id).select_related("client").first()
    if not brief and hub_brief_id:
        brief = ModelingBrief.objects.filter(hub_brief_id=hub_brief_id).select_related("client").first()
    if not brief:
        return False, "brief not found"

    HubWebhookEvent.objects.create(
        event_id=event_id,
        payload=json.dumps(payload, ensure_ascii=False)[:8000],
    )

    message = str(payload.get("message") or payload.get("text") or "").strip()
    designer_name = str(payload.get("designer_name") or "").strip()
    designer_id = str(payload.get("designer_id") or "").strip()
    eta = str(payload.get("eta") or "").strip()
    if hub_brief_id and not brief.hub_brief_id:
        brief.hub_brief_id = hub_brief_id
    if designer_name:
        brief.designer_name = designer_name
    if designer_id:
        brief.designer_id = designer_id
    if eta:
        brief.eta = eta
    if message:
        brief.last_hub_message = message

    if event in {"taken_in_work", "assigned", "in_progress"}:
        brief.status = (
            ModelingBriefStatus.IN_PROGRESS if event == "in_progress" else ModelingBriefStatus.ASSIGNED
        )
        brief.manager_alert = False
        brief.save()
        notify_staff_max(
            text=(
                f"3D {brief.brief_number}: взято в работу.\n"
                f"Дизайнер: {brief.designer_name or '—'} (id {brief.designer_id or '—'})\n"
                f"Срок: {brief.eta or '—'}"
            )
        )
        return True, "taken"

    if event in {"needs_clarification", "clarification"}:
        brief.status = ModelingBriefStatus.NEEDS_CLARIFICATION
        brief.manager_alert = True
        brief.save()
        client_text = message or (
            f"Здравствуйте! По заявке на 3D-моделирование {brief.brief_number} "
            "нужны уточнения. Пожалуйста, свяжитесь с мастерской или ответьте в этот чат."
        )
        notify_client_max(brief.client, client_text)
        notify_staff_max(
            text=f"3D {brief.brief_number}: требуется переуточнение у клиента.\n{message or ''}".strip()
        )
        return True, "clarification"

    if event in {"done", "completed", "closed", "finished"}:
        brief.status = ModelingBriefStatus.DONE
        brief.manager_alert = True
        brief.done_at = timezone.now()
        brief.mark_rating_pending_if_needed()
        brief.save()
        notify_client_max(
            brief.client,
            message
            or (
                f"Здравствуйте! 3D-модель по заявке {brief.brief_number} готова. "
                "Можно забирать в мастерской."
            ),
        )
        notify_staff_max(
            text=(
                f"3D {brief.brief_number}: выполнено.\n"
                f"Клиент: {brief.client.name}. Сумма: {brief.agreed_price} "
                f"(точка {brief.site_share_amount} / дизайнер {brief.designer_share_amount})"
            )
        )
        return True, "done"

    if event in {"cancelled", "canceled"}:
        brief.status = ModelingBriefStatus.CANCELLED
        brief.manager_alert = True
        brief.save()
        notify_staff_max(text=f"3D {brief.brief_number}: отменено на хабе.\n{message or ''}".strip())
        return True, "cancelled"

    if event in {"queued"}:
        brief.status = ModelingBriefStatus.QUEUED
        brief.save()
        return True, "queued"

    brief.save()
    return True, f"stored:{event or 'unknown'}"
