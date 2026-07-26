"""UU 加速器控制模块 — 正式启动链路."""

import ctypes
import logging
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import psutil

# 125% 缩放下必须在 import pyautogui 前设置，否则坐标会错位
ctypes.windll.shcore.SetProcessDpiAwareness(2)

import pyautogui

# ── 配置 ──────────────────────────────────────────────
UU_EXE = Path(r"D:\2_Software\4_Games\Netease\UU\uu_launcher.exe")
TEMPLATES_DIR = Path(__file__).parent / "templates"
DEBUG_DIR = Path(__file__).parent / "debug"

TPL_STEP_1 = TEMPLATES_DIR / "uu_01.png"
TPL_STEP_2 = TEMPLATES_DIR / "uu_02.png"
TPL_STEP_3 = TEMPLATES_DIR / "uu_03.png"
TPL_UPDATE_NOTICE = TEMPLATES_DIR / "uu_update_notice.png"
TPL_UPDATE_CONFIRM = TEMPLATES_DIR / "uu_update_confirm.png"
TPL_UPDATE_CLOSE = TEMPLATES_DIR / "uu_update_close.png"

UU_WINDOW_TIMEOUT = 30
WINDOW_CHECK_INTERVAL = 1
IMAGE_SEARCH_TIMEOUT = 30
IMAGE_RETRY_INTERVAL = 0.25
IMAGE_CONFIDENCE = 0.8
STEP_1_INITIAL_DELAY = 3.0
STEP_1_TIMEOUT = 10.0
STEP_2_TIMEOUT = 5.0
REUSE_CONFIRM_TIMEOUT = 3.0
POST_MOVE_DELAY = 0.5
POST_CLICK_WAIT = 20         # 点击后等待确认的时间（秒）
CONFIRM_TIMEOUT = 30         # 确认图片搜索超时（秒）
STOP_ACCELERATION_TIMEOUT = 10.0
UU_RESTART_DELAY = 5.0
UU_MINIMIZE_BEST_EFFORT_TIMEOUT = 5.0
UU_POPUP_DETECT_TIMEOUT = 0.8
UU_POPUP_ACTION_TIMEOUT = 2.0
UU_POPUP_SETTLE_DELAY = 0.5
UU_POPUP_MAX_DISMISSALS = 3
UU_STARTUP_MAX_RESTARTS = 3
EXPECTED_PRIMARY_SCREEN_SIZE = (2560, 1440)

UU_WINDOW_KEYWORDS = ("uu", "网易uu", "uu加速器")
UU_PROCESS_KEYWORDS = ("uu", "uuaccelerator", "uulauncher")
UU_UPDATE_POPUP_ACTIONS = (
    ("confirm", TPL_UPDATE_CONFIRM),
    ("close", TPL_UPDATE_CLOSE),
)

log = logging.getLogger(__name__)


class UuStartupError(RuntimeError):
    """UU 启动链路失败，携带可观测证据和是否值得重启重试。"""

    def __init__(
        self,
        step_name: str,
        reason: str,
        *,
        retryable: bool = True,
        screenshot_path: Path | None = None,
        restarts_used: int = 0,
    ) -> None:
        self.step_name = step_name
        self.reason = reason
        self.retryable = retryable
        self.screenshot_path = screenshot_path
        self.restarts_used = restarts_used

        details = f"{step_name}: {reason}"
        if screenshot_path is not None:
            details = f"{details}; screenshot={screenshot_path}"
        if not retryable:
            details = f"{details}; retryable=false"
        super().__init__(details)


class UuStartupFinalError(RuntimeError):
    """UU 启动最终失败，携带最后失败证据和已使用重启次数。"""

    def __init__(self, last_error: UuStartupError, restarts_used: int) -> None:
        self.last_error = last_error
        self.restarts_used = restarts_used
        super().__init__(
            "UU startup failed after "
            f"{restarts_used} restart(s): {last_error}"
        )


def _is_running_as_admin() -> bool:
    """当前进程是否以管理员权限运行。"""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # pragma: no cover - 依赖宿主环境
        return False


def _require_admin() -> None:
    """GUI 自动化统一要求在提权进程中执行。"""
    if _is_running_as_admin():
        return
    raise RuntimeError(
        "UU automation requires an elevated process; run it via "
        "`uv run python run_elevated.py uu_accel.py` or start the terminal as administrator"
    )


