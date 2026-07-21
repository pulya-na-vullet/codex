from __future__ import annotations

import os

from django.conf import settings

from workshop.network import get_lan_ipv4_addresses


def workshop_settings(request):
    from workshop.authz import can_delete, is_admin

    port = int(os.getenv("IT_MASTER_PORT", "8000"))
    lan_urls = [f"http://{ip}:{port}" for ip in get_lan_ipv4_addresses() if ip != "127.0.0.1"]
    pending_rating = None
    if getattr(request, "session", None) and request.session.get("workshop_authenticated"):
        skipped = set(request.session.get("rating_skip_ids") or [])
        try:
            from workshop.models import ModelingBrief

            qs = (
                ModelingBrief.objects.filter(rating_pending=True, status="done")
                .exclude(hub_brief_id="")
                .select_related("client")
                .order_by("done_at", "id")
            )
            for brief in qs[:10]:
                if brief.id in skipped:
                    continue
                pending_rating = brief
                break
        except Exception:
            pending_rating = None
    return {
        "COMPANY_NAME": getattr(settings, "COMPANY_NAME", "ИТ-М"),
        "lan_urls": lan_urls,
        "workshop_user": request.session.get("workshop_username", ""),
        "workshop_role": request.session.get("workshop_role", ""),
        "is_workshop_admin": is_admin(request) if getattr(request, "session", None) else False,
        "can_workshop_delete": can_delete(request) if getattr(request, "session", None) else False,
        "pending_rating_brief": pending_rating,
    }
