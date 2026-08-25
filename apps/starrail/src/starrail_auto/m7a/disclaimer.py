"""Handle the M7A startup disclaimer without modifying the upstream package."""

from __future__ import annotations

import ctypes
import logging
import re
import time
from collections.abc import Callable, Sequence
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from typing import Any

import pyautogui

from game_automation_core.windows.desktop_guard import (
    WindowSnapshot,
    activate_window,
    visible_windows,
)
from starrail_auto.m7a.config import (
    DEBUG_DIR,
    M7A_ASSISTANT_PROCESS_NAME,
    M7A_CONFIG_PATH,
    M7A_DISCLAIMER_DETECTION_TIMEOUT,
    M7A_DISCLAIMER_MIN_CONFIRM_SECONDS,
    M7A_DISCLAIMER_TITLE_KEYWORD,
    M7A_DISCLAIMER_VERIFY_TIMEOUT,
    M7A_LAUNCHER_PROCESS_NAME,
)

log = logging.getLogger(__name__)

_AUTO_UPDATE_PATTERN = re.compile(
    r"(?im)^\s*auto_update\s*:\s*(true|false)(?:\s+#.*)?$"
)
_PINK_BUTTON_COLOR = (255, 165, 205)


def read_auto_update(path: Path = M7A_CONFIG_PATH) -> bool | None:
    """Read only the upstream acknowledgement flag; never write it ourselves."""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("cannot read M7A auto_update flag: path=%s error=%s", path, exc)
        return None
    match = _AUTO_UPDATE_PATTERN.search(content)
    if not match:
        log.warning("M7A auto_update flag not found: path=%s", path)
        return None
    return match.group(1).casefold() == "true"


def _is_recent_process(window: WindowSnapshot, started_at: float | None) -> bool:
    if started_at is None or started_at <= 0:
        return True
    try:
        import psutil

        return psutil.Process(window.pid).create_time() >= started_at - 2.0
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return False


def find_m7a_window(
    *,
    started_at: float | None,
    windows_getter: Callable[[], Sequence[WindowSnapshot]] = visible_windows,
) -> WindowSnapshot | None:
    """Return a visible, newly launched M7A Assistant window."""
    process_names = {
        M7A_ASSISTANT_PROCESS_NAME.casefold(),
        M7A_LAUNCHER_PROCESS_NAME.casefold(),
    }
    title_keyword = M7A_DISCLAIMER_TITLE_KEYWORD.casefold()
    candidates = [
        window
        for window in windows_getter()
        if window.process_name.casefold() in process_names
        and title_keyword in window.title.casefold()
        and _is_recent_process(window, started_at)
    ]
    return candidates[0] if candidates else None


def _window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    rect = wintypes.RECT()
    if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)


def _virtual_screen_origin() -> tuple[int, int]:
    user32 = ctypes.windll.user32
    return int(user32.GetSystemMetrics(76)), int(user32.GetSystemMetrics(77))


def _pink(pixel: tuple[int, int, int]) -> bool:
    red, green, blue = pixel
    target_red, target_green, target_blue = _PINK_BUTTON_COLOR
    return (
        red >= target_red - 30
        and abs(green - target_green) <= 40
        and abs(blue - target_blue) <= 40
        and red - green >= 45
        and blue - green >= 15
    )


def _horizontal_runs(
    image: Any,
    *,
    y: int,
    left: int,
    right: int,
    step: int = 2,
) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    run_start: int | None = None
    for x in range(left, right, step):
        matched = _pink(image.getpixel((x, y))[:3])
        if matched and run_start is None:
            run_start = x
        elif not matched and run_start is not None:
            runs.append((run_start, x - step))
            run_start = None
    if run_start is not None:
        runs.append((run_start, right - step))
    return runs


def find_acknowledge_position(
    window: WindowSnapshot,
    *,
    screenshot: Any | None = None,
    screenshot_getter: Callable[[], Any] | None = None,
) -> tuple[int, int] | None:
    """Find the left acknowledgement button from the right pink exit button.

    The upstream dialog keeps both buttons at the same size and row.  Detecting
    the pink ``退出`` button gives us a scale-aware anchor; the host clicks the
    equally-sized button immediately to its left (``我已知晓``).
    """
    rect = _window_rect(window.hwnd)
    if rect is None:
        return None
    if screenshot is None:
        if screenshot_getter is None:
            from PIL import ImageGrab

            screenshot_getter = lambda: ImageGrab.grab(all_screens=True)
        screenshot = screenshot_getter()
    origin_x, origin_y = _virtual_screen_origin()
    window_left, window_top, window_right, window_bottom = rect
    left = window_left - origin_x
    top = window_top - origin_y
    right = window_right - origin_x
    bottom = window_bottom - origin_y
    if left < 0 or top < 0 or right > screenshot.width or bottom > screenshot.height:
        return None
    width = right - left
    height = bottom - top
    min_button_width = max(120, int(width * 0.18))
    max_button_width = int(width * 0.60)
    candidates: list[tuple[int, int, int, int]] = []
    for y in range(top + int(height * 0.60), top + int(height * 0.96), 2):
        for start, end in _horizontal_runs(
            screenshot,
            y=y,
            left=left + int(width * 0.35),
            right=right,
        ):
            run_width = end - start
            if min_button_width <= run_width <= max_button_width:
                candidates.append((start, end, y, run_width))
    if not candidates:
        return None
    pink_left, pink_right, pink_row, pink_width = max(
        candidates,
        key=lambda item: item[3],
    )
    matching_rows = [
        row
        for start, end, row, _ in candidates
        if abs(start - pink_left) <= 12 and abs(end - pink_right) <= 12
    ]
    row_top = min(matching_rows) if matching_rows else pink_row
    row_bottom = max(matching_rows) if matching_rows else pink_row
    pink_center_y = (row_top + row_bottom) // 2
    gap = max(8, int(pink_width * 0.03))
    acknowledge_center_x = pink_left - gap - pink_width // 2
    if acknowledge_center_x <= left or acknowledge_center_x >= pink_left:
        return None
    return acknowledge_center_x, pink_center_y


