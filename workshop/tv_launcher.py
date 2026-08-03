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
    # Unique marker in query + page title so Win32 never matches the CRM window.
    return f"http://{host}:{port}/tv?fs=1&os=1&kiosk=ITM-TV-ADS-KIOSK"


def stop_tv_browser() -> None:
    global _chrome_proc
    proc = _chrome_proc
    _chrome_proc = None
    if not proc:
        return
    pid = proc.pid
    try:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception:
        logger.exception("Failed to stop TV browser")
    if platform.system() == "Windows" and pid:
        _win_kill_process_tree(pid)


def _win_kill_process_tree(root_pid: int) -> None:
    """Best-effort kill of Chrome child processes for the TV profile."""
    try:
        subprocess.run(
            ["taskkill", "/PID", str(root_pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception:
        pass


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

    # Separate profile — never attach to the CRM browser profile/window.
    args = [
        browser,
        f"--user-data-dir={profile_dir}",
        "--profile-directory=TVAds",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "--disable-infobars",
        "--disable-features=TranslateUI",
        "--autoplay-policy=no-user-gesture-required",
        f"--window-position={int(left)},{int(top)}",
        f"--window-size={int(width)},{int(height)}",
        # Do NOT use --start-fullscreen: Esc would leave a blank window Chrome owns.
        # Page enters document fullscreen; Esc closes via /tv/close (CRM untouched).
        f"--app={url}",
    ]

    popen_kwargs: dict[str, Any] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if platform.system() == "Windows":
        creation = 0
        creation |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        popen_kwargs["creationflags"] = creation

    try:
        _chrome_proc = subprocess.Popen(args, **popen_kwargs)
    except Exception as exc:
        logger.exception("TV browser launch failed")
        return {"ok": False, "error": str(exc)}

    root_pid = int(_chrome_proc.pid) if _chrome_proc and _chrome_proc.pid else 0
    if platform.system() == "Windows" and root_pid:
        import threading

        def _nudge() -> None:
            _win_place_tv_window(root_pid, left, top, width, height)

        threading.Timer(0.8, _nudge).start()
        threading.Timer(2.0, _nudge).start()

    return {
        "ok": True,
        "pid": root_pid or None,
        "url": url,
        "browser": browser,
        "bounds": {"left": left, "top": top, "width": width, "height": height},
    }


def _win_process_descendants(root_pid: int) -> set[int]:
    """Return root PID + children via CreateToolhelp32Snapshot."""
    import ctypes
    from ctypes import wintypes

    pids = {int(root_pid)}
    TH32CS_SNAPPROCESS = 0x00000002

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == -1:
        return pids
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not kernel32.Process32FirstW(snap, ctypes.byref(entry)):
            return pids
        # Multi-pass to catch nested children.
        for _ in range(4):
            changed = False
            kernel32.Process32FirstW(snap, ctypes.byref(entry))
            while True:
                ppid = int(entry.th32ParentProcessID)
                pid = int(entry.th32ProcessID)
                if ppid in pids and pid not in pids:
                    pids.add(pid)
                    changed = True
                if not kernel32.Process32NextW(snap, ctypes.byref(entry)):
                    break
            if not changed:
                break
    finally:
        kernel32.CloseHandle(snap)
    return pids


def _win_place_tv_window(root_pid: int, left: int, top: int, width: int, height: int) -> None:
    """Move ONLY windows that belong to the TV Chrome process tree (never CRM).

    Never match by page title across all windows — that used to grab the CRM
    Chrome window (title contains «ИТ-М») and drag it onto the TV.
    """
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        allowed = _win_process_descendants(root_pid)
        hwnds: list[int] = []
        pid_out = wintypes.DWORD()

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", wintypes.LONG),
                ("top", wintypes.LONG),
                ("right", wintypes.LONG),
                ("bottom", wintypes.LONG),
            ]

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def enum_proc(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_out))
            if int(pid_out.value) not in allowed:
                return True
            # Skip tiny helper/tool windows; keep real browser chrome frames.
            rect = RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True
            w = int(rect.right) - int(rect.left)
            h = int(rect.bottom) - int(rect.top)
            if w < 200 or h < 200:
                return True
            cls = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, cls, 64)
            class_name = (cls.value or "").lower()
            if "chrome" not in class_name and "chrome_widgetwin" not in class_name:
                # Edge/Chrome main frame is Chrome_WidgetWin_1; allow empty too early.
                if class_name and "widgetwin" not in class_name:
                    return True
            hwnds.append(int(hwnd))
            return True

        user32.EnumWindows(enum_proc, 0)
        HWND_TOP = 0
        SWP_SHOWWINDOW = 0x0040
        for hwnd in hwnds[:2]:
            user32.SetWindowPos(
                hwnd,
                HWND_TOP,
                int(left),
                int(top),
                int(width),
                int(height),
                SWP_SHOWWINDOW,
            )
    except Exception:
        logger.debug("Win32 TV window place skipped", exc_info=True)


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
