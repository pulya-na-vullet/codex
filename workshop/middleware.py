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

        # Fallback: if the background AI scheduler thread is dead/stuck,
        # authenticated page views can still deliver a due daily report.
        try:
            from workshop.yandex_ai import (
                ensure_due_ai_report,
                is_ai_report_scheduler_running,
                start_ai_report_scheduler,
            )

            if not is_ai_report_scheduler_running():
                start_ai_report_scheduler()
            ensure_due_ai_report()
        except Exception:
            pass

        return self.get_response(request)
