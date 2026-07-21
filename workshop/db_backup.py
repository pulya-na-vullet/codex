"""Backup live SQLite DB into dumpDB/ on application start."""

from __future__ import annotations

import logging
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Keep a rolling history of stamped copies (latest is always dumpDB/orders.db).
KEEP_STAMPED = 30


def _project_root() -> Path:
    from django.conf import settings

    return Path(settings.BASE_DIR)


def live_db_path() -> Path:
    from django.conf import settings

    return Path(settings.DATABASES["default"]["NAME"])


def dump_dir() -> Path:
    return _project_root() / "dumpDB"


def backup_database_to_dumpdb(*, keep_stamped: int = KEEP_STAMPED) -> Path | None:
    """
    Copy the current Django SQLite DB into dumpDB/.

    - dumpDB/orders.db — always the latest snapshot (overwritten each start)
    - dumpDB/orders_YYYYMMDD_HHMMSS.db — dated copy (rolling keep_stamped)
    """
    src = live_db_path()
    if not src.is_file():
        print(f"Бэкап БД пропущен: файл не найден ({src})", flush=True)
        return None

    target_dir = dump_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    latest = target_dir / "orders.db"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stamped = target_dir / f"orders_{stamp}.db"

    try:
        # Consistent snapshot even if another connection is open.
        src_conn = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
        try:
            dst_conn = sqlite3.connect(latest.as_posix())
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
        finally:
            src_conn.close()
        shutil.copy2(latest, stamped)
    except Exception:
        # Fallback: plain file copy.
        logger.exception("SQLite backup() failed, using file copy")
        shutil.copy2(src, latest)
        shutil.copy2(latest, stamped)

    _prune_stamped(target_dir, keep=keep_stamped)
    print(f"=== Бэкап БД → {latest} (+ {stamped.name}) ===", flush=True)
    return latest


def _prune_stamped(target_dir: Path, *, keep: int) -> None:
    files = sorted(
        target_dir.glob("orders_????????_??????.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in files[max(0, int(keep)) :]:
        try:
            old.unlink(missing_ok=True)
        except OSError:
            pass
