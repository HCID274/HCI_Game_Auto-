"""DPI-aware, screenshot-first UU desktop operations."""

import ctypes
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import psutil

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except (AttributeError, OSError):
    pass

import pyautogui

from wuwa_auto.uu.config import (
    DEBUG_DIR,
    EXPECTED_PRIMARY_SCREEN_SIZE,
    IMAGE_CONFIDENCE,
    IMAGE_INTERVAL,
    MINIMIZE_TIMEOUT,
    TPL_UPDATE_CLOSE,
    TPL_UPDATE_CONFIRM,
    TPL_UPDATE_NOTICE,
    UPDATE_ACTION_TIMEOUT,
    UPDATE_DETECT_TIMEOUT,
    UPDATE_MAX_DISMISSALS,
    UPDATE_SETTLE_DELAY,
    UU_PROCESS_NAMES,
    UU_WINDOW_KEYWORDS,
    WINDOW_POLL_INTERVAL,
    WINDOW_TIMEOUT,
)
from wuwa_auto.uu.errors import UuStartupError
from wuwa_auto.uu.processes import is_uu_running
from wuwa_auto.windows.desktop_guard import activate_window

log = logging.getLogger(__name__)

def _leave_pyautogui_failsafe_corner() -> None:
    """Move an unattended cursor away from PyAutoGUI's emergency corners.

    The virtual-HID probe restores the cursor to its original position.  On an
    unattended desktop that position can legitimately be (0, 0), which makes
    the next PyAutoGUI mouse action raise FailSafeException before it can move.
    Keep the fail-safe enabled for normal operation; only escape an exact
    screen corner with the Win32 cursor API before a bounded UU action.
    """
    screen = pyautogui.size()
    position = pyautogui.position()
    corners = {
        (0, 0),
        (screen.width - 1, 0),
        (0, screen.height - 1),
        (screen.width - 1, screen.height - 1),
    }
    if (position.x, position.y) not in corners:
        return

    safe_position = (screen.width // 2, screen.height // 2)
    if not ctypes.windll.user32.SetCursorPos(*safe_position):
        raise RuntimeError(
            f"failed to move cursor out of PyAutoGUI fail-safe corner {position}"
        )
    log.info(
        "moved cursor out of PyAutoGUI fail-safe corner %s to %s",
        (position.x, position.y),
        safe_position,
    )


def park_cursor_for_detection() -> None:
    """Park the cursor away from the centered UU window before state matching."""
    screen = pyautogui.size()
    parked = (screen.width - 2, screen.height // 2)
    if not ctypes.windll.user32.SetCursorPos(*parked):
        raise RuntimeError(f"failed to park cursor at {parked}")
    log.info("parked cursor for stable UU detection at %s", parked)


def require_admin() -> None:
    try:
        is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        is_admin = False
    if not is_admin:
        raise RuntimeError(
            "UU automation requires elevation; run "
            "`uv run wuwa-auto elevate uu <action>`"
        )


def require_supported_display() -> None:
    screen = pyautogui.size()
    actual = (screen.width, screen.height)
    if actual != EXPECTED_PRIMARY_SCREEN_SIZE:
        raise startup_error(
            "display_environment",
            f"unsupported primary screen {actual}; expected "
            f"{EXPECTED_PRIMARY_SCREEN_SIZE}",
            retryable=False,
        )


def save_step_screenshot(prefix: str) -> Path:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    path = DEBUG_DIR / f"{prefix}_{datetime.now():%Y%m%d_%H%M%S_%f}.png"
    pyautogui.screenshot(str(path))
    log.info("screenshot saved: %s", path)
    return path


def startup_error(
    step_name: str,
    reason: str,
    *,
    retryable: bool = True,
    screenshot: bool = True,
) -> UuStartupError:
    slug = "".join(
        char if char.isalnum() else "_" for char in step_name.casefold()
    ).strip("_")
    evidence = save_step_screenshot(f"uu_{slug}_failed") if screenshot else None
    return UuStartupError(
        step_name,
        reason,
        retryable=retryable,
        screenshot_path=evidence,
    )


def _window_process_name(window: object) -> str:
    hwnd = getattr(window, "_hWnd", None)
    if hwnd is None:
        return ""
    process_id = ctypes.c_ulong()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    try:
        return psutil.Process(process_id.value).name().casefold()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return ""


def _is_uu_window(window: object) -> bool:
    title = (getattr(window, "title", "") or "").casefold()
    if not any(keyword in title for keyword in UU_WINDOW_KEYWORDS):
        return False
    process_name = _window_process_name(window)
    return not process_name or process_name in UU_PROCESS_NAMES


def get_uu_windows() -> list[object]:
    try:
        return [window for window in pyautogui.getAllWindows() if _is_uu_window(window)]
    except Exception as exc:
        log.warning("failed to enumerate UU windows: %s", exc)
        return []


def focus_uu_window(timeout: float = WINDOW_TIMEOUT) -> str:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        for window in get_uu_windows():
            try:
                if getattr(window, "isMinimized", False):
                    window.restore()
                    time.sleep(0.4)
                hwnd = getattr(window, "_hWnd", None)
                if hwnd is not None:
                    activate_window(int(hwnd), timeout=1.5)
                else:
                    window.activate()
                time.sleep(0.8)
                foreground = ctypes.windll.user32.GetForegroundWindow()
                if hwnd is not None and foreground == hwnd:
                    return getattr(window, "title", "") or "<untitled>"
                last_error = RuntimeError(
                    f"UU activation did not own foreground: expected={hwnd}, "
                    f"actual={foreground}"
                )
            except Exception as exc:
                last_error = exc
        time.sleep(WINDOW_POLL_INTERVAL)
    if last_error:
        raise RuntimeError(f"failed to focus UU window: {last_error}")
    raise RuntimeError(f"UU window not detected within {timeout:.0f}s")


def minimize_uu_window(timeout: float = MINIMIZE_TIMEOUT) -> None:
    require_admin()
    if not is_uu_running():
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        windows = get_uu_windows()
        if windows:
            for window in windows:
                if not getattr(window, "isMinimized", False):
                    window.minimize()
                    log.info("UU window minimized: %s", getattr(window, "title", ""))
            return
        time.sleep(WINDOW_POLL_INTERVAL)
    raise RuntimeError("UU window not found for minimization")


@contextmanager
def minimize_on_exit(context: str) -> Iterator[None]:
    try:
        yield
    finally:
        try:
            minimize_uu_window()
        except RuntimeError as exc:
            log.warning("best-effort minimize failed after %s: %s", context, exc)


def _require_template(template: Path) -> None:
    if not template.is_file():
        raise UuStartupError(
            "template_preflight",
            f"required template not found: {template}",
            retryable=False,
        )


def try_locate_image(
    template: Path,
    *,
    timeout: float,
    confidence: float = IMAGE_CONFIDENCE,
) -> tuple[int, int] | None:
    _require_template(template)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            location = pyautogui.locateOnScreen(str(template), confidence=confidence)
            if location is not None:
                center = pyautogui.center(location)
                return center.x, center.y
        except pyautogui.ImageNotFoundException:
            pass
        time.sleep(IMAGE_INTERVAL)
    return None


def wait_for_image(
    template: Path,
    *,
    step_name: str,
    timeout: float,
    confidence: float = IMAGE_CONFIDENCE,
) -> tuple[int, int]:
    log.info("polling %s for step %s", template.name, step_name)
    position = try_locate_image(
        template,
        timeout=timeout,
        confidence=confidence,
    )
    if position is None:
        raise startup_error(
            step_name,
            f"cannot locate {template.name} within {timeout:.1f}s",
        )
    log.info("matched %s at %s", template.name, position)
    return position


def click_after_evidence(position: tuple[int, int], step_name: str) -> None:
    save_step_screenshot(f"uu_{step_name}_before_click")
    _leave_pyautogui_failsafe_corner()
    pyautogui.click(*position)
    log.info("clicked %s at %s", step_name, position)


def hover_after_evidence(position: tuple[int, int], step_name: str) -> None:
    save_step_screenshot(f"uu_{step_name}_before_hover")
    _leave_pyautogui_failsafe_corner()
    pyautogui.moveTo(*position, duration=0.25)
    log.info("hovered %s at %s", step_name, position)


def dismiss_known_popups(context: str) -> None:
    actions = (("confirm", TPL_UPDATE_CONFIRM), ("close", TPL_UPDATE_CLOSE))
    for _ in range(UPDATE_MAX_DISMISSALS):
        if not TPL_UPDATE_NOTICE.is_file():
            return
        notice = try_locate_image(TPL_UPDATE_NOTICE, timeout=UPDATE_DETECT_TIMEOUT)
        if notice is None:
            return
        save_step_screenshot("uu_update_popup_detected")
        for action_name, template in actions:
            if not template.is_file():
                continue
            target = try_locate_image(template, timeout=UPDATE_ACTION_TIMEOUT)
            if target is not None:
                click_after_evidence(target, f"update_{action_name}")
                time.sleep(UPDATE_SETTLE_DELAY)
                break
        else:
            raise startup_error(
                "dismiss_update_popup",
                f"popup detected during {context}, but no known action matched",
            )
