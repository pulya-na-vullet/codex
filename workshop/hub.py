"""HUB integration helpers (HMAC + outbound stubs)."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import mimetypes
import time
import uuid
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import Any, BinaryIO
from urllib import error, request as urlrequest

from workshop.models import HubConnectionSettings, ModelingBrief

logger = logging.getLogger(__name__)

# Multipart bodies larger than this spool to disk instead of RAM.
_MULTIPART_SPOOL_MAX = 1 * 1024 * 1024
_READ_CHUNK = 64 * 1024


def sign_body(secret: str, timestamp: str, body: bytes) -> str:
    msg = timestamp.encode("utf-8") + b"\n" + body
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def sign_body_stream(secret: str, timestamp: str, body_stream: BinaryIO, *, chunk_size: int = _READ_CHUNK) -> str:
    """HMAC over timestamp + '\\n' + raw body bytes, reading the stream in chunks."""
    digester = hmac.new(secret.encode("utf-8"), digestmod=hashlib.sha256)
    digester.update(timestamp.encode("utf-8"))
    digester.update(b"\n")
    while True:
        chunk = body_stream.read(chunk_size)
        if not chunk:
            break
        digester.update(chunk)
    return digester.hexdigest()


def verify_signature(secret: str, timestamp: str, body: bytes, signature: str, *, max_skew: int = 300) -> bool:
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs(int(time.time()) - ts) > max_skew:
        return False
    expected = sign_body(secret, timestamp, body)
    return hmac.compare_digest(expected, (signature or "").strip())


def _headers(cfg: HubConnectionSettings, body: bytes, *, content_type: str = "application/json") -> dict[str, str]:
    ts = str(int(time.time()))
    return {
        "Content-Type": content_type,
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


def _extract_hub_brief_id(data: dict[str, Any], brief: ModelingBrief) -> str:
    return str(
        data.get("brief_id")
        or data.get("id")
        or brief.hub_brief_id
        or ""
    ).strip()


def _stl_filename(brief: ModelingBrief) -> str:
    name = Path(getattr(brief.stl_file, "name", "") or "").name
    if name:
        return name
    return f"{brief.brief_number or 'model'}.stl"


def build_stl_multipart(brief: ModelingBrief) -> tuple[SpooledTemporaryFile, str, int]:
    """Build multipart/form-data with field `file` into a spooled temp file."""
    if not brief.stl_file:
        raise ValueError("ModelingBrief has no stl_file")

    boundary = f"----CrmHubBoundary{uuid.uuid4().hex}"
    filename = _stl_filename(brief)
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    spool = SpooledTemporaryFile(max_size=_MULTIPART_SPOOL_MAX, mode="w+b")
    size = 0

    def _write(chunk: bytes) -> None:
        nonlocal size
        spool.write(chunk)
        size += len(chunk)

    preamble = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n"
        f"\r\n"
    ).encode("utf-8")
    _write(preamble)

    stl = brief.stl_file
    # FileField may already be open; open() is safe for Django FieldFile.
    opened = stl.open("rb")
    try:
        while True:
            chunk = opened.read(_READ_CHUNK)
            if not chunk:
                break
            _write(chunk)
    finally:
        # Don't close the FieldFile permanently if Django still needs it;
        # opened is the same object — reopen later if needed.
        try:
            opened.seek(0)
        except Exception:
            pass

    _write(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    spool.seek(0)
    return spool, f"multipart/form-data; boundary={boundary}", size


def upload_stl_to_hub(
    brief: ModelingBrief,
    hub_brief_id: str,
    *,
    cfg: HubConnectionSettings | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """POST binary STL to HUB /api/v1/briefs/{id}/source-stl (HMAC over raw multipart bytes)."""
    cfg = cfg or HubConnectionSettings.get_solo()
    hub_brief_id = (hub_brief_id or "").strip()
    if not hub_brief_id:
        return False, "нет brief_id для загрузки STL", {}
    if not brief.stl_file:
        return True, "no stl", {}

    base = (cfg.hub_base_url or "").rstrip("/")
    url = f"{base}/api/v1/briefs/{hub_brief_id}/source-stl"
    spool: SpooledTemporaryFile | None = None
    try:
        spool, content_type, body_len = build_stl_multipart(brief)
        ts = str(int(time.time()))
        spool.seek(0)
        signature = sign_body_stream(cfg.site_secret, ts, spool)
        spool.seek(0)
        headers = {
            "Content-Type": content_type,
            "Content-Length": str(body_len),
            "Authorization": f"Bearer {cfg.site_token}",
            "X-Site-Id": cfg.site_id,
            "X-Timestamp": ts,
            "X-Signature": signature,
        }
        req = urlrequest.Request(url, data=spool, headers=headers, method="POST")
        with urlrequest.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8") or "{}"
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = {"raw": raw[:500]}
            return True, "stl uploaded", data if isinstance(data, dict) else {}
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        logger.warning("HUB STL upload failed HTTP %s: %s", exc.code, detail)
        return False, f"HUB STL HTTP {exc.code}: {detail}", {}
    except Exception as exc:
        logger.exception("HUB STL upload failed")
        return False, f"STL upload error: {str(exc)[:400]}", {}
    finally:
        if spool is not None:
            try:
                spool.close()
            except Exception:
                pass


def push_brief_to_hub(brief: ModelingBrief) -> tuple[bool, str, dict[str, Any]]:
    """Create or update brief on HUB, then upload STL if present.

    Returns (ok, detail, response_json). If JSON succeeds but STL fails, ok=False and
    detail explains the STL error; response_json still contains brief_id when available.
    """
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
            if not isinstance(data, dict):
                data = {}
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        logger.warning("HUB push failed HTTP %s: %s", exc.code, detail)
        return False, f"HUB HTTP {exc.code}: {detail}", {}
    except Exception as exc:
        logger.exception("HUB push failed")
        return False, str(exc)[:500], {}

    hub_brief_id = _extract_hub_brief_id(data, brief)
    if hub_brief_id:
        data.setdefault("brief_id", hub_brief_id)

    if brief.stl_file:
        if not hub_brief_id:
            return (
                False,
                "Заявка принята HUB, но в ответе нет brief_id — STL не загружен",
                data,
            )
        stl_ok, stl_detail, stl_data = upload_stl_to_hub(brief, hub_brief_id, cfg=cfg)
        if stl_data:
            data["stl_upload"] = stl_data
        if not stl_ok:
            return (
                False,
                f"Заявка создана/обновлена в HUB (brief_id={hub_brief_id}), но STL не загружен: {stl_detail}",
                data,
            )
        return True, "ok+stl", data

    return True, "ok", data


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