def _require_supported_display() -> None:
    """Reject unsupported display modes before image matching and pointless UU restarts."""
    screen = pyautogui.size()
    actual = (screen.width, screen.height)
    if actual == EXPECTED_PRIMARY_SCREEN_SIZE:
        return

    raise _startup_error(
        "display_environment",
        "unsupported primary screen size "
        f"{actual[0]}x{actual[1]}; expected "
        f"{EXPECTED_PRIMARY_SCREEN_SIZE[0]}x{EXPECTED_PRIMARY_SCREEN_SIZE[1]}",
        retryable=False,
    )


def _save_debug_screenshot(prefix: str) -> Path:
    """失败时截图存入 debug/ 目录。"""
    DEBUG_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = DEBUG_DIR / f"{prefix}_{ts}.png"
    pyautogui.screenshot(str(path))
    log.info("debug screenshot saved: %s", path)
    return path


def _debug_slug(value: str) -> str:
    """将步骤名转成稳定的调试截图文件名前缀。"""
    return "".join(ch if ch.isalnum() else "_" for ch in value.casefold()).strip("_")


def _startup_error(
    step_name: str,
    reason: str,
    *,
    retryable: bool = True,
    screenshot: bool = True,
) -> UuStartupError:
    screenshot_path = _save_debug_screenshot(f"uu_{_debug_slug(step_name)}_failed") if screenshot else None
    return UuStartupError(
        step_name,
        reason,
        retryable=retryable,
        screenshot_path=screenshot_path,
    )


def _is_uu_running() -> bool:
    """检查 UU 加速器进程是否已运行。"""
    for proc in psutil.process_iter(["name"]):
        if proc.info["name"] and "uu" in proc.info["name"].lower():
            return True
    return False


