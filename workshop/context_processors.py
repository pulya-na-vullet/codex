from __future__ import annotations

import os

from django.conf import settings

from workshop.network import get_lan_ipv4_addresses


def workshop_settings(request):
    port = int(os.getenv("IT_MASTER_PORT", "8000"))
    lan_urls = [f"http://{ip}:{port}" for ip in get_lan_ipv4_addresses() if ip != "127.0.0.1"]
    return {
        "COMPANY_NAME": getattr(settings, "COMPANY_NAME", "ИТ-мастерская"),
        "lan_urls": lan_urls,
        "workshop_user": request.session.get("workshop_username", ""),
    }
