"""Interactive-desktop preflight and verified foreground activation."""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes
from dataclasses import dataclass

import psutil

log = logging.getLogger(__name__)

DESKTOP_READOBJECTS = 0x0001
SW_RESTORE = 9
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002


@dataclass(frozen=True, slots=True)
class WindowSnapshot:
    hwnd: int
    pid: int
    process_name: str
    executable: str
    title: str
    command_line: str
    foreground: bool = False


class DesktopBlockedError(RuntimeError):
    """Raised when a system-owned modal window makes GUI input unsafe."""

    def __init__(self, blockers: list[tuple[WindowSnapshot, str]]) -> None:
        self.blockers = tuple(blockers)
        details = "; ".join(
            f"{reason} (pid={window.pid}, process={window.process_name}, "
            f"title={window.title or '<untitled>'})"
            for window, reason in blockers
        )
        super().__init__(f"interactive desktop is blocked: {details}")


def _configure_win32() -> None:
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    user32.CloseDesktop.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [
        wintypes.HWND,
        wintypes.LPWSTR,
        ctypes.c_int,
    ]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindowAsync.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.SetActiveWindow.argtypes = [wintypes.HWND]
    user32.SetActiveWindow.restype = wintypes.HWND
    user32.AttachThreadInput.argtypes = [
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.BOOL,
    ]
    user32.AttachThreadInput.restype = wintypes.BOOL


def _window_text(hwnd: int) -> str:
    user32 = ctypes.windll.user32
    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(max(length + 1, 1))
    user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value


def _process_details(pid: int) -> tuple[str, str, str]:
    try:
        process = psutil.Process(pid)
        name = process.name() or ""
        try:
            executable = process.exe() or ""
        except (psutil.AccessDenied, OSError):
            executable = ""
        try:
            command_line = " ".join(process.cmdline())
        except (psutil.AccessDenied, OSError):
            command_line = ""
        return name, executable, command_line
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return "", "", ""


def snapshot_window(hwnd: int, *, foreground: bool = False) -> WindowSnapshot:
    process_id = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(
        hwnd,
        ctypes.byref(process_id),
    )
    name, executable, command_line = _process_details(process_id.value)
    return WindowSnapshot(
        hwnd=int(hwnd),
        pid=int(process_id.value),
        process_name=name,
        executable=executable,
        title=_window_text(hwnd),
        command_line=command_line,
        foreground=foreground,
    )


def foreground_window() -> WindowSnapshot | None:
    _configure_win32()
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    if not hwnd or not ctypes.windll.user32.IsWindowVisible(hwnd):
        return None
    return snapshot_window(hwnd, foreground=True)


def visible_windows() -> list[WindowSnapshot]:
    _configure_win32()
    user32 = ctypes.windll.user32
    handles: list[int] = []

    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HWND,
        wintypes.LPARAM,
    )

    @callback_type
    def collect(hwnd: int, _lparam: int) -> bool:
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            handles.append(int(hwnd))
        return True

    user32.EnumWindows(collect, 0)
    foreground_handle = int(user32.GetForegroundWindow() or 0)
    return [
        snapshot_window(hwnd, foreground=hwnd == foreground_handle)
        for hwnd in handles
    ]


def classify_blocker(window: WindowSnapshot) -> str | None:
    process_name = window.process_name.casefold()
    command_line = window.command_line.casefold()
    title = window.title.casefold()

    if process_name == "pickerhost.exe" and (
        "firewallnotificationdialogserver" in command_line
        or "windows 安全" in title
        or "windows security" in title
    ):
        return "Windows firewall notification"
    if process_name == "consent.exe":
        return "Windows UAC consent dialog"
    if process_name == "credentialuibroker.exe":
        return "Windows credential dialog"
    if process_name in {"lockapp.exe", "logonui.exe"}:
        return "Windows session is locked"
    return None


def desktop_blockers(
    windows: list[WindowSnapshot] | None = None,
) -> list[tuple[WindowSnapshot, str]]:
    blockers: list[tuple[WindowSnapshot, str]] = []
    for window in windows if windows is not None else visible_windows():
        reason = classify_blocker(window)
        if reason:
            blockers.append((window, reason))
    return blockers


def _require_input_desktop() -> None:
    _configure_win32()
    user32 = ctypes.windll.user32
    handle = user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
    if not handle:
        raise RuntimeError("Windows input desktop is not accessible")
    user32.CloseDesktop(handle)


def describe_window(window: WindowSnapshot | None) -> str:
    if window is None:
        return "no foreground window"
    return (
        f"pid={window.pid} process={window.process_name or '<unknown>'} "
        f"title={window.title or '<untitled>'} hwnd=0x{window.hwnd:X}"
    )


def require_desktop_ready(
    *,
    windows: list[WindowSnapshot] | None = None,
    check_input_desktop: bool = True,
) -> WindowSnapshot | None:
    if check_input_desktop:
        _require_input_desktop()
    blockers = desktop_blockers(windows)
    if blockers:
        raise DesktopBlockedError(blockers)
    current = foreground_window() if windows is None else next(
        (window for window in windows if window.foreground),
        None,
    )
    log.info("desktop guard passed; foreground=%s", describe_window(current))
    return current


def _try_foreground(hwnd: int, *, synthesize_alt: bool) -> None:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    foreground = int(user32.GetForegroundWindow() or 0)
    current_thread = int(kernel32.GetCurrentThreadId())
    target_thread = int(user32.GetWindowThreadProcessId(hwnd, None))
    foreground_thread = (
        int(user32.GetWindowThreadProcessId(foreground, None)) if foreground else 0
    )
    attached: list[int] = []

    for thread_id in {target_thread, foreground_thread}:
        if (
            thread_id
            and thread_id != current_thread
            and user32.AttachThreadInput(current_thread, thread_id, True)
        ):
            attached.append(thread_id)
    try:
        user32.ShowWindowAsync(hwnd, SW_RESTORE)
        user32.BringWindowToTop(hwnd)
        if synthesize_alt:
            user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
        if synthesize_alt:
            user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    finally:
        for thread_id in reversed(attached):
            user32.AttachThreadInput(current_thread, thread_id, False)


def activate_window(hwnd: int, *, timeout: float = 2.0) -> WindowSnapshot:
    """Restore and activate a window, then verify it actually owns foreground."""
    _configure_win32()
    require_desktop_ready()
    deadline = time.monotonic() + timeout
    attempts = 0
    while time.monotonic() < deadline:
        attempts += 1
        _try_foreground(hwnd, synthesize_alt=attempts > 1)
        time.sleep(0.15)
        actual = int(ctypes.windll.user32.GetForegroundWindow() or 0)
        if actual == int(hwnd):
            return snapshot_window(hwnd, foreground=True)
    current = foreground_window()
    raise RuntimeError(
        f"window did not acquire foreground after {attempts} attempt(s); "
        f"target=0x{int(hwnd):X}; actual={describe_window(current)}"
    )


__all__ = [
    "DesktopBlockedError",
    "WindowSnapshot",
    "activate_window",
    "classify_blocker",
    "describe_window",
    "desktop_blockers",
    "foreground_window",
    "require_desktop_ready",
    "visible_windows",
]
