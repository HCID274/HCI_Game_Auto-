"""DPI-aware UU window and image automation."""

import ctypes
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import psutil

# This must run before importing pyautogui or Windows may virtualize coordinates.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except (AttributeError, OSError):  # pragma: no cover - non-Windows import safety
    pass

import pyautogui

from starrail_auto.uu.config import (
    DEBUG_DIR,
    EXPECTED_PRIMARY_SCREEN_SIZE,
    IMAGE_CONFIDENCE,
    IMAGE_RETRY_INTERVAL,
    IMAGE_SEARCH_TIMEOUT,
    TPL_UPDATE_NOTICE,
    UU_MINIMIZE_BEST_EFFORT_TIMEOUT,
    UU_POPUP_ACTION_TIMEOUT,
    UU_POPUP_DETECT_TIMEOUT,
    UU_POPUP_MAX_DISMISSALS,
    UU_POPUP_SETTLE_DELAY,
    UU_PROCESS_KEYWORDS,
    UU_UPDATE_POPUP_ACTIONS,
    UU_WINDOW_KEYWORDS,
    UU_WINDOW_TIMEOUT,
    WINDOW_CHECK_INTERVAL,
)
from starrail_auto.uu.errors import UuStartupError
from starrail_auto.uu.processes import is_uu_running

log = logging.getLogger(__name__)


def require_admin() -> None:
    try:
        is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # pragma: no cover - host dependent
        is_admin = False
    if not is_admin:
        raise RuntimeError(
            "UU automation requires elevation; run "
            "`uv run starrail-auto elevate uu start`"
        )


def save_debug_screenshot(prefix: str) -> Path:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    path = DEBUG_DIR / f"{prefix}_{datetime.now():%Y%m%d_%H%M%S}.png"
    pyautogui.screenshot(str(path))
    log.info("debug screenshot saved: %s", path)
    return path


def startup_error(
    step_name: str,
    reason: str,
    *,
    retryable: bool = True,
    screenshot: bool = True,
) -> UuStartupError:
    slug = "".join(
        ch if ch.isalnum() else "_" for ch in step_name.casefold()
    ).strip("_")
    path = save_debug_screenshot(f"uu_{slug}_failed") if screenshot else None
    return UuStartupError(
        step_name,
        reason,
        retryable=retryable,
        screenshot_path=path,
    )


def require_supported_display() -> None:
    screen = pyautogui.size()
    actual = (screen.width, screen.height)
    if actual != EXPECTED_PRIMARY_SCREEN_SIZE:
        raise startup_error(
            "display_environment",
            f"unsupported primary screen size {actual[0]}x{actual[1]}; expected "
            f"{EXPECTED_PRIMARY_SCREEN_SIZE[0]}x{EXPECTED_PRIMARY_SCREEN_SIZE[1]}",
            retryable=False,
        )


def _window_process_identity(window: object) -> str:
    hwnd = getattr(window, "_hWnd", None)
    if hwnd is None:
        return ""
    pid = ctypes.c_ulong()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    try:
        proc = psutil.Process(pid.value)
        return f"{proc.name() or ''} {proc.exe() or ''}".casefold()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return ""


def _is_uu_window(window: object) -> bool:
    title = (getattr(window, "title", "") or "").casefold()
    if not any(keyword in title for keyword in UU_WINDOW_KEYWORDS):
        return False
    identity = _window_process_identity(window)
    return not identity or any(keyword in identity for keyword in UU_PROCESS_KEYWORDS)


def get_uu_windows() -> list[object]:
    try:
        return [window for window in pyautogui.getAllWindows() if _is_uu_window(window)]
    except Exception as exc:  # pragma: no cover - host dependent
        log.warning("failed to enumerate windows: %s", exc)
        return []


def focus_uu_window(timeout: float = UU_WINDOW_TIMEOUT) -> str:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        for window in get_uu_windows():
            title = getattr(window, "title", "") or "<untitled>"
            try:
                if getattr(window, "isMinimized", False):
                    window.restore()
                    time.sleep(0.3)
                window.activate()
                time.sleep(0.5)
                return title
            except Exception as exc:  # pragma: no cover - host dependent
                last_error = exc
        time.sleep(WINDOW_CHECK_INTERVAL)
    if last_error:
        raise RuntimeError(f"failed to focus UU window: {last_error}")
    raise RuntimeError(f"UU window not detected within {timeout:.0f}s")


