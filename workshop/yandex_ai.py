"""YandexGPT daily work report for admin."""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, time as dt_time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

MSK = ZoneInfo("Europe/Moscow")
YANDEX_COMPLETION_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"


def get_or_create_ai_settings():
    from workshop.models import YandexAiSettings

    return YandexAiSettings.get_solo()


def msk_day_bounds(day=None) -> tuple[datetime, datetime]:
    """Return timezone-aware [start, end) for a Moscow calendar day."""
    if day is None:
        day = timezone.now().astimezone(MSK).date()
    start = datetime.combine(day, dt_time.min, tzinfo=MSK)
    end = start + timedelta(days=1)
    return start, end


def collect_day_facts(day=None) -> dict[str, Any]:
    """Collect revenue + audit anomalies for a Moscow working day."""
    from workshop.models import AuditLog, Order, OrderStatus, PaymentMethod

    start, end = msk_day_bounds(day)
    day = start.date()

    created_orders = list(
        Order.objects.filter(created_at__gte=start, created_at__lt=end).select_related("client")
    )
    paid_orders = list(
        Order.objects.filter(
            payment_at__gte=start,
            payment_at__lt=end,
            payment_method__in=[PaymentMethod.CASH, PaymentMethod.TRANSFER],
        ).select_related("client")
    )
    revenue = sum((o.total_sum for o in paid_orders), Decimal("0"))
    created_sum = sum((o.total_sum for o in created_orders), Decimal("0"))

    logs = list(
        AuditLog.objects.filter(created_at__gte=start, created_at__lt=end).order_by("created_at")[:5000]
    )
    anomaly_actions = {
        "order_delete",
        "acceptance_delete",
        "client_delete",
        "service_delete",
        "order_line_delete",
    }
    anomalies = [log for log in logs if log.action in anomaly_actions]
    action_counts: dict[str, int] = {}
    for log in logs:
        action_counts[log.action] = action_counts.get(log.action, 0) + 1

    done_today = Order.objects.filter(
        status=OrderStatus.DONE,
        closed_at__gte=start,
        closed_at__lt=end,
    ).count()

    return {
        "day": day.isoformat(),
        "day_display": day.strftime("%d.%m.%Y"),
        "revenue": revenue,
        "created_orders_count": len(created_orders),
        "created_orders_sum": created_sum,
        "paid_orders_count": len(paid_orders),
        "done_orders_count": done_today,
        "audit_total": len(logs),
        "action_counts": action_counts,
        "anomalies": [
            {
                "when": timezone.localtime(log.created_at, MSK).strftime("%H:%M:%S"),
                "user": log.username,
                "action": log.action,
                "details": (log.details or "")[:200],
            }
            for log in anomalies[:80]
        ],
        "log_sample": [
            {
                "when": timezone.localtime(log.created_at, MSK).strftime("%H:%M:%S"),
                "user": log.username,
                "action": log.action,
                "details": (log.details or "")[:160],
            }
            for log in logs[:200]
        ],
    }


def build_fallback_report(facts: dict[str, Any]) -> str:
    anomalies = facts.get("anomalies") or []
    if anomalies:
        anomaly_lines = [
            f"- {a['when']} {a['user']}: {a['action']} ({a['details']})" for a in anomalies[:20]
        ]
        anomaly_text = "\n".join(anomaly_lines)
    else:
        anomaly_text = "существенных аномалий не обнаружено"

    return (
        f"День: {facts['day_display']}\n"
        f"Выручка: {facts['revenue']:.2f} руб.\n"
        f"Аномалии работы:\n{anomaly_text}\n\n"
        f"(создано заказов: {facts['created_orders_count']}, "
        f"оплачено: {facts['paid_orders_count']}, "
        f"завершено: {facts['done_orders_count']}, "
        f"событий в журнале: {facts['audit_total']})"
    )


