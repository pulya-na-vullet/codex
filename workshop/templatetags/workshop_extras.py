from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django import template

register = template.Library()

# url_name groups for navbar active state
_NAV_SECTIONS: dict[str, frozenset[str]] = {
    "orders": frozenset(
        {
            "orders",
            "order_detail",
            "order_update_meta",
            "order_add_service",
            "order_line_delete",
            "order_delete",
            "order_print",
            "order_pdf",
            "order_print_direct",
            "order_set_payment",
            "order_set_mytax",
            "order_set_status",
            "order_mark_called",
            "orders_export_excel",
        }
    ),
    "work_queue": frozenset({"work_queue"}),
    "debtors": frozenset({"debtors", "debtors_sms_all", "debtors_sms_one"}),
    "marketing": frozenset(
        {
            "marketing_sms",
            "marketing_blast_delete",
            "marketing_message_delete",
            "max_bot_qr",
            "max_bot_poster",
        }
    ),
    "clients": frozenset(
        {
            "clients",
            "client_detail",
            "client_delete",
            "export_clients_excel",
            "import_clients_excel",
        }
    ),
    "services": frozenset({"services", "services_print", "service_toggle_active", "service_delete"}),
    "acceptance": frozenset(
        {
            "acceptance_acts",
            "acceptance_detail",
            "acceptance_set_status",
            "acceptance_mark_called",
            "acceptance_print",
            "acceptance_pdf",
            "acceptance_print_direct",
            "acceptance_delete",
        }
    ),
    "modeling": frozenset(
        {
            "modeling_list",
            "modeling_create",
            "modeling_detail",
            "modeling_delete",
        }
    ),
    "statistics": frozenset({"statistics"}),
    "audit": frozenset({"audit_log", "audit_log_export"}),
    "admin": frozenset({"admin_panel", "max_webhook", "api_docs"}),
    "max_log": frozenset({"max_message_log"}),
    "create_order": frozenset({"create_order"}),
}


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


@register.simple_tag(takes_context=True)
def nav_active(context, section: str) -> str:
    """Return 'is-active' when the current URL belongs to the nav section."""
    request = context.get("request")
    match = getattr(request, "resolver_match", None) if request is not None else None
    url_name = getattr(match, "url_name", None) or ""
    names = _NAV_SECTIONS.get(section, frozenset())
    return "is-active" if url_name in names else ""
