from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from django.conf import settings

from workshop.models import Client, Order, SmsKind, SmsLog, SmsProvider, SmsSettings


@dataclass
class SmsResult:
    success: bool
    response: str = ""
    simulated: bool = False


def _digits_phone(phone: str) -> str:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    if digits.startswith("7") and len(digits) == 11:
        return digits
    return digits


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


def _send_smsru(api_id: str, phone: str, text: str, sender: str = "") -> SmsResult:
    params = {
        "api_id": api_id,
        "to": phone,
        "msg": text,
        "json": "1",
    }
    if sender:
        params["from"] = sender
    url = "https://sms.ru/sms/send?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        status = str(data.get("status", ""))
        ok = status == "OK"
        return SmsResult(success=ok, response=raw[:2000])
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, ValueError) as err:
        return SmsResult(success=False, response=str(err)[:2000])


def send_sms(
    *,
    phone: str,
    text: str,
    kind: str,
    client: Client | None = None,
    order: Order | None = None,
    username: str = "",
    force: bool = False,
) -> SmsResult:
    cfg = SmsSettings.get_solo()
    normalized = _digits_phone(phone)
    if len(normalized) < 11:
        result = SmsResult(success=False, response="Некорректный телефон")
        SmsLog.objects.create(
            kind=kind,
            phone=phone or "",
            text=text,
            success=False,
            provider=cfg.provider,
            response=result.response,
            client=client,
            order=order,
            username=username,
        )
        return result

    if not cfg.enabled and not force:
        result = SmsResult(success=False, response="SMS отключены в админ-панели")
        SmsLog.objects.create(
            kind=kind,
            phone=normalized,
            text=text,
            success=False,
            provider=cfg.provider,
            response=result.response,
            client=client,
            order=order,
            username=username,
        )
        return result

    if kind == SmsKind.MARKETING and not cfg.marketing_enabled and not force:
        result = SmsResult(success=False, response="Маркетинг SMS отключён в админ-панели")
        SmsLog.objects.create(
            kind=kind,
            phone=normalized,
            text=text,
            success=False,
            provider=cfg.provider,
            response=result.response,
            client=client,
            order=order,
            username=username,
        )
        return result

    if cfg.provider == SmsProvider.SMSRU and cfg.api_id.strip():
        result = _send_smsru(cfg.api_id.strip(), normalized, text, cfg.sender.strip())
    else:
        result = SmsResult(
            success=True,
            response="Симуляция: SMS не уходила в шлюз (провайдер «Только журнал» или нет API ключа)",
            simulated=True,
        )

    SmsLog.objects.create(
        kind=kind,
        phone=normalized,
        text=text,
        success=result.success,
        provider=cfg.provider,
        response=result.response,
        client=client,
        order=order,
        username=username,
    )
    return result


def send_debt_sms_for_order(order: Order, *, username: str = "") -> SmsResult:
    if not order.client:
        return SmsResult(success=False, response="У заказа нет клиента")
    if not order.is_debtor:
        return SmsResult(success=False, response="Заказ не является долгом")
    cfg = SmsSettings.get_solo()
    text = render_template(cfg.debt_template, **debt_context(order))
    return send_sms(
        phone=order.client.phone,
        text=text,
        kind=SmsKind.DEBT,
        client=order.client,
        order=order,
        username=username,
    )


def send_marketing_sms(client: Client, text: str, *, username: str = "") -> SmsResult:
    if not client.allow_marketing_sms:
        return SmsResult(success=False, response="Клиент отключил маркетинговые SMS")
    cfg = SmsSettings.get_solo()
    rendered = render_template(text or cfg.marketing_default_text, **marketing_context(client))
    return send_sms(
        phone=client.phone,
        text=rendered,
        kind=SmsKind.MARKETING,
        client=client,
        username=username,
    )
