from __future__ import annotations

import os
from io import BytesIO
from textwrap import wrap

from django.conf import settings
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


MAX_MAILING_CONSENT = (
    "* Клиент даёт согласие на информационную рассылку в мессенджере Max."
)


def _font_name() -> str:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            try:
                pdfmetrics.registerFont(TTFont("AppFont", candidate))
                return "AppFont"
            except Exception:
                continue
    return "Helvetica"


def _normalize_multiline(text: str) -> list[str]:
    """Split user text into lines; support Enter and Shift+Enter style breaks."""
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    # Remove form-feed / weird control chars that show as boxes in PDF
    raw = "".join(ch if (ch == "\n" or ch >= " ") else " " for ch in raw)
    return raw.split("\n")


def _draw_wrapped(c, font: str, size: int, text: str, x: float, y: float, max_width_chars: int, min_y: float, page_height: float) -> float:
    c.setFont(font, size)
    for paragraph in _normalize_multiline(text):
        chunks = wrap(paragraph, width=max_width_chars) if paragraph.strip() else [""]
        for chunk in chunks:
            if y < min_y:
                c.showPage()
                c.setFont(font, size)
                y = page_height - 40
            c.drawString(x, y, chunk)
            y -= size + 3
    return y


def build_order_pdf(order, lines) -> bytes:
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - 40
    font = _font_name()

    c.setFont(font, 16)
    c.drawString(40, y, f"Заказ-наряд {order.order_number}")
    y -= 22
    c.setFont(font, 11)
    c.drawString(40, y, f"{settings.COMPANY_NAME}, тел.: {settings.COMPANY_PHONE}")
    y -= 16
    c.drawString(40, y, f"Контроль качества: {settings.QUALITY_PHONE}")
    y -= 16
    c.drawString(40, y, f"Адрес: {settings.COMPANY_ADDRESS}")
    y -= 16
    client_name = order.client.name if order.client else "Без клиента"
    client_phone = order.client.phone if order.client else ""
    c.drawString(40, y, f"Клиент: {client_name}  {client_phone}")
    y -= 16
    created = order.created_at.strftime("%d.%m.%Y %H:%M")
    c.drawString(40, y, f"Дата: {created}")
    y -= 22
    c.drawString(40, y, f"Устройство: {order.device_type}")
    y -= 16
    if getattr(order, "additive_services_enabled", False):
        additive = getattr(order, "additive_service_type", "") or "-"
        c.drawString(40, y, f"Аддитивные услуги: {additive}")
        y -= 16
    else:
        c.drawString(40, y, f"Доп. периферия: {order.extra_periphery or '-'}")
        y -= 16

    c.setFont(font, 10)
    c.drawString(40, y, "Услуга")
    c.drawString(350, y, "Цена")
    c.drawString(430, y, "Кол-во")
    c.drawString(500, y, "Сумма")
    y -= 10
    c.line(40, y, width - 40, y)
    y -= 14

    for line in lines:
        if y < 120:
            c.showPage()
            c.setFont(font, 10)
            y = height - 50
        line_total = float(line.unit_price) * int(line.quantity)
        c.drawString(40, y, str(line.service_name)[:52])
        c.drawRightString(400, y, f"{float(line.unit_price):.2f}")
        c.drawRightString(470, y, f"{int(line.quantity)}")
        c.drawRightString(555, y, f"{line_total:.2f}")
        y -= 14

    y -= 8
    c.line(40, y, width - 40, y)
    y -= 20
    c.setFont(font, 12)
    if float(order.discount_percent or 0) > 0:
        discount_amount = float(order.subtotal_sum) - float(order.total_sum)
        c.drawRightString(width - 40, y, f"Сумма расчёта: {float(order.subtotal_sum):.2f}")
        y -= 16
        c.drawRightString(
            width - 40,
            y,
            f"Дополнительная скидка: {float(order.discount_percent):.0f}% (−{discount_amount:.2f})",
        )
        y -= 16
    c.drawRightString(width - 40, y, f"ИТОГО: {float(order.total_sum):.2f}")
    y -= 28

    warranty = (
        "Гарантия: На выполненные работы и установленные новые детали предоставляется гарантия 3 месяца. "
        "Гарантия не распространяется на программное обеспечение и устранение последствий некорректного использования."
    )
    y = _draw_wrapped(c, font, 10, warranty, 40, y, 95, 80, height)
    y -= 8
    y = _draw_wrapped(
        c,
        font,
        10,
        f"Техническая информация/рекомендации: {order.technical_notes or '-'}",
        40,
        y,
        95,
        80,
        height,
    )
    y -= 18
    if y < 100:
        c.showPage()
        y = height - 40
        c.setFont(font, 10)
    c.setFont(font, 10)
    c.drawString(40, y, f"Исполнитель: _________________ / {settings.MASTER_SIGN}")
    y -= 14
    c.drawString(40, y, "(Подпись) (Ф.И.О.)")
    y -= 18
    c.drawString(40, y, "Заказчик с работами ознакомлен, результат меня устраивает, претензий не имею.")
    y -= 16
    c.drawString(40, y, "Заказчик:___________________ / __________________________ / «        » _______ 2026г.")
    y -= 14
    c.drawString(40, y, "(Подпись) (Ф.И.О.) (Дата)")
    y -= 22
    if y < 40:
        c.showPage()
        y = height - 40
    y = _draw_wrapped(c, font, 9, MAX_MAILING_CONSENT, 40, y, 100, 30, height)
    c.save()
    buffer.seek(0)
    return buffer.getvalue()


