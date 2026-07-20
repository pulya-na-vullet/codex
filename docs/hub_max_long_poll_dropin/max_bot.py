"""Max messenger client + long-poll for HUB designer bot."""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from django.conf import settings

from .services import process_bot_message

logger = logging.getLogger(__name__)


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
    }
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8") or "{}"
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"max_http_{exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"network_error: {exc.reason}") from exc


def send_max_message(*, token: str, user_id: int | str, text: str) -> dict[str, Any]:
    return _json_request(
        "POST",
        "/messages",
        token=token,
        query={"user_id": int(user_id)},
        body={"text": text},
    )


def extract_user_and_text(update: dict[str, Any]) -> tuple[str, str, str]:
    """Return (user_id, text, update_type) from a Max update object."""
    update_type = str(update.get("update_type") or "")
    message = update.get("message") or {}
    sender = message.get("sender") or update.get("user") or {}
    user_id = sender.get("user_id")
    if user_id is None:
        user_id = ""
    text = (message.get("body") or {}).get("text") or ""
    return str(user_id).strip(), str(text).strip(), update_type


def process_max_update(update: dict[str, Any], *, token: str = "", welcome_text: str = "") -> str | None:
    """Handle one Max update; send reply to chat when token is set. Returns reply text."""
    user_id, text, update_type = extract_user_and_text(update)
    if not user_id:
        return None

    if update_type == "bot_started" or (
        update_type == "message_created" and text.lower() in {"/start", "start"}
    ):
        reply = (welcome_text or "").strip() or (
            "Здравствуйте! Для регистрации дизайнера отправьте точно:\nРегистрация: Дизайнер"
        )
        if token:
            try:
                send_max_message(token=token, user_id=user_id, text=reply)
            except Exception:
                logger.exception("Failed Max welcome to user_id=%s", user_id)
        return reply

    if update_type and update_type != "message_created":
        return None
    if not text:
        return None

    reply = process_bot_message(max_user_id=user_id, text=text).text
    if token and reply:
        try:
            send_max_message(token=token, user_id=user_id, text=reply)
        except Exception:
            logger.exception("Failed Max reply to user_id=%s", user_id)
    return reply


def process_updates_payload(payload: dict[str, Any] | list[Any], *, token: str = "", welcome_text: str = "") -> list[str]:
    updates: list[Any]
    if isinstance(payload, list):
        updates = payload
    elif isinstance(payload, dict):
        if "updates" in payload:
            updates = payload.get("updates") or []
        elif payload.get("update_type"):
            updates = [payload]
        elif payload.get("user_id") and payload.get("text"):
            # Simplified test/manual format
            return [
                process_bot_message(
                    max_user_id=str(payload["user_id"]).strip(),
                    text=str(payload["text"]).strip(),
                ).text
            ]
        else:
            updates = []
    else:
        updates = []

    replies: list[str] = []
    for update in updates:
        if not isinstance(update, dict):
            continue
        try:
            reply = process_max_update(update, token=token, welcome_text=welcome_text)
            if reply:
                replies.append(reply)
        except Exception:
            logger.exception("Failed to process Max update")
    return replies


def _max_long_poll_loop(stop_event: threading.Event) -> None:
    from django.db import close_old_connections
    from django.db.utils import OperationalError, ProgrammingError

    from .models import MaxBotSettings

    marker: int | None = None
    while not stop_event.is_set():
        try:
            close_old_connections()
            cfg = MaxBotSettings.get_solo()
            token = (cfg.bot_token or "").strip()
            if not cfg.long_poll_enabled or not token:
                stop_event.wait(3.0)
                continue

            if marker is None and cfg.updates_marker is not None:
                marker = int(cfg.updates_marker)

            query: dict[str, Any] = {"limit": 100, "timeout": 25}
            if marker is not None:
                query["marker"] = marker

            try:
                payload = _json_request("GET", "/updates", token=token, query=query, timeout=35)
            except Exception:
                logger.exception("Max long-poll /updates failed")
                stop_event.wait(5.0)
                continue

            updates = payload.get("updates") or []
            new_marker = payload.get("marker")
            for update in updates:
                try:
                    process_max_update(update, token=token, welcome_text=cfg.welcome_text or "")
                except Exception:
                    logger.exception("Failed to process Max update")
            if new_marker is not None:
                try:
                    marker = int(new_marker)
                except (TypeError, ValueError):
                    marker = None
                if marker is not None:
                    MaxBotSettings.objects.filter(pk=cfg.pk).update(updates_marker=marker)
        except (OperationalError, ProgrammingError):
            logger.warning("Max long-poll: database not ready, retrying")
            stop_event.wait(5.0)
        except Exception:
            logger.exception("Max long-poll worker crashed iteration")
            stop_event.wait(5.0)


_worker_stop: threading.Event | None = None
_worker_thread: threading.Thread | None = None
_worker_lock = threading.Lock()


def start_max_long_poll_worker() -> None:
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
            name="hub-max-long-poll",
            daemon=True,
        )
        _worker_thread.start()
        logger.info("HUB Max long-poll worker started")


def stop_max_long_poll_worker() -> None:
    global _worker_stop, _worker_thread
    with _worker_lock:
        if _worker_stop is not None:
            _worker_stop.set()
        if _worker_thread is not None:
            _worker_thread.join(timeout=2.0)
        _worker_stop = None
        _worker_thread = None


def restart_max_long_poll_worker() -> None:
    stop_max_long_poll_worker()
    start_max_long_poll_worker()


def is_max_long_poll_running() -> bool:
    return bool(_worker_thread and _worker_thread.is_alive())
