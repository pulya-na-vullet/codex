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


_ACCESS_DAYS_WORDS = {
    1: "Одного",
    2: "Двух",
    3: "Трёх",
    4: "Четырёх",
    5: "Пяти",
    6: "Шести",
    7: "Семи",
    10: "Десяти",
    14: "Четырнадцати",
    30: "Тридцати",
}


def build_software_chatbot_contract_pdf(contract) -> bytes:
    """Печатная форма договора авторского заказа (чат-бот) по образцу ИТ-М."""
    from workshop.money_words import amount_to_words_rub

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    height = A4[1]
    font = _font_name()
    y = height - 36
    left = 40
    max_chars = 95

    def line(text: str, size: int = 10, gap: int = 4) -> None:
        nonlocal y
        y = _draw_wrapped(c, font, size, text, left, y, max_chars, 50, height)
        y -= gap

    amount = contract.amount or 0
    amount_digits = f"{amount:.2f}".replace(".", ",")
    amount_words = amount_to_words_rub(amount)
    customer = contract.customer_display_name() or "________________________________"
    product = (contract.product_name or "").strip() or "________________________________"
    date_s = contract.contract_date.strftime("%d.%m.%Y") if contract.contract_date else "«__» __________ 20__ г."
    access_days = int(contract.access_days or 7)
    access_word = _ACCESS_DAYS_WORDS.get(access_days, str(access_days))

    c.setFont(font, 13)
    c.drawCentredString(A4[0] / 2, y, f"ДОГОВОР АВТОРСКОГО ЗАКАЗА № {contract.contract_number}")
    y -= 18
    line(f"{settings.COMPANY_ADDRESS}, {settings.COMPANY_NAME}, {date_s}", 9, 10)

    line(
        "Григорьев Дмитрий Вячеславович, в дальнейшем именуемый «Исполнитель», "
        "действующий как физическое лицо, применяющее специальный налоговый режим "
        "«Налог на профессиональный доход» (самозанятый), с одной стороны, и",
        10,
        6,
    )
    line(customer, 10, 4)
    line(
        "в дальнейшем именуемый «Заказчик», действующий как физическое лицо, с другой стороны, "
        "совместно именуемые «Стороны», заключили настоящий Договор о нижеследующем:",
        10,
        10,
    )

    line("1. ПРЕДМЕТ ДОГОВОРА", 11, 8)
    line("1.1. Исполнитель обязуется по заданию Заказчика разработать программный комплекс", 10, 4)
    line(product, 10, 4)
    line("и предоставить Заказчику доступ к его использованию через сеть Интернет.", 10, 6)
    line(
        "1.2. Конкретные требования к функционалу, составу, архитектуре, техническим "
        "характеристикам и срокам разработки определяются в Техническом задании "
        "(Приложение №1), которое является неотъемлемой частью настоящего Договора.",
        10,
        6,
    )
    line(
        "1.3. Исключительное право на созданный Программный комплекс (включая его исходный "
        "и объектный код, базы данных, архитектуру, дизайн, интерфейс, все компоненты и любые "
        "их части) в полном объеме принадлежит Исполнителю. Заказчику не передается ни "
        "исключительное, ни какое-либо иное право на Программный комплекс как на объект "
        "интеллектуальной собственности.",
        10,
        6,
    )
    line(
        "1.4. В рамках настоящего Договора Исполнитель предоставляет Заказчику право "
        "использования (доступ) к функционалу Бота способом, указанным в п. 1.5 Договора. "
        "Это право является строго личным, непередаваемым и не подлежит сублицензированию.",
        10,
        6,
    )
    line(
        f"1.5. Срок доступа: Исполнитель предоставляет Заказчику доступ к работе Бота на срок "
        f"{access_days} ({access_word}) календарных дней с момента подписания Акта сдачи-приемки "
        f"выполненных работ (Приложение №2). По истечении этого срока доступ к Боту автоматически "
        f"блокируется/прекращается, если Стороны не заключили отдельный договор на обслуживание "
        f"и поддержку работоспособности сервера.",
        10,
        6,
    )
    line(
        "1.6. Заказчик имеет право использовать Бот только следующими способами: отправлять "
        "текстовые и голосовые сообщения через интерфейс MAX; получать ответы и результаты "
        "работы Бота в личных, некоммерческих целях.",
        10,
        6,
    )
    line(
        "1.7. Заказчику строго запрещается: требовать передачи исходного кода, объектного кода, "
        "файлов баз данных или любой другой технической документации Программного комплекса; "
        "осуществлять декомпиляцию, дизассемблирование, модификацию или любое иное исследование "
        "кода Бота; передавать доступ к Боту третьим лицам или использовать его в коммерческих "
        "целях; копировать, распространять или создавать производные работы на основе "
        "Программного комплекса.",
        10,
        6,
    )
    line(
        "1.8. Условия по технической поддержке, обслуживанию сервера, обеспечению круглосуточной "
        "работы, продлению доступа и сопровождению Программного комплекса после истечения срока, "
        "указанного в п. 1.5, не являются предметом настоящего Договора и регулируются отдельным "
        "соглашением Сторон.",
        10,
        10,
    )

    line("2. ПОРЯДОК СДАЧИ-ПРИЕМКИ РАБОТ", 11, 8)
    line(
        "2.1. Исполнитель представляет результаты работы Заказчику путем предоставления доступа "
        "к Боту по сети Интернет.",
        10,
        6,
    )
    line(
        "2.2. Сдача-приемка выполненных работ оформляется подписанием Сторонами Акта "
        "сдачи-приемки выполненных работ (Приложение №2). В Акте Стороны подтверждают, что Бот "
        "создан в соответствии с Техническим заданием, развернут на сервере Исполнителя и "
        "доступен Заказчику.",
        10,
        6,
    )
    line(
        "2.3. В случае обнаружения недостатков в работе Бота Заказчик направляет Исполнителю "
        "мотивированный отказ в письменной форме. Исполнитель обязуется устранить недостатки в "
        "согласованный Сторонами срок. При отсутствии мотивированного отказа в течение 3 (Трёх) "
        "рабочих дней с момента получения Акта, работа считается принятой и подлежащей оплате.",
        10,
        10,
    )

    line("3. ПРАВА И ОБЯЗАННОСТИ СТОРОН", 11, 8)
    line(
        "3.1. Исполнитель обязуется: разработать Бот в соответствии с Техническим заданием; "
        "развернуть Бот на своем серверном оборудовании; предоставить Заказчику доступ к Боту на "
        "срок, указанный в п. 1.5 Договора; уведомить Заказчика о своем статусе самозанятого и "
        "выдать чек на сумму вознаграждения через приложение «Мой налог» после получения оплаты.",
        10,
        6,
    )
    line(
        "3.2. Заказчик обязуется: своевременно принять и оплатить работы; предоставить "
        "Исполнителю всю необходимую информацию для выполнения работ; использовать Бот "
        "исключительно в рамках прав, предоставленных настоящим Договором; не нарушать условия "
        "использования Бота, установленные Договором.",
        10,
        6,
    )
    line(
        "3.3. Исполнитель имеет право: использовать любые технологии, библиотеки, фреймворки и "
        "инструменты по своему усмотрению для достижения целей Технического задания; сохранять "
        "за собой исключительное право на Программный комплекс в полном объеме; указывать свое "
        "имя в качестве автора Программного комплекса.",
        10,
        10,
    )

    line("4. СТОИМОСТЬ РАБОТ И ПОРЯДОК РАСЧЕТОВ", 11, 8)
    line(
        f"4.1. Общая стоимость работ по разработке Программного комплекса и предоставлению "
        f"доступа к нему на срок {access_days} дней по настоящему Договору составляет "
        f"{amount_digits} ({amount_words}).",
        10,
        6,
    )
    if (contract.payment_variant or "B") == "A":
        prep = f"{(contract.prepayment_amount or 0):.2f}".replace(".", ",")
        due = contract.prepayment_due.strftime("%d.%m.%Y") if contract.prepayment_due else "[дата]"
        line(
            f"4.2. Вариант А: Оплата производится в следующем порядке: предоплата (аванс) в размере "
            f"{prep} рублей выплачивается в срок до {due}, оставшаяся часть выплачивается в течение "
            f"3 (Трёх) рабочих дней с момента подписания Акта сдачи-приемки выполненных работ "
            f"(Приложение №2).",
            10,
            6,
        )
    else:
        line(
            "4.2. Вариант Б: Оплата производится в течение 3 (Трёх) рабочих дней с момента "
            "подписания Сторонами Акта сдачи-приемки выполненных работ (Приложение №2).",
            10,
            6,
        )
    line(
        "4.3. Исполнитель в день получения оплаты обязан сформировать и передать Заказчику чек, "
        "сформированный через мобильное приложение «Мой налог», подтверждающий получение оплаты "
        "по настоящему Договору.",
        10,
        10,
    )

    line("5–9. ОТВЕТСТВЕННОСТЬ, ФОРС-МАЖОР, СРОК, ПРИЛОЖЕНИЯ, ЗАКЛЮЧИТЕЛЬНЫЕ ПОЛОЖЕНИЯ", 11, 8)
    line(
        "Стороны несут ответственность в соответствии с законодательством РФ. Исполнитель "
        "гарантирует оригинальность Программного комплекса и наличие необходимых прав. "
        "Договор действует до полного исполнения обязательств. Неотъемлемые приложения: "
        "№1 — Техническое задание (ТЗ); №2 — Акт сдачи-приемки выполненных работ. "
        "Споры разрешаются по месту нахождения Исполнителя. Договор составлен в двух экземплярах.",
        10,
        12,
    )

    line("10. АДРЕСА И РЕКВИЗИТЫ СТОРОН", 11, 8)
    line("ИСПОЛНИТЕЛЬ (Самозанятый):", 10, 4)
    line("ФИО: Григорьев Дмитрий Вячеславович", 10, 3)
    line(f"Телефон: {settings.COMPANY_PHONE}", 10, 3)
    line(f"Адрес: {settings.COMPANY_ADDRESS}", 10, 8)
    line("ЗАКАЗЧИК:", 10, 4)
    line(f"ФИО: {customer}", 10, 3)
    line(
        f"Паспорт: серия {contract.customer_passport_series or '_____'} "
        f"№ {contract.customer_passport_number or '________'}",
        10,
        3,
    )
    line(f"Выдан: {contract.customer_passport_issued or '_________________________'}", 10, 3)
    line(f"Адрес регистрации: {contract.customer_address or '_________________________'}", 10, 3)
    phone = contract.client.phone if contract.client_id else "_________________________"
    line(f"Телефон: {phone}", 10, 3)
    line(f"Email: {contract.customer_email or '_________________________'}", 10, 3)
    line(f"ИНН (при наличии): {contract.customer_inn or '_________________________'}", 10, 12)
    line("ПОДПИСИ СТОРОН:", 10, 8)
    line(f"Исполнитель: ________________ / {settings.MASTER_SIGN}    {date_s}", 10, 10)
    line(f"Заказчик: _____________________ / {customer}", 10, 6)

    c.save()
    buffer.seek(0)
    return buffer.getvalue()