def build_acceptance_act_pdf(act) -> bytes:
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - 40
    font = _font_name()

    c.setFont(font, 16)
    c.drawString(40, y, f"Акт приёма-передачи техники {act.act_number}")
    y -= 24
    c.setFont(font, 11)
    c.drawString(40, y, f"{settings.COMPANY_NAME}, тел.: {settings.COMPANY_PHONE}")
    y -= 16
    c.drawString(40, y, f"Адрес: {settings.COMPANY_ADDRESS}")
    y -= 20
    c.drawString(40, y, f"Дата приёма: {act.created_at.strftime('%d.%m.%Y %H:%M')}")
    y -= 16
    c.drawString(40, y, f"Клиент: {act.client.name}  {act.client.phone}")
    y -= 16
    if act.order_id:
        c.drawString(40, y, f"Связанный заказ-наряд: {act.order.order_number}")
        y -= 16
    c.drawString(40, y, f"Тип устройства: {act.device_type}")
    y -= 16
    c.drawString(40, y, f"Марка / модель: {act.brand_model or '-'}")
    y -= 16
    c.drawString(40, y, f"Серийный номер: {act.serial_number or '-'}")
    y -= 16
    c.drawString(40, y, f"Пароль / PIN: {act.password_info or '-'}")
    y -= 20

    y = _draw_wrapped(c, font, 10, f"Комплектация: {act.accessories or '-'}", 40, y, 95, 80, height)
    y -= 6
    y = _draw_wrapped(c, font, 10, f"Внешний вид / повреждения: {act.appearance or '-'}", 40, y, 95, 80, height)
    y -= 6
    y = _draw_wrapped(c, font, 10, f"Заявленная неисправность: {act.declared_defect}", 40, y, 95, 80, height)
    y -= 6
    y = _draw_wrapped(c, font, 10, f"Примечания: {act.notes or '-'}", 40, y, 95, 80, height)
    y -= 24

    notice = (
        "Клиент передаёт указанную технику в сервис для диагностики/ремонта. "
        "Мастерская не несёт ответственности за данные на носителях при отсутствии резервной копии. "
        "Ориентировочные сроки и стоимость работ сообщаются после диагностики."
    )
    y = _draw_wrapped(c, font, 10, notice, 40, y, 95, 80, height)
    y -= 28
    if y < 120:
        c.showPage()
        y = height - 40
    c.setFont(font, 10)
    c.drawString(40, y, f"Принял: _________________ / {settings.MASTER_SIGN}")
    y -= 28
    c.drawString(40, y, "Сдал (клиент): _________________ / __________________________")
    y -= 16
    c.drawString(40, y, "(Подпись) (Ф.И.О.)")
    y -= 22
    if y < 40:
        c.showPage()
        y = height - 40
    y = _draw_wrapped(c, font, 9, MAX_MAILING_CONSENT, 40, y, 100, 30, height)
    c.save()
    buffer.seek(0)
    return buffer.getvalue()
