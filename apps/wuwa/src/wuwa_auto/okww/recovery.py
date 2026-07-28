"""Narrow, screenshot-gated recoveries for known upstream OK-WW UI bugs."""

import ctypes
import json
import logging
import subprocess
import time
from dataclasses import dataclass
from collections.abc import Iterator
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path

import psutil

from wuwa_auto.input.viiper import managed_virtual_mouse
from wuwa_auto.settings import (
    OK_PYTHON_EXE,
    OK_PYTHONW_EXE,
    OK_WORKING_DIR,
    TEMPLATES_DIR,
)
from wuwa_auto.uu import desktop

log = logging.getLogger(__name__)

RECOVERY_WORKER = Path(__file__).with_name("recovery_worker.py")
RECOVERY_TIMEOUT = 360.0

WEEKLY_GARDEN_TAB = TEMPLATES_DIR / "ok_weekly_garden_tab.png"
DAILY_CLAIM = TEMPLATES_DIR / "ok_daily_claim.png"
LOGIN_CONNECT = TEMPLATES_DIR / "ok_login_connect.png"
# The template deliberately includes the right edge of the active tab as
# context.  Its geometric centre is therefore near the rounded left cap of
# "Weekly Journey", not in the label's reliable hit area.
WEEKLY_GARDEN_TAB_CLICK_OFFSET = (110, 0)
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_ACTIVATE = 0x0006
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WA_ACTIVE = 1
MK_LBUTTON = 0x0001
SW_RESTORE = 9
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("mi", _MouseInput)]


class _Input(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", ctypes.c_ulong), ("data", _InputUnion)]


@dataclass(frozen=True)
class FarmEchoRecoveryResult:
    success: bool
    reason: str
    evidence_path: str | None
    worker_result_path: str


@contextmanager
def _suspend_ok_workers() -> Iterator[None]:
    expected = OK_PYTHONW_EXE.resolve()
    suspended: list[psutil.Process] = []
    try:
        for process in psutil.process_iter(["name"]):
            try:
                if (process.info["name"] or "").casefold() != "pythonw.exe":
                    continue
                if process.exe() and Path(process.exe()).resolve() == expected:
                    process.suspend()
                    suspended.append(process)
                    log.info("suspended OK-WW worker pid=%s", process.pid)
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
        if suspended:
            time.sleep(0.3)
        else:
            log.info("no running OK-WW worker; recovering the preserved game")
        yield
    finally:
        for process in suspended:
            try:
                process.resume()
                log.info("resumed OK-WW worker pid=%s", process.pid)
            except psutil.NoSuchProcess:
                pass


def _game_windows() -> list[int]:
    matches: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(
        ctypes.c_bool,
        ctypes.c_void_p,
        ctypes.c_void_p,
    )

    @callback_type
    def collect(hwnd: int, _lparam: int) -> bool:
        if not ctypes.windll.user32.IsWindowVisible(hwnd):
            return True
        process_id = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(
            hwnd,
            ctypes.byref(process_id),
        )
        try:
            name = psutil.Process(process_id.value).name().casefold()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            return True
        if name == "client-win64-shipping.exe":
            matches.append(hwnd)
        return True

    ctypes.windll.user32.EnumWindows(collect, 0)
    return matches


def _focus_game_window(timeout: float = 8) -> int:
    windows = _game_windows()
    if not windows:
        raise RuntimeError("Wuthering Waves top-level window not found")
    hwnd = windows[0]
    user32 = ctypes.windll.user32
    user32.ShowWindow(hwnd, SW_RESTORE)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # The temporary Alt state is the standard Win32 foreground-lock
        # workaround; the process is elevated and the target was PID-checked.
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.SetForegroundWindow(hwnd)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.3)
        if user32.GetForegroundWindow() == hwnd:
            return hwnd
    raise RuntimeError("Wuthering Waves window did not become foreground")


