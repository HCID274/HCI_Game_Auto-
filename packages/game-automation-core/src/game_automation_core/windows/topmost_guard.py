"""自动化运行期间，把盖在 UU、游戏等窗口上的别家置顶窗口临时压到最底下，结束时放回置顶。

11-09 05:30 星铁失败：UU 其实已经加速成功，但 Claude Code 的提醒窗口（置顶、停在屏幕正中）
盖住了 UU 的“停止加速”按钮，脚本看不到按钮，判成失败，重开 UU 4 次都被同一个窗口挡住；
10-28 也被它挡过。置顶窗口用“把 UU 拉到最前”压不住，只能先取消它的置顶。

进程被强行结束时来不及放回；Claude Code 提醒窗口下次来提醒时会自己重新置顶。
"""

from __future__ import annotations

import ctypes
import functools
import logging
import threading
from collections.abc import Callable, Collection, Iterator, Sequence
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass

import psutil

from game_automation_core.windows.desktop_guard import describe_window, snapshot_window

log = logging.getLogger(__name__)

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
DWMWA_CLOAKED = 14
HWND_TOPMOST = -1
HWND_BOTTOM = 1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
# 别的进程卡住时不陪着等：把请求投递给窗口所属线程。
SWP_ASYNCWINDOWPOS = 0x4000
_REORDER_ONLY = SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS
SWEEP_INTERVAL_SECONDS = 0.25

# 任务栏、开始菜单、输入法、锁屏和系统授权窗口也是置顶的，不动。
SYSTEM_PROCESS_NAMES = frozenset(
    {
        "explorer.exe",
        "shellexperiencehost.exe",
        "startmenuexperiencehost.exe",
        "searchhost.exe",
        "searchapp.exe",
        "textinputhost.exe",
        "ctfmon.exe",
        "dwm.exe",
        "csrss.exe",
        "lockapp.exe",
        "logonui.exe",
        "consent.exe",
        "credentialuibroker.exe",
    }
)

_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
Rect = tuple[int, int, int, int]


@functools.cache
def _win32() -> tuple[ctypes.WinDLL, ctypes.WinDLL]:
    # 独立 DLL 句柄，参数类型不污染 PyGetWindow 共用的 windll.user32。
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi")
    signatures = {
        user32: {
            "EnumWindows": ([_WNDENUMPROC, wintypes.LPARAM], wintypes.BOOL),
            "IsWindow": ([wintypes.HWND], wintypes.BOOL),
            "IsWindowVisible": ([wintypes.HWND], wintypes.BOOL),
            "IsIconic": ([wintypes.HWND], wintypes.BOOL),
            "GetWindowRect": ([wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            "GetWindowLongW": ([wintypes.HWND, ctypes.c_int], wintypes.LONG),
            "GetWindowThreadProcessId": (
                [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)],
                wintypes.DWORD,
            ),
            "SetWindowPos": (
                [
                    wintypes.HWND,
                    wintypes.HWND,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_int,
                    wintypes.UINT,
                ],
                wintypes.BOOL,
            ),
        },
        dwmapi: {
            "DwmGetWindowAttribute": (
                [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD],
                ctypes.c_long,
            ),
        },
    }
    for library, functions in signatures.items():
        for name, (argtypes, restype) in functions.items():
            function = getattr(library, name)
            function.argtypes = argtypes
            function.restype = restype
    return user32, dwmapi


@dataclass(frozen=True, slots=True)
class TopLevelWindow:
    hwnd: int
    pid: int
    process_name: str
    rect: Rect
    topmost: bool


def _overlaps(first: Rect, second: Rect) -> bool:
    return (
        first[0] < second[2]
        and second[0] < first[2]
        and first[1] < second[3]
        and second[1] < first[3]
    )


def covering_windows(
    windows: Sequence[TopLevelWindow], protected_process_names: Collection[str]
) -> list[TopLevelWindow]:
    """别家进程的置顶窗口里，和受保护窗口（UU、游戏等）有重叠的那些。"""
    protected_rects = [
        window.rect for window in windows if window.process_name in protected_process_names
    ]
    return [
        window
        for window in windows
        if window.topmost
        and window.process_name
        and window.process_name not in protected_process_names
        and window.process_name not in SYSTEM_PROCESS_NAMES
        and any(_overlaps(window.rect, rect) for rect in protected_rects)
    ]


def window_pid(hwnd: int) -> int:
    user32, _ = _win32()
    if not user32.IsWindow(hwnd):
        return 0
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    return int(process_id.value)


def _is_cloaked(dwmapi: ctypes.WinDLL, hwnd: int) -> bool:
    # 后台挂起的商店应用窗口“可见”但被系统藏起来了，看不见也挡不住。
    cloaked = wintypes.DWORD()
    result = dwmapi.DwmGetWindowAttribute(
        hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
    )
    return result == 0 and bool(cloaked.value)


def visible_top_level_windows() -> list[TopLevelWindow]:
    """屏幕上看得见的顶层窗口，按 Z 序从上到下。"""
    user32, dwmapi = _win32()
    names: dict[int, str] = {}
    windows: list[TopLevelWindow] = []

    def process_name(pid: int) -> str:
        if pid not in names:
            try:
                names[pid] = psutil.Process(pid).name().casefold()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                names[pid] = ""
        return names[pid]

    @_WNDENUMPROC
    def collect(hwnd: int, _lparam: int) -> bool:
        if not hwnd or not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        rect = wintypes.RECT()
        if (
            not user32.GetWindowRect(hwnd, ctypes.byref(rect))
            or rect.right <= rect.left
            or rect.bottom <= rect.top
            or _is_cloaked(dwmapi, hwnd)
        ):
            return True
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        windows.append(
            TopLevelWindow(
                hwnd=int(hwnd),
                pid=int(process_id.value),
                process_name=process_name(int(process_id.value)),
                rect=(rect.left, rect.top, rect.right, rect.bottom),
                topmost=bool(user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST),
            )
        )
        return True

    user32.EnumWindows(collect, 0)
    return windows


def push_to_bottom(hwnd: int) -> bool:
    """取消置顶并压到所有窗口下面；不激活、不挪位置、不改大小。"""
    user32, _ = _win32()
    return bool(user32.SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0, _REORDER_ONLY))


