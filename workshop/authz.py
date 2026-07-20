from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect

from workshop.models import StaffRole, StaffUser


SESSION_STAFF_ID = "workshop_staff_id"
SESSION_ROLE = "workshop_role"


def current_staff(request: HttpRequest) -> StaffUser | None:
    staff_id = request.session.get(SESSION_STAFF_ID)
    if not staff_id:
        return None
    return StaffUser.objects.filter(pk=staff_id, is_active=True).first()


def login_staff(request: HttpRequest, staff: StaffUser) -> None:
    request.session.flush()
    request.session["workshop_authenticated"] = True
    request.session["workshop_username"] = staff.username
    request.session[SESSION_STAFF_ID] = staff.id
    request.session[SESSION_ROLE] = staff.role
    import time

    request.session["workshop_last_active"] = time.time()


def is_admin(request: HttpRequest) -> bool:
    staff = current_staff(request)
    return bool(staff and staff.is_admin)


def can_delete(request: HttpRequest) -> bool:
    return is_admin(request)


def require_admin(view):
    @wraps(view)
    def wrapped(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not is_admin(request):
            messages.error(request, "Доступно только администратору")
            return redirect("dashboard")
        return view(request, *args, **kwargs)

    return wrapped


def require_delete_permission(view):
    @wraps(view)
    def wrapped(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not can_delete(request):
            messages.error(request, "Удаление доступно только администратору")
            return redirect(request.META.get("HTTP_REFERER") or "dashboard")
        return view(request, *args, **kwargs)

    return wrapped