def _validate_game_window(hwnd: int) -> None:
    if not ctypes.windll.user32.IsWindow(hwnd):
        raise RuntimeError(f"Wuthering Waves window is no longer valid: {hwnd}")
    process_id = ctypes.c_ulong()
    ctypes.windll.user32.GetWindowThreadProcessId(
        hwnd,
        ctypes.byref(process_id),
    )
    try:
        name = psutil.Process(process_id.value).name().casefold()
    except (psutil.AccessDenied, psutil.NoSuchProcess) as exc:
        raise RuntimeError("cannot validate foreground game window") from exc
    if name != "client-win64-shipping.exe":
        raise RuntimeError(
            f"target is not Wuthering Waves: hwnd={hwnd}, process={name}"
        )


def run_farm_echo_death_recovery(
    run_dir: Path,
    *,
    attempt: int,
) -> FarmEchoRecoveryResult:
    """Keep the game alive and ask OK-WW to exit the realm and heal."""
    desktop.require_admin()
    desktop.require_supported_display()
    hwnd = _focus_game_window()
    _validate_game_window(hwnd)
    before = desktop.save_step_screenshot(
        f"ok_farm_echo_recovery_{attempt}_before"
    )
    result_path = run_dir / f"farm-echo-recovery-{attempt}.json"
    command = [
        str(OK_PYTHON_EXE),
        str(RECOVERY_WORKER),
        str(OK_WORKING_DIR),
        str(result_path),
    ]
    log.info("starting FarmEcho death recovery attempt=%s", attempt)
    try:
        completed = subprocess.run(
            command,
            cwd=OK_WORKING_DIR,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=RECOVERY_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except subprocess.TimeoutExpired:
        evidence = desktop.save_step_screenshot(
            f"ok_farm_echo_recovery_{attempt}_timeout"
        )
        return FarmEchoRecoveryResult(
            success=False,
            reason=f"recovery worker timed out after {RECOVERY_TIMEOUT:.0f}s",
            evidence_path=str(evidence),
            worker_result_path=str(result_path),
        )

    payload: dict[str, object] = {}
    if result_path.is_file():
        try:
            loaded = json.loads(result_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        except (OSError, json.JSONDecodeError):
            log.exception("invalid FarmEcho recovery result: %s", result_path)
    success = completed.returncode == 0 and payload.get("success") is True
    if success:
        evidence = desktop.save_step_screenshot(
            f"ok_farm_echo_recovery_{attempt}_completed"
        )
        reason = str(payload.get("reason") or "recovery completed")
    else:
        evidence = desktop.save_step_screenshot(
            f"ok_farm_echo_recovery_{attempt}_failed"
        )
        reason = str(
            payload.get("reason")
            or completed.stdout.strip()
            or f"recovery worker exited with code {completed.returncode}"
        )
    log.info(
        "FarmEcho death recovery attempt=%s success=%s reason=%s before=%s after=%s",
        attempt,
        success,
        reason,
        before,
        evidence,
    )
    return FarmEchoRecoveryResult(
        success=success,
        reason=reason,
        evidence_path=str(evidence),
        worker_result_path=str(result_path),
    )


def _post_message_click(
    hwnd: int,
    position: tuple[int, int],
    step_name: str,
) -> None:
    _validate_game_window(hwnd)
    x, y = (int(value) for value in position)
    desktop.save_step_screenshot(f"ok_{step_name}_before_click")
    lparam = (y << 16) | (x & 0xFFFF)
    user32 = ctypes.windll.user32
    messages = (
        (WM_ACTIVATE, WA_ACTIVE, 0),
        (WM_MOUSEMOVE, 0, lparam),
        (WM_LBUTTONDOWN, MK_LBUTTON, lparam),
    )
    for message, wparam, message_lparam in messages:
        if not user32.PostMessageW(hwnd, message, wparam, message_lparam):
            raise ctypes.WinError()
    time.sleep(0.08)
    if not user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lparam):
        raise ctypes.WinError()
    log.info("posted game click %s at %s hwnd=%s", step_name, position, hwnd)


def _post_key(hwnd: int, virtual_key: int, step_name: str) -> None:
    _validate_game_window(hwnd)
    scan_code = ctypes.windll.user32.MapVirtualKeyW(virtual_key, 0)
    down_lparam = (scan_code << 16) | 1
    up_lparam = down_lparam | (1 << 30) | (1 << 31)
    user32 = ctypes.windll.user32
    user32.PostMessageW(hwnd, WM_ACTIVATE, WA_ACTIVE, 0)
    if not user32.PostMessageW(hwnd, WM_KEYDOWN, virtual_key, down_lparam):
        raise ctypes.WinError()
    time.sleep(0.08)
    if not user32.PostMessageW(hwnd, WM_KEYUP, virtual_key, up_lparam):
        raise ctypes.WinError()
    log.info("posted game key %s vk=%s hwnd=%s", step_name, virtual_key, hwnd)


def _send_input_click(position: tuple[int, int], step_name: str) -> None:
    """Send a foreground hardware-style click using the Win32 input queue."""
    x, y = (int(value) for value in position)
    width = ctypes.windll.user32.GetSystemMetrics(0)
    height = ctypes.windll.user32.GetSystemMetrics(1)
    normalized_x = round(x * 65535 / max(width - 1, 1))
    normalized_y = round(y * 65535 / max(height - 1, 1))
    common = MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
    inputs = (_Input * 3)(
        _Input(
            type=0,
            mi=_MouseInput(
                normalized_x,
                normalized_y,
                0,
                common | MOUSEEVENTF_MOVE,
                0,
                0,
            ),
        ),
        _Input(
            type=0,
            mi=_MouseInput(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, 0),
        ),
        _Input(
            type=0,
            mi=_MouseInput(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, 0),
        ),
    )
    sent = ctypes.windll.user32.SendInput(
        len(inputs),
        ctypes.byref(inputs),
        ctypes.sizeof(_Input),
    )
    if sent != len(inputs):
        raise ctypes.WinError()
    log.info("SendInput-clicked %s at %s", step_name, position)


def _click_until_template_disappears(
    hwnd: int,
    template: Path,
    position: tuple[int, int],
    step_name: str,
) -> None:
    """Click a verified target and require a visible state transition."""
    _focus_game_window()
    desktop.save_step_screenshot(f"ok_{step_name}_before_virtual_hid_click")
    with managed_virtual_mouse() as mouse:
        mouse.click_at(*position)
    time.sleep(2)
    if desktop.try_locate_image(template, timeout=1, confidence=0.90) is None:
        log.info("game accepted virtual HID click for %s", step_name)
        return

    _post_message_click(hwnd, position, step_name)
    time.sleep(2)
    if desktop.try_locate_image(template, timeout=1, confidence=0.90) is None:
        return

    _focus_game_window()
    desktop.save_step_screenshot(f"ok_{step_name}_before_foreground_click")
    desktop.pyautogui.moveTo(*position, duration=0.35)
    desktop.pyautogui.mouseDown()
    time.sleep(0.12)
    desktop.pyautogui.mouseUp()
    log.info(
        "foreground-clicked %s at %s cursor=%s foreground=%s",
        step_name,
        position,
        desktop.pyautogui.position(),
        ctypes.windll.user32.GetForegroundWindow(),
    )
    time.sleep(2)
    if desktop.try_locate_image(template, timeout=1, confidence=0.90) is None:
        return

    _focus_game_window()
    desktop.save_step_screenshot(f"ok_{step_name}_before_send_input_click")
    _send_input_click(position, step_name)
    time.sleep(2)
    if desktop.try_locate_image(template, timeout=1, confidence=0.90) is None:
        return

    # Unreal renders/captures in physical pixels, while some Windows builds
    # deliver client mouse messages in 96-DPI logical coordinates.  This is
    # most visible on controls far from the origin at 125% display scaling.
    dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
    logical_scale = 96 / dpi if dpi else 1.0
    logical_position = (
        round(int(position[0]) * logical_scale),
        round(int(position[1]) * logical_scale),
    )
    if logical_position != tuple(int(value) for value in position):
        log.info(
            "retrying %s in logical coordinate space dpi=%s physical=%s logical=%s",
            step_name,
            dpi,
            position,
            logical_position,
        )
        _post_message_click(hwnd, logical_position, f"{step_name}_logical")
        time.sleep(2)
        if desktop.try_locate_image(template, timeout=1, confidence=0.90) is None:
            return

    raise RuntimeError(f"{step_name} rejected every supported click method")


def recover_garden_entry() -> None:
    """Recover when GardenTask clicked past the weekly-tab hit target."""
    desktop.require_admin()
    desktop.require_supported_display()
    with _suspend_ok_workers():
        hwnd = _focus_game_window()
        claim = desktop.try_locate_image(
            DAILY_CLAIM,
            timeout=1,
            confidence=0.90,
        )
        if claim is not None:
            log.info("pending daily reward detected at %s", claim)
            try:
                _click_until_template_disappears(
                    hwnd,
                    DAILY_CLAIM,
                    claim,
                    "daily_claim",
                )
            except RuntimeError:
                # A claim is useful diagnostic evidence but is not a
                # prerequisite for selecting the weekly tab.
                log.warning("daily reward click was rejected; continuing to weekly tab")
            time.sleep(1)

        tab_match = desktop.wait_for_image(
            WEEKLY_GARDEN_TAB,
            step_name="locate_weekly_garden_tab",
            timeout=8,
            confidence=0.90,
        )
        tab = (
            tab_match[0] + WEEKLY_GARDEN_TAB_CLICK_OFFSET[0],
            tab_match[1] + WEEKLY_GARDEN_TAB_CLICK_OFFSET[1],
        )
        log.info("weekly garden match=%s click_target=%s", tab_match, tab)
        try:
            _click_until_template_disappears(
                hwnd,
                WEEKLY_GARDEN_TAB,
                tab,
                "weekly_garden_tab",
            )
        except RuntimeError:
            for virtual_key, key_name in ((0x27, "right"), (0x45, "e")):
                desktop.save_step_screenshot(
                    f"ok_weekly_garden_tab_before_{key_name}_key"
                )
                _post_key(hwnd, virtual_key, f"weekly_garden_{key_name}")
                time.sleep(2)
                if desktop.try_locate_image(
                    WEEKLY_GARDEN_TAB,
                    timeout=1,
                    confidence=0.90,
                ) is None:
                    log.info("weekly tab selected using %s key", key_name)
                    break
            else:
                raise RuntimeError("weekly garden tab rejected mouse and keyboard")
        desktop.save_step_screenshot("ok_weekly_garden_tab_selected")
    log.info("GardenTask weekly tab recovery completed")


def probe_login_connect() -> None:
    """Prove whether an independently controlled click enters the game."""
    desktop.require_admin()
    desktop.require_supported_display()
    hwnd = _focus_game_window()
    target = desktop.wait_for_image(
        LOGIN_CONNECT,
        step_name="locate_login_connect",
        timeout=8,
        confidence=0.90,
    )
    _click_until_template_disappears(
        hwnd,
        LOGIN_CONNECT,
        target,
        "login_connect",
    )
    desktop.save_step_screenshot("ok_login_connect_accepted")
    log.info("game accepted externally controlled login click")


def reset_game_mouse_capture() -> None:
    """Force Unreal to rebuild mouse capture by toggling display mode."""
    desktop.require_admin()
    hwnd = _focus_game_window()
    desktop.save_step_screenshot("ok_mouse_capture_before_alt_enter")
    desktop.pyautogui.keyDown("alt")
    time.sleep(0.1)
    desktop.pyautogui.press("enter")
    desktop.pyautogui.keyUp("alt")
    time.sleep(3)
    _validate_game_window(hwnd)
    desktop.save_step_screenshot("ok_mouse_capture_after_alt_enter")
    log.info("toggled Wuthering Waves display mode to rebuild mouse capture")


def probe_windowed_claim() -> None:
    """Test foreground input in the current window using client-relative coordinates."""
    desktop.require_admin()
    hwnd = _focus_game_window()
    rect = wintypes.RECT()
    if not ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rect)):
        raise ctypes.WinError()
    origin = wintypes.POINT(0, 0)
    if not ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        raise ctypes.WinError()
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    target = (
        origin.x + round(width * 0.885),
        origin.y + round(height * 0.250),
    )
    desktop.save_step_screenshot("ok_windowed_claim_before_virtual_hid_click")
    with managed_virtual_mouse() as mouse:
        mouse.click_at(*target)
    log.info(
        "windowed claim probe clicked target=%s client=%sx%s origin=%s",
        target,
        width,
        height,
        (origin.x, origin.y),
    )
    time.sleep(3)
    desktop.save_step_screenshot("ok_windowed_claim_after_click")
