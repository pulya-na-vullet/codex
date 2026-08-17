"""Client-zone TV: switch between ads and a CRM page, resume ads by slide index."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from workshop.authz import SESSION_ROLE, SESSION_STAFF_ID
from workshop.models import StaffRole, StaffUser, TvDisplayMode, TvDisplaySettings

_ALLOWED_PATHS = (
    re.compile(r"^/$"),
    re.compile(r"^/orders$"),
    re.compile(r"^/orders/\d+$"),
    re.compile(r"^/orders/\d+/print$"),
    re.compile(r"^/work-queue$"),
    re.compile(r"^/services$"),
    re.compile(r"^/services/print$"),
    re.compile(r"^/clients$"),
    re.compile(r"^/clients/\d+$"),
    re.compile(r"^/statistics$"),
    re.compile(r"^/software$"),
    re.compile(r"^/software/\d+$"),
    re.compile(r"^/modeling$"),
    re.compile(r"^/modeling/\d+$"),
    re.compile(r"^/acceptance$"),
    re.compile(r"^/acceptance/\d+$"),
    re.compile(r"^/acceptance/\d+/print$"),
    re.compile(r"^/debtors$"),
)

_BLOCK = ("delete", "export", "import", "print-direct", "admin-panel", "login", "/tv", "webhook")


class TvCastSession(dict):
    """In-memory session so the TV browser never receives a CRM login cookie."""

    modified = False
    accessed = False
    session_key = None

    def cycle_key(self):
        return None

    def save(self, *args, **kwargs):
        return None

    def flush(self):
        self.clear()

    def set_expiry(self, *args, **kwargs):
        return None

    def get_session_cookie_age(self):
        return 0

    def get_expire_at_browser_close(self):
        return True

    def is_empty(self):
        return True


def sanitize_tv_crm_path(raw: str | None) -> str:
    value = (raw or "").strip() or "/"
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        return "/"
    path = parsed.path or "/"
    if not path.startswith("/"):
        path = "/" + path
    path = path.split("?")[0].split("#")[0]
    if len(path) > 1:
        path = path.rstrip("/")
    low = path.lower()
    if any(b in low for b in _BLOCK):
        return "/"
    for rule in _ALLOWED_PATHS:
        if rule.match(path):
            return path
    return "/"


def ads_slide_count() -> int:
    return 14


def clamp_ads_index(value: int | None) -> int:
    try:
        idx = int(value or 0)
    except (TypeError, ValueError):
        idx = 0
    n = ads_slide_count()
    if n <= 0:
        return 0
    return idx % n


def get_settings() -> TvDisplaySettings:
    return TvDisplaySettings.get_solo()


def state_payload() -> dict:
    cfg = get_settings()
    return {
        "ok": True,
        "mode": cfg.mode,
        "crm_path": sanitize_tv_crm_path(cfg.crm_path),
        "ads_index": clamp_ads_index(cfg.ads_index),
        "rev": int(cfg.rev or 1),
    }


def set_ads_index(index: int) -> TvDisplaySettings:
    cfg = get_settings()
    cfg.ads_index = clamp_ads_index(index)
    cfg.save(update_fields=["ads_index", "updated_at"])
    return cfg


def set_display(*, mode: str, path: str | None = None) -> TvDisplaySettings:
    cfg = get_settings()
    if mode == TvDisplayMode.CRM:
        cfg.mode = TvDisplayMode.CRM
        if path is not None:
            cfg.crm_path = sanitize_tv_crm_path(path)
        elif not cfg.crm_path:
            cfg.crm_path = "/"
    else:
        cfg.mode = TvDisplayMode.ADS
    cfg.rev = int(cfg.rev or 1) + 1
    cfg.save()
    return cfg


def attach_tv_cast(request) -> None:
    staff = StaffUser.objects.filter(is_active=True, role=StaffRole.ADMIN).first()
    if staff is None:
        staff = StaffUser.ensure_bootstrap_admin()
    session = TvCastSession()
    session["workshop_authenticated"] = True
    session["workshop_username"] = "ТВ"
    session[SESSION_STAFF_ID] = staff.id
    session[SESSION_ROLE] = staff.role
    request.session = session
    request.tv_cast_staff = staff
