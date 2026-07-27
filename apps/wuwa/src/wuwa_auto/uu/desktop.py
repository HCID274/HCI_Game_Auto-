"""Wuthering Waves template and timing adapter for shared UU desktop primitives."""

from __future__ import annotations

import ctypes
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from game_automation_core.uu.desktop import (
    UuDesktopConfig,
    UuDesktopController,
    pyautogui,
)
from game_automation_core.windows.desktop_guard import activate_window
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
from wuwa_auto.uu.processes import is_uu_running

_controller = UuDesktopController(
    UuDesktopConfig(
        evidence_dir=DEBUG_DIR,
        expected_screen_size=EXPECTED_PRIMARY_SCREEN_SIZE,
        window_keywords=UU_WINDOW_KEYWORDS,
        process_names=frozenset(UU_PROCESS_NAMES),
        update_notice=TPL_UPDATE_NOTICE,
        update_actions=(("confirm", TPL_UPDATE_CONFIRM), ("close", TPL_UPDATE_CLOSE)),
        admin_hint="`uv run wuwa-auto elevate uu <action>`",
        window_timeout=WINDOW_TIMEOUT,
        window_poll_interval=WINDOW_POLL_INTERVAL,
        minimize_timeout=MINIMIZE_TIMEOUT,
        image_interval=IMAGE_INTERVAL,
        image_confidence=IMAGE_CONFIDENCE,
        popup_detect_timeout=UPDATE_DETECT_TIMEOUT,
        popup_action_timeout=UPDATE_ACTION_TIMEOUT,
        popup_settle_delay=UPDATE_SETTLE_DELAY,
        popup_max_dismissals=UPDATE_MAX_DISMISSALS,
    ),
    is_process_running=is_uu_running,
)


def _leave_pyautogui_failsafe_corner() -> None:
    _controller.leave_failsafe_corner()


def park_cursor_for_detection() -> None:
    _controller.park_cursor_for_detection()


def require_admin() -> None:
    _controller.require_admin()


def require_supported_display() -> None:
    _controller.require_supported_display()


def save_step_screenshot(prefix: str) -> Path:
    return _controller.save_screenshot(prefix)


def startup_error(
    step_name: str,
    reason: str,
    *,
    retryable: bool = True,
    screenshot: bool = True,
):
    return _controller.startup_error(
        step_name, reason, retryable=retryable, screenshot=screenshot
    )


def get_uu_windows() -> list[object]:
    return _controller.get_windows()


def focus_uu_window(timeout: float = WINDOW_TIMEOUT) -> str:
    return _controller.focus_window(
        timeout,
        activator=activate_window,
        windows_getter=get_uu_windows,
    )


def minimize_uu_window(timeout: float = MINIMIZE_TIMEOUT) -> None:
    _controller.minimize_window(timeout)


@contextmanager
def minimize_on_exit(context: str) -> Iterator[None]:
    with _controller.minimize_on_exit(context):
        yield


def try_locate_image(
    template: Path,
    *,
    timeout: float,
    confidence: float = IMAGE_CONFIDENCE,
) -> tuple[int, int] | None:
    return _controller.try_locate_image(
        template, timeout=timeout, confidence=confidence
    )


def wait_for_image(
    template: Path,
    *,
    step_name: str,
    timeout: float,
    confidence: float = IMAGE_CONFIDENCE,
) -> tuple[int, int]:
    return _controller.wait_for_image(
        template,
        step_name=step_name,
        timeout=timeout,
        confidence=confidence,
    )


def click_after_evidence(position: tuple[int, int], step_name: str) -> None:
    _controller.click_after_evidence(position, step_name)


def hover_after_evidence(position: tuple[int, int], step_name: str) -> None:
    _controller.hover_after_evidence(position, step_name)


def dismiss_known_popups(context: str) -> None:
    _controller.dismiss_known_popups(context)
