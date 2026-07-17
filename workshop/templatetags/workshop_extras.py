from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django import template

register = template.Library()


@register.filter(name="money")
def money(value) -> str:
    """Format money as 0,00 with Russian decimal comma."""
    if value is None or value == "":
        return "0,00"
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        return "0,00"
    text = f"{amount:.2f}"
    return text.replace(".", ",")