def minimize_uu_window(timeout: float = UU_WINDOW_TIMEOUT) -> None:
    require_admin()
    if not is_uu_running():
        log.info("UU accelerator is not running; nothing to minimize")
        return
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        windows = get_uu_windows()
        if not windows:
            time.sleep(WINDOW_CHECK_INTERVAL)
            continue
        for window in windows:
            try:
                if not getattr(window, "isMinimized", False):
                    window.minimize()
                    log.info("UU window minimized: %s", getattr(window, "title", ""))
            except Exception as exc:  # pragma: no cover - host dependent
                last_error = exc
        return
    if last_error:
        raise RuntimeError(f"failed to minimize UU window: {last_error}")
    raise RuntimeError(f"UU window not detected within {timeout:.0f}s")


def minimize_best_effort(context: str) -> None:
    try:
        minimize_uu_window(timeout=UU_MINIMIZE_BEST_EFFORT_TIMEOUT)
    except RuntimeError as exc:
        log.warning("best-effort UU minimize skipped after %s: %s", context, exc)


@contextmanager
def keep_uu_in_background_on_exit(context: str) -> Iterator[None]:
    try:
        yield
    finally:
        minimize_best_effort(context)


def _require_template(template: Path) -> None:
    if not template.exists():
        raise RuntimeError(f"required template not found: {template}")


def try_locate_image(
    template: Path,
    timeout: float = IMAGE_SEARCH_TIMEOUT,
    interval: float = IMAGE_RETRY_INTERVAL,
) -> tuple[int, int] | None:
    _require_template(template)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            location = pyautogui.locateOnScreen(str(template), confidence=IMAGE_CONFIDENCE)
            if location is not None:
                center = pyautogui.center(location)
                return center.x, center.y
        except pyautogui.ImageNotFoundException:
            pass
        time.sleep(interval)
    return None


def wait_for_image(
    template: Path,
    *,
    step_name: str,
    timeout: float,
    initial_delay: float = 0.0,
    interval: float = IMAGE_RETRY_INTERVAL,
) -> tuple[int, int]:
    _require_template(template)
    if initial_delay:
        log.info("waiting %.2fs before polling %s", initial_delay, template.name)
        time.sleep(initial_delay)
    log.info("polling %s every %.2fs for up to %.1fs", template.name, interval, timeout)
    position = try_locate_image(template, timeout=timeout, interval=interval)
    if position is None:
        raise startup_error(
            step_name,
            f"cannot locate template {template.name} within {timeout:.1f}s",
        )
    return position


def click(position: tuple[int, int]) -> None:
    pyautogui.click(*position)
    log.info("clicked at (%d, %d)", *position)


def move_mouse_to(position: tuple[int, int]) -> None:
    pyautogui.moveTo(*position, duration=0.2)
    log.info("mouse moved to (%d, %d)", *position)


def _dismiss_update_popup_once(context: str) -> bool:
    notice = try_locate_image(TPL_UPDATE_NOTICE, timeout=UU_POPUP_DETECT_TIMEOUT)
    if notice is None:
        return False
    log.info("known UU update popup detected during %s at %s", context, notice)
    for action_name, template in UU_UPDATE_POPUP_ACTIONS:
        target = try_locate_image(template, timeout=UU_POPUP_ACTION_TIMEOUT)
        if target is not None:
            click(target)
            log.info("known UU update popup %s clicked during %s", action_name, context)
            time.sleep(UU_POPUP_SETTLE_DELAY)
            return True
    path = save_debug_screenshot("uu_update_popup_action_not_found")
    raise UuStartupError(
        "dismiss_uu_update_popup",
        f"popup detected during {context}, but no known action was found",
        screenshot_path=path,
    )


def dismiss_known_popups(context: str) -> None:
    dismissed = 0
    for _ in range(UU_POPUP_MAX_DISMISSALS):
        if not _dismiss_update_popup_once(context):
            if dismissed:
                log.info("known UU popups cleared after %d action(s)", dismissed)
            return
        dismissed += 1
    path = save_debug_screenshot("uu_update_popup_still_present")
    raise UuStartupError(
        "dismiss_known_uu_popups",
        f"popup remained after {dismissed} action(s) during {context}",
        screenshot_path=path,
    )
