"""Latest JPEG of the Mac CRM window for the client-zone TV."""

from __future__ import annotations

import re
from threading import Lock

MAX_JPEG_BYTES = 2_400_000
_CASTER_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")

_lock = Lock()
_jpeg = b""
_seq = 0
_caster = ""


def sanitize_caster(raw: str | None) -> str:
    value = (raw or "").strip()
    if _CASTER_RE.match(value):
        return value
    return ""


def get_caster() -> str:
    with _lock:
        return _caster


def set_caster(token: str | None) -> str:
    """Remember which browser tab is allowed to push CRM frames."""
    global _caster
    value = sanitize_caster(token)
    with _lock:
        _caster = value
        return _caster


def put_jpeg(data: bytes | None, caster: str | None = None) -> int | None:
    """Store a JPEG frame. Returns seq, 0 if not JPEG, None if another tab owns the TV."""
    global _jpeg, _seq
    raw = data or b""
    if len(raw) < 4 or len(raw) > MAX_JPEG_BYTES:
        return 0
    if raw[:3] != b"\xff\xd8\xff":
        return 0
    token = sanitize_caster(caster)
    with _lock:
        if _caster and token != _caster:
            return None
        _jpeg = raw
        _seq += 1
        return _seq


def get_jpeg() -> tuple[bytes, int]:
    with _lock:
        return _jpeg, _seq


def cast_seq() -> int:
    with _lock:
        return _seq


def clear_jpeg(*, reset_caster: bool = True) -> None:
    global _jpeg, _seq, _caster
    with _lock:
        _jpeg = b""
        _seq = 0
        if reset_caster:
            _caster = ""
