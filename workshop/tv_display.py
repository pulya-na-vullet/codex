"""Client-zone TV: switch between ads and a CRM page, resume ads by slide index."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from django.http import QueryDict

from workshop.authz import SESSION_ROLE, SESSION_STAFF_ID
from workshop.models import StaffRole, StaffUser, TvDisplayMode, TvDisplaySettings

_BLOCK = (
    "delete",
    "export",
    "import",
    "print-direct",
    "admin-panel",
    "login",
    "logout",
    "/tv",
    "webhook",
    "password",
)

_PATH_OK = re.compile(r"^/[-a-zA-Z0-9_./]*$")
_QUERY_OK = re.compile(r"^[A-Za-z0-9_.=&%\-]*$")


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


def _split_path(raw: str | None) -> tuple[str, str]:
    value = (raw or "").strip() or "/"
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        return "/", ""
    path = parsed.path or "/"
    if not path.startswith("/"):
        path = "/" + path
    path = path.split("?")[0].split("#")[0]
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    query = parsed.query or ""
    return path, query


def is_allowed_tv_path(raw: str | None) -> bool:
    path, query = _split_path(raw)
    low = path.lower()
    if any(b in low for b in _BLOCK):
        return False
    if not _PATH_OK.match(path):
        return False
    if query and not _QUERY_OK.match(query):
        return False
    return True


def sanitize_tv_crm_path(raw: str | None) -> str:
    path, query = _split_path(raw)
    combined = path + (("?" + query) if query else "")
    if is_allowed_tv_path(combined):
        return combined
    return "/"


ADS_CANVAS_WIDTH = 1920
ADS_CANVAS_HEIGHT = 1080
DEFAULT_CRM_WIDTH = 1440
DEFAULT_CRM_HEIGHT = 900


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


def clamp_viewport_dim(value, default: int, lo: int, hi: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def tv_page_version() -> str:
    """Changes when lite ads templates/code change so the TV can auto-reload."""
    root = Path(__file__).resolve().parent
    latest = 0
    for rel in (
        "templates/workshop/tv_ads_lite.html",
        "tv_display.py",
        "views.py",
    ):
        path = root / rel
        if path.is_file():
            latest = max(latest, int(path.stat().st_mtime))
    return str(latest or 1)


def get_settings() -> TvDisplaySettings:
    return TvDisplaySettings.get_solo()


def state_payload() -> dict:
    from workshop.tv_cast_frames import cast_seq

    cfg = get_settings()
    return {
        "ok": True,
        "mode": cfg.mode,
        "crm_path": sanitize_tv_crm_path(cfg.crm_path),
        "crm_width": clamp_viewport_dim(cfg.crm_width, DEFAULT_CRM_WIDTH, 640, 5120),
        "crm_height": clamp_viewport_dim(cfg.crm_height, DEFAULT_CRM_HEIGHT, 360, 2880),
        "ads_width": ADS_CANVAS_WIDTH,
        "ads_height": ADS_CANVAS_HEIGHT,
        "ads_index": clamp_ads_index(cfg.ads_index),
        "cast_seq": int(cast_seq() or 0),
        "rev": int(cfg.rev or 1),
        "page_v": tv_page_version(),
    }


def set_ads_index(index: int) -> TvDisplaySettings:
    cfg = get_settings()
    idx = clamp_ads_index(index)
    if int(cfg.ads_index or 0) == idx:
        return cfg
    cfg.ads_index = idx
    cfg.save(update_fields=["ads_index", "updated_at"])
    return cfg


def set_display(
    *,
    mode: str,
    path: str | None = None,
    follow: bool = False,
    viewport_w=None,
    viewport_h=None,
) -> TvDisplaySettings:
    cfg = get_settings()
    old_mode = cfg.mode
    old_path = cfg.crm_path or "/"
    old_w = int(cfg.crm_width or DEFAULT_CRM_WIDTH)
    old_h = int(cfg.crm_height or DEFAULT_CRM_HEIGHT)
    if mode == TvDisplayMode.CRM:
        cfg.mode = TvDisplayMode.CRM
        if path is not None:
            if is_allowed_tv_path(path):
                cfg.crm_path = sanitize_tv_crm_path(path)
            elif not follow:
                cfg.crm_path = "/"
        elif not cfg.crm_path:
            cfg.crm_path = "/"
    else:
        cfg.mode = TvDisplayMode.ADS
        from workshop.tv_cast_frames import clear_jpeg

        clear_jpeg()
    if viewport_w is not None:
        cfg.crm_width = clamp_viewport_dim(viewport_w, old_w, 640, 5120)
    if viewport_h is not None:
        cfg.crm_height = clamp_viewport_dim(viewport_h, old_h, 360, 2880)
    changed = (
        cfg.mode != old_mode
        or (cfg.crm_path or "/") != old_path
        or int(cfg.crm_width or 0) != old_w
        or int(cfg.crm_height or 0) != old_h
    )
    if changed:
        cfg.rev = int(cfg.rev or 1) + 1
        cfg.save()
    elif not follow:
        cfg.save(update_fields=["updated_at"])
    return cfg


def allow_tv_embed(response):
    response["X-Frame-Options"] = "SAMEORIGIN"
    response["Content-Security-Policy"] = "frame-ancestors 'self'"
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


def apply_tv_frame_path(request, raw_path: str) -> str:
    """Point this request at the CRM path so the existing view can render it."""
    combined = sanitize_tv_crm_path(raw_path)
    parsed = urlparse(combined)
    path = parsed.path or "/"
    query = parsed.query or ""
    request.path = path
    request.path_info = path
    request.META["PATH_INFO"] = path
    request.META["QUERY_STRING"] = query
    request.GET = QueryDict(query)
    return path


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
