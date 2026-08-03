"""Client-zone TV agent: ads slideshow or mirror of the CRM monitor.

Runs as a daemon thread (started from app.py / AppConfig.ready).
Monitor assignments are stored in ClientDisplaySettings (DB) and reused after restart.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import threading
import time
from io import BytesIO
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import close_old_connections

logger = logging.getLogger(__name__)

_worker_lock = threading.Lock()
_worker_started = False
_worker_stop: threading.Event | None = None
_worker_thread: threading.Thread | None = None
_wake = threading.Event()

_frame_lock = threading.Lock()
_latest_jpeg: bytes | None = None
_chrome_proc: subprocess.Popen | None = None
_chrome_mode: str | None = None
_chrome_bounds: tuple[int, int, int, int] | None = None
_status: dict[str, Any] = {
    "running": False,
    "monitors": [],
    "last_error": "",
    "chrome_pid": None,
}


def agent_status() -> dict[str, Any]:
    return dict(_status)


def get_latest_jpeg() -> bytes | None:
    with _frame_lock:
        return _latest_jpeg


def wake_client_display_agent() -> None:
    start_client_display_agent()
    _wake.set()


def start_client_display_agent() -> None:
    """Start background agent once (safe to call repeatedly)."""
    global _worker_started, _worker_stop, _worker_thread
    if os.environ.get("IT_MASTER_SKIP_WORKERS") == "1":
        return
    if not getattr(settings, "CLIENT_DISPLAY_ENABLED", True):
        return
    with _worker_lock:
        if _worker_started and _worker_thread and _worker_thread.is_alive():
            return
        _worker_stop = threading.Event()
        _worker_thread = threading.Thread(
            target=_agent_loop,
            name="client-display-agent",
            daemon=True,
            args=(_worker_stop,),
        )
        _worker_started = True
        _worker_thread.start()
        logger.info("Client display agent started")


def is_client_display_agent_running() -> bool:
    return bool(_worker_thread and _worker_thread.is_alive())


def list_monitors() -> list[dict[str, Any]]:
    """Enumerate connected displays. Keys are stable enough to remember in DB."""
    system = platform.system()
    try:
        if system == "Windows":
            return _list_monitors_windows()
        return _list_monitors_fallback()
    except Exception as exc:
        logger.exception("Monitor enumeration failed")
        _status["last_error"] = f"enumerate: {exc}"
        return []


def refresh_monitors_cache() -> list[dict[str, Any]]:
    """Scan monitors and persist the list into ClientDisplaySettings."""
    close_old_connections()
    from workshop.models import ClientDisplaySettings

    monitors = list_monitors()
    cfg = ClientDisplaySettings.get_solo()
    cfg.monitors_cache = monitors
    # If assignments exist, refresh bounds from current scan by key.
    by_key = {m["key"]: m for m in monitors}
    if cfg.tv_monitor_key and cfg.tv_monitor_key in by_key:
        cfg.apply_monitor("tv", by_key[cfg.tv_monitor_key])
    if cfg.crm_monitor_key and cfg.crm_monitor_key in by_key:
        cfg.apply_monitor("crm", by_key[cfg.crm_monitor_key])
    cfg.last_error = ""
    cfg.save(
        update_fields=[
            "monitors_cache",
            "tv_monitor_key",
            "tv_left",
            "tv_top",
            "tv_width",
            "tv_height",
            "crm_monitor_key",
            "crm_left",
            "crm_top",
            "crm_width",
            "crm_height",
            "last_error",
            "updated_at",
        ]
    )
    _status["monitors"] = monitors
    return monitors


def _list_monitors_windows() -> list[dict[str, Any]]:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    monitors: list[dict[str, Any]] = []

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", RECT),
            ("rcWork", RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    MonitorEnumProc = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HMONITOR,
        wintypes.HDC,
        ctypes.POINTER(RECT),
        wintypes.LPARAM,
    )

    def _callback(hmonitor, _hdc, _lprect, _lparam):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if not user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            return True
        left, top = int(info.rcMonitor.left), int(info.rcMonitor.top)
        right, bottom = int(info.rcMonitor.right), int(info.rcMonitor.bottom)
        width, height = right - left, bottom - top
        device = str(info.szDevice or "").strip() or f"MONITOR-{len(monitors)+1}"
        primary = bool(info.dwFlags & 1)
        key = f"{device}|{width}x{height}@{left},{top}"
        monitors.append(
            {
                "key": key,
                "name": device,
                "label": f"{device} · {width}×{height}" + (" · основной" if primary else ""),
                "left": left,
                "top": top,
                "width": width,
                "height": height,
                "primary": primary,
                "index": len(monitors),
            }
        )
        return True

    user32.EnumDisplayMonitors(0, 0, MonitorEnumProc(_callback), 0)
    monitors.sort(key=lambda m: (not m["primary"], m["left"], m["top"]))
    for i, m in enumerate(monitors):
        m["index"] = i
    return monitors


def _list_monitors_fallback() -> list[dict[str, Any]]:
    """Single virtual display for Linux/dev (or multi via tk if available)."""
    width, height = 1920, 1080
    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        width = int(root.winfo_screenwidth() or width)
        height = int(root.winfo_screenheight() or height)
        root.destroy()
    except Exception:
        pass
    return [
        {
            "key": f"SCREEN-0|{width}x{height}@0,0",
            "name": "SCREEN-0",
            "label": f"Экран · {width}×{height} · основной",
            "left": 0,
            "top": 0,
            "width": width,
            "height": height,
            "primary": True,
            "index": 0,
        }
    ]


def _find_browser(cfg_chrome_path: str = "") -> str | None:
    candidates: list[str] = []
    if cfg_chrome_path:
        candidates.append(cfg_chrome_path)
    env = os.getenv("IT_MASTER_CHROME_PATH", "").strip()
    if env:
        candidates.append(env)
    if platform.system() == "Windows":
        pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        pf86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        local = os.environ.get("LOCALAPPDATA", "")
        candidates.extend(
            [
                str(Path(pf) / "Google" / "Chrome" / "Application" / "chrome.exe"),
                str(Path(pf86) / "Google" / "Chrome" / "Application" / "chrome.exe"),
                str(Path(local) / "Google" / "Chrome" / "Application" / "chrome.exe") if local else "",
                str(Path(pf) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
                str(Path(pf86) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
            ]
        )
    else:
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge"):
            found = shutil.which(name)
            if found:
                candidates.append(found)
    for path in candidates:
        if path and Path(path).exists():
            return path
    return None


def _tv_url() -> str:
    port = int(os.getenv("IT_MASTER_PORT", "8000"))
    host = os.getenv("IT_MASTER_TV_HOST", "127.0.0.1")
    return f"http://{host}:{port}/tv"


def _close_chrome() -> None:
    global _chrome_proc, _chrome_mode, _chrome_bounds
    proc = _chrome_proc
    _chrome_proc = None
    _chrome_mode = None
    _chrome_bounds = None
    _status["chrome_pid"] = None
    if not proc:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception:
        pass


def _ensure_chrome(cfg) -> None:
    """Keep a browser window on the remembered TV bounds."""
    global _chrome_proc, _chrome_mode, _chrome_bounds
    browser = _find_browser(cfg.chrome_path)
    if not browser:
        msg = "Chrome/Edge не найден — укажите путь в настройках ТВ"
        _status["last_error"] = msg
        if cfg.last_error != msg:
            cfg.last_error = msg
            cfg.save(update_fields=["last_error", "updated_at"])
        return

    bounds = (int(cfg.tv_left), int(cfg.tv_top), int(cfg.tv_width), int(cfg.tv_height))
    mode = cfg.mode
    need_restart = (
        _chrome_proc is None
        or _chrome_proc.poll() is not None
        or _chrome_mode != mode
        or _chrome_bounds != bounds
    )
    if not need_restart:
        return

    _close_chrome()
    url = _tv_url()
    left, top, width, height = bounds
    profile_dir = Path(settings.BASE_DIR) / "runtime" / "tv-chrome-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    args = [
        browser,
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--disable-session-crashed-bubble",
        "--disable-infobars",
        "--autoplay-policy=no-user-gesture-required",
        f"--window-position={left},{top}",
        f"--window-size={width},{height}",
        f"--app={url}",
    ]
    try:
        popen_kwargs: dict[str, Any] = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if platform.system() == "Windows":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        _chrome_proc = subprocess.Popen(args, **popen_kwargs)
        _chrome_mode = mode
        _chrome_bounds = bounds
        _status["chrome_pid"] = _chrome_proc.pid
        _status["last_error"] = ""
        if cfg.last_error:
            cfg.last_error = ""
            cfg.save(update_fields=["last_error", "updated_at"])
        # Nudge window to TV bounds (Windows).
        if platform.system() == "Windows":
            threading.Timer(1.5, lambda: _win_move_chrome(left, top, width, height)).start()
    except Exception as exc:
        msg = f"chrome: {exc}"
        logger.exception("Failed to launch TV browser")
        _status["last_error"] = msg
        cfg.last_error = msg[:500]
        cfg.save(update_fields=["last_error", "updated_at"])


def _win_move_chrome(left: int, top: int, width: int, height: int) -> None:
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnds: list[int] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def enum_proc(hwnd, _lparam):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value or ""
                if "ИТ-М" in title or "/tv" in title or "tv" in title.lower():
                    hwnds.append(int(hwnd))
            return True

        user32.EnumWindows(enum_proc, 0)
        for hwnd in hwnds[:2]:
            user32.MoveWindow(hwnd, int(left), int(top), int(width), int(height), True)
            user32.ShowWindow(hwnd, 5)  # SW_SHOW — keep size on target monitor
    except Exception:
        logger.debug("Win32 MoveWindow skipped", exc_info=True)


def _capture_crm_frame(cfg) -> None:
    global _latest_jpeg
    try:
        from PIL import ImageGrab
    except Exception:
        return
    left, top = int(cfg.crm_left), int(cfg.crm_top)
    right = left + max(1, int(cfg.crm_width))
    bottom = top + max(1, int(cfg.crm_height))
    try:
        grab_kwargs = {"bbox": (left, top, right, bottom)}
        # Pillow on Windows supports all_screens for multi-monitor coords.
        try:
            img = ImageGrab.grab(all_screens=True, **grab_kwargs)
        except TypeError:
            img = ImageGrab.grab(**grab_kwargs)
        buf = BytesIO()
        img = img.convert("RGB")
        # Fit Full HD-ish for TV bandwidth.
        max_w = min(1920, int(cfg.tv_width or 1920))
        if img.width > max_w:
            ratio = max_w / img.width
            img = img.resize((max_w, max(1, int(img.height * ratio))))
        img.save(buf, format="JPEG", quality=70, optimize=True)
        data = buf.getvalue()
        with _frame_lock:
            _latest_jpeg = data
    except Exception as exc:
        _status["last_error"] = f"capture: {exc}"


def _agent_loop(stop_event: threading.Event) -> None:
    close_old_connections()
    _status["running"] = True
    last_scan = 0.0
    try:
        while not stop_event.is_set():
            close_old_connections()
            try:
                from workshop.models import ClientDisplayMode, ClientDisplaySettings

                cfg = ClientDisplaySettings.get_solo()
                now = time.time()
                if now - last_scan > 30 or not cfg.monitors_cache:
                    try:
                        refresh_monitors_cache()
                        cfg = ClientDisplaySettings.get_solo()
                    except Exception as exc:
                        _status["last_error"] = f"scan: {exc}"
                    last_scan = now

                if not cfg.enabled:
                    _close_chrome()
                    _wake.wait(2.0)
                    _wake.clear()
                    continue

                if not cfg.tv_monitor_key:
                    _status["last_error"] = "Назначьте монитор ТВ в админ-панели"
                    _wake.wait(2.0)
                    _wake.clear()
                    continue

                _ensure_chrome(cfg)

                if cfg.mode == ClientDisplayMode.MIRROR and cfg.crm_monitor_key:
                    _capture_crm_frame(cfg)
                    # ~10–12 fps
                    if _wake.wait(0.08):
                        _wake.clear()
                    continue

                _wake.wait(1.0)
                _wake.clear()
            except Exception as exc:
                logger.exception("Client display agent iteration failed")
                _status["last_error"] = str(exc)
                _wake.wait(2.0)
                _wake.clear()
    finally:
        _close_chrome()
        _status["running"] = False
        close_old_connections()
