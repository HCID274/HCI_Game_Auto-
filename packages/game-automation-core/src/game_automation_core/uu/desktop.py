"""DPI-aware, screenshot-first UU desktop primitives configured by adapters."""

from __future__ import annotations

import ctypes
import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psutil

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except (AttributeError, OSError):  # pragma: no cover - non-Windows import safety
    pass

import pyautogui

from game_automation_core.uu.errors import UuStartupError
from game_automation_core.windows.desktop_guard import activate_window

log = logging.getLogger(__name__)


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
            try:
                location = pyautogui.locateOnScreen(
                    str(template), confidence=confidence
                )
                if location is not None:
                    center = pyautogui.center(location)
                    return center.x, center.y
            except pyautogui.ImageNotFoundException:
                pass
            time.sleep(interval)
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
            raise self.startup_error(
                step_name,
                f"cannot locate {template.name} within {timeout:.1f}s",
            )
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
