"""Messaging via Max messenger (replaces SMS.ru)."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.conf import settings

from workshop.models import Client, MarketingBlast, Order, SmsKind, SmsLog, SmsProvider, SmsSettings

logger = logging.getLogger(__name__)

PHONE_RE = re.compile(r"(?:\+7|8|7)\D*\d(?:\D*\d){9}")


@dataclass
class MessageResult:
    success: bool
    response: str = ""
    simulated: bool = False


def _digits_phone(phone: str) -> str:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    if len(digits) == 10:
        digits = "7" + digits
    return digits


def normalize_ru_phone(phone: str) -> str:
    return _digits_phone(phone)


def format_phone_display(phone: str) -> str:
    digits = normalize_ru_phone(phone)
    if len(digits) == 11 and digits.startswith("7"):
        return f"+7 ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    return phone or ""


def render_template(template: str, **kwargs) -> str:
    text = template or ""
    for key, value in kwargs.items():
        text = text.replace("{" + key + "}", "" if value is None else str(value))
    return text


def debt_context(order: Order) -> dict:
    client = order.client
    return {
        "name": client.name if client else "Клиент",
        "phone": client.phone if client else "",
        "order": order.order_number,
        "sum": f"{order.total_sum:.2f}".replace(".", ","),
        "company": getattr(settings, "COMPANY_NAME", "ИТ-мастерская"),
        "company_phone": getattr(settings, "COMPANY_PHONE", ""),
    }


def marketing_context(client: Client) -> dict:
    return {
        "name": client.name,
        "phone": client.phone,
        "company": getattr(settings, "COMPANY_NAME", "ИТ-мастерская"),
        "company_phone": getattr(settings, "COMPANY_PHONE", ""),
    }


def _api_base() -> str:
    return getattr(settings, "MAX_API_BASE", "https://platform-api2.max.ru").rstrip("/")


def _json_request(
    method: str,
    path: str,
    *,
    token: str,
    query: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    timeout: int = 35,
) -> dict[str, Any]:
    query = query or {}
    url = f"{_api_base()}{path}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"
    data = None
    headers = {
        "Authorization": token,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "WorkshopApp/1.0",
        "Connection": "close",
    }
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw": raw}
        code = payload.get("code") or f"http_{exc.code}"
        message = payload.get("message") or raw or str(exc)
        raise RuntimeError(f"{code}: {message}") from exc
    except (urllib.error.URLError, TimeoutError, ConnectionResetError, BrokenPipeError, OSError) as exc:
        reason = getattr(exc, "reason", None) or str(exc)
        raise RuntimeError(f"network_error: {reason}") from exc


def _is_transient_max_network_error(exc: BaseException) -> bool:
    """Long-poll resets/timeouts/offline are expected; reconnect without traceback spam."""
    if isinstance(
        exc,
        (
            TimeoutError,
            ConnectionResetError,
            BrokenPipeError,
            ConnectionAbortedError,
            ConnectionRefusedError,
            OSError,
        ),
    ):
        return True
    text = str(exc).lower()
    needles = (
        "network_error",
        "connection reset",
        "connection refused",
        "forcibly closed",
        "10054",
        "10061",
        "timed out",
        "timeout",
        "broken pipe",
        "connection aborted",
        "temporarily unavailable",
        "remote end closed",
        "errno 104",
        "errno 110",
        "errno 111",
        "отверг запрос",
        "подключение не установлено",
        "name or service not known",
        "nodename nor servname",
        "getaddrinfo failed",
        "failed to establish",
    )
    return any(n in text for n in needles)


def is_outbound_network_error(exc: BaseException | str) -> bool:
    """True for offline / firewall / DNS failures toward Max or Yandex."""
    if isinstance(exc, BaseException):
        if _is_transient_max_network_error(exc):
            return True
        text = str(exc)
    else:
        text = str(exc or "")
    return _is_transient_max_network_error(RuntimeError(text))


def send_max_message(
    *,
    token: str,
    user_id: int | str,
    text: str,
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"text": text}
    if attachments:
        body["attachments"] = attachments
    return _json_request(
        "POST",
        "/messages",
        token=token,
        query={"user_id": int(user_id)},
        body=body,
    )


def _contact_keyboard() -> list[dict[str, Any]]:
    return [
        {
            "type": "inline_keyboard",
            "payload": {
                "buttons": [
                    [{"type": "request_contact", "text": "Поделиться контактом"}],
                ]
            },
        }
    ]


def extract_phone_from_text(text: str) -> str | None:
    match = PHONE_RE.search(text or "")
    if not match:
        return None
    digits = normalize_ru_phone(match.group(0))
    return digits if len(digits) == 11 and digits.startswith("7") else None


def extract_phone_from_message(message: dict[str, Any]) -> str | None:
    """Phone from plain text or request_contact attachment."""
    body = message.get("body") or {}
    text_phone = extract_phone_from_text(body.get("text") or "")
    if text_phone:
        return text_phone

    for att in body.get("attachments") or message.get("attachments") or []:
        if not isinstance(att, dict):
            continue
        payload = att.get("payload") or {}
        max_info = payload.get("max_info") or {}
        for key in ("phone", "phone_number", "contact_phone"):
            value = max_info.get(key) or payload.get(key)
            if value:
                digits = normalize_ru_phone(str(value))
                if len(digits) == 11 and digits.startswith("7"):
                    return digits
        vcf = payload.get("vcf_info") or ""
        if vcf:
            digits = extract_phone_from_text(str(vcf))
            if digits:
                return digits
    return None


def find_client_by_phone_digits(phone_digits: str) -> Client | None:
    target = normalize_ru_phone(phone_digits)
    for client in Client.objects.all().only("id", "phone", "max_user_id", "name"):
        if normalize_ru_phone(client.phone) == target:
            return client
    return None


def _log(
    *,
    kind: str,
    phone: str,
    text: str,
    success: bool,
    provider: str,
    response: str,
    client: Client | None = None,
    order: Order | None = None,
    username: str = "",
    blast: MarketingBlast | None = None,
) -> None:
    SmsLog.objects.create(
        kind=kind,
        phone=phone or "",
        text=text,
        success=success,
        provider=provider,
        response=(response or "")[:2000],
        client=client,
        order=order,
        username=username,
        blast=blast,
    )
    _console_log_max_send(
        kind=kind,
        phone=phone or "",
        text=text,
        success=success,
        response=response or "",
        username=username,
        client=client,
        order=order,
    )


def _console_log_max_send(
    *,
    kind: str,
    phone: str,
    text: str,
    success: bool,
    response: str,
    username: str = "",
    client: Client | None = None,
    order: Order | None = None,
) -> None:
    """Human-readable Max send line for the server console."""
    status = "OK" if success else "FAIL"
    who = ""
    if client is not None:
        who = f" client={getattr(client, 'name', '') or client.pk}"
        mid = (getattr(client, "max_user_id", None) or "").strip()
        if mid:
            who += f" max_user_id={mid}"
    if order is not None:
        who += f" order={getattr(order, 'order_number', order.pk)}"
    preview = " ".join((text or "").split())
    if len(preview) > 220:
        preview = preview[:217] + "..."
    line = (
        f"Max send [{status}] kind={kind} to={phone or '—'}{who} "
        f"by={username or '-'} resp={ (response or '')[:160] } | text={preview!r}"
    )
    if success:
        logger.info(line)
    else:
        logger.warning(line)
    # Also print so Windows console (runserver) always shows it clearly.
    print(f"=== {line} ===", flush=True)
    _append_max_send_logfile(line)


def _append_max_send_logfile(line: str) -> None:
    try:
        from django.conf import settings

        log_dir = Path(settings.BASE_DIR) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "max_messages.log"
        from datetime import datetime

        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {line}\n")
    except Exception:
        logger.debug("Could not append max_messages.log", exc_info=True)


def send_message(
    *,
    phone: str,
    text: str,
    kind: str,
    client: Client | None = None,
    order: Order | None = None,
    username: str = "",
    force: bool = False,
    blast: MarketingBlast | None = None,
    max_user_id: str = "",
) -> MessageResult:
    cfg = SmsSettings.get_solo()
    normalized = _digits_phone(phone)
    phone_label = normalized or (phone or "")
    target_max_id = (max_user_id or "").strip()
    if not target_max_id and client is not None:
        target_max_id = (getattr(client, "max_user_id", None) or "").strip()

    if not (text or "").strip():
        result = MessageResult(success=False, response="Пустой текст сообщения")
        _log(
            kind=kind,
            phone=phone_label or (f"max:{target_max_id}" if target_max_id else ""),
            text=text or "",
            success=False,
            provider=cfg.provider,
            response=result.response,
            client=client,
            order=order,
            username=username,
            blast=blast,
        )
        return result

    if not cfg.enabled and not force:
        result = MessageResult(success=False, response="Рассылки отключены в админ-панели")
        _log(
            kind=kind,
            phone=phone_label or (f"max:{target_max_id}" if target_max_id else ""),
            text=text,
            success=False,
            provider=cfg.provider,
            response=result.response,
            client=client,
            order=order,
            username=username,
            blast=blast,
        )
        return result

    if kind == SmsKind.MARKETING and not cfg.marketing_enabled and not force:
        result = MessageResult(success=False, response="Маркетинг отключён в админ-панели")
        _log(
            kind=kind,
            phone=phone_label,
            text=text,
            success=False,
            provider=cfg.provider,
            response=result.response,
            client=client,
            order=order,
            username=username,
            blast=blast,
        )
        return result

    if cfg.provider == SmsProvider.LOG_ONLY:
        result = MessageResult(
            success=True,
            response="Симуляция: сообщение записано в журнал (канал «Только журнал»)",
            simulated=True,
        )
        _log(
            kind=kind,
            phone=phone_label or (f"max:{target_max_id}" if target_max_id else ""),
            text=text,
            success=True,
            provider=cfg.provider,
            response=result.response,
            client=client,
            order=order,
            username=username,
            blast=blast,
        )
        return result

    # Max messenger
    token = (cfg.bot_token or "").strip()
    if not token:
        result = MessageResult(success=False, response="Не указан токен бота Max")
        _log(
            kind=kind,
            phone=phone_label or (f"max:{target_max_id}" if target_max_id else ""),
            text=text,
            success=False,
            provider=cfg.provider,
            response=result.response,
            client=client,
            order=order,
            username=username,
            blast=blast,
        )
        return result

    if not target_max_id:
        result = MessageResult(
            success=False,
            response="Клиент не привязан к Max (нет user_id). Пусть напишет боту свой телефон.",
        )
        _log(
            kind=kind,
            phone=phone_label,
            text=text,
            success=False,
            provider=cfg.provider,
            response=result.response,
            client=client,
            order=order,
            username=username,
            blast=blast,
        )
        return result

    try:
        api_result = send_max_message(token=token, user_id=target_max_id, text=text)
        mid = ""
        if isinstance(api_result, dict):
            msg = api_result.get("message") or api_result
            if isinstance(msg, dict):
                body = msg.get("body") or {}
                mid = str(body.get("mid") or msg.get("message_id") or "")
        result = MessageResult(
            success=True,
            response=f"Max ok user_id={target_max_id}" + (f" mid={mid}" if mid else ""),
        )
    except Exception as exc:
        result = MessageResult(success=False, response=str(exc)[:2000])

    _log(
        kind=kind,
        phone=phone_label or f"max:{target_max_id}",
        text=text,
        success=result.success,
        provider=cfg.provider,
        response=result.response,
        client=client,
        order=order,
        username=username,
        blast=blast,
    )
    return result


def send_debt_message_for_order(order: Order, *, username: str = "") -> MessageResult:
    if not order.client:
        return MessageResult(success=False, response="У заказа нет клиента")
    if not order.is_debtor:
        return MessageResult(success=False, response="Заказ не является долгом")
    cfg = SmsSettings.get_solo()
    text = render_template(cfg.debt_template, **debt_context(order))
    return send_message(
        phone=order.client.phone,
        text=text,
        kind=SmsKind.DEBT,
        client=order.client,
        order=order,
        username=username,
    )


def send_marketing_message(
    client: Client,
    text: str,
    *,
    username: str = "",
    blast: MarketingBlast | None = None,
) -> MessageResult:
    if not client.allow_marketing_sms:
        return MessageResult(success=False, response="Клиент отключил маркетинговые сообщения")
    cfg = SmsSettings.get_solo()
    rendered = render_template(text or cfg.marketing_default_text, **marketing_context(client))
    return send_message(
        phone=client.phone,
        text=rendered,
        kind=SmsKind.MARKETING,
        client=client,
        username=username,
        blast=blast,
    )


@dataclass(frozen=True)
class StatusNotifyFlash:
    """UI feedback for Max status notifies. None from helpers = stay silent."""

    level: str  # success | warning | info
    text: str


def _flash_from_message_result(result: MessageResult, *, ok_text: str) -> StatusNotifyFlash:
    if result.success:
        if result.simulated:
            return StatusNotifyFlash(
                "info",
                "Max (тест/журнал): уведомление записано локально, в мессенджер не уходило.",
            )
        return StatusNotifyFlash("success", ok_text)
    return StatusNotifyFlash("warning", f"Max: не отправлено — {result.response}")


def _client_linked_to_max(client: Client | None) -> bool:
    return bool(client and (client.max_user_id or "").strip())


def send_order_done_message(order: Order, *, username: str = "") -> MessageResult:
    """Notify client in Max when order work is ready / completed."""
    client = order.client
    if not _client_linked_to_max(client):
        return MessageResult(success=False, response="Клиент не привязан к Max")
    cfg = SmsSettings.get_solo()
    text = render_template(cfg.order_done_template, **debt_context(order))
    return send_message(
        phone=client.phone,
        text=text,
        kind=SmsKind.SYSTEM,
        client=client,
        order=order,
        username=username or "status-bot",
    )


def send_diagnostics_done_message(act, *, username: str = "") -> MessageResult:
    """Notify client in Max when acceptance act diagnostics is done."""
    client = getattr(act, "client", None)
    if not _client_linked_to_max(client):
        return MessageResult(success=False, response="Клиент не привязан к Max")
    cfg = SmsSettings.get_solo()
    ctx = {
        "name": client.name,
        "phone": client.phone,
        "act": act.act_number,
        "device": f"{act.device_type} {act.brand_model or ''}".strip(),
        "company": getattr(settings, "COMPANY_NAME", "ИТ-М"),
        "company_phone": getattr(settings, "COMPANY_PHONE", ""),
    }
    text = render_template(cfg.diagnostics_done_template, **ctx)
    return send_message(
        phone=client.phone,
        text=text,
        kind=SmsKind.SYSTEM,
        client=client,
        username=username or "status-bot",
    )


def maybe_notify_order_done(order: Order, *, old_status: str, username: str = "") -> StatusNotifyFlash | None:
    """
    Send Max «заказ готов» when work becomes ready.

    - On «Работа выполнена — позвонить» (ready_call): notify if client has Max.
    - On «Выполнена» (done): notify only if we skipped ready_call (avoid double send).
    - No Max user_id: silent (no popup).
    """
    from workshop.models import OrderStatus

    should_send = False
    if order.status == OrderStatus.READY_CALL and old_status != OrderStatus.READY_CALL:
        should_send = True
    elif (
        order.status == OrderStatus.DONE
        and old_status != OrderStatus.DONE
        and old_status != OrderStatus.READY_CALL
    ):
        # Direct jump to done without ready_call step.
        should_send = True

    if not should_send:
        return None
    if not _client_linked_to_max(order.client):
        return None
    try:
        result = send_order_done_message(order, username=username)
    except Exception:
        logger.exception("Failed Max notify for order done %s", order.order_number)
        return StatusNotifyFlash("warning", "Max: ошибка отправки уведомления клиенту")
    return _flash_from_message_result(
        result,
        ok_text="Max: уведомление о готовности заказа отправлено клиенту",
    )


def maybe_notify_diagnostics_done(act, *, old_status: str, username: str = "") -> StatusNotifyFlash | None:
    from workshop.models import AcceptanceActStatus

    if act.status != AcceptanceActStatus.DIAGNOSTICS_DONE or old_status == AcceptanceActStatus.DIAGNOSTICS_DONE:
        return None
    if not _client_linked_to_max(getattr(act, "client", None)):
        return None
    try:
        result = send_diagnostics_done_message(act, username=username)
    except Exception:
        logger.exception("Failed Max notify for diagnostics done %s", act.act_number)
        return StatusNotifyFlash("warning", "Max: ошибка отправки уведомления клиенту")
    return _flash_from_message_result(
        result,
        ok_text="Max: уведомление о диагностике отправлено клиенту",
    )


def process_max_update(update: dict[str, Any], settings_obj: SmsSettings | None = None) -> None:
    """Link client by phone when they message the bot; reply with confirmation."""
    if settings_obj is None:
        settings_obj = SmsSettings.get_solo()

    token = (settings_obj.bot_token or "").strip()
    if not token:
        return

    update_type = update.get("update_type") or ""
    message = update.get("message") or {}
    sender = message.get("sender") or update.get("user") or {}
    user_id = sender.get("user_id")
    if user_id is None:
        return
    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        return

    text = (message.get("body") or {}).get("text") or ""
    if update_type == "bot_started" or (
        update_type == "message_created" and text.strip().lower() in {"/start", "start"}
    ):
        welcome = (settings_obj.welcome_text or "").strip() or (
            "Здравствуйте! Отправьте номер телефона в формате +7XXXXXXXXXX "
            "или нажмите «Поделиться контактом»."
        )
        try:
            send_max_message(
                token=token,
                user_id=user_id_int,
                text=welcome,
                attachments=_contact_keyboard(),
            )
        except Exception:
            logger.exception("Failed to send Max welcome to user_id=%s", user_id_int)
        return

    if update_type != "message_created":
        return

    phone_digits = extract_phone_from_message(message)
    if not phone_digits:
        try:
            send_max_message(
                token=token,
                user_id=user_id_int,
                text="Не распознал номер. Пришлите телефон +7XXXXXXXXXX или нажмите «Поделиться контактом».",
                attachments=_contact_keyboard(),
            )
        except Exception:
            logger.exception("Failed to send Max hint to user_id=%s", user_id_int)
        return

    client = find_client_by_phone_digits(phone_digits)
    if client is None:
        try:
            send_max_message(
                token=token,
                user_id=user_id_int,
                text=(
                    f"Номер {format_phone_display(phone_digits)} не найден в базе мастерской. "
                    "Обратитесь к администратору."
                ),
            )
        except Exception:
            logger.exception("Failed to send Max not-found to user_id=%s", user_id_int)
        return

    client.max_user_id = str(user_id_int)
    client.save(update_fields=["max_user_id"])
    try:
        send_max_message(
            token=token,
            user_id=user_id_int,
            text=f"Готово, {client.name}! Номер привязан. Теперь вы будете получать сообщения от мастерской.",
        )
    except Exception:
        logger.exception("Failed to send Max link confirmation to user_id=%s", user_id_int)

    _log(
        kind=SmsKind.SYSTEM,
        phone=client.phone,
        text=f"Клиент привязал Max user_id={user_id_int}",
        success=True,
        provider=SmsProvider.MAX,
        response=f"linked user_id={user_id_int}",
        client=client,
        username="max-bot",
    )


def process_updates_payload(payload: dict[str, Any] | list[Any]) -> None:
    """Handle webhook body: single update, list, or {updates: [...]}."""
    settings_obj = SmsSettings.get_solo()
    updates: list[Any]
    if isinstance(payload, list):
        updates = payload
    elif isinstance(payload, dict):
        if "updates" in payload:
            updates = payload.get("updates") or []
        elif payload.get("update_type"):
            updates = [payload]
        else:
            updates = []
    else:
        updates = []

    for update in updates:
        if isinstance(update, dict):
            try:
                process_max_update(update, settings_obj)
            except Exception:
                logger.exception("Failed to process Max update")


def _max_long_poll_loop(stop_event: threading.Event) -> None:
    from django.db import close_old_connections
    from django.db.utils import OperationalError, ProgrammingError

    marker: int | None = None
    backoff_sec = 1.0
    max_backoff = 60.0
    last_warn_mono = 0.0
    last_warn_key = ""
    warn_every_sec = 300.0  # don't spam console every reconnect
    while not stop_event.is_set():
        try:
            close_old_connections()
            settings_obj = SmsSettings.get_solo()
            if (
                not settings_obj.enabled
                or settings_obj.provider != SmsProvider.MAX
                or not settings_obj.long_poll_enabled
                or not (settings_obj.bot_token or "").strip()
            ):
                stop_event.wait(3.0)
                continue

            token = settings_obj.bot_token.strip()
            if marker is None and settings_obj.updates_marker is not None:
                marker = int(settings_obj.updates_marker)

            # Server waits ~25s; client timeout must be larger so idle polls don't look like hangs.
            query: dict[str, Any] = {"limit": 100, "timeout": 25}
            if marker is not None:
                query["marker"] = marker

            try:
                payload = _json_request("GET", "/updates", token=token, query=query, timeout=40)
            except Exception as exc:
                if _is_transient_max_network_error(exc):
                    # Connection refused / offline: back off harder than a one-off reset.
                    detail = str(exc)[:160]
                    refused = is_outbound_network_error(exc) and any(
                        x in detail.lower() for x in ("10061", "refused", "отверг", "111")
                    )
                    if refused:
                        backoff_sec = max(backoff_sec, 30.0)
                        max_here = 120.0
                    else:
                        max_here = max_backoff
                    now = time.monotonic()
                    key = detail[:80]
                    if key != last_warn_key or (now - last_warn_mono) >= warn_every_sec:
                        logger.warning(
                            "Max long-poll: нет связи с API (%s) — повтор через %.0f с "
                            "(это не ошибка CRM; проверьте интернет/VPN/firewall)",
                            detail,
                            backoff_sec,
                        )
                        last_warn_mono = now
                        last_warn_key = key
                    stop_event.wait(backoff_sec)
                    backoff_sec = min(max_here, backoff_sec * 1.7)
                    continue
                logger.exception("Max long-poll /updates failed")
                stop_event.wait(backoff_sec)
                backoff_sec = min(max_backoff, backoff_sec * 1.7)
                continue

            backoff_sec = 1.0
            last_warn_key = ""
            updates = payload.get("updates") or []
            new_marker = payload.get("marker")
            for update in updates:
                try:
                    process_max_update(update, settings_obj)
                except Exception:
                    logger.exception("Failed to process Max update")
            if new_marker is not None:
                try:
                    marker = int(new_marker)
                except (TypeError, ValueError):
                    marker = None
                if marker is not None:
                    SmsSettings.objects.filter(pk=settings_obj.pk).update(updates_marker=marker)
        except (OperationalError, ProgrammingError):
            logger.warning("Max long-poll: database not ready, retrying")
            stop_event.wait(5.0)
        except Exception:
            logger.exception("Max long-poll worker crashed iteration")
            stop_event.wait(min(max_backoff, backoff_sec))
            backoff_sec = min(max_backoff, backoff_sec * 1.7)


_worker_stop: threading.Event | None = None
_worker_thread: threading.Thread | None = None
_worker_lock = threading.Lock()


def start_max_long_poll_worker() -> None:
    """Start background Max updates long-poller (LAN / no public webhook)."""
    global _worker_stop, _worker_thread
    if not getattr(settings, "MAX_LONG_POLL_WORKER", True):
        return
    with _worker_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return
        _worker_stop = threading.Event()
        _worker_thread = threading.Thread(
            target=_max_long_poll_loop,
            args=(_worker_stop,),
            name="max-long-poll-worker",
            daemon=True,
        )
        _worker_thread.start()
        logger.info("Max long-poll worker started")


def stop_max_long_poll_worker() -> None:
    global _worker_stop, _worker_thread
    with _worker_lock:
        if _worker_stop is not None:
            _worker_stop.set()
        thread = _worker_thread
        _worker_thread = None
        _worker_stop = None
    if thread is not None and thread.is_alive():
        thread.join(timeout=2.0)


def restart_max_long_poll_worker() -> None:
    stop_max_long_poll_worker()
    start_max_long_poll_worker()


def is_max_long_poll_running() -> bool:
    return bool(_worker_thread and _worker_thread.is_alive())
