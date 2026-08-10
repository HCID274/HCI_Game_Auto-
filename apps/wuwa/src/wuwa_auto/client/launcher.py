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

from wuwa_auto.input.viiper import VirtualHidMouse
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
)
from wuwa_auto.uu.desktop import require_admin

log = logging.getLogger(__name__)

LAUNCHER_WINDOW_TIMEOUT_SECONDS = 90.0
GAME_WINDOW_TIMEOUT_SECONDS = 300.0
CLIENT_UPDATE_TIMEOUT_SECONDS = 9000.0
POLL_INTERVAL_SECONDS = 5.0
READY_RETRY_SECONDS = 60.0
CLIENT_RESTART_TIMEOUT_SECONDS = 180.0
WORLD_STABLE_POLLS = 2
MAX_CLICKS_PER_STATE = 2
PRIMARY_ANCHOR_TO_BUTTON_CENTER = (117, 0)

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

        network_retry = _locate_network_retry(region=region)
        network_retry_due = clock() - last_network_retry >= READY_RETRY_SECONDS
        if (
            network_retry is not None
            and _point_in_window(network_retry, game)
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
    "WindowInfo",
    "ensure_client_ready",
    "is_client_launcher_running",
    "stop_client_launchers",
]
