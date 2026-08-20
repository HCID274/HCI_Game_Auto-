"""Screenshot-driven official launcher update and game-start recovery."""

from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import subprocess
import time
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import psutil

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except (AttributeError, OSError):  # pragma: no cover - non-Windows import safety
    pass

import pyautogui
from game_automation_core.windows.desktop_guard import activate_window
from PIL import Image

from wuwa_auto.input.viiper import VirtualHidMouse, managed_virtual_mouse
from wuwa_auto.settings import (
    EVIDENCE_DIR,
    WUWA_CLIENT_EXE,
    WUWA_CLIENT_LOGIN_TEMPLATE,
    WUWA_CLIENT_MONTHLY_REWARD_TEMPLATE,
    WUWA_CLIENT_NETWORK_RETRY_TEMPLATE,
    WUWA_CLIENT_REMOTE_CONFIG_RETRY_TEMPLATE,
    WUWA_CLIENT_REWARD_RESULT_TEMPLATE,
    WUWA_CLIENT_UPDATE_RESTART_CONFIRM_TEMPLATE,
    WUWA_CLIENT_UPDATE_RESTART_NOTICE_TEMPLATE,
    WUWA_INSTALL_DIR,
    WUWA_LAUNCHER_EXE,
    WUWA_LAUNCHER_PRIMARY_ANCHOR_TEMPLATE,
    WUWA_LAUNCHER_READY_TEMPLATE,
    WUWA_LAUNCHER_SELFUPDATE_CONFIRM_TEMPLATE,
)
from wuwa_auto.uu.desktop import require_admin

log = logging.getLogger(__name__)

LAUNCHER_WINDOW_TIMEOUT_SECONDS = 90.0
GAME_WINDOW_TIMEOUT_SECONDS = 300.0
# 4h launcher-side budget agreed on 2026-08-20: a version-day download must
# survive slow networks instead of failing at the previous 2.5h cap.
CLIENT_UPDATE_TIMEOUT_SECONDS = 14400.0
POLL_INTERVAL_SECONDS = 5.0
READY_RETRY_SECONDS = 60.0
CLIENT_RESTART_TIMEOUT_SECONDS = 180.0
WORLD_STABLE_POLLS = 2
MAX_CLICKS_PER_STATE = 2
PRIMARY_ANCHOR_TO_BUTTON_CENTER = (117, 0)
# Version-day gate budgets agreed on 2026-08-20: a normal night must reach a
# recognizable launcher state quickly, while a version day may spend hours
# downloading.  User rule (2026-08-21): a click that shows no visible
# acknowledgment within 15s is a failed click and must be retried at once;
# a frozen update UI is restarted (resume) after 120s instead of silently
# waiting out the old 15-minute window.  Restarts are cheap and the CDN
# stalls often, so the ladder is deep before giving up.
VERSION_UPDATE_DETECT_TIMEOUT_SECONDS = 300.0
SELFUPDATE_ACK_TIMEOUT_SECONDS = 15.0
# A frozen UI is restarted after 5 minutes; 0820 live data showed legitimate
# tails crawl at ~0.05%/min where sub-percent bar movement is invisible to a
# coarse hash, so the window must stay generous and the hash fine-grained.
VERSION_UPDATE_STALL_SECONDS = 300.0
VERSION_UPDATE_MAX_RESTARTS = 6
SELFUPDATE_MAX_CLICK_ATTEMPTS = 4
# The launcher self-update reminder is a modal 1600x950 dialog (true pixels
# on a 2560x1440 desktop at 125% scaling, top-left (480,215)).  Its big black
# "更新" button sits at the dialog's bottom-RIGHT (center ≈ (1282,824)
# dialog-relative); the left column carries an auto-downloading progress
# strip and the 退出 link.  The template is a 170x110 crop centered on the
# button, so the click point is the match center with no offset.  Calibrated
# 2026-08-20 by pristine-state vision + on-screen template back-match after
# the first template (built from a wrongly-scaled crop) clicked blank space
# 1038px off all night.
LAUNCHER_SELFUPDATE_REMINDER_TITLE = "KRUpdateReminderDlg"
SELFUPDATE_CONFIRM_CLICK_OFFSET = (0, 0)

SW_RESTORE = 9
SW_MINIMIZE = 6


@dataclass(frozen=True, slots=True)
class WindowInfo:
    hwnd: int
    pid: int
    title: str
    executable: Path
    rect: tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class ClientPreparationResult:
    updated: bool
    launcher_actions: tuple[str, ...]
    evidence_paths: tuple[str, ...]
    game_pid: int


@dataclass(frozen=True, slots=True)
class ClientUpdateOutcome:
    """Launcher-side update result; the game itself is never launched."""

    update_performed: bool
    launcher_actions: tuple[str, ...]
    evidence_paths: tuple[str, ...]


class ClientLauncherError(RuntimeError):
    pass


class _ClientRestartRequired(RuntimeError):
    def __init__(self, previous_pid: int) -> None:
        super().__init__(f"client update requested restart for pid={previous_pid}")
        self.previous_pid = previous_pid


def _normal(path: Path) -> Path:
    return path.resolve()


def _is_under(path: Path, root: Path) -> bool:
    try:
        _normal(path).relative_to(_normal(root))
        return True
    except ValueError:
        return False


def _window_process(hwnd: int) -> tuple[int, Path] | None:
    pid = ctypes.c_ulong()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return None
    try:
        return pid.value, Path(psutil.Process(pid.value).exe())
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
        return None


def _window_title(hwnd: int) -> str:
    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    ctypes.windll.user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value


def _window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    rect = wintypes.RECT()
    if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    if rect.right <= rect.left or rect.bottom <= rect.top:
        return None
    return rect.left, rect.top, rect.right, rect.bottom


