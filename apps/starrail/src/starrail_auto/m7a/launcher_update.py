"""Local image-driven recovery for HoYo launcher update failures."""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes
from pathlib import Path

import pyautogui
from game_automation_core.windows.desktop_guard import activate_window

from starrail_auto.m7a.environment import (
    game_launcher_process_ids,
    is_game_launcher_running,
)
from starrail_auto.m7a.logs import game_update_waiting
from starrail_auto.m7a.models import M7ALogCheckpoint
from starrail_auto.settings import RUNTIME_DIR, TEMPLATES_DIR

log = logging.getLogger(__name__)

DOWNLOAD_FAILED_TEMPLATE = TEMPLATES_DIR / "hyp_download_failed.png"
EVIDENCE_DIR = RUNTIME_DIR / "evidence"
MATCH_CONFIDENCE = 0.92
DISAPPEAR_TIMEOUT = 15.0
RETRY_RESET_ABSENCE_SECONDS = 30.0


def _hyp_window() -> tuple[int, int, int, int, int] | None:
    process_ids = game_launcher_process_ids()
    if not process_ids:
        return None
    found: list[tuple[int, int, int, int, int]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd: int, _lparam: int) -> bool:
        if not ctypes.windll.user32.IsWindowVisible(hwnd):
            return True
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in process_ids:
            return True
        rect = wintypes.RECT()
        if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width > 600 and height > 400:
            found.append((hwnd, rect.left, rect.top, width, height))
        return True

    ctypes.windll.user32.EnumWindows(callback, 0)
    return max(found, key=lambda item: item[3] * item[4], default=None)


def _locate_failure(region: tuple[int, int, int, int]):
    try:
        return pyautogui.locateOnScreen(
            str(DOWNLOAD_FAILED_TEMPLATE),
            confidence=MATCH_CONFIDENCE,
            region=region,
        )
    except pyautogui.ImageNotFoundException:
        return None


def _save_evidence(name: str, region: tuple[int, int, int, int]) -> Path:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE_DIR / f"{name}_{time.strftime('%Y%m%d_%H%M%S')}.png"
    pyautogui.screenshot(region=region).save(path)
    return path


_retry_clicked = False
_absent_since: float | None = None


def recover_launcher_download_failure() -> bool:
    """Click the audited failure button once and require it to disappear."""
    global _retry_clicked, _absent_since
    if not DOWNLOAD_FAILED_TEMPLATE.is_file():
        log.error("HoYo update recovery template is missing: %s", DOWNLOAD_FAILED_TEMPLATE)
        return False
    window = _hyp_window()
    if window is None:
        return False
    hwnd, left, top, width, height = window
    region = (left, top, width, height)
    activate_window(hwnd, timeout=3.0)
    time.sleep(0.5)
    match = _locate_failure(region)
    if match is None:
        if _retry_clicked:
            now = time.monotonic()
            if _absent_since is None:
                _absent_since = now
            elif now - _absent_since >= RETRY_RESET_ABSENCE_SECONDS:
                log.info("HoYo failure resolved; retry guard reset")
                _retry_clicked = False
                _absent_since = None
        return False
    _absent_since = None
    center = pyautogui.center(match)
    relative_x = center.x - left
    relative_y = center.y - top
    if relative_x < width * 0.60 or relative_y < height * 0.70:
        log.error(
            "rejected HoYo failure match outside bottom-right action area: point=%s region=%s",
            center,
            region,
        )
        return False
    if _retry_clicked:
        log.warning("HoYo failure retry already attempted; skipping repeat click")
        return False
    _retry_clicked = True
    before = _save_evidence("hyp_download_failure_before_retry", region)
    pyautogui.click(center.x, center.y)
    deadline = time.monotonic() + DISAPPEAR_TIMEOUT
    while time.monotonic() < deadline:
        time.sleep(0.5)
        if _locate_failure(region) is None:
            after = _save_evidence("hyp_download_failure_retry_started", region)
            log.warning(
                "HoYo launcher download failure retried once: before=%s after=%s",
                before,
                after,
            )
            return True
    log.error("HoYo launcher failure button remained after one click: evidence=%s", before)
    return False


def launcher_update_maintenance(checkpoint: M7ALogCheckpoint) -> bool:
    """Keep M7A alive during its launcher handoff and recover a failed retry."""
    active = game_update_waiting(checkpoint) and is_game_launcher_running()
    if active:
        recover_launcher_download_failure()
    return active


if __name__ == "__main__":
    raise SystemExit(0 if recover_launcher_download_failure() else 1)
