"""Keep Django runserver console readable while the TV polls."""

from __future__ import annotations

import logging

_QUIET_SNIPPETS = (
    '"GET /tv/state',
    '"GET /tv/cast.jpg',
    '"POST /tv/progress',
    '"POST /admin-panel/tv-display',
    '"POST /admin-panel/tv-cast-frame',
)


class QuietTvPollFilter(logging.Filter):
    """Hide successful TV heartbeat requests; keep 4xx/5xx visible."""

    def filter(self, record: logging.LogRecord) -> bool:
        code = getattr(record, "status_code", None)
        try:
            if code is not None and int(code) >= 400:
                return True
        except (TypeError, ValueError):
            pass
        try:
            msg = record.getMessage()
        except Exception:
            return True
        return not any(snippet in msg for snippet in _QUIET_SNIPPETS)