def yandex_completion(*, api_key: str, folder_id: str, model: str, prompt: str) -> str:
    model_uri = f"gpt://{folder_id}/{model}"
    payload = {
        "modelUri": model_uri,
        "completionOptions": {
            "stream": False,
            "temperature": 0.2,
            "maxTokens": 1200,
        },
        "messages": [
            {
                "role": "system",
                "text": (
                    "Ты аналитик работы ИТ-мастерской. По журналу действий и фактам дня "
                    "сформируй краткий отчёт строго на русском в формате:\n"
                    "День: <дата>\n"
                    "Выручка: <сумма> руб.\n"
                    "Аномалии работы:\n"
                    "- ...\n"
                    "Если аномалий нет — напиши, что существенных аномалий не обнаружено. "
                    "Аномалии: удаления заказов/актов/клиентов/услуг, массовые странные действия, "
                    "отмены, подозрительная активность. Не выдумывай факты вне данных."
                ),
            },
            {"role": "user", "text": prompt},
        ],
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        YANDEX_COMPLETION_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Api-Key {api_key}",
            "Content-Type": "application/json",
            "x-folder-id": folder_id,
            "User-Agent": "WorkshopApp/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Yandex AI HTTP {exc.code}: {err[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Yandex AI network: {exc.reason}") from exc

    data = json.loads(raw)
    alternatives = (((data.get("result") or {}).get("alternatives")) or [])
    if not alternatives:
        raise RuntimeError(f"Yandex AI empty result: {raw[:500]}")
    message = alternatives[0].get("message") or {}
    text = (message.get("text") or "").strip()
    if not text:
        raise RuntimeError("Yandex AI returned empty text")
    return text


def build_ai_prompt(facts: dict[str, Any]) -> str:
    return (
        "Проанализируй рабочий день сотрудника ИТ-мастерской.\n\n"
        f"Дата: {facts['day_display']}\n"
        f"Выручка по оплатам за день: {facts['revenue']:.2f} руб.\n"
        f"Создано заказ-нарядов: {facts['created_orders_count']} на сумму {facts['created_orders_sum']:.2f}\n"
        f"Оплачено заказов: {facts['paid_orders_count']}\n"
        f"Завершено заказов: {facts['done_orders_count']}\n"
        f"Всего событий журнала: {facts['audit_total']}\n"
        f"Счётчики действий: {json.dumps(facts['action_counts'], ensure_ascii=False)}\n\n"
        f"Удаления и аномальные действия:\n{json.dumps(facts['anomalies'], ensure_ascii=False)}\n\n"
        f"Выборка журнала:\n{json.dumps(facts['log_sample'], ensure_ascii=False)}\n"
    )


def generate_day_report(day=None, *, use_ai: bool = True) -> tuple[str, str]:
    """Return (report_text, source) where source is 'yandex'|'fallback'."""
    facts = collect_day_facts(day)
    cfg = get_or_create_ai_settings()
    if use_ai and cfg.api_key.strip() and cfg.folder_id.strip():
        try:
            text = yandex_completion(
                api_key=cfg.api_key.strip(),
                folder_id=cfg.folder_id.strip(),
                model=(cfg.model_name or "yandexgpt-lite").strip(),
                prompt=build_ai_prompt(facts),
            )
            # Ensure required headers present even if model rewrites style.
            if "День:" not in text:
                text = f"День: {facts['day_display']}\n" + text
            if "Выручка:" not in text:
                text = text.replace(
                    f"День: {facts['day_display']}\n",
                    f"День: {facts['day_display']}\nВыручка: {facts['revenue']:.2f} руб.\n",
                    1,
                )
            return text.strip(), "yandex"
        except Exception:
            logger.exception("Yandex AI report failed, using fallback")
    return build_fallback_report(facts), "fallback"


def resolve_admin_target(cfg) -> tuple[str, str, Any]:
    """Return (phone, max_user_id, client_or_none)."""
    from workshop.messaging import normalize_ru_phone
    from workshop.models import Client

    phone = (cfg.admin_phone or "").strip()
    max_user_id = (cfg.admin_max_user_id or "").strip()
    client = None
    if phone:
        digits = normalize_ru_phone(phone)
        for c in Client.objects.all().only("id", "phone", "max_user_id", "name"):
            if normalize_ru_phone(c.phone) == digits:
                client = c
                if not max_user_id and c.max_user_id:
                    max_user_id = c.max_user_id
                if not phone:
                    phone = c.phone
                break
    return phone, max_user_id, client