def kill_uu() -> None:
    """关闭所有 UU 加速器进程。"""
    killed: list[str] = []
    for proc in psutil.process_iter(["name", "pid"]):
        name = proc.info["name"] or ""
        if "uu" in name.lower():
            try:
                proc.terminate()
                killed.append(f"{name} (pid={proc.info['pid']})")
            except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
                log.warning("cannot terminate %s (pid=%d): %s", name, proc.info["pid"], exc)

    if not killed:
        log.info("no UU processes found")
        return

    log.info("terminated: %s", ", ".join(killed))

    # 等待进程退出，超时则强制 kill
    gone, alive = psutil.wait_procs(
        [p for p in psutil.process_iter(["name"]) if p.info["name"] and "uu" in p.info["name"].lower()],
        timeout=5,
    )
    for proc in alive:
        try:
            proc.kill()
            log.warning("force-killed %s (pid=%d)", proc.name(), proc.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    log.info("UU accelerator stopped")


def _start_uu_process() -> None:
    """启动 UU。"""
    if not UU_EXE.exists():
        raise UuStartupError(
            "start_uu_process",
            f"UU executable not found: {UU_EXE}",
            retryable=False,
        )

    try:
        subprocess.Popen([str(UU_EXE)])
    except OSError as exc:
        raise UuStartupError(
            "start_uu_process",
            f"failed to start UU accelerator: {exc}",
            retryable=False,
        ) from exc


def _ensure_uu_started() -> None:
    """确保 UU 已启动。"""
    if _is_uu_running():
        log.info("UU accelerator already running")
        return

    log.info("starting UU accelerator: %s", UU_EXE)
    _start_uu_process()
    time.sleep(5)
    if not _is_uu_running():
        raise _startup_error(
            "ensure_uu_started",
            "UU process was not detected after launch",
        )
    log.info("UU accelerator started")


def _get_window_process_identity(window: object) -> str:
    """获取窗口所属进程信息，用于过滤误匹配标题。"""
    hwnd = getattr(window, "_hWnd", None)
    if hwnd is None:
        return ""

    pid = ctypes.c_ulong()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""

    try:
        proc = psutil.Process(pid.value)
        name = proc.name() or ""
        exe = proc.exe() or ""
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return ""
    return f"{name} {exe}".casefold()


def _is_uu_window(window: object) -> bool:
    """判断窗口是否属于 UU。"""
    title = (getattr(window, "title", "") or "").casefold()
    if not any(keyword in title for keyword in UU_WINDOW_KEYWORDS):
        return False

    process_identity = _get_window_process_identity(window)
    if process_identity:
        return any(keyword in process_identity for keyword in UU_PROCESS_KEYWORDS)
    return True


def _get_uu_windows() -> list[object]:
    """返回 UU 相关窗口对象。"""
    try:
        windows = pyautogui.getAllWindows()
    except Exception as exc:  # pragma: no cover - 依赖宿主环境
        log.warning("failed to enumerate windows: %s", exc)
        return []

    matched: list[object] = []
    for window in windows:
        if _is_uu_window(window):
            matched.append(window)
    return matched


def _focus_uu_window(timeout: float = UU_WINDOW_TIMEOUT) -> str:
    """将 UU 窗口置顶到前台并返回标题。"""
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        windows = _get_uu_windows()
        if not windows:
            time.sleep(WINDOW_CHECK_INTERVAL)
            continue

        for window in windows:
            title = getattr(window, "title", "") or "<untitled>"
            try:
                if getattr(window, "isMinimized", False):
                    window.restore()
                    time.sleep(0.3)
                window.activate()
                time.sleep(0.5)
                return title
            except Exception as exc:  # pragma: no cover - 依赖宿主环境
                last_error = exc

        time.sleep(WINDOW_CHECK_INTERVAL)

    if last_error is not None:
        raise RuntimeError(f"failed to focus UU window: {last_error}")
    raise RuntimeError(f"UU window not detected within {timeout:.0f}s")


def minimize_uu_window(timeout: float = UU_WINDOW_TIMEOUT) -> None:
    """将 UU 窗口最小化到任务栏，不关闭进程或停止加速。"""
    _require_admin()

    if not _is_uu_running():
        log.info("UU accelerator is not running; nothing to minimize")
        return

    deadline = time.monotonic() + timeout
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        windows = _get_uu_windows()
        if not windows:
            time.sleep(WINDOW_CHECK_INTERVAL)
            continue

        minimized = 0
        for window in windows:
            title = getattr(window, "title", "") or "<untitled>"
            try:
                if not getattr(window, "isMinimized", False):
                    window.minimize()
                    minimized += 1
                    log.info("UU window minimized: %s", title)
            except Exception as exc:  # pragma: no cover - 依赖宿主环境
                last_error = exc

        if minimized > 0:
            return

        log.info("UU windows are already minimized")
        return

    if last_error is not None:
        raise RuntimeError(f"failed to minimize UU window: {last_error}")
    raise RuntimeError(f"UU window not detected within {timeout:.0f}s")


def _minimize_uu_window_best_effort(context: str) -> None:
    """兜底收起 UU；失败只记日志，不覆盖主流程结果。"""
    try:
        minimize_uu_window(timeout=UU_MINIMIZE_BEST_EFFORT_TIMEOUT)
    except RuntimeError as exc:
        log.warning("best-effort UU minimize skipped after %s: %s", context, exc)


@contextmanager
def _keep_uu_in_background_on_exit(context: str) -> Iterator[None]:
    """统一的 UU 窗口生命周期保护层：业务链路退出时不把 UU 留在前台。"""
    try:
        yield
    finally:
        _minimize_uu_window_best_effort(context)


def _require_template(template: Path) -> None:
    """确保模板文件存在。"""
    if not template.exists():
        raise RuntimeError(f"required template not found: {template}")


def _try_locate_image(
    template: Path,
    timeout: float = IMAGE_SEARCH_TIMEOUT,
    interval: float = IMAGE_RETRY_INTERVAL,
) -> tuple[int, int] | None:
    """在屏幕上尝试定位模板，找不到时返回 None，不保存调试截图。"""
    _require_template(template)
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        try:
            location = pyautogui.locateOnScreen(
                str(template),
                confidence=IMAGE_CONFIDENCE,
            )
            if location is not None:
                center = pyautogui.center(location)
                return center.x, center.y
        except pyautogui.ImageNotFoundException:
            pass
        time.sleep(interval)
    return None


def _wait_for_image(
    template: Path,
    *,
    step_name: str,
    timeout: float,
    initial_delay: float = 0.0,
    interval: float = IMAGE_RETRY_INTERVAL,
) -> tuple[int, int]:
    """按业务步骤等待图片；内部保留 0.25s 高频轮询，失败时保存证据。"""
    _require_template(template)

    if initial_delay > 0:
        log.info(
            "waiting %.2fs before polling %s for step=%s",
            initial_delay,
            template.name,
            step_name,
        )
        time.sleep(initial_delay)

    log.info(
        "polling %s every %.2fs for up to %.1fs for step=%s",
        template.name,
        interval,
        timeout,
        step_name,
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            location = pyautogui.locateOnScreen(
                str(template),
                confidence=IMAGE_CONFIDENCE,
            )
            if location is not None:
                center = pyautogui.center(location)
                return center.x, center.y
        except pyautogui.ImageNotFoundException:
            pass
        time.sleep(interval)

    raise _startup_error(
        step_name,
        f"cannot locate template {template.name} within {timeout:.1f}s",
    )


def _dismiss_uu_update_popup_once(context: str) -> bool:
    """识别并关闭 UU 更新确认弹窗；未出现时返回 False。"""
    notice_pos = _try_locate_image(
        TPL_UPDATE_NOTICE,
        timeout=UU_POPUP_DETECT_TIMEOUT,
    )
    if notice_pos is None:
        return False

    log.info(
        "known UU update popup detected during %s at (%d, %d)",
        context,
        notice_pos[0],
        notice_pos[1],
    )

    for action_name, template in UU_UPDATE_POPUP_ACTIONS:
        action_pos = _try_locate_image(
            template,
            timeout=UU_POPUP_ACTION_TIMEOUT,
        )
        if action_pos is None:
            continue

        _click(action_pos)
        log.info(
            "known UU update popup %s clicked during %s at (%d, %d)",
            action_name,
            context,
            action_pos[0],
            action_pos[1],
        )
        time.sleep(UU_POPUP_SETTLE_DELAY)
        return True

    screenshot = _save_debug_screenshot("uu_update_popup_action_not_found")
    raise UuStartupError(
        "dismiss_uu_update_popup",
        "known UU update popup detected during "
        f"{context}, but neither confirm nor close button was found",
        screenshot_path=screenshot,
    )


def _dismiss_known_uu_popups(context: str) -> None:
    """统一处理会遮挡 UU 识图链路的已知弹窗。"""
    dismissed = 0
    for _ in range(UU_POPUP_MAX_DISMISSALS):
        if not _dismiss_uu_update_popup_once(context):
            if dismissed:
                log.info(
                    "known UU popups cleared during %s after %d action(s)",
                    context,
                    dismissed,
                )
            return
        dismissed += 1

    screenshot = _save_debug_screenshot("uu_update_popup_still_present")
    raise UuStartupError(
        "dismiss_known_uu_popups",
        "known UU update popup remained after "
        f"{dismissed} dismiss attempt(s) during {context}",
        screenshot_path=screenshot,
    )


def _move_mouse_to(position: tuple[int, int]) -> None:
    """将鼠标移动到指定位置。"""
    pyautogui.moveTo(position[0], position[1], duration=0.2)
    log.info("mouse moved to (%d, %d)", position[0], position[1])


def _click(position: tuple[int, int]) -> None:
    """点击指定位置。"""
    pyautogui.click(position[0], position[1])
    log.info("clicked at (%d, %d)", position[0], position[1])


def _run_uu_startup_attempt(attempt_no: int) -> None:
    """执行一轮 UU 启动链路；失败交给外层 supervisor 决定是否重启。"""
    log.info("UU startup attempt %d started", attempt_no)
    _require_supported_display()
    uu_was_running = _is_uu_running()
    _ensure_uu_started()

    try:
        title = _focus_uu_window()
    except RuntimeError as exc:
        raise _startup_error(
            "focus_uu_window",
            str(exc),
        ) from exc
    log.info("UU window focused: %s", title)
    _dismiss_known_uu_popups("startup focus")

    if uu_was_running:
        log.info(
            "UU was already running, checking %s for reusable acceleration state",
            TPL_STEP_3.name,
        )
        _dismiss_known_uu_popups("reuse acceleration check")
        confirm_pos = _try_locate_image(
            TPL_STEP_3,
            timeout=REUSE_CONFIRM_TIMEOUT,
        )
        if confirm_pos is not None:
            log.info(
                "existing acceleration confirmed at (%d, %d), skipping step 1/2",
                confirm_pos[0],
                confirm_pos[1],
            )
            return
        log.info("existing UU session is not in accelerated state, running full startup chain")

    first_target = _wait_for_image(
        TPL_STEP_1,
        step_name="locate_startup_move_target",
        initial_delay=STEP_1_INITIAL_DELAY,
        timeout=STEP_1_TIMEOUT,
    )
    _move_mouse_to(first_target)

    log.info("waiting %.1fs before second step", POST_MOVE_DELAY)
    time.sleep(POST_MOVE_DELAY)
    _dismiss_known_uu_popups("before second startup step")

    second_target = _wait_for_image(
        TPL_STEP_2,
        step_name="locate_startup_click_target",
        timeout=STEP_2_TIMEOUT,
    )
    _click(second_target)

    log.info("waiting %ds for acceleration to take effect", POST_CLICK_WAIT)
    time.sleep(POST_CLICK_WAIT)
    _dismiss_known_uu_popups("before acceleration confirmation")

    confirm_pos = _wait_for_image(
        TPL_STEP_3,
        step_name="confirm_acceleration",
        timeout=CONFIRM_TIMEOUT,
    )
    log.info("acceleration confirmed at (%d, %d)", confirm_pos[0], confirm_pos[1])

    log.info("UU startup chain completed")


def ensure_uu_connected() -> int:
    """正式 UU 启动链路：最多重启 UU 3 次，总共执行 4 轮。"""
    _require_admin()
    max_attempts = UU_STARTUP_MAX_RESTARTS + 1
    last_error: UuStartupError | None = None
    restarts_used = 0

    with _keep_uu_in_background_on_exit("startup chain exit"):
        for attempt in range(1, max_attempts + 1):
            log.info(
                "UU startup supervisor attempt %d/%d; max_restarts=%d",
                attempt,
                max_attempts,
                UU_STARTUP_MAX_RESTARTS,
            )
            try:
                _run_uu_startup_attempt(attempt)
                return restarts_used
            except UuStartupError as exc:
                last_error = exc
                log.warning(
                    (
                        "UU startup attempt %d/%d failed; "
                        "retryable=%s; step=%s; reason=%s; screenshot=%s"
                    ),
                    attempt,
                    max_attempts,
                    exc.retryable,
                    exc.step_name,
                    exc.reason,
                    exc.screenshot_path,
                )

                if not exc.retryable:
                    exc.restarts_used = restarts_used
                    raise

                if attempt >= max_attempts:
                    break

                log.info(
                    "restarting UU before next startup attempt; restart %d/%d",
                    attempt,
                    UU_STARTUP_MAX_RESTARTS,
                )
                kill_uu()
                restarts_used += 1
                log.info("waiting %.1fs before retry", UU_RESTART_DELAY)
                time.sleep(UU_RESTART_DELAY)

        if last_error is None:
            raise RuntimeError("UU startup failed without a captured error")
        raise UuStartupFinalError(last_error, restarts_used)


def stop_uu_acceleration() -> None:
    """正式 UU 收尾链路：点击已加速状态下的停止加速按钮。"""
    _require_admin()

    if not _is_uu_running():
        log.info("UU accelerator is not running; acceleration is already stopped")
        return

    with _keep_uu_in_background_on_exit("stop acceleration exit"):
        title = _focus_uu_window()
        log.info("UU window focused: %s", title)
        _dismiss_known_uu_popups("stop acceleration focus")

        stop_target = _wait_for_image(
            TPL_STEP_3,
            step_name="locate_stop_acceleration_button",
            timeout=STOP_ACCELERATION_TIMEOUT,
        )
        _click(stop_target)
        log.info("UU acceleration stop button clicked")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="UU 加速器控制")
    parser.add_argument(
        "action",
        nargs="?",
        default="start",
        choices=["start", "disconnect", "minimize", "stop"],
        help=(
            "start: 启动并验证加速; disconnect: 点击停止加速; "
            "minimize: 最小化 UU 窗口; stop: 关闭 UU 进程 (default: start)"
        ),
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        help="optional log file path for manual GUI test evidence",
    )
    args = parser.parse_args()

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if args.log_file:
        args.log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(args.log_file, encoding="utf-8"))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        handlers=handlers,
    )

    try:
        if args.action == "disconnect":
            stop_uu_acceleration()
        elif args.action == "minimize":
            minimize_uu_window()
        elif args.action == "stop":
            kill_uu()
        else:
            ensure_uu_connected()
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
