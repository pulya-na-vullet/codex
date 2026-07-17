from __future__ import annotations

from django.http import HttpRequest

from workshop.models import AuditLog


def client_ip(request: HttpRequest) -> str | None:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def log_action(
    request: HttpRequest | None,
    action: str,
    *,
    entity_type: str = "",
    entity_id: str | int | None = "",
    details: str = "",
) -> None:
    username = ""
    ip = None
    if request is not None:
        username = str(request.session.get("workshop_username") or "")
        ip = client_ip(request)
    AuditLog.objects.create(
        username=username,
        action=action,
        entity_type=entity_type or "",
        entity_id="" if entity_id is None else str(entity_id),
        details=details or "",
        ip_address=ip,
    )
