"""Recover narrowly identified Star Rail tutorial overlays during M7A combat."""

# pyautogui must be imported only after Windows DPI awareness is configured.
# ruff: noqa: I001

from __future__ import annotations

import ctypes
import logging
import time
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except (AttributeError, OSError):  # pragma: no cover - host dependent
    pass

import pyautogui

from game_automation_core.windows.desktop_guard import activate_window
from starrail_auto.m7a.config import GAME_PROCESS_NAMES, GAME_WINDOW_KEYWORDS
from starrail_auto.settings import EVIDENCE_DIR, TEMPLATES_DIR

log = logging.getLogger(__name__)

REFERENCE_SIZE = (1920, 1080)
TITLE_TEMPLATE = TEMPLATES_DIR / "gluttony_tutorial_title.png"
NEXT_TEMPLATE = TEMPLATES_DIR / "gluttony_tutorial_next.png"
TITLE_ROI = (700, 130, 1220, 320)
NEXT_ROI = (1650, 350, 1920, 700)
CONTENT_ROI = (450, 260, 1470, 720)
TITLE_CONFIDENCE = 0.88
NEXT_CONFIDENCE = 0.88
PAGE_CHANGE_RATIO = 0.10
MAX_PAGE_CLICKS = 3
TRANSITION_TIMEOUT = 8.0
POLL_INTERVAL = 0.5


@dataclass(frozen=True, slots=True)
class GameWindow:
    hwnd: int
    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class TemplateMatch:
    score: float
    center_x: int
    center_y: int


def _game_process_ids() -> set[int]:
    import psutil

    return {
        proc.pid
        for proc in psutil.process_iter(["name"])
        if (proc.info["name"] or "").casefold() in GAME_PROCESS_NAMES
    }


def find_game_window() -> GameWindow | None:
    """Return the largest visible client window owned by StarRail.exe."""
    process_ids = _game_process_ids()
    if not process_ids:
        return None
    user32 = ctypes.windll.user32
    found: list[GameWindow] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in process_ids:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        title_buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title_buffer, len(title_buffer))
        if not any(
            keyword.casefold() in title_buffer.value.casefold()
            for keyword in GAME_WINDOW_KEYWORDS
        ):
            return True
        client = wintypes.RECT()
        origin = wintypes.POINT(0, 0)
        if not user32.GetClientRect(hwnd, ctypes.byref(client)):
            return True
        if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            return True
        width = int(client.right - client.left)
        height = int(client.bottom - client.top)
        if width >= 1280 and height >= 720:
            found.append(GameWindow(int(hwnd), origin.x, origin.y, width, height))
        return True

    user32.EnumWindows(callback, 0)
    return max(found, key=lambda item: item.width * item.height, default=None)


