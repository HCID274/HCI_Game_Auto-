"""UU 加速器控制模块 — 正式启动链路."""

import ctypes
import logging
import subprocess
import sys
import time
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

UU_WINDOW_KEYWORDS = ("uu", "网易uu", "uu加速器")
UU_PROCESS_KEYWORDS = ("uu", "uuaccelerator", "uulauncher")

log = logging.getLogger(__name__)


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


def _save_debug_screenshot(prefix: str) -> Path:
    """失败时截图存入 debug/ 目录。"""
    DEBUG_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = DEBUG_DIR / f"{prefix}_{ts}.png"
    pyautogui.screenshot(str(path))
    log.info("debug screenshot saved: %s", path)
    return path


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
    try:
        subprocess.Popen([str(UU_EXE)])
    except OSError as exc:
        raise RuntimeError(f"failed to start UU accelerator: {exc}") from exc


def _ensure_uu_started() -> None:
    """确保 UU 已启动。"""
    if _is_uu_running():
        log.info("UU accelerator already running")
        return

    log.info("starting UU accelerator: %s", UU_EXE)
    _start_uu_process()
    time.sleep(5)
    if not _is_uu_running():
        raise RuntimeError("failed to start UU accelerator")
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


def _require_template(template: Path) -> None:
    """确保模板文件存在。"""
    if not template.exists():
        raise RuntimeError(f"required template not found: {template}")


def _locate_image(
    template: Path,
    timeout: float = IMAGE_SEARCH_TIMEOUT,
    interval: float = IMAGE_RETRY_INTERVAL,
) -> tuple[int, int]:
    """在屏幕上定位图像模板，返回中心坐标。"""
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

    _save_debug_screenshot(f"{template.stem}_not_found")
    raise RuntimeError(f"cannot locate template: {template}")


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


def _wait_and_locate_image(
    template: Path,
    *,
    initial_delay: float = 0.0,
    timeout: float = IMAGE_SEARCH_TIMEOUT,
    interval: float = IMAGE_RETRY_INTERVAL,
) -> tuple[int, int]:
    """先等待宽限期，再按固定频率轮询识图。"""
    if initial_delay > 0:
        log.info(
            "waiting %.2fs before polling %s",
            initial_delay,
            template.name,
        )
        time.sleep(initial_delay)

    log.info(
        "polling %s every %.2fs for up to %.1fs",
        template.name,
        interval,
        timeout,
    )
    return _locate_image(template, timeout=timeout, interval=interval)


def _move_mouse_to(position: tuple[int, int]) -> None:
    """将鼠标移动到指定位置。"""
    pyautogui.moveTo(position[0], position[1], duration=0.2)
    log.info("mouse moved to (%d, %d)", position[0], position[1])


def _click(position: tuple[int, int]) -> None:
    """点击指定位置。"""
    pyautogui.click(position[0], position[1])
    log.info("clicked at (%d, %d)", position[0], position[1])


def ensure_uu_connected() -> None:
    """正式 UU 启动链路：优先复用已加速状态，否则补跑完整识图流程。"""
    _require_admin()
    uu_was_running = _is_uu_running()
    _ensure_uu_started()

    title = _focus_uu_window()
    log.info("UU window focused: %s", title)

    if uu_was_running:
        log.info(
            "UU was already running, checking %s for reusable acceleration state",
            TPL_STEP_3.name,
        )
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

    first_target = _wait_and_locate_image(
        TPL_STEP_1,
        initial_delay=STEP_1_INITIAL_DELAY,
        timeout=STEP_1_TIMEOUT,
    )
    _move_mouse_to(first_target)

    log.info("waiting %.1fs before second step", POST_MOVE_DELAY)
    time.sleep(POST_MOVE_DELAY)

    second_target = _wait_and_locate_image(
        TPL_STEP_2,
        timeout=STEP_2_TIMEOUT,
    )
    _click(second_target)

    log.info("waiting %ds for acceleration to take effect", POST_CLICK_WAIT)
    time.sleep(POST_CLICK_WAIT)

    confirm_pos = _locate_image(TPL_STEP_3, timeout=CONFIRM_TIMEOUT)
    log.info("acceleration confirmed at (%d, %d)", confirm_pos[0], confirm_pos[1])

    log.info("UU startup chain completed")


def stop_uu_acceleration() -> None:
    """正式 UU 收尾链路：点击已加速状态下的停止加速按钮。"""
    _require_admin()

    if not _is_uu_running():
        log.info("UU accelerator is not running; acceleration is already stopped")
        return

    title = _focus_uu_window()
    log.info("UU window focused: %s", title)

    stop_target = _wait_and_locate_image(
        TPL_STEP_3,
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
        choices=["start", "disconnect", "stop"],
        help=(
            "start: 启动并验证加速; disconnect: 点击停止加速; "
            "stop: 关闭 UU 进程 (default: start)"
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
