"""OS-level TV ads launcher: enumerate monitors and start Chrome fullscreen.

Does not move/close the CRM browser window. Uses a separate Chrome user-data-dir.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

_chrome_proc: subprocess.Popen | None = None


def list_monitors() -> list[dict[str, Any]]:
    system = platform.system()
    if system == "Windows":
        return _list_monitors_windows()
    return _list_monitors_fallback()


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
        device = str(info.szDevice or "").strip() or f"DISPLAY{len(monitors)+1}"
        primary = bool(info.dwFlags & 1)
        monitors.append(
            {
                "id": f"{device}|{width}x{height}@{left},{top}",
                "device": device,
                "left": left,
                "top": top,
                "width": width,
                "height": height,
                "primary": primary,
                "label": (
                    f"{'Основной' if primary else 'Монитор'} · {width}×{height} "
                    f"@{left},{top} ({device})"
                ),
            }
        )
        return True

    user32.EnumDisplayMonitors(0, 0, MonitorEnumProc(_callback), 0)
    monitors.sort(key=lambda m: (not m["primary"], m["left"], m["top"]))
    for i, m in enumerate(monitors):
        m["index"] = i
    return monitors


def _list_monitors_fallback() -> list[dict[str, Any]]:
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
            "id": f"SCREEN-0|{width}x{height}@0,0",
            "device": "SCREEN-0",
            "left": 0,
            "top": 0,
            "width": width,
            "height": height,
            "primary": True,
            "index": 0,
            "label": f"Основной · {width}×{height} @0,0",
        }
    ]


def find_browser(chrome_path: str = "") -> str | None:
    candidates: list[str] = []
    if chrome_path:
        candidates.append(chrome_path)
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
    # os=1 → page knows it was launched by OS helper (try auto fullscreen / Esc closes)
    return f"http://{host}:{port}/tv?fs=1&os=1"


def stop_tv_browser() -> None:
    global _chrome_proc
    proc = _chrome_proc
    _chrome_proc = None
    if not proc:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception:
        logger.exception("Failed to stop TV browser")


def open_tv_on_monitor(
    *,
    left: int,
    top: int,
    width: int,
    height: int,
    chrome_path: str = "",
) -> dict[str, Any]:
    """Launch a separate Chrome/Edge window on the given OS monitor, fullscreen."""
    global _chrome_proc

    browser = find_browser(chrome_path)
    if not browser:
        return {"ok": False, "error": "Chrome/Edge не найден. Укажите путь или установите браузер."}

    stop_tv_browser()

    profile_dir = Path(settings.BASE_DIR) / "runtime" / "tv-chrome-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    url = _tv_url()

    # Separate profile so CRM tabs are never touched.
    # 1) Position on target monitor  2) start-fullscreen (Esc exits FS; Alt+F4 closes).
    args = [
        browser,
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "--disable-infobars",
        "--autoplay-policy=no-user-gesture-required",
        f"--window-position={int(left)},{int(top)}",
        f"--window-size={int(width)},{int(height)}",
        "--start-fullscreen",
        "--new-window",
        url,
    ]

    popen_kwargs: dict[str, Any] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if platform.system() == "Windows":
        # Detach from Django console; do not create a console window.
        creation = 0
        creation |= getattr(subprocess, "DETACHED_PROCESS", 0)
        creation |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        popen_kwargs["creationflags"] = creation
        popen_kwargs["close_fds"] = True

    try:
        _chrome_proc = subprocess.Popen(args, **popen_kwargs)
    except Exception as exc:
        logger.exception("TV browser launch failed")
        return {"ok": False, "error": str(exc)}

    if platform.system() == "Windows":
        def _nudge() -> None:
            _win_force_fullscreen(left, top, width, height)

        # Page title appears after load — nudge a couple of times.
        import threading

        threading.Timer(0.6, _nudge).start()
        threading.Timer(1.8, _nudge).start()
        threading.Timer(3.0, _nudge).start()

    return {
        "ok": True,
        "pid": _chrome_proc.pid if _chrome_proc else None,
        "url": url,
        "browser": browser,
        "bounds": {"left": left, "top": top, "width": width, "height": height},
    }


def _win_force_fullscreen(left: int, top: int, width: int, height: int) -> None:
    """Find the TV Chrome window by title and move + maximize on target monitor."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnds: list[int] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def enum_proc(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = (buf.value or "").lower()
            # Title contains page title once loaded.
            if "тв-реклама" in title or "ит-м" in title or "/tv" in title or "tv" in title:
                hwnds.append(int(hwnd))
            return True

        user32.EnumWindows(enum_proc, 0)
        SW_SHOW = 5
        HWND_TOP = 0
        SWP_SHOWWINDOW = 0x0040
        for hwnd in hwnds[:3]:
            user32.SetWindowPos(
                hwnd,
                HWND_TOP,
                int(left),
                int(top),
                int(width),
                int(height),
                SWP_SHOWWINDOW,
            )
            user32.ShowWindow(hwnd, SW_SHOW)
            # Borderless-ish fullscreen via size covering the monitor; true FS already from --start-fullscreen.
    except Exception:
        logger.debug("Win32 reposition skipped", exc_info=True)


def resolve_monitor(monitor_id: str = "", index: int | None = None) -> dict[str, Any] | None:
    monitors = list_monitors()
    if not monitors:
        return None
    if monitor_id:
        for m in monitors:
            if m.get("id") == monitor_id:
                return m
    if index is not None:
        for m in monitors:
            if int(m.get("index", -1)) == int(index):
                return m
        if 0 <= int(index) < len(monitors):
            return monitors[int(index)]
    # Prefer non-primary (often the TV).
    for m in monitors:
        if not m.get("primary"):
            return m
    return monitors[0]
