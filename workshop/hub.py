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
    """Send Max message to manager/admin."""
    from workshop.messaging import send_max_message
    from workshop.models import SmsSettings, YandexAiSettings

    cfg = SmsSettings.get_solo()
    token = (cfg.bot_token or "").strip()
    if not token:
        return False, "Нет токена бота Max"
    uid = (max_user_id or "").strip()
    if not uid:
        ai = YandexAiSettings.get_solo()
        uid = (ai.admin_max_user_id or "").strip()
    if not uid:
        return False, "Не указан Max user_id менеджера/админа"
    try:
        send_max_message(token=token, user_id=uid, text=text)
        return True, "sent"
    except Exception as exc:
        return False, str(exc)[:500]


def notify_client_max(client, text: str) -> tuple[bool, str]:
    from workshop.messaging import send_max_message
    from workshop.models import SmsSettings

    uid = (getattr(client, "max_user_id", None) or "").strip()
    if not uid:
        return False, "У клиента нет Max user_id"
    cfg = SmsSettings.get_solo()
    token = (cfg.bot_token or "").strip()
    if not token:
        return False, "Нет токена бота Max"
    try:
        send_max_message(token=token, user_id=uid, text=text)
        return True, "sent"
    except Exception as exc:
        return False, str(exc)[:500]


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

    if event in {"done", "completed"}:
        brief.status = ModelingBriefStatus.DONE
        brief.manager_alert = True
        brief.done_at = timezone.now()
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