def _top_level_windows() -> list[WindowInfo]:
    windows: list[WindowInfo] = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    @callback_type
    def collect(hwnd: int, _: int) -> bool:
        process = _window_process(hwnd)
        rect = _window_rect(hwnd)
        if process is None or rect is None:
            return True
        pid, executable = process
        windows.append(
            WindowInfo(
                hwnd=int(hwnd),
                pid=pid,
                title=_window_title(hwnd),
                executable=executable,
                rect=rect,
            )
        )
        return True

    ctypes.windll.user32.EnumWindows(collect, 0)
    return windows


def _launcher_window() -> WindowInfo | None:
    candidates = [
        window
        for window in _top_level_windows()
        if (
            window.executable.name.casefold() == "launcher_main.exe"
            and _is_under(window.executable, WUWA_INSTALL_DIR)
            and ctypes.windll.user32.IsWindowVisible(window.hwnd)
            and window.title not in {"Hidden Window", "GDI+ Window (launcher_main.exe)"}
            and (
                window.title in {"鸣潮", "MainWindow"}
                or _window_area(window) >= 500 * 300
            )
        )
    ]
    return max(
        candidates,
        key=lambda window: (
            window.title in {"鸣潮", "MainWindow"},
            _window_area(window),
        ),
        default=None,
    )


def _launcher_update_reminder_window() -> WindowInfo | None:
    """Find the modal launcher self-update reminder owned by the official launcher.

    On version days (first seen 2026-08-20) the launcher opens with a modal
    KRUpdateReminderDlg that blocks the main page.  The primary-anchor click
    lands on the main page BEHIND the modal, so this dialog must be handled
    before any main-page state.
    """
    for window in _top_level_windows():
        if window.title != LAUNCHER_SELFUPDATE_REMINDER_TITLE:
            continue
        if window.executable.name.casefold() != "launcher_main.exe":
            continue
        if not _is_under(window.executable, WUWA_INSTALL_DIR):
            continue
        if not ctypes.windll.user32.IsWindowVisible(window.hwnd):
            continue
        return window
    return None


def _game_window() -> WindowInfo | None:
    expected = _normal(WUWA_CLIENT_EXE)
    candidates = [
        window
        for window in _top_level_windows()
        if _normal(window.executable) == expected
        and ctypes.windll.user32.IsWindowVisible(window.hwnd)
        and _window_area(window) >= 640 * 360
        and "invisible" not in window.title.casefold()
    ]
    return max(
        candidates,
        key=lambda window: ("鸣潮" in window.title, _window_area(window)),
        default=None,
    )


def _window_area(window: WindowInfo) -> int:
    left, top, right, bottom = window.rect
    return (right - left) * (bottom - top)


