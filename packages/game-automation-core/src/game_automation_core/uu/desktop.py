"""DPI-aware, screenshot-first UU desktop primitives configured by adapters."""

from __future__ import annotations

import ctypes
import functools
import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psutil
from PIL import Image

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except (AttributeError, OSError):  # pragma: no cover - non-Windows import safety
    pass

import pyautogui

from game_automation_core.uu.errors import UuStartupError
from game_automation_core.windows.desktop_guard import (
    activate_window,
    describe_window,
    snapshot_window,
)

log = logging.getLogger(__name__)

# 找图只截 UU 自己的窗口（PrintWindow），不读整块屏幕：1017 内存耗尽后整屏截图
# 一直全黑，单窗口截图仍正常；系统弹窗盖在 UU 上时，也能按 UU 的真实内容判断。
PW_RENDERFULLCONTENT = 2  # 连硬件加速绘制的内容一起画进位图
GA_ROOT = 2
_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


@functools.cache
def _win32() -> tuple[ctypes.WinDLL, ctypes.WinDLL]:
    # 独立 DLL 句柄，参数类型不污染 PyGetWindow 共用的 windll.user32。
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    signatures = {
        user32: {
            "EnumWindows": ([_WNDENUMPROC, wintypes.LPARAM], wintypes.BOOL),
            "IsWindowVisible": ([wintypes.HWND], wintypes.BOOL),
            "IsIconic": ([wintypes.HWND], wintypes.BOOL),
            "GetWindowThreadProcessId": (
                [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)],
                wintypes.DWORD,
            ),
            "GetWindowRect": ([wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            "GetWindowDC": ([wintypes.HWND], wintypes.HDC),
            "ReleaseDC": ([wintypes.HWND, wintypes.HDC], ctypes.c_int),
            "PrintWindow": ([wintypes.HWND, wintypes.HDC, wintypes.UINT], wintypes.BOOL),
            "WindowFromPoint": ([wintypes.POINT], wintypes.HWND),
            "GetAncestor": ([wintypes.HWND, wintypes.UINT], wintypes.HWND),
        },
        gdi32: {
            "CreateCompatibleDC": ([wintypes.HDC], wintypes.HDC),
            "CreateCompatibleBitmap": (
                [wintypes.HDC, ctypes.c_int, ctypes.c_int],
                wintypes.HBITMAP,
            ),
            "SelectObject": ([wintypes.HDC, wintypes.HGDIOBJ], wintypes.HGDIOBJ),
            "GetDIBits": (
                [
                    wintypes.HDC,
                    wintypes.HBITMAP,
                    wintypes.UINT,
                    wintypes.UINT,
                    ctypes.c_void_p,
                    ctypes.POINTER(_BitmapInfoHeader),
                    wintypes.UINT,
                ],
                ctypes.c_int,
            ),
            "DeleteObject": ([wintypes.HGDIOBJ], wintypes.BOOL),
            "DeleteDC": ([wintypes.HDC], wintypes.BOOL),
        },
    }
    for library, functions in signatures.items():
        for name, (argtypes, restype) in functions.items():
            function = getattr(library, name)
            function.argtypes = argtypes
            function.restype = restype
    return user32, gdi32


def _hwnd_process_name(hwnd: int) -> str:
    user32, _ = _win32()
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    try:
        return psutil.Process(process_id.value).name().casefold()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return ""


def capture_window(hwnd: int) -> tuple[Image.Image, int, int] | None:
    """截一个窗口的完整内容；返回图片和窗口左上角的屏幕坐标。"""
    user32, gdi32 = _win32()
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return None
    window_dc = user32.GetWindowDC(hwnd)
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    previous = gdi32.SelectObject(memory_dc, bitmap)
    try:
        if not user32.PrintWindow(hwnd, memory_dc, PW_RENDERFULLCONTENT):
            return None
        header = _BitmapInfoHeader(
            ctypes.sizeof(_BitmapInfoHeader), width, -height, 1, 32, 0, 0, 0, 0, 0, 0
        )
        pixels = ctypes.create_string_buffer(width * height * 4)
        if not gdi32.GetDIBits(memory_dc, bitmap, 0, height, pixels, ctypes.byref(header), 0):
            return None
        image = Image.frombytes("RGB", (width, height), pixels.raw, "raw", "BGRX")
        return image, rect.left, rect.top
    finally:
        gdi32.SelectObject(memory_dc, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd, window_dc)


@dataclass(frozen=True, slots=True)
class UuDesktopConfig:
    evidence_dir: Path
    expected_screen_size: tuple[int, int]
    window_keywords: tuple[str, ...]
    process_names: frozenset[str]
    update_notice: Path
    update_actions: tuple[tuple[str, Path], ...]
    admin_hint: str
    mandatory_update_notice: Path | None = None
    mandatory_update_action: Path | None = None
    window_timeout: float = 30.0
    window_poll_interval: float = 0.5
    minimize_timeout: float = 5.0
    image_interval: float = 0.25
    image_confidence: float = 0.8
    popup_detect_timeout: float = 0.8
    popup_action_timeout: float = 2.0
    popup_settle_delay: float = 0.5
    popup_max_dismissals: int = 3

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "window_keywords",
            tuple(keyword.casefold() for keyword in self.window_keywords),
        )
        object.__setattr__(
            self,
            "process_names",
            frozenset(name.casefold() for name in self.process_names),
        )