def _read_template(path: Path) -> np.ndarray | None:
    if not path.is_file():
        return None
    data = np.fromfile(path, dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def _normalized_frame(image: object) -> np.ndarray:
    rgb = np.asarray(image.convert("RGB"))
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    return cv2.resize(bgr, REFERENCE_SIZE, interpolation=cv2.INTER_AREA)


def _match_in_roi(
    frame: np.ndarray,
    template: np.ndarray | None,
    roi: tuple[int, int, int, int],
    confidence: float,
) -> TemplateMatch | None:
    if template is None:
        return None
    left, top, right, bottom = roi
    search = frame[top:bottom, left:right]
    if search.shape[0] < template.shape[0] or search.shape[1] < template.shape[1]:
        return None
    result = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
    _, score, _, location = cv2.minMaxLoc(result)
    if score < confidence:
        return None
    return TemplateMatch(
        score=float(score),
        center_x=left + location[0] + template.shape[1] // 2,
        center_y=top + location[1] + template.shape[0] // 2,
    )


def _page_signature(frame: np.ndarray) -> np.ndarray:
    left, top, right, bottom = CONTENT_ROI
    gray = cv2.cvtColor(frame[top:bottom, left:right], cv2.COLOR_BGR2GRAY)
    sample = cv2.resize(gray, (32, 16), interpolation=cv2.INTER_AREA)
    return sample >= float(sample.mean())


def _signature_distance(first: np.ndarray, second: np.ndarray) -> float:
    return float(np.mean(first != second))


class GluttonyTutorialRecovery:
    """Click only verified tutorial page transitions, never combat controls."""

    def __init__(
        self,
        *,
        window_getter: Callable[[], GameWindow | None] = find_game_window,
        screenshotter: Callable[..., object] = pyautogui.screenshot,
        activator: Callable[..., object] = activate_window,
        clicker: Callable[[int, int], None] = pyautogui.click,
        clock: Callable[[], float] = time.perf_counter,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._window_getter = window_getter
        self._screenshotter = screenshotter
        self._activator = activator
        self._clicker = clicker
        self._clock = clock
        self._sleeper = sleeper
        self._title = _read_template(TITLE_TEMPLATE)
        self._next = _read_template(NEXT_TEMPLATE)
        self._clicked_pages: list[np.ndarray] = []
        self._click_count = 0
        self._template_error_logged = False

    def _capture(self, window: GameWindow) -> tuple[object, np.ndarray]:
        image = self._screenshotter(
            region=(window.left, window.top, window.width, window.height)
        )
        return image, _normalized_frame(image)

    def _save(self, prefix: str, image: object) -> Path:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now(tz=timezone.utc).astimezone()
        path = EVIDENCE_DIR / f"{prefix}_{now:%Y%m%d_%H%M%S_%f}.png"
        image.save(path)
        return path

    def _tutorial_matches(
        self, frame: np.ndarray
    ) -> tuple[TemplateMatch | None, TemplateMatch | None]:
        return (
            _match_in_roi(frame, self._title, TITLE_ROI, TITLE_CONFIDENCE),
            _match_in_roi(frame, self._next, NEXT_ROI, NEXT_CONFIDENCE),
        )

    def poll(self) -> bool:
        """Attempt at most one verified page transition for this poll."""
        if self._title is None or self._next is None:
            if not self._template_error_logged:
                log.error(
                    "Star Rail tutorial recovery templates missing: title=%s next=%s",
                    TITLE_TEMPLATE,
                    NEXT_TEMPLATE,
                )
                self._template_error_logged = True
            return False
        window = self._window_getter()
        if window is None:
            return False
        image, frame = self._capture(window)
        title, action = self._tutorial_matches(frame)
        if title is None or action is None:
            return False
        signature = _page_signature(frame)
        if any(
            _signature_distance(signature, clicked) < PAGE_CHANGE_RATIO
            for clicked in self._clicked_pages
        ):
            log.warning("Star Rail tutorial page already clicked; refusing repeat input")
            return False
        if self._click_count >= MAX_PAGE_CLICKS:
            log.error("Star Rail tutorial page transition budget exhausted")
            self._save("starrail_tutorial_budget_exhausted", image)
            return False

        self._activator(window.hwnd, timeout=3.0)
        image, frame = self._capture(window)
        title, action = self._tutorial_matches(frame)
        if title is None or action is None:
            return False
        signature = _page_signature(frame)
        if any(
            _signature_distance(signature, clicked) < PAGE_CHANGE_RATIO
            for clicked in self._clicked_pages
        ):
            return False

        before = self._save("starrail_tutorial_before_click", image)
        scale_x = window.width / REFERENCE_SIZE[0]
        scale_y = window.height / REFERENCE_SIZE[1]
        target_x = window.left + round(action.center_x * scale_x)
        target_y = window.top + round(action.center_y * scale_y)
        self._clicked_pages.append(signature.copy())
        self._click_count += 1
        self._clicker(target_x, target_y)
        log.warning(
            "Star Rail tutorial page clicked once: transition=%d/%d point=(%d,%d) "
            "title_score=%.3f action_score=%.3f evidence=%s",
            self._click_count,
            MAX_PAGE_CLICKS,
            target_x,
            target_y,
            title.score,
            action.score,
            before,
        )

        deadline = self._clock() + TRANSITION_TIMEOUT
        while self._clock() < deadline:
            self._sleeper(POLL_INTERVAL)
            after_image, after_frame = self._capture(window)
            after_title, _ = self._tutorial_matches(after_frame)
            if after_title is None:
                after = self._save("starrail_tutorial_dismissed", after_image)
                log.warning("Star Rail tutorial overlay dismissed: evidence=%s", after)
                return True
            after_signature = _page_signature(after_frame)
            if _signature_distance(signature, after_signature) >= PAGE_CHANGE_RATIO:
                after = self._save("starrail_tutorial_page_advanced", after_image)
                log.warning("Star Rail tutorial page advanced: evidence=%s", after)
                return True
        log.error("Star Rail tutorial page did not change after its single click")
        return False


__all__ = [
    "GameWindow",
    "GluttonyTutorialRecovery",
    "TemplateMatch",
    "find_game_window",
]