def restore_topmost(hwnd: int) -> bool:
    user32, _ = _win32()
    return bool(user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, _REORDER_ONLY))


def _describe(hwnd: int) -> str:
    try:
        return describe_window(snapshot_window(hwnd))
    except Exception:  # 只用于日志，取不到详情也不影响压窗口
        return f"hwnd=0x{hwnd:X}"


class TopmostCoverGuard:
    """记下压下去的窗口（句柄 → 进程号），结束时只放回同一个进程的同一个窗口。"""

    def __init__(
        self,
        protected_process_names: Collection[str],
        *,
        enumerate_windows: Callable[[], list[TopLevelWindow]] = visible_top_level_windows,
        push_down: Callable[[int], bool] = push_to_bottom,
        raise_topmost: Callable[[int], bool] = restore_topmost,
        owner_pid: Callable[[int], int] = window_pid,
        describe: Callable[[int], str] = _describe,
    ) -> None:
        self.protected_process_names = frozenset(
            name.casefold() for name in protected_process_names
        )
        self._enumerate_windows = enumerate_windows
        self._push_down = push_down
        self._raise_topmost = raise_topmost
        self._owner_pid = owner_pid
        self._describe = describe
        self.pushed: dict[int, int] = {}
        self._failed: set[int] = set()

    def sweep(self) -> None:
        for window in covering_windows(
            self._enumerate_windows(), self.protected_process_names
        ):
            if not self._push_down(window.hwnd):
                if window.hwnd not in self._failed:
                    self._failed.add(window.hwnd)
                    log.warning(
                        "cannot push down topmost window covering the game: %s",
                        self._describe(window.hwnd),
                    )
                continue
            if window.hwnd not in self.pushed:
                log.info(
                    "pushed down topmost window covering the game: %s",
                    self._describe(window.hwnd),
                )
            self.pushed[window.hwnd] = window.pid

    def restore(self) -> None:
        for hwnd, pid in self.pushed.items():
            # 窗口已关闭，或句柄被别的窗口重新用了，都不放回。
            if self._owner_pid(hwnd) != pid:
                continue
            if self._raise_topmost(hwnd):
                log.info("restored topmost window: %s", self._describe(hwnd))
            else:
                log.warning("cannot restore topmost window: %s", self._describe(hwnd))
        self.pushed.clear()


@contextmanager
def keep_covering_windows_down(
    protected_process_names: Collection[str],
    *,
    interval: float = SWEEP_INTERVAL_SECONDS,
    guard: TopmostCoverGuard | None = None,
) -> Iterator[TopmostCoverGuard]:
    """整段自动化期间在后台反复清场；提醒窗口来新提醒时会重新置顶，所以不能只清一次。"""
    guard = guard or TopmostCoverGuard(protected_process_names)
    stop = threading.Event()
    reported: set[str] = set()

    def run() -> None:
        while not stop.is_set():
            try:
                guard.sweep()
            except Exception as exc:  # 清场失败不能拖垮自动化本身；同一种错误只记一次
                if str(exc) not in reported:
                    reported.add(str(exc))
                    log.exception("topmost cover guard sweep failed")
            stop.wait(interval)

    thread = threading.Thread(target=run, name="topmost-cover-guard", daemon=True)
    thread.start()
    try:
        yield guard
    finally:
        stop.set()
        thread.join(timeout=5)
        try:
            guard.restore()
        except Exception:  # 放回失败只记日志，不能改掉任务本身的结果
            log.exception("topmost cover guard restore failed")


__all__ = [
    "TopLevelWindow",
    "TopmostCoverGuard",
    "covering_windows",
    "keep_covering_windows_down",
    "visible_top_level_windows",
]