class UuDesktopController:
    def __init__(
        self,
        config: UuDesktopConfig,
        *,
        is_process_running: Callable[[], bool],
    ) -> None:
        self.config = config
        self.is_process_running = is_process_running
        # 最近一次找到了图、但点击位置被别的窗口挡住时，记下挡住的是谁。
        self.last_cover = ""

    def require_admin(self) -> None:
        try:
            is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:  # pragma: no cover - host-dependent fallback
            is_admin = False
        if not is_admin:
            raise RuntimeError(
                f"UU automation requires elevation; run {self.config.admin_hint}"
            )

    def require_supported_display(self) -> None:
        screen = pyautogui.size()
        actual = (screen.width, screen.height)
        if actual != self.config.expected_screen_size:
            raise self.startup_error(
                "display_environment",
                f"unsupported primary screen {actual}; expected "
                f"{self.config.expected_screen_size}",
                retryable=False,
            )

    def save_screenshot(self, prefix: str) -> Path:
        self.config.evidence_dir.mkdir(parents=True, exist_ok=True)
        path = self.config.evidence_dir / (
            f"{prefix}_{datetime.now():%Y%m%d_%H%M%S_%f}.png"
        )
        pyautogui.screenshot(str(path))
        log.info("screenshot saved: %s", path)
        return path

    def startup_error(
        self,
        step_name: str,
        reason: str,
        *,
        retryable: bool = True,
        screenshot: bool = True,
    ) -> UuStartupError:
        slug = "".join(
            character if character.isalnum() else "_"
            for character in step_name.casefold()
        ).strip("_")
        evidence = self.save_screenshot(f"uu_{slug}_failed") if screenshot else None
        return UuStartupError(
            step_name,
            reason,
            retryable=retryable,
            screenshot_path=evidence,
        )

    @staticmethod
    def leave_failsafe_corner() -> None:
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
        log.info("moved cursor from fail-safe corner to %s", safe_position)

    @staticmethod
    def park_cursor_for_detection() -> None:
        screen = pyautogui.size()
        parked = (screen.width - 2, screen.height // 2)
        if not ctypes.windll.user32.SetCursorPos(*parked):
            raise RuntimeError(f"failed to park cursor at {parked}")
        log.info("parked cursor for stable UU detection at %s", parked)

    @staticmethod
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

    def is_uu_window(self, window: object) -> bool:
        title = (getattr(window, "title", "") or "").casefold()
        if not any(keyword in title for keyword in self.config.window_keywords):
            return False
        process_name = self._window_process_name(window)
        return not process_name or process_name in self.config.process_names

    def get_windows(self) -> list[object]:
        try:
            return [
                window
                for window in pyautogui.getAllWindows()
                if self.is_uu_window(window)
            ]
        except Exception as exc:  # pragma: no cover - host-dependent fallback
            log.warning("failed to enumerate UU windows: %s", exc)
            return []

    def focus_window(
        self,
        timeout: float | None = None,
        *,
        activator: Callable[..., object] = activate_window,
        windows_getter: Callable[[], list[object]] | None = None,
    ) -> str:
        timeout = self.config.window_timeout if timeout is None else timeout
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            windows = (windows_getter or self.get_windows)()
            for window in windows:
                try:
                    if getattr(window, "isMinimized", False):
                        window.restore()
                        time.sleep(0.4)
                    hwnd = getattr(window, "_hWnd", None)
                    if hwnd is not None:
                        activator(int(hwnd), timeout=1.5)
                    else:
                        window.activate()
                    time.sleep(0.5)
                    if hwnd is None:
                        return getattr(window, "title", "") or "<untitled>"
                    foreground = int(ctypes.windll.user32.GetForegroundWindow() or 0)
                    if foreground == int(hwnd):
                        return getattr(window, "title", "") or "<untitled>"
                    last_error = RuntimeError(
                        "UU activation did not own foreground: "
                        f"expected={hwnd}, actual={foreground}"
                    )
                except Exception as exc:  # pragma: no cover - host-dependent fallback
                    last_error = exc
            time.sleep(self.config.window_poll_interval)
        if last_error:
            raise RuntimeError(f"failed to focus UU window: {last_error}")
        raise RuntimeError(f"UU window not detected within {timeout:.0f}s")

    def minimize_window(self, timeout: float | None = None) -> None:
        self.require_admin()
        if not self.is_process_running():
            log.info("UU is not running; nothing to minimize")
            return
        timeout = self.config.minimize_timeout if timeout is None else timeout
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            windows = self.get_windows()
            if not windows:
                time.sleep(self.config.window_poll_interval)
                continue
            for window in windows:
                try:
                    if not getattr(window, "isMinimized", False):
                        window.minimize()
                        log.info(
                            "UU window minimized: %s", getattr(window, "title", "")
                        )
                except Exception as exc:  # pragma: no cover - host-dependent fallback
                    last_error = exc
            if last_error:
                raise RuntimeError(f"failed to minimize UU window: {last_error}")
            return
        raise RuntimeError(f"UU window not detected within {timeout:.0f}s")

    def minimize_best_effort(self, context: str) -> None:
        try:
            self.minimize_window()
        except RuntimeError as exc:
            log.warning("best-effort UU minimize failed after %s: %s", context, exc)

    @contextmanager
    def minimize_on_exit(self, context: str) -> Iterator[None]:
        try:
            yield
        finally:
            self.minimize_best_effort(context)

    @staticmethod
    def _require_template(template: Path) -> None:
        if not template.is_file():
            raise UuStartupError(
                "template_preflight",
                f"required template not found: {template}",
                retryable=False,
            )

    def try_locate_image(
        self,
        template: Path,
        *,
        timeout: float,
        confidence: float | None = None,
        interval: float | None = None,
    ) -> tuple[int, int] | None:
        self._require_template(template)
        confidence = self.config.image_confidence if confidence is None else confidence
        interval = self.config.image_interval if interval is None else interval
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            position = self.locate_in_uu_windows(template, confidence=confidence)
            if position is None and not self.last_cover:
                position = self._locate_on_screen(template, confidence=confidence)
            if position is not None:
                return position
            time.sleep(interval)
        return None

    @staticmethod
    def _locate_on_screen(template: Path, *, confidence: float) -> tuple[int, int] | None:
        # 兜底：UU 窗口截图在真机验证前保留整屏找图；日志里出现 "screen fallback"
        # 说明窗口截图没找到。05:30 真机日志确认窗口截图可用后删掉这段。
        try:
            location = pyautogui.locateOnScreen(str(template), confidence=confidence)
        except pyautogui.ImageNotFoundException:
            return None
        if location is None:
            return None
        center = pyautogui.center(location)
        log.warning("matched %s via screen fallback, not the UU window", template.name)
        return center.x, center.y

    def uu_window_handles(self) -> list[int]:
        """可见且未最小化的 UU 顶层窗口（含无标题弹窗），按 Z 序从上到下。"""
        user32, _ = _win32()
        handles: list[int] = []

        @_WNDENUMPROC
        def collect(hwnd: int, _lparam: int) -> bool:
            if (
                hwnd
                and user32.IsWindowVisible(hwnd)
                and not user32.IsIconic(hwnd)
                and _hwnd_process_name(hwnd) in self.config.process_names
            ):
                handles.append(int(hwnd))
            return True

        user32.EnumWindows(collect, 0)
        return handles

    def cover_at(self, point: tuple[int, int]) -> str:
        """点击位置最上层不是 UU 的窗口时，返回挡住它的窗口描述。"""
        user32, _ = _win32()
        hwnd = user32.WindowFromPoint(wintypes.POINT(*point))
        root = user32.GetAncestor(hwnd, GA_ROOT) if hwnd else None
        if not root:
            return f"no window at {point}"
        if _hwnd_process_name(root) in self.config.process_names:
            return ""
        return describe_window(snapshot_window(root))

    def locate_in_uu_windows(
        self, template: Path, *, confidence: float
    ) -> tuple[int, int] | None:
        self.last_cover = ""
        for hwnd in self.uu_window_handles():
            captured = capture_window(hwnd)
            if captured is None:
                continue
            image, left, top = captured
            try:
                box = pyautogui.locate(str(template), image, confidence=confidence)
            except pyautogui.ImageNotFoundException:
                box = None
            if box is None:
                continue
            point = (
                int(left + box.left + box.width // 2),
                int(top + box.top + box.height // 2),
            )
            # 截图里找到了，还要确认真点下去落在 UU 上，不点到盖在上面的弹窗。
            cover = self.cover_at(point)
            if cover:
                self.last_cover = cover
                continue
            return point
        return None

    def wait_for_image(
        self,
        template: Path,
        *,
        step_name: str,
        timeout: float,
        initial_delay: float = 0.0,
        confidence: float | None = None,
        interval: float | None = None,
    ) -> tuple[int, int]:
        if initial_delay:
            time.sleep(initial_delay)
        log.info("polling %s for step %s", template.name, step_name)
        position = self.try_locate_image(
            template,
            timeout=timeout,
            confidence=confidence,
            interval=interval,
        )
        if position is None:
            reason = f"cannot locate {template.name} within {timeout:.1f}s"
            if self.last_cover:
                reason += f"; covered by {self.last_cover}"
            raise self.startup_error(step_name, reason)
        log.info("matched %s at %s", template.name, position)
        return position

    @staticmethod
    def click(position: tuple[int, int]) -> None:
        UuDesktopController.leave_failsafe_corner()
        pyautogui.click(*position)
        log.info("clicked at %s", position)

    @staticmethod
    def move_mouse_to(position: tuple[int, int]) -> None:
        UuDesktopController.leave_failsafe_corner()
        pyautogui.moveTo(*position, duration=0.25)
        log.info("moved mouse to %s", position)

    def click_after_evidence(
        self, position: tuple[int, int], step_name: str
    ) -> None:
        self.save_screenshot(f"uu_{step_name}_before_click")
        self.click(position)

    def hover_after_evidence(
        self, position: tuple[int, int], step_name: str
    ) -> None:
        self.save_screenshot(f"uu_{step_name}_before_hover")
        self.move_mouse_to(position)

    def dismiss_known_popups(self, context: str) -> None:
        if not self.config.update_notice.is_file():
            return
        dismissed = 0
        for _ in range(self.config.popup_max_dismissals):
            notice = self.try_locate_image(
                self.config.update_notice,
                timeout=self.config.popup_detect_timeout,
            )
            if notice is None:
                if dismissed:
                    log.info("known UU popups cleared after %d action(s)", dismissed)
                return
            self.save_screenshot("uu_update_popup_detected")
            for action_name, template in self.config.update_actions:
                if not template.is_file():
                    continue
                target = self.try_locate_image(
                    template,
                    timeout=self.config.popup_action_timeout,
                )
                if target is not None:
                    self.click_after_evidence(target, f"update_{action_name}")
                    time.sleep(self.config.popup_settle_delay)
                    dismissed += 1
                    break
            else:
                raise self.startup_error(
                    "dismiss_update_popup",
                    f"popup detected during {context}, but no known action matched",
                )
        raise self.startup_error(
            "dismiss_known_uu_popups",
            f"popup remained after {dismissed} action(s) during {context}",
        )

    def mandatory_update_visible(self, *, timeout: float | None = None) -> bool:
        template = self.config.mandatory_update_notice
        if template is None:
            return False
        if not template.is_file():
            raise UuStartupError(
                "mandatory_update_preflight",
                f"required template not found: {template}",
                retryable=False,
            )
        return self.try_locate_image(
            template,
            timeout=(
                self.config.popup_detect_timeout if timeout is None else timeout
            ),
        ) is not None

    def accept_mandatory_update(self, context: str) -> bool:
        if not self.mandatory_update_visible():
            return False
        self.save_screenshot("uu_mandatory_update_detected")
        action = self.config.mandatory_update_action
        if action is None or not action.is_file():
            raise UuStartupError(
                "mandatory_update_preflight",
                f"mandatory update detected during {context}, but action template is missing: {action}",
                retryable=False,
            )
        target = self.try_locate_image(
            action,
            timeout=self.config.popup_action_timeout,
        )
        if target is None:
            raise self.startup_error(
                "mandatory_update_action",
                f"mandatory update detected during {context}, but upgrade action was not found",
                retryable=False,
            )
        self.click_after_evidence(target, "mandatory_update")
        log.info("mandatory UU update accepted during %s", context)
        return True


__all__ = ["UuDesktopConfig", "UuDesktopController", "pyautogui"]