def send_report_to_admin(report: str, *, username: str = "ai-report") -> tuple[bool, str]:
    from workshop.messaging import send_max_message
    from workshop.models import Client, SmsKind, SmsLog, SmsProvider, SmsSettings

    cfg = get_or_create_ai_settings()
    phone, max_user_id, client = resolve_admin_target(cfg)
    msg_cfg = SmsSettings.get_solo()
    token = (msg_cfg.bot_token or "").strip()
    if not token:
        return False, "Не указан токен бота Max в админ-панели"
    if not max_user_id:
        return False, (
            "Администратор не привязан к Max. Укажите Max user_id или телефон клиента, "
            "который уже написал боту."
        )

    try:
        send_max_message(token=token, user_id=max_user_id, text=report)
        SmsLog.objects.create(
            kind=SmsKind.SYSTEM,
            phone=phone or f"max:{max_user_id}",
            text=report,
            success=True,
            provider=SmsProvider.MAX if msg_cfg.provider == SmsProvider.MAX else msg_cfg.provider,
            response=f"admin AI report user_id={max_user_id}",
            client=client if client and getattr(client, "pk", None) else None,
            username=username,
        )
        return True, "sent"
    except Exception as exc:
        SmsLog.objects.create(
            kind=SmsKind.SYSTEM,
            phone=phone or f"max:{max_user_id}",
            text=report,
            success=False,
            provider=msg_cfg.provider,
            response=str(exc)[:2000],
            client=client if client and getattr(client, "pk", None) else None,
            username=username,
        )
        return False, str(exc)


def report_schedule_parts(cfg) -> tuple[int, int]:
    hour = int(cfg.report_hour_msk if getattr(cfg, "report_hour_msk", None) is not None else 20)
    minute = int(getattr(cfg, "report_minute_msk", 0) or 0)
    return max(0, min(23, hour)), max(0, min(59, minute))


def now_msk() -> datetime:
    return timezone.now().astimezone(MSK)


def should_send_daily_report(cfg, now: datetime | None = None) -> bool:
    """True when today's report is due and has not been sent yet.

    Uses catch-up: after the configured MSK time, keep trying until success
    (or until the calendar day ends), instead of a narrow 3-minute window.
    """
    if not getattr(cfg, "enabled", False):
        return False
    current = now.astimezone(MSK) if now is not None else now_msk()
    if cfg.last_report_date == current.date():
        return False
    hour, minute = report_schedule_parts(cfg)
    scheduled = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return current >= scheduled


def run_daily_ai_report(*, day=None, force: bool = False) -> dict[str, Any]:
    cfg = get_or_create_ai_settings()
    if not cfg.enabled and not force:
        return {"ok": False, "detail": "Yandex AI отчёт отключён"}

    if day is None:
        day = now_msk().date()

    if not force and cfg.last_report_date == day:
        return {"ok": False, "detail": f"Отчёт за {day} уже отправлялся"}

    report, source = generate_day_report(day, use_ai=True)
    ok, detail = send_report_to_admin(report)
    cfg.last_report_text = report[:4000]
    cfg.last_report_error = "" if ok else (detail or "")[:1000]
    cfg.last_report_at = timezone.now()
    if ok:
        cfg.last_report_date = day
    cfg.save(
        update_fields=[
            "last_report_text",
            "last_report_error",
            "last_report_at",
            "last_report_date",
            "updated_at",
        ]
    )
    return {"ok": ok, "detail": detail, "source": source, "report": report, "day": day.isoformat()}


_due_check_lock = threading.Lock()
_due_check_last_mono = 0.0
_due_send_lock = threading.Lock()


