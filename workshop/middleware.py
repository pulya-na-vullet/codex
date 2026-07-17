from __future__ import annotations

import time

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse


class IdleLogoutMiddleware:
    """Require workshop login and expire idle sessions after N hours."""

    EXEMPT_PREFIXES = ("/login", "/logout", "/static/", "/admin/", "/max/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if any(path.startswith(p) for p in self.EXEMPT_PREFIXES):
            return self.get_response(request)

        if not request.session.get("workshop_authenticated"):
            login_url = reverse("login")
            return redirect(f"{login_url}?next={path}")

        now = time.time()
        last = float(request.session.get("workshop_last_active", now))
        idle = int(getattr(settings, "WORKSHOP_IDLE_SECONDS", 6 * 60 * 60))
        if now - last > idle:
            request.session.flush()
            login_url = reverse("login")
            return redirect(f"{login_url}?next={path}")

        request.session["workshop_last_active"] = now
        return self.get_response(request)
