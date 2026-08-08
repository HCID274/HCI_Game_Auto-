"""Star Rail template and timing adapter for shared UU desktop primitives."""

from __future__ import annotations

import ctypes  # noqa: F401 - keep legacy test/adapter monkeypatch compatibility
import time  # noqa: F401 - keep legacy test/adapter monkeypatch compatibility
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from game_automation_core.uu.desktop import (
    UuDesktopConfig,
    UuDesktopController,
)
from game_automation_core.windows.desktop_guard import activate_window

from starrail_auto.uu.config import (
    DEBUG_DIR,
    EXPECTED_PRIMARY_SCREEN_SIZE,
    IMAGE_CONFIDENCE,
    IMAGE_RETRY_INTERVAL,
    TPL_MANDATORY_UPDATE_ACTION,
    TPL_MANDATORY_UPDATE_NOTICE,
    TPL_UPDATE_NOTICE,
    UU_MINIMIZE_BEST_EFFORT_TIMEOUT,
    UU_POPUP_ACTION_TIMEOUT,
    UU_POPUP_DETECT_TIMEOUT,
    UU_POPUP_MAX_DISMISSALS,
    UU_POPUP_SETTLE_DELAY,
    UU_PROCESS_NAMES,
    UU_UPDATE_POPUP_ACTIONS,
    UU_WINDOW_KEYWORDS,
    UU_WINDOW_TIMEOUT,
    WINDOW_CHECK_INTERVAL,
)
from starrail_auto.uu.processes import is_uu_running

_controller = UuDesktopController(
    UuDesktopConfig(
        evidence_dir=DEBUG_DIR,
        expected_screen_size=EXPECTED_PRIMARY_SCREEN_SIZE,
        window_keywords=UU_WINDOW_KEYWORDS,
        process_names=frozenset(UU_PROCESS_NAMES),
        update_notice=TPL_UPDATE_NOTICE,
        update_actions=UU_UPDATE_POPUP_ACTIONS,
        admin_hint="`uv run starrail-auto elevate uu start`",
        mandatory_update_notice=TPL_MANDATORY_UPDATE_NOTICE,
        mandatory_update_action=TPL_MANDATORY_UPDATE_ACTION,
        window_timeout=UU_WINDOW_TIMEOUT,
        window_poll_interval=WINDOW_CHECK_INTERVAL,
        minimize_timeout=UU_MINIMIZE_BEST_EFFORT_TIMEOUT,
        image_interval=IMAGE_RETRY_INTERVAL,
        image_confidence=IMAGE_CONFIDENCE,
        popup_detect_timeout=UU_POPUP_DETECT_TIMEOUT,
        popup_action_timeout=UU_POPUP_ACTION_TIMEOUT,
        popup_settle_delay=UU_POPUP_SETTLE_DELAY,
        popup_max_dismissals=UU_POPUP_MAX_DISMISSALS,
    ),
    is_process_running=is_uu_running,
)


def require_admin() -> None:
    _controller.require_admin()


def require_supported_display() -> None:
    _controller.require_supported_display()


def save_debug_screenshot(prefix: str) -> Path:
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


def focus_uu_window(timeout: float = UU_WINDOW_TIMEOUT) -> str:
    return _controller.focus_window(
        timeout,
        activator=activate_window,
        windows_getter=get_uu_windows,
    )


def minimize_uu_window(timeout: float = UU_WINDOW_TIMEOUT) -> None:
    _controller.minimize_window(timeout)


def minimize_best_effort(context: str) -> None:
    _controller.minimize_best_effort(context)


@contextmanager
def keep_uu_in_background_on_exit(context: str) -> Iterator[None]:
    try:
        yield
    finally:
        minimize_best_effort(context)


def try_locate_image(
    template: Path,
    timeout: float,
    interval: float = IMAGE_RETRY_INTERVAL,
) -> tuple[int, int] | None:
    return _controller.try_locate_image(
        template, timeout=timeout, interval=interval
    )


def wait_for_image(
    template: Path,
    *,
    step_name: str,
    timeout: float,
    initial_delay: float = 0.0,
    interval: float = IMAGE_RETRY_INTERVAL,
) -> tuple[int, int]:
    return _controller.wait_for_image(
        template,
        step_name=step_name,
        timeout=timeout,
        initial_delay=initial_delay,
        interval=interval,
    )


def click(position: tuple[int, int]) -> None:
    _controller.click(position)


def move_mouse_to(position: tuple[int, int]) -> None:
    _controller.move_mouse_to(position)


def dismiss_known_popups(context: str) -> None:
    _controller.dismiss_known_popups(context)


def mandatory_update_visible(timeout: float = UU_POPUP_DETECT_TIMEOUT) -> bool:
    return _controller.mandatory_update_visible(timeout=timeout)


def accept_mandatory_update(context: str) -> bool:
    return _controller.accept_mandatory_update(context)