def ensure_due_ai_report(*, force_check: bool = False, min_interval_sec: float = 20.0) -> dict[str, Any] | None:
    """If today's scheduled report is due, send it.

    Safe to call from the web request path as a fallback when the background
    thread is stuck/dead. Throttled so it does not run on every click.
    """
    global _due_check_last_mono
    if not getattr(settings, "YANDEX_AI_SCHEDULER", True):
        return None

    now_mono = time.monotonic()
    with _due_check_lock:
        if not force_check and (now_mono - _due_check_last_mono) < min_interval_sec:
            return None
        _due_check_last_mono = now_mono

    if not _due_send_lock.acquire(blocking=False):
        return None
    try:
        from django.db import close_old_connections

        close_old_connections()
        cfg = get_or_create_ai_settings()
        if not should_send_daily_report(cfg):
            return None
        hour, minute = report_schedule_parts(cfg)
        logger.info(
            "Due AI report triggered (schedule %02d:%02d MSK, server now %s)",
            hour,
            minute,
            now_msk().strftime("%Y-%m-%d %H:%M:%S"),
        )
        return run_daily_ai_report(day=now_msk().date(), force=False)
    except Exception:
        logger.exception("ensure_due_ai_report failed")
        return {"ok": False, "detail": "ensure_due_ai_report failed"}
    finally:
        _due_send_lock.release()


def _scheduler_loop(stop_event: threading.Event) -> None:
    from django.db import close_old_connections
    from django.db.utils import OperationalError, ProgrammingError

    while not stop_event.is_set():
        try:
            close_old_connections()
            result = ensure_due_ai_report(force_check=True, min_interval_sec=0)
            if result is not None:
                stop_event.wait(60.0 if result.get("ok") else 90.0)
                continue
        except (OperationalError, ProgrammingError):
            logger.warning("AI scheduler: database not ready")
            stop_event.wait(10.0)
            continue
        except Exception:
            logger.exception("AI scheduler iteration failed")
        # Near the scheduled minute poll more often.
        try:
            cfg = get_or_create_ai_settings()
            hour, minute = report_schedule_parts(cfg)
            now = now_msk()
            if cfg.enabled and now.hour == hour and abs(now.minute - minute) <= 1:
                stop_event.wait(5.0)
                continue
        except Exception:
            pass
        stop_event.wait(15.0)


_worker_stop: threading.Event | None = None
_worker_thread: threading.Thread | None = None
_worker_lock = threading.Lock()


def is_ai_report_scheduler_running() -> bool:
    return _worker_thread is not None and _worker_thread.is_alive()


def stop_ai_report_scheduler() -> None:
    global _worker_stop, _worker_thread
    with _worker_lock:
        if _worker_stop is not None:
            _worker_stop.set()
        thread = _worker_thread
        _worker_thread = None
        _worker_stop = None
    if thread is not None and thread.is_alive():
        thread.join(timeout=2.0)


def start_ai_report_scheduler() -> None:
    global _worker_stop, _worker_thread
    if not getattr(settings, "YANDEX_AI_SCHEDULER", True):
        return
    with _worker_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return
        _worker_stop = threading.Event()
        _worker_thread = threading.Thread(
            target=_scheduler_loop,
            args=(_worker_stop,),
            name="yandex-ai-daily-report",
            daemon=True,
        )
        _worker_thread.start()
        logger.info("Yandex AI daily report scheduler started")


def restart_ai_report_scheduler() -> None:
    stop_ai_report_scheduler()
    start_ai_report_scheduler()


def scheduler_status() -> dict[str, Any]:
    cfg = get_or_create_ai_settings()
    hour, minute = report_schedule_parts(cfg)
    current = now_msk()
    due = should_send_daily_report(cfg, current)
    return {
        "enabled": bool(cfg.enabled),
        "running": is_ai_report_scheduler_running(),
        "server_now_msk": current.strftime("%d.%m.%Y %H:%M:%S"),
        "schedule": f"{hour:02d}:{minute:02d}",
        "due_now": due,
        "last_report_date": cfg.last_report_date.isoformat() if cfg.last_report_date else "",
        "last_report_error": (cfg.last_report_error or "")[:300],
        "setting_enabled": getattr(settings, "YANDEX_AI_SCHEDULER", True),
    }