def save_screenshot(prefix: str) -> Path | None:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    path = DEBUG_DIR / f"{prefix}_{datetime.now():%Y%m%d_%H%M%S_%f}.png"
    try:
        from PIL import ImageGrab

        ImageGrab.grab(all_screens=True).save(path)
        log.info("M7A disclaimer screenshot saved: %s", path)
        return path
    except Exception as exc:  # pragma: no cover - interactive desktop required
        log.warning("failed to capture M7A disclaimer screenshot: %s", exc)
        return None


class M7ADisclaimerHandler:
    """Small state machine for one startup acknowledgement click."""

    def __init__(
        self,
        *,
        started_at: float,
        windows_getter: Callable[[], Sequence[WindowSnapshot]] = visible_windows,
        target_finder: Callable[[WindowSnapshot], tuple[int, int] | None] = find_acknowledge_position,
        config_reader: Callable[[], bool | None] = read_auto_update,
        activator: Callable[[int], object] = activate_window,
        clicker: Callable[[int, int], None] = pyautogui.click,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.started_at = started_at
        self._windows_getter = windows_getter
        self._target_finder = target_finder
        self._config_reader = config_reader
        self._activator = activator
        self._clicker = clicker
        self._clock = clock
        self._seen_at: float | None = None
        self._suspected_at: float | None = None
        self._clicked = False
        self._verify_deadline: float | None = None
        self._confirmed = False
        self.failure_reason: str | None = None

    @property
    def clicked(self) -> bool:
        return self._clicked

    def poll(self) -> bool:
        """Return whether startup is unblocked for the game-ready check."""
        if self._confirmed:
            return True
        window = find_m7a_window(
            started_at=self.started_at,
            windows_getter=self._windows_getter,
        )
        if window is None:
            return True
        auto_update = self._config_reader()
        if auto_update is True and not self._clicked:
            self._confirmed = True
            log.info("M7A disclaimer already acknowledged; no click needed")
            return True
        target = self._target_finder(window)
        now = self._clock()
        if target is None:
            if self._clicked:
                if auto_update is True:
                    self._confirmed = True
                    log.info("M7A disclaimer cleared and auto_update=true")
                    return True
                if self._verify_deadline is not None and now >= self._verify_deadline:
                    self.failure_reason = "disclaimer_confirmation_not_verified"
                    log.error(
                        "M7A disclaimer closed but auto_update=true was not observed"
                    )
                return False
            if self._suspected_at is None:
                self._suspected_at = now
                log.info(
                    "M7A disclaimer candidate detected; waiting for acknowledgement button"
                )
            elif now - self._suspected_at >= M7A_DISCLAIMER_DETECTION_TIMEOUT:
                self.failure_reason = "disclaimer_button_not_found"
            return False
        self._suspected_at = None
        if self._seen_at is None:
            self._seen_at = now
            log.info(
                "M7A disclaimer detected: confirmation will be allowed after %.0fs",
                M7A_DISCLAIMER_MIN_CONFIRM_SECONDS,
            )
        if not self._clicked:
            if now - self._seen_at < M7A_DISCLAIMER_MIN_CONFIRM_SECONDS:
                return False
            try:
                self._activator(window.hwnd)
                save_screenshot("m7a_disclaimer_before_click")
                self._clicker(*target)
            except Exception as exc:
                self.failure_reason = f"disclaimer_click_failed:{exc}"
                log.error("M7A disclaimer click failed: %s", exc)
                return False
            self._clicked = True
            self._verify_deadline = now + M7A_DISCLAIMER_VERIFY_TIMEOUT
            log.info("M7A disclaimer acknowledgement clicked once at %s", target)
            return False
        if self._verify_deadline is not None and now >= self._verify_deadline:
            self.failure_reason = "disclaimer_confirmation_not_verified"
            log.error("M7A disclaimer did not clear after the single acknowledgement click")
        return False


__all__ = [
    "M7ADisclaimerHandler",
    "find_acknowledge_position",
    "find_m7a_window",
    "read_auto_update",
]