def _wait_for_window(
    finder: Callable[[], WindowInfo | None],
    timeout: float,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> WindowInfo | None:
    deadline = clock() + timeout
    while clock() < deadline:
        if window := finder():
            return window
        sleep(0.5)
    return None


def _save_screenshot(prefix: str) -> Path:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE_DIR / f"{prefix}_{datetime.now():%Y%m%d_%H%M%S_%f}.png"
    pyautogui.screenshot(str(path))
    log.info("client launcher screenshot saved: %s", path)
    return path


def _save_environment_snapshot(prefix: str) -> tuple[Path, Path]:
    """Preserve the desktop plus relevant window/process facts on every failure."""
    screenshot = _save_screenshot(prefix)
    inventory = screenshot.with_suffix(".json")
    windows = []
    for window in _top_level_windows():
        if not (
            _is_under(window.executable, WUWA_INSTALL_DIR)
            or window.executable.name.casefold()
            in {"ok-ww.exe", "pythonw.exe", "uu.exe", "uu_launcher.exe"}
        ):
            continue
        windows.append(
            {
                "hwnd": window.hwnd,
                "pid": window.pid,
                "title": window.title,
                "executable": str(window.executable),
                "rect": window.rect,
            }
        )
    processes = []
    for process in psutil.process_iter(["pid", "name"]):
        try:
            executable = Path(process.exe())
            if not (
                _is_under(executable, WUWA_INSTALL_DIR)
                or executable.name.casefold()
                in {"ok-ww.exe", "pythonw.exe", "uu.exe", "uu_launcher.exe"}
            ):
                continue
            processes.append(
                {
                    "pid": process.pid,
                    "name": process.info["name"],
                    "executable": str(executable),
                }
            )
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    inventory.write_text(
        json.dumps(
            {
                "captured_at": datetime.now().astimezone().isoformat(),
                "windows": windows,
                "processes": processes,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    log.info("client environment inventory saved: %s", inventory)
    return screenshot, inventory


def _require_templates() -> None:
    missing = [
        path
        for path in (
            WUWA_LAUNCHER_READY_TEMPLATE,
            WUWA_LAUNCHER_PRIMARY_ANCHOR_TEMPLATE,
            WUWA_LAUNCHER_SELFUPDATE_CONFIRM_TEMPLATE,
            WUWA_CLIENT_LOGIN_TEMPLATE,
            WUWA_CLIENT_MONTHLY_REWARD_TEMPLATE,
            WUWA_CLIENT_NETWORK_RETRY_TEMPLATE,
            WUWA_CLIENT_REMOTE_CONFIG_RETRY_TEMPLATE,
            WUWA_CLIENT_REWARD_RESULT_TEMPLATE,
            WUWA_CLIENT_UPDATE_RESTART_NOTICE_TEMPLATE,
            WUWA_CLIENT_UPDATE_RESTART_CONFIRM_TEMPLATE,
        )
        if not path.is_file()
    ]
    if missing:
        raise ClientLauncherError(f"missing client launcher templates: {missing}")


def _locate(
    template: Path,
    confidence: float = 0.86,
    *,
    region: tuple[int, int, int, int] | None = None,
) -> tuple[int, int] | None:
    try:
        match = pyautogui.locateOnScreen(
            str(template),
            confidence=confidence,
            region=region,
        )
    except pyautogui.ImageNotFoundException:
        return None
    if match is None:
        return None
    center = pyautogui.center(match)
    return int(center.x), int(center.y)


def _locate_network_retry(
    *,
    region: tuple[int, int, int, int] | None = None,
) -> tuple[int, int] | None:
    """Recognize both network-error button layouts shipped by the client.

    The 2.6.3 client enlarged the remote-configuration failure dialog and
    changed the button border.  Keep the old asset as a fallback so a client
    rollback does not reintroduce the startup deadlock.

    Callers must pass a right-half button-row ``region`` (see
    ``_network_retry_region``) so the match can never resolve to the left
    "退出" button.  ``retry_button_in_right_half`` rejects any result that falls
    on the left side of the window as a final geometric guard.
    """
    current = _locate(
        WUWA_CLIENT_REMOTE_CONFIG_RETRY_TEMPLATE,
        confidence=0.88,
        region=region,
    )
    if current is not None:
        return current
    return _locate(
        WUWA_CLIENT_NETWORK_RETRY_TEMPLATE,
        confidence=0.84,
        region=region,
    )


def _retry_button_in_right_half(
    point: tuple[int, int],
    window: WindowInfo,
) -> bool:
    """Reject any retry candidate on the left half of the client window.

    The dialog always pairs 退出 (left) with 重试 (right).  A retry match whose
    x coordinate lands left of the window midline is, by construction, the exit
    button and must never be clicked.
    """
    left, _top, right, _bottom = window.rect
    midpoint = left + (right - left) / 2
    return point[0] >= midpoint


def startup_network_retry_visible() -> bool:
    """Return whether the exact retry action is visible in the owned game window."""
    game = _game_window()
    if game is None:
        return False
    retry = _locate_network_retry(region=_network_retry_region(game))
    return retry is not None and _retry_button_in_right_half(retry, game)


def click_startup_network_retry() -> bool:
    """Click exactly one verified startup-network retry action.

    This helper deliberately does not cross login or gameplay states.  The caller
    owns the three-click budget and may restart the OK-owned client afterward.

    Returns False (and never clicks) when the only match is on the left "退出"
    button, or when the client window disappears within a short post-click probe
    (the signature symptom of having clicked exit by mistake).
    """
    game = _game_window()
    if game is None:
        return False
    retry = _locate_network_retry(region=_network_retry_region(game))
    if retry is None or not _retry_button_in_right_half(retry, game):
        log.warning(
            "startup network retry rejected: candidate %s is not the right-half "
            "retry button in window %s",
            retry,
            game.rect,
        )
        return False
    _restore_game(game)
    with managed_virtual_mouse() as mouse:
        evidence: list[str] = []
        _click_state(mouse, game, retry, "ok_startup_network_retry", evidence)
    # Post-click liveness probe: if the client vanished within a couple of
    # seconds, the click almost certainly hit 退出 instead of 重试.  Report the
    # miss so the caller stops immediately instead of waiting out the full
    # log-stall timeout.
    time.sleep(2.0)
    if not is_game_window_alive():
        log.error(
            "client window disappeared after startup network retry click at %s; "
            "the click likely hit 退出 instead of 重试",
            retry,
        )
        try:
            _save_screenshot("wuwa_launcher_startup_network_retry_client_gone")
        except Exception:
            log.exception("could not save post-click evidence")
        return False
    return True


def _point_in_window(point: tuple[int, int], window: WindowInfo) -> bool:
    x, y = point
    left, top, right, bottom = window.rect
    return left <= x < right and top <= y < bottom


def _primary_button_center(anchor: tuple[int, int]) -> tuple[int, int]:
    return (
        anchor[0] + PRIMARY_ANCHOR_TO_BUTTON_CENTER[0],
        anchor[1] + PRIMARY_ANCHOR_TO_BUTTON_CENTER[1],
    )


def _button_box(window: WindowInfo) -> tuple[int, int, int, int]:
    left, top, right, bottom = window.rect
    width = right - left
    height = bottom - top
    return (
        round(left + width * 0.758),
        round(top + height * 0.833),
        round(left + width * 0.961),
        round(top + height * 0.912),
    )


def _button_state_hash(window: WindowInfo) -> str:
    screenshot = pyautogui.screenshot()
    crop = screenshot.crop(_button_box(window)).convert("L").resize((32, 8))
    return hashlib.sha256(crop.tobytes()).hexdigest()


def _save_action_crop(
    window: WindowInfo,
    state: str,
    point: tuple[int, int],
) -> Path:
    directory = EVIDENCE_DIR / "client_launcher_actions"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{datetime.now():%Y%m%d_%H%M%S_%f}_{state}.png"
    left, top, right, bottom = window.rect
    x, y = point
    box = (
        max(left, x - 240),
        max(top, y - 70),
        min(right, x + 240),
        min(bottom, y + 70),
    )
    pyautogui.screenshot().crop(box).save(path)
    log.info("client launcher action crop saved: %s", path)
    return path


def _focus(window: WindowInfo) -> None:
    ctypes.windll.user32.ShowWindow(window.hwnd, SW_RESTORE)
    activate_window(window.hwnd, timeout=3.0)


def _restore_game(window: WindowInfo) -> None:
    ctypes.windll.user32.ShowWindow(window.hwnd, SW_RESTORE)
    try:
        activate_window(window.hwnd, timeout=3.0)
    except Exception as exc:
        log.warning("game window activation was not confirmed: %s", exc)


def _search_region(window: WindowInfo) -> tuple[int, int, int, int]:
    left, top, right, bottom = window.rect
    screen_width, screen_height = pyautogui.size()
    left = max(0, left)
    top = max(0, top)
    right = min(screen_width, right)
    bottom = min(screen_height, bottom)
    return left, top, max(1, right - left), max(1, bottom - top)


# The remote-configuration failure dialog shows two buttons side by side:
# "退出" (exit) on the LEFT and "重试" (retry) on the RIGHT.  On 2026-08-13 a
# full-window template match drifted onto the left "退出" button (click landed
# near x=855 on a 2560-wide client, i.e. left of center), closing the client.
# Pin the retry search to the right half of the button row so a match can never
# resolve to the exit button.  Proportions match the 2.6.3 enlarged dialog where
# both button centers sit near y=905 on a 2560x1440 client (~63% height) and the
# retry button center is near x=1607.
def _network_retry_region(window: WindowInfo) -> tuple[int, int, int, int]:
    """Return the right-half button-row ROI that excludes the exit button."""
    left, top, right, bottom = window.rect
    screen_width, screen_height = pyautogui.size()
    width = right - left
    height = bottom - top
    region_left = round(left + width * 0.55)
    region_top = round(top + height * 0.55)
    region_right = min(screen_width, round(left + width * 0.99))
    region_bottom = min(screen_height, round(top + height * 0.75))
    region_left = max(0, min(region_left, screen_width - 1))
    region_top = max(0, min(region_top, screen_height - 1))
    if region_right <= region_left:
        region_right = region_left + 1
    if region_bottom <= region_top:
        region_bottom = region_top + 1
    return region_left, region_top, region_right - region_left, region_bottom - region_top


def is_game_window_alive() -> bool:
    """Return True iff a visible Wuthering Waves client window still exists.

    Used as a post-click liveness probe: if a startup-network retry click
    closes the client (the classic "clicked 退出 instead of 重试" symptom),
    the caller must stop immediately instead of waiting out the full log-stall
    timeout.
    """
    return _game_window() is not None


def _near_white_ratio(image: Image.Image) -> float:
    pixels = image.convert("RGB").get_flattened_data()
    near_white = sum(
        1
        for red, green, blue in pixels
        if red > 215
        and green > 215
        and blue > 215
        and max(red, green, blue) - min(red, green, blue) < 25
    )
    return near_white / max(1, image.width * image.height)


def _world_hud_visible(window: WindowInfo) -> bool:
    """Recognize the distributed HUD without depending on scene backgrounds."""
    left, top, width, height = _search_region(window)
    screenshot = pyautogui.screenshot(region=(left, top, width, height))
    boxes = (
        (0, 0, round(width * 0.254), round(height * 0.292)),
        (round(width * 0.684), 0, width, round(height * 0.556)),
        (round(width * 0.645), round(height * 0.660), width, height),
    )
    ratios = tuple(_near_white_ratio(screenshot.crop(box)) for box in boxes)
    visible = ratios[0] > 0.020 and ratios[1] > 0.012 and ratios[2] > 0.015
    log.debug("client world HUD ratios=%s visible=%s", ratios, visible)
    return visible


def _client_update_restart_target(game: WindowInfo) -> tuple[int, int] | None:
    """Require both the update-complete notice and its confirm action."""
    region = _search_region(game)
    notice = _locate(
        WUWA_CLIENT_UPDATE_RESTART_NOTICE_TEMPLATE,
        confidence=0.88,
        region=region,
    )
    if notice is None or not _point_in_window(notice, game):
        return None
    confirm = _locate(
        WUWA_CLIENT_UPDATE_RESTART_CONFIRM_TEMPLATE,
        confidence=0.88,
        region=region,
    )
    if confirm is None or not _point_in_window(confirm, game):
        return None
    return confirm


def _ensure_game_world(
    mouse: VirtualHidMouse,
    game: WindowInfo,
    *,
    timeout: float,
    evidence: list[str],
    actions: list[str],
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> WindowInfo:
    """Cross the login screen and require a stable in-world HUD before OK-WW."""
    deadline = clock() + timeout
    connect_clicks = 0
    reward_clicks = 0
    reward_result_clicks = 0
    network_retry_clicks = 0
    last_network_retry = 0.0
    last_connect_click = 0.0
    stable_world = 0
    waiting_captured = False
    _restore_game(game)

    while clock() < deadline:
        current = _game_window()
        if current is None:
            stable_world = 0
            sleep(POLL_INTERVAL_SECONDS)
            continue
        game = current
        region = _search_region(game)
        if _world_hud_visible(game):
            stable_world += 1
            if stable_world >= WORLD_STABLE_POLLS:
                evidence.append(str(_save_screenshot("wuwa_client_world_ready")))
                return game
            sleep(POLL_INTERVAL_SECONDS)
            continue
        stable_world = 0

        update_restart = _client_update_restart_target(game)
        if update_restart is not None:
            _restore_game(game)
            _click_state(
                mouse,
                game,
                update_restart,
                "confirm_client_update_restart",
                evidence,
            )
            actions.append("confirm_client_update_restart")
            raise _ClientRestartRequired(game.pid)

        network_retry = _locate_network_retry(region=_network_retry_region(game))
        network_retry_due = clock() - last_network_retry >= READY_RETRY_SECONDS
        if (
            network_retry is not None
            and _retry_button_in_right_half(network_retry, game)
            and network_retry_clicks < MAX_CLICKS_PER_STATE
            and (network_retry_clicks == 0 or network_retry_due)
        ):
            _restore_game(game)
            _click_state(
                mouse,
                game,
                network_retry,
                "retry_game_network",
                evidence,
            )
            actions.append("retry_game_network")
            network_retry_clicks += 1
            last_network_retry = clock()
            waiting_captured = False
            sleep(POLL_INTERVAL_SECONDS)
            continue

        reward_result = _locate(
            WUWA_CLIENT_REWARD_RESULT_TEMPLATE,
            confidence=0.82,
            region=region,
        )
        if (
            reward_result is not None
            and _point_in_window(reward_result, game)
            and reward_result_clicks < MAX_CLICKS_PER_STATE
        ):
            _restore_game(game)
            _click_state(
                mouse,
                game,
                reward_result,
                "close_reward_result",
                evidence,
            )
            actions.append("close_reward_result")
            reward_result_clicks += 1
            waiting_captured = False
            sleep(POLL_INTERVAL_SECONDS)
            continue

        reward = _locate(
            WUWA_CLIENT_MONTHLY_REWARD_TEMPLATE,
            confidence=0.82,
            region=region,
        )
        if (
            reward is not None
            and _point_in_window(reward, game)
            and reward_clicks < MAX_CLICKS_PER_STATE
        ):
            _restore_game(game)
            _click_state(mouse, game, reward, "claim_monthly_reward", evidence)
            actions.append("claim_monthly_reward")
            reward_clicks += 1
            waiting_captured = False
            sleep(POLL_INTERVAL_SECONDS)
            continue

        connect = _locate(WUWA_CLIENT_LOGIN_TEMPLATE, confidence=0.84, region=region)
        retry_due = clock() - last_connect_click >= READY_RETRY_SECONDS
        if (
            connect is not None
            and _point_in_window(connect, game)
            and connect_clicks < MAX_CLICKS_PER_STATE
            and (connect_clicks == 0 or retry_due)
        ):
            _restore_game(game)
            _click_state(mouse, game, connect, "connect_game", evidence)
            actions.append("connect_game")
            connect_clicks += 1
            last_connect_click = clock()
            waiting_captured = False
        elif not waiting_captured:
            evidence.append(str(_save_screenshot("wuwa_client_world_waiting")))
            actions.append("world_waiting")
            waiting_captured = True
        sleep(POLL_INTERVAL_SECONDS)

    raise ClientLauncherError(
        f"game did not reach a stable in-world HUD within {timeout:.0f}s"
    )


def _click_state(
    mouse: VirtualHidMouse,
    window: WindowInfo,
    point: tuple[int, int],
    state: str,
    evidence: list[str],
) -> None:
    if not _point_in_window(point, window):
        raise ClientLauncherError(
            f"launcher action {point} was outside verified window {window.rect}"
        )
    evidence.append(str(_save_screenshot(f"wuwa_launcher_{state}_before_click")))
    evidence.append(str(_save_action_crop(window, state, point)))
    mouse.click_at(*point)
    log.info("client launcher action=%s point=%s", state, point)


def _wait_for_restarted_game(
    previous_pid: int,
    *,
    timeout: float = CLIENT_RESTART_TIMEOUT_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> WindowInfo | None:
    deadline = clock() + timeout
    while clock() < deadline:
        current = _game_window()
        if current is not None and current.pid != previous_pid:
            return current
        sleep(POLL_INTERVAL_SECONDS)
    return None


def _launch_launcher() -> None:
    if not WUWA_LAUNCHER_EXE.is_file():
        raise ClientLauncherError(f"launcher not found: {WUWA_LAUNCHER_EXE}")
    subprocess.Popen(
        [str(WUWA_LAUNCHER_EXE)],
        cwd=WUWA_INSTALL_DIR,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _official_launcher_processes() -> list[psutil.Process]:
    processes: list[psutil.Process] = []
    for process in psutil.process_iter(["name"]):
        try:
            if (process.info["name"] or "").casefold() not in {
                "launcher.exe",
                "launcher_main.exe",
                "launcher_updater.exe",
            }:
                continue
            if _is_under(Path(process.exe()), WUWA_INSTALL_DIR):
                processes.append(process)
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    return processes


def is_client_launcher_running() -> bool:
    return bool(_official_launcher_processes())


def stop_client_launchers() -> int:
    """Stop only official launcher processes under the verified install root."""
    stopped = 0
    for process in _official_launcher_processes():
        try:
            executable = Path(process.exe())
            process.terminate()
            try:
                process.wait(timeout=10)
            except psutil.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            stopped += 1
            log.info("stopped official Wuwa launcher %s pid=%s", executable, process.pid)
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.TimeoutExpired):
            continue
    return stopped


def _ensure_client_ready(
    mouse: VirtualHidMouse,
    *,
    update_timeout: float = CLIENT_UPDATE_TIMEOUT_SECONDS,
    game_timeout: float = GAME_WINDOW_TIMEOUT_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> ClientPreparationResult:
    """Finish launcher updates and hand a restored game window to OK-WW."""
    require_admin()
    _require_templates()
    evidence: list[str] = []
    actions: list[str] = []
    updated = False

    if game := _game_window():
        try:
            game = _ensure_game_world(
                mouse,
                game,
                timeout=game_timeout,
                evidence=evidence,
                actions=actions,
                sleep=sleep,
                clock=clock,
            )
        except _ClientRestartRequired as restart:
            updated = True
            game = _wait_for_restarted_game(
                restart.previous_pid,
                sleep=sleep,
                clock=clock,
            )
        if game is not None:
            if updated:
                game = _ensure_game_world(
                    mouse,
                    game,
                    timeout=game_timeout,
                    evidence=evidence,
                    actions=actions,
                    sleep=sleep,
                    clock=clock,
                )
            evidence.append(str(_save_screenshot("wuwa_client_reused")))
            stop_client_launchers()
            return ClientPreparationResult(
                updated,
                tuple(actions),
                tuple(evidence),
                game.pid,
            )

    launcher = _launcher_window()
    if launcher is None:
        _launch_launcher()
        launcher = _wait_for_window(
            _launcher_window,
            LAUNCHER_WINDOW_TIMEOUT_SECONDS,
            sleep=sleep,
            clock=clock,
        )
    if launcher is None:
        evidence.append(str(_save_screenshot("wuwa_launcher_not_found")))
        raise ClientLauncherError(
            f"official launcher window did not appear; evidence={evidence[-1]}"
        )

    _focus(launcher)
    evidence.append(str(_save_screenshot("wuwa_launcher_open")))
    deadline = clock() + update_timeout
    last_state_hash = ""
    clicks_for_hash: dict[str, int] = {}
    last_click_at = 0.0
    waiting_captured = False
    game_deadline: float | None = None

    while clock() < deadline:
        if game := _game_window():
            try:
                game = _ensure_game_world(
                    mouse,
                    game,
                    timeout=game_timeout,
                    evidence=evidence,
                    actions=actions,
                    sleep=sleep,
                    clock=clock,
                )
            except _ClientRestartRequired as restart:
                updated = True
                restarted = _wait_for_restarted_game(
                    restart.previous_pid,
                    sleep=sleep,
                    clock=clock,
                )
                if restarted is None and _launcher_window() is None:
                    _launch_launcher()
                    _wait_for_window(
                        _launcher_window,
                        LAUNCHER_WINDOW_TIMEOUT_SECONDS,
                        sleep=sleep,
                        clock=clock,
                    )
                waiting_captured = False
                game_deadline = None
                continue
            evidence.append(str(_save_screenshot("wuwa_client_window_ready")))
            stop_client_launchers()
            return ClientPreparationResult(
                updated,
                tuple(actions),
                tuple(evidence),
                game.pid,
            )

        current = _launcher_window()
        if current is None:
            sleep(POLL_INTERVAL_SECONDS)
            continue
        launcher = current

        # Version-day ordering: a modal self-update reminder shadows the main
        # page, so it must be dismissed before any main-page template can be
        # trusted (the anchor offset would click behind the modal otherwise).
        reminder = _launcher_update_reminder_window()
        if reminder is not None:
            state_hash = f"selfupdate_reminder_{reminder.hwnd}"
            count = clicks_for_hash.get(state_hash, 0)
            if count >= MAX_CLICKS_PER_STATE:
                if not waiting_captured:
                    evidence.append(
                        str(
                            _save_screenshot(
                                "wuwa_launcher_selfupdate_reminder_stuck"
                            )
                        )
                    )
                    actions.append("selfupdate_reminder_stuck")
                    waiting_captured = True
                sleep(POLL_INTERVAL_SECONDS)
                continue
            confirm = _locate(
                WUWA_LAUNCHER_SELFUPDATE_CONFIRM_TEMPLATE,
                confidence=0.86,
                region=_search_region(reminder),
            )
            if confirm is None:
                if not waiting_captured:
                    evidence.append(
                        str(_save_screenshot("wuwa_launcher_selfupdate_waiting"))
                    )
                    actions.append("selfupdate_waiting")
                    waiting_captured = True
                sleep(POLL_INTERVAL_SECONDS)
                continue
            if count == 0 or clock() - last_click_at >= READY_RETRY_SECONDS:
                _focus(reminder)
                point = (
                    confirm[0] + SELFUPDATE_CONFIRM_CLICK_OFFSET[0],
                    confirm[1] + SELFUPDATE_CONFIRM_CLICK_OFFSET[1],
                )
                _click_state(mouse, reminder, point, "selfupdate_confirm", evidence)
                actions.append("selfupdate_confirm")
                updated = True
                clicks_for_hash[state_hash] = count + 1
                last_click_at = clock()
                waiting_captured = False
            sleep(POLL_INTERVAL_SECONDS)
            continue

        ready = _locate(WUWA_LAUNCHER_READY_TEMPLATE, confidence=0.88)
        if ready is not None and _point_in_window(ready, launcher):
            if game_deadline is None:
                game_deadline = min(deadline, clock() + game_timeout)
            elif clock() >= game_deadline:
                evidence.append(str(_save_screenshot("wuwa_client_start_timeout")))
                raise ClientLauncherError(
                    "launcher reached the enter-game state, but no game window appeared "
                    f"within {game_timeout:.0f}s; evidence={evidence[-1]}"
                )
            state_hash = "ready"
            count = clicks_for_hash.get(state_hash, 0)
            if count < MAX_CLICKS_PER_STATE and (
                count == 0 or clock() - last_click_at >= READY_RETRY_SECONDS
            ):
                _focus(launcher)
                _click_state(mouse, launcher, ready, "enter_game", evidence)
                actions.append("enter_game")
                clicks_for_hash[state_hash] = count + 1
                last_click_at = clock()
            sleep(POLL_INTERVAL_SECONDS)
            continue

        anchor = _locate(
            WUWA_LAUNCHER_PRIMARY_ANCHOR_TEMPLATE,
            confidence=0.84,
        )
        if anchor is not None and _point_in_window(anchor, launcher):
            updated = True
            state_hash = _button_state_hash(launcher)
            count = clicks_for_hash.get(state_hash, 0)
            changed = state_hash != last_state_hash
            retry_due = count < MAX_CLICKS_PER_STATE and (
                count == 0 or clock() - last_click_at >= READY_RETRY_SECONDS
            )
            if count < MAX_CLICKS_PER_STATE and (changed or retry_due):
                _focus(launcher)
                target = _primary_button_center(anchor)
                _click_state(mouse, launcher, target, "update_action", evidence)
                actions.append("update_action")
                clicks_for_hash[state_hash] = count + 1
                last_state_hash = state_hash
                last_click_at = clock()
                waiting_captured = False
            sleep(POLL_INTERVAL_SECONDS)
            continue

        if not waiting_captured:
            evidence.append(str(_save_screenshot("wuwa_launcher_update_waiting")))
            actions.append("update_waiting")
            waiting_captured = True
        sleep(POLL_INTERVAL_SECONDS)

    evidence.append(str(_save_screenshot("wuwa_launcher_update_timeout")))
    raise ClientLauncherError(
        "official launcher did not reach a game window within "
        f"{update_timeout:.0f}s; actions={actions}; evidence={evidence[-1]}"
    )


def _window_state_hash(window: WindowInfo) -> str:
    """Hash the whole window at fine resolution to track download progress."""
    left, top, width, height = _search_region(window)
    screenshot = pyautogui.screenshot(region=(left, top, width, height))
    crop = screenshot.convert("L").resize((96, 48))
    return hashlib.sha256(crop.tobytes()).hexdigest()


def _click_acknowledged(
    window: WindowInfo,
    pre_click_hash: str,
    *,
    timeout: float,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Return True when the window visibly reacts to a click within timeout.

    User rule (2026-08-21): a click with no visible acknowledgment inside
    15 seconds is a miss and must be retried immediately instead of burning
    the retry interval.
    """
    deadline = clock() + timeout
    while True:
        if _window_state_hash(window) != pre_click_hash:
            return True
        if clock() >= deadline:
            return False
        sleep(POLL_INTERVAL_SECONDS)


def _launcher_io_bytes() -> int:
    """Total disk IO of official launcher processes; real downloads move it."""
    total = 0
    for process in psutil.process_iter(["name"]):
        try:
            if (process.info["name"] or "").casefold() not in {
                "launcher_main.exe",
                "launcher_updater.exe",
            }:
                continue
            counters = process.io_counters()
            total += counters.read_bytes + counters.write_bytes
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    return total


def ensure_client_updated(
    mouse: VirtualHidMouse,
    *,
    detect_timeout: float = VERSION_UPDATE_DETECT_TIMEOUT_SECONDS,
    update_timeout: float = CLIENT_UPDATE_TIMEOUT_SECONDS,
    stall_timeout: float = VERSION_UPDATE_STALL_SECONDS,
    max_restarts: int = VERSION_UPDATE_MAX_RESTARTS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> ClientUpdateOutcome:
    """Drive launcher-side updates without ever entering the game.

    Version-day contract (agreed 2026-08-20): the nightly chain must be able
    to download a new game version while login servers are still closed, so
    this gate stops at the launcher's enter-game state and closes the
    launcher.  The game launch itself always belongs to OK-WW afterwards.
    A frozen update UI is recovered by relaunching the launcher (the CDN
    download resumes) at most ``max_restarts`` times before failing.
    """
    require_admin()
    _require_templates()
    evidence: list[str] = []
    actions: list[str] = []
    update_performed = False
    restarts = 0
    update_budget_end: float | None = None

    if _game_window() is not None:
        # A live client cannot be outdated; the nightly cold start rebuilds
        # the entry boundary anyway.
        log.info("client window already present; launcher update gate skipped")
        return ClientUpdateOutcome(False, (), ())

    while True:
        launcher = _launcher_window()
        if launcher is None:
            _launch_launcher()
            launcher = _wait_for_window(
                _launcher_window,
                LAUNCHER_WINDOW_TIMEOUT_SECONDS,
                sleep=sleep,
                clock=clock,
            )
        if launcher is None:
            evidence.append(str(_save_screenshot("wuwa_launcher_not_found")))
            raise ClientLauncherError(
                f"official launcher window did not appear; evidence={evidence[-1]}"
            )

        _focus(launcher)
        evidence.append(str(_save_screenshot("wuwa_launcher_open")))
        phase_deadline = clock() + detect_timeout
        clicks_for_hash: dict[str, int] = {}
        last_click_at = 0.0
        last_state_hash = ""
        stall_io_last = -1
        stall_since = clock()
        waiting_captured = False
        click_not_acked = False

        while True:
            if _game_window() is not None:
                stop_client_launchers()
                return ClientUpdateOutcome(
                    update_performed, tuple(actions), tuple(evidence)
                )
            current = _launcher_window()
            if current is None:
                if update_budget_end is not None and clock() >= update_budget_end:
                    evidence.append(
                        str(_save_screenshot("wuwa_launcher_update_timeout"))
                    )
                    raise ClientLauncherError(
                        "launcher update did not finish within "
                        f"{update_timeout:.0f}s; actions={actions}; "
                        f"evidence={evidence[-1]}"
                    )
                sleep(POLL_INTERVAL_SECONDS)
                continue
            launcher = current

            if update_budget_end is None and clock() >= phase_deadline:
                evidence.append(str(_save_screenshot("wuwa_launcher_state_unknown")))
                raise ClientLauncherError(
                    "launcher showed neither the enter-game state nor an update "
                    f"action within {detect_timeout:.0f}s; evidence={evidence[-1]}"
                )
            if update_budget_end is not None and clock() >= update_budget_end:
                evidence.append(str(_save_screenshot("wuwa_launcher_update_timeout")))
                raise ClientLauncherError(
                    "launcher update did not finish within "
                    f"{update_timeout:.0f}s; actions={actions}; "
                    f"evidence={evidence[-1]}"
                )

            ready = _locate(WUWA_LAUNCHER_READY_TEMPLATE, confidence=0.88)
            if ready is not None and _point_in_window(ready, launcher):
                stop_client_launchers()
                return ClientUpdateOutcome(
                    update_performed, tuple(actions), tuple(evidence)
                )

            reminder = _launcher_update_reminder_window()
            if reminder is not None:
                update_performed = True
                if update_budget_end is None:
                    update_budget_end = clock() + update_timeout
                state_hash = f"selfupdate_reminder_{reminder.hwnd}"
                count = clicks_for_hash.get(state_hash, 0)
                confirm = _locate(
                    WUWA_LAUNCHER_SELFUPDATE_CONFIRM_TEMPLATE,
                    confidence=0.86,
                    region=_search_region(reminder),
                )
                if (
                    confirm is not None
                    and count < SELFUPDATE_MAX_CLICK_ATTEMPTS
                    and (
                        count == 0
                        or clock() - last_click_at >= READY_RETRY_SECONDS
                        or click_not_acked
                    )
                ):
                    _focus(reminder)
                    point = (
                        confirm[0] + SELFUPDATE_CONFIRM_CLICK_OFFSET[0],
                        confirm[1] + SELFUPDATE_CONFIRM_CLICK_OFFSET[1],
                    )
                    pre_click_hash = _window_state_hash(reminder)
                    _click_state(mouse, reminder, point, "selfupdate_confirm", evidence)
                    actions.append("selfupdate_confirm")
                    click_not_acked = not _click_acknowledged(
                        reminder,
                        pre_click_hash,
                        timeout=SELFUPDATE_ACK_TIMEOUT_SECONDS,
                        sleep=sleep,
                        clock=clock,
                    )
                    if click_not_acked:
                        log.warning(
                            "selfupdate confirm click at %s showed no visible "
                            "acknowledgment within %.0fs; retrying at once",
                            point,
                            SELFUPDATE_ACK_TIMEOUT_SECONDS,
                        )
                    else:
                        clicks_for_hash[state_hash] = count + 1
                        last_click_at = clock()
                    waiting_captured = False
                    stall_since = clock()
            else:
                anchor = _locate(
                    WUWA_LAUNCHER_PRIMARY_ANCHOR_TEMPLATE,
                    confidence=0.84,
                )
                if anchor is not None and _point_in_window(anchor, launcher):
                    update_performed = True
                    if update_budget_end is None:
                        update_budget_end = clock() + update_timeout
                    state_hash = _button_state_hash(launcher)
                    count = clicks_for_hash.get(state_hash, 0)
                    changed = state_hash != last_state_hash
                    if count < MAX_CLICKS_PER_STATE and (
                        changed
                        or count == 0
                        or clock() - last_click_at >= READY_RETRY_SECONDS
                    ):
                        _focus(launcher)
                        target = _primary_button_center(anchor)
                        _click_state(mouse, launcher, target, "update_action", evidence)
                        actions.append("update_action")
                        clicks_for_hash[state_hash] = count + 1
                        last_state_hash = state_hash
                        last_click_at = clock()
                        waiting_captured = False
                        stall_since = clock()
                else:
                    if not waiting_captured:
                        evidence.append(
                            str(_save_screenshot("wuwa_launcher_update_waiting"))
                        )
                        actions.append("update_waiting")
                        waiting_captured = True

            if update_budget_end is not None:
                # 0821 live data: a silently-dead download keeps an animated
                # loader rendering while the launcher's own disk IO is frozen
                # at 99%.  Watch ONLY the launcher processes' IO, quantized
                # to 1MB buckets: system-wide network counters are moved by
                # unrelated background traffic every second and would reset
                # the stall clock forever.
                io_bucket = _launcher_io_bytes() // (1024 * 1024)
                if io_bucket != stall_io_last:
                    stall_io_last = io_bucket
                    stall_since = clock()
                elif clock() - stall_since >= stall_timeout:
                    evidence.append(
                        str(_save_screenshot("wuwa_launcher_update_stalled"))
                    )
                    if restarts >= max_restarts:
                        raise ClientLauncherError(
                            "launcher update stalled for "
                            f"{stall_timeout:.0f}s with frozen network and "
                            f"launcher IO; restart ladder exhausted "
                            f"({restarts}/{max_restarts}); "
                            f"actions={actions}; evidence={evidence[-1]}"
                        )
                    restarts += 1
                    actions.append(f"update_stall_restart_{restarts}")
                    log.warning(
                        "launcher update stalled; restart %d/%d to resume",
                        restarts,
                        max_restarts,
                    )
                    stop_client_launchers()
                    break

            sleep(POLL_INTERVAL_SECONDS)


def ensure_client_ready(
    mouse: VirtualHidMouse,
    *,
    update_timeout: float = CLIENT_UPDATE_TIMEOUT_SECONDS,
    game_timeout: float = GAME_WINDOW_TIMEOUT_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> ClientPreparationResult:
    """Prepare the client and always preserve the full environment on failure."""
    try:
        return _ensure_client_ready(
            mouse,
            update_timeout=update_timeout,
            game_timeout=game_timeout,
            sleep=sleep,
            clock=clock,
        )
    except Exception as exc:
        try:
            screenshot, inventory = _save_environment_snapshot(
                "wuwa_client_prepare_failed"
            )
            evidence = f"screenshot={screenshot}; inventory={inventory}"
        except Exception as capture_exc:  # pragma: no cover - last-resort logging
            evidence = f"environment capture failed: {capture_exc}"
        log.exception("client preparation failed; %s", evidence)
        if isinstance(exc, ClientLauncherError):
            raise ClientLauncherError(f"{exc}; {evidence}") from exc
        raise


__all__ = [
    "ClientLauncherError",
    "ClientPreparationResult",
    "ClientUpdateOutcome",
    "WindowInfo",
    "ensure_client_ready",
    "ensure_client_updated",
    "is_client_launcher_running",
    "stop_client_launchers",
]
