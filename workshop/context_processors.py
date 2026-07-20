from __future__ import annotations

import os

from django.conf import settings

from workshop.network import get_lan_ipv4_addresses


def workshop_settings(request):
    from workshop.authz import can_delete, is_admin

    port = int(os.getenv("IT_MASTER_PORT", "8000"))
    lan_urls = [f"http://{ip}:{port}" for ip in get_lan_ipv4_addresses() if ip != "127.0.0.1"]
    return {
        "COMPANY_NAME": getattr(settings, "COMPANY_NAME", "ИТ-мастерская"),
        "lan_urls": lan_urls,
        "workshop_user": request.session.get("workshop_username", ""),
        "workshop_role": request.session.get("workshop_role", ""),
        "is_workshop_admin": is_admin(request) if getattr(request, "session", None) else False,
        "can_workshop_delete": can_delete(request) if getattr(request, "session", None) else False,
    }
