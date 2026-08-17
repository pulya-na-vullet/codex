"""Latest JPEG of the Mac CRM window for the client-zone TV."""

from __future__ import annotations

from threading import Lock

MAX_JPEG_BYTES = 2_400_000

_lock = Lock()
_jpeg = b""
_seq = 0


def put_jpeg(data: bytes | None) -> int:
    """Store a JPEG frame. Returns the new sequence number, or 0 if rejected."""
    global _jpeg, _seq
    raw = data or b""
    if len(raw) < 4 or len(raw) > MAX_JPEG_BYTES:
        return 0
    if raw[:3] != b"\xff\xd8\xff":
        return 0
    with _lock:
        _jpeg = raw
        _seq += 1
        return _seq


def get_jpeg() -> tuple[bytes, int]:
    with _lock:
        return _jpeg, _seq


def cast_seq() -> int:
    with _lock:
        return _seq


def clear_jpeg() -> None:
    global _jpeg, _seq
    with _lock:
        _jpeg = b""
        _seq = 0
