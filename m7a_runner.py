"""核心执行器 — UU 加速 + M7A 启动 + 看门狗监控."""

import argparse
import ctypes
import ipaddress
import logging
import re
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psutil

from feishu_notify import notify_starrail_failure, notify_starrail_success
from reporting.report_service import report_main_run
from uu_accel import UuStartupError, UuStartupFinalError, ensure_uu_connected

# ── 配置 ──────────────────────────────────────────────
M7A_LAUNCHER = Path(
    r"D:\2_Software\4_Games\StarRail\Auto\March7thAssistant_full\March7th Launcher.exe"
)
M7A_LOG_DIR = Path(
    r"D:\2_Software\4_Games\StarRail\Auto\March7thAssistant_full\logs"
)
LOGS_DIR = Path(__file__).parent / "logs"
DEBUG_DIR = Path(__file__).parent / "debug"

# 看门狗参数
GRACE_PERIOD = 60          # 启动宽限期（秒）
CPU_IDLE_THRESHOLD = 2.0   # CPU 使用率阈值（%）
CPU_IDLE_WINDOW = 900      # CPU 空闲持续时间（秒）= 15 分钟
LOG_HEARTBEAT_TIMEOUT = 600  # 日志无更新超时（秒）= 10 分钟
WATCHDOG_INTERVAL = 30     # 看门狗检查间隔（秒）
DAILY_RESULT_POLL_INTERVAL = 5  # 每日实训结果检查间隔（秒）
GAME_READY_TIMEOUT = 120   # 游戏启动确认超时（秒）
GAME_READY_INTERVAL = 2    # 游戏启动确认轮询间隔（秒）
GAME_NETWORK_HOST = "hsr.hoyoverse.com"
GAME_NETWORK_PORT = 443
GAME_NETWORK_TIMEOUT = 10

# 默认超时
DEFAULT_TIMEOUTS = {
    "universe": 7200,
    "main": 1800,
}

GAME_PROCESS_NAMES = {"starrail.exe"}
GAME_WINDOW_KEYWORDS = ("崩坏：星穹铁道",)
M7A_ASSISTANT_PROCESS_NAME = "march7th assistant.exe"
M7A_RUNTIME_DISCOVERY_TIMEOUT = 15
M7A_RUNTIME_DISCOVERY_INTERVAL = 0.5
M7A_DAILY_COMPLETION_MARKER = "每日实训已完成"
M7A_DAILY_INCOMPLETE_MARKER = "每日实训未完成"
M7A_COMPLETION_VALIDATION_TIMEOUT = 15
M7A_COMPLETION_VALIDATION_INTERVAL = 0.5
M7A_DAILY_SCORE_PATTERN = re.compile(r"当前(?:累计)?分数[：:]\s*(\d+)\s*/\s*(\d+)")
M7A_DAILY_BLOCKER_PATTERN = re.compile(r"任务无法完成:\s*(.+)")
M7A_DAILY_BLOCKER_LABELS = (
    ("累计消耗120点开拓力", "体力"),
    ("使用支援角色", "支援"),
    ("差分宇宙", "差分"),
    ("货币战争", "差分"),
    ("万能合成机", "合成"),
    ("派遣委托", "委托"),
)

# 统一退出码
EXIT_OK = 0
EXIT_UU_FAILED = 10
EXIT_M7A_LAUNCH_FAILED = 20
EXIT_GAME_READY_TIMEOUT = 21
EXIT_M7A_EXIT_NONZERO = 22
EXIT_DAILY_VALIDATION_FAILED = 23
EXIT_GAME_NETWORK_FAILED = 24
EXIT_WATCHDOG_HARD_TIMEOUT = 30
EXIT_WATCHDOG_CPU_IDLE = 31
EXIT_WATCHDOG_LOG_STALLED = 32

log = logging.getLogger("m7a_runner")


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    stage: str = ""
    retries: int = 0
    report_log_path: Path | None = None
    report_log_offset: int = 0


@dataclass(frozen=True)
class M7ALogCheckpoint:
    path: Path
    offset: int

# ── 日志配置 ───────────────────────────────────────────

def _setup_logging() -> None:
    """按天写日志到 logs/ 目录."""
    LOGS_DIR.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = LOGS_DIR / f"{today}.log"

    formatter = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s %(message)s")

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console_handler)


# ── 看门狗 ─────────────────────────────────────────────

def _get_m7a_latest_log() -> Path | None:
    """获取 M7A 最新日志文件."""
    if not M7A_LOG_DIR.exists():
        return None
    logs = sorted(M7A_LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return logs[0] if logs else None


def _capture_today_m7a_log_checkpoint() -> M7ALogCheckpoint:
    """Capture today's M7A log offset so validation cannot reuse an older run."""
    path = M7A_LOG_DIR / f"{datetime.now():%Y-%m-%d}.log"
    offset = path.stat().st_size if path.exists() else 0
    log.info("captured M7A log checkpoint: path=%s offset=%d", path, offset)
    return M7ALogCheckpoint(path=path, offset=offset)


def _read_m7a_log_since(checkpoint: M7ALogCheckpoint) -> str:
    if not checkpoint.path.exists():
        return ""

    with checkpoint.path.open("rb") as handle:
        current_size = checkpoint.path.stat().st_size
        handle.seek(checkpoint.offset if current_size >= checkpoint.offset else 0)
        return handle.read().decode("utf-8", errors="replace")


def _wait_for_daily_completion(checkpoint: M7ALogCheckpoint) -> bool:
    """Require the current run to emit the daily-training completion marker."""
    deadline = time.monotonic() + M7A_COMPLETION_VALIDATION_TIMEOUT
    while time.monotonic() < deadline:
        if M7A_DAILY_COMPLETION_MARKER in _read_m7a_log_since(checkpoint):
            log.info(
                "daily completion validated from current M7A log: %s",
                M7A_DAILY_COMPLETION_MARKER,
            )
            return True
        time.sleep(M7A_COMPLETION_VALIDATION_INTERVAL)

    log.error(
        "daily completion validation failed: marker=%s path=%s offset=%d",
        M7A_DAILY_COMPLETION_MARKER,
        checkpoint.path,
        checkpoint.offset,
    )
    return False


def _daily_run_outcome(checkpoint: M7ALogCheckpoint) -> str | None:
    """Return a terminal daily result from only this M7A run's new log lines."""
    content = _read_m7a_log_since(checkpoint)
    if M7A_DAILY_COMPLETION_MARKER in content:
        return "completed"
    if M7A_DAILY_INCOMPLETE_MARKER in content:
        return "incomplete"
    return None


def _summarize_daily_failure(checkpoint: M7ALogCheckpoint) -> str:
    """Build a concise score/blocker label from this run's new M7A log."""
    content = _read_m7a_log_since(checkpoint)
    score_matches = M7A_DAILY_SCORE_PATTERN.findall(content)
    score = f"{score_matches[-1][0]}/{score_matches[-1][1]}" if score_matches else "未达标"

    blockers: list[str] = []
    for raw_blocker in M7A_DAILY_BLOCKER_PATTERN.findall(content):
        for marker, label in M7A_DAILY_BLOCKER_LABELS:
            if marker in raw_blocker and label not in blockers:
                blockers.append(label)
                break

    summary = f"实训{score}"
    if blockers:
        summary = f"{summary} 卡{'/'.join(blockers)}"
    log.info("daily failure summary: %s", summary)
    return summary


def _stage_for_exit_code(exit_code: int, checkpoint: M7ALogCheckpoint) -> str:
    """Resolve the notification stage without evaluating failure-only work."""
    if exit_code == EXIT_DAILY_VALIDATION_FAILED:
        return _summarize_daily_failure(checkpoint)
    return {
        EXIT_OK: "",
        EXIT_M7A_EXIT_NONZERO: "M7A",
        EXIT_GAME_NETWORK_FAILED: "网络代理",
        EXIT_WATCHDOG_HARD_TIMEOUT: "超时",
        EXIT_WATCHDOG_CPU_IDLE: "CPU",
        EXIT_WATCHDOG_LOG_STALLED: "日志",
    }.get(exit_code, "看门狗")


def _capture_failure_evidence(reason: str, checkpoint: M7ALogCheckpoint | None) -> None:
    """Preserve the game screen and current M7A log without changing the scene."""
    DEBUG_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshot_path = DEBUG_DIR / f"m7a_failure_{stamp}.png"
    log_path = DEBUG_DIR / f"m7a_failure_{stamp}.log"

    try:
        from PIL import ImageGrab

        ImageGrab.grab(all_screens=True).save(screenshot_path)
    except Exception as exc:  # pragma: no cover - requires an interactive desktop
        log.warning("failed to capture watchdog screenshot: %s", exc)
        screenshot_path = None

    try:
        content = _read_m7a_log_since(checkpoint) if checkpoint else ""
        log_path.write_text(
            f"reason={reason}\n\n{content}",
            encoding="utf-8",
        )
    except OSError as exc:
        log.warning("failed to save watchdog log evidence: %s", exc)
        log_path = None

    log.warning(
        "failure evidence preserved: reason=%s screenshot=%s log=%s",
        reason,
        screenshot_path,
        log_path,
    )


def _stop_assistant_for_evidence(proc: subprocess.Popen | psutil.Process) -> None:
    """Stop only the automation leaf process; preserve game and Launcher evidence."""
    if not isinstance(proc, psutil.Process):
        log.warning("Assistant PID is unavailable; preserving Launcher and game unchanged")
        return

    try:
        if proc.name().casefold() != M7A_ASSISTANT_PROCESS_NAME:
            log.warning(
                "watchdog target is not Assistant (pid=%d); preserving all processes",
                proc.pid,
            )
            return
        proc.kill()
        log.info("stopped M7A Assistant only for evidence preservation: pid=%d", proc.pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
        log.warning("cannot stop M7A Assistant for evidence preservation: %s", exc)


def _iter_visible_window_titles() -> list[str]:
    """枚举当前桌面的可见窗口标题。"""
    titles: list[str] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def enum_windows_proc(hwnd: int, lparam: int) -> bool:
        if not ctypes.windll.user32.IsWindowVisible(hwnd):
            return True

        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True

        buffer = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buffer, len(buffer))
        title = buffer.value.strip()
        if title:
            titles.append(title)
        return True

    ctypes.windll.user32.EnumWindows(enum_windows_proc, 0)
    return titles


def _is_game_process_running() -> bool:
    """检查游戏进程是否已出现。"""
    for proc in psutil.process_iter(["name"]):
        name = (proc.info["name"] or "").casefold()
        if name in GAME_PROCESS_NAMES:
            return True
    return False


def _is_game_window_present() -> bool:
    """检查游戏窗口是否已出现。"""
    for title in _iter_visible_window_titles():
        lowered = title.casefold()
        if any(keyword.casefold() in lowered for keyword in GAME_WINDOW_KEYWORDS):
            return True
    return False


def _is_game_network_ready(
    *,
    resolver: object = socket.getaddrinfo,
) -> bool:
    """Reject TUN/fake DNS answers before the game starts its login flow."""
    try:
        addresses = resolver(GAME_NETWORK_HOST, GAME_NETWORK_PORT, type=socket.SOCK_STREAM)
    except OSError as exc:
        log.error("game DNS preflight failed for %s: %s", GAME_NETWORK_HOST, exc)
        return False

    resolved_ips: list[str] = []
    for address in addresses:
        ip = address[4][0]
        if ip in resolved_ips:
            continue
        resolved_ips.append(ip)
        try:
            if ipaddress.ip_address(ip).is_global:
                log.info("game DNS preflight passed: host=%s ip=%s", GAME_NETWORK_HOST, ip)
                return True
        except ValueError:
            continue

    log.error(
        "game DNS preflight rejected non-public addresses: host=%s addresses=%s",
        GAME_NETWORK_HOST,
        resolved_ips,
    )
    return False


def _check_game_network() -> bool:
    """Verify the game endpoint is both publicly resolved and reachable."""
    if not _is_game_network_ready():
        return False

    try:
        with socket.create_connection(
            (GAME_NETWORK_HOST, GAME_NETWORK_PORT),
            timeout=GAME_NETWORK_TIMEOUT,
        ):
            log.info(
                "game TCP preflight passed: host=%s port=%d",
                GAME_NETWORK_HOST,
                GAME_NETWORK_PORT,
            )
            return True
    except OSError as exc:
        log.error(
            "game TCP preflight failed: host=%s port=%d error=%s",
            GAME_NETWORK_HOST,
            GAME_NETWORK_PORT,
            exc,
        )
        return False


def _wait_for_game_ready(
    timeout: int = GAME_READY_TIMEOUT,
    *,
    process_check: object = _is_game_process_running,
    window_check: object = _is_game_window_present,
) -> bool:
    """Wait for both the game process and its visible window before monitoring."""
    deadline = time.monotonic() + timeout

    log.info(
        "waiting up to %ds for game process and visible window to appear",
        timeout,
    )
    while time.monotonic() < deadline:
        process_ready = process_check()
        window_ready = window_check()
        if process_ready and window_ready:
            log.info(
                "game ready: process=%s, window=%s",
                process_ready,
                window_ready,
            )
            return True
        time.sleep(GAME_READY_INTERVAL)

    return False


def _poll_process(proc: subprocess.Popen | psutil.Process) -> int | None:
    """Poll a subprocess or a process discovered after launcher forwarding."""
    if isinstance(proc, subprocess.Popen):
        return proc.poll()

    try:
        if not proc.is_running():
            return 0
        exit_code = proc.wait(timeout=0)
        # psutil can return None on Windows when the process has exited but its
        # exit code is unavailable. Reaching this line still means wait ended.
        return 0 if exit_code is None else exit_code
    except psutil.TimeoutExpired:
        return None
    except psutil.NoSuchProcess:
        return 0


def _find_new_m7a_assistant(started_after: float) -> psutil.Process | None:
    """Find the Assistant process created for the current M7A run."""
    deadline = time.monotonic() + M7A_RUNTIME_DISCOVERY_TIMEOUT
    while time.monotonic() < deadline:
        candidates: list[psutil.Process] = []
        for proc in psutil.process_iter(["name", "create_time"]):
            name = (proc.info["name"] or "").casefold()
            created_at = proc.info["create_time"] or 0
            if name == M7A_ASSISTANT_PROCESS_NAME and created_at >= started_after - 2:
                candidates.append(proc)

        if candidates:
            return max(candidates, key=lambda item: item.create_time())
        time.sleep(M7A_RUNTIME_DISCOVERY_INTERVAL)
    return None


def _hard_timeout_for_task(task: str, timeout: int) -> int | None:
    """Main ends on daily status; only standalone long-running tasks keep a cap."""
    return None if task == "main" else timeout


def _watchdog(
    proc: subprocess.Popen | psutil.Process,
    hard_timeout: int | None,
    *,
    checkpoint: M7ALogCheckpoint | None = None,
    stop_when_daily_resolved: bool = False,
) -> int:
    """看门狗监控 M7A 进程。返回统一退出码。"""
    start_time = time.monotonic()
    cpu_idle_since: float | None = None

    log.info(
        "watchdog started: hard_timeout=%s, grace=%ds, daily_result_exit=%s",
        hard_timeout,
        GRACE_PERIOD,
        stop_when_daily_resolved,
    )

    while True:
        if stop_when_daily_resolved and checkpoint is not None:
            daily_outcome = _daily_run_outcome(checkpoint)
            if daily_outcome == "completed":
                log.info("daily completion observed while M7A continues secondary tasks")
                return EXIT_OK
            if daily_outcome == "incomplete":
                log.error("daily incomplete observed before M7A exited")
                return EXIT_DAILY_VALIDATION_FAILED

        # 进程已退出
        ret = _poll_process(proc)
        if ret is not None:
            log.info("M7A exited with code %d", ret)
            return EXIT_OK if ret == 0 else EXIT_M7A_EXIT_NONZERO

        elapsed = time.monotonic() - start_time
        in_grace = elapsed < GRACE_PERIOD

        # 硬超时
        if hard_timeout is not None and elapsed >= hard_timeout:
            log.warning("HARD TIMEOUT reached (%ds), preserving evidence", hard_timeout)
            _capture_failure_evidence("hard_timeout", checkpoint)
            _stop_assistant_for_evidence(proc)
            return EXIT_WATCHDOG_HARD_TIMEOUT

        if not in_grace:
            # CPU 空闲检测
            try:
                p = psutil.Process(proc.pid)
                cpu = p.cpu_percent(interval=1)
                if cpu < CPU_IDLE_THRESHOLD:
                    if cpu_idle_since is None:
                        cpu_idle_since = time.monotonic()
                    elif time.monotonic() - cpu_idle_since >= CPU_IDLE_WINDOW:
                        log.warning(
                            "CPU idle for %ds (<%s%%), preserving evidence",
                            CPU_IDLE_WINDOW, CPU_IDLE_THRESHOLD,
                        )
                        _capture_failure_evidence("cpu_idle", checkpoint)
                        _stop_assistant_for_evidence(proc)
                        return EXIT_WATCHDOG_CPU_IDLE
                else:
                    cpu_idle_since = None
            except psutil.NoSuchProcess:
                continue

            # 日志心跳检测
            m7a_log = _get_m7a_latest_log()
            if m7a_log:
                last_modified = m7a_log.stat().st_mtime
                if time.time() - last_modified > LOG_HEARTBEAT_TIMEOUT:
                    log.warning(
                        "M7A log not updated for %ds, preserving evidence",
                        LOG_HEARTBEAT_TIMEOUT,
                    )
                    _capture_failure_evidence("log_stalled", checkpoint)
                    _stop_assistant_for_evidence(proc)
                    return EXIT_WATCHDOG_LOG_STALLED

        time.sleep(
            DAILY_RESULT_POLL_INTERVAL if stop_when_daily_resolved else WATCHDOG_INTERVAL
        )


# ── 主流程 ─────────────────────────────────────────────

def run(task: str, timeout: int) -> RunResult:
    """执行完整流程：UU加速 → M7A启动 → 看门狗监控."""
    hard_timeout = _hard_timeout_for_task(task, timeout)
    log.info("=== task: %s, hard_timeout: %s ===", task, hard_timeout)
    uu_retries = 0

    # 1. TUN fake-DNS/proxy failures otherwise surface only as a game login popup.
    if not _check_game_network():
        return RunResult(EXIT_GAME_NETWORK_FAILED, stage="网络代理")

    # 2. 确保 UU 加速器已连接
    try:
        uu_retries = ensure_uu_connected()
    except UuStartupFinalError as e:
        log.error("UU acceleration failed: %s", e)
        return RunResult(EXIT_UU_FAILED, stage="UU", retries=e.restarts_used)
    except UuStartupError as e:
        log.error("UU acceleration failed: %s", e)
        return RunResult(EXIT_UU_FAILED, stage="UU", retries=e.restarts_used)
    except RuntimeError as e:
        log.error("UU acceleration failed: %s", e)
        return RunResult(EXIT_UU_FAILED, stage="UU", retries=uu_retries)

    # Check again after UU changes the game traffic path.
    if not _check_game_network():
        return RunResult(EXIT_GAME_NETWORK_FAILED, stage="网络代理", retries=uu_retries)

    # 3. 启动 M7A
    # 当前版本实测需要走 Launcher.exe 才能正确接收任务参数并启动游戏
    # 注意：需要管理员权限，Windows 任务计划程序设置"使用最高权限运行"
    exe = M7A_LAUNCHER
    cmd = [str(exe), task, "-e"]
    log.info("launching M7A: %s", " ".join(cmd))
    launch_started_at = time.time()
    log_checkpoint = _capture_today_m7a_log_checkpoint()
    try:
        proc = subprocess.Popen(cmd)
    except OSError as exc:
        log.error("failed to launch M7A: %s", exc)
        return RunResult(
            EXIT_M7A_LAUNCH_FAILED,
            stage="M7A启动",
            retries=uu_retries,
            report_log_path=log_checkpoint.path,
            report_log_offset=log_checkpoint.offset,
        )
    log.info("M7A started (pid=%d)", proc.pid)

    # 4. 启动成功判定：游戏进程和可见窗口都必须出现。
    if not _wait_for_game_ready():
        log.error("game was not detected within %ds", GAME_READY_TIMEOUT)
        _capture_failure_evidence("game_window_timeout", log_checkpoint)
        return RunResult(
            EXIT_GAME_READY_TIMEOUT,
            stage="游戏检测",
            retries=uu_retries,
            report_log_path=log_checkpoint.path,
            report_log_offset=log_checkpoint.offset,
        )

    # 5. 看门狗监控：优先监控实际执行自动化的 Assistant。
    assistant = _find_new_m7a_assistant(launch_started_at)
    watchdog_target: subprocess.Popen | psutil.Process = assistant or proc
    if assistant is not None:
        log.info(
            "watchdog target switched to M7A Assistant (pid=%d); launcher pid=%d",
            assistant.pid,
            proc.pid,
        )
    else:
        log.info("M7A Assistant not discovered; watchdog keeps launcher pid=%d", proc.pid)

    exit_code = _watchdog(
        watchdog_target,
        hard_timeout,
        checkpoint=log_checkpoint,
        stop_when_daily_resolved=(task == "main"),
    )
    if exit_code == EXIT_OK and task == "main":
        # The daily marker is the main task's success boundary. The assistant may
        # legitimately continue with optional secondary content afterwards.
        if _daily_run_outcome(log_checkpoint) != "completed" and not _wait_for_daily_completion(
            log_checkpoint
        ):
            return RunResult(
                EXIT_DAILY_VALIDATION_FAILED,
                stage=_summarize_daily_failure(log_checkpoint),
                retries=uu_retries,
                report_log_path=log_checkpoint.path,
                report_log_offset=log_checkpoint.offset,
            )

    stage = _stage_for_exit_code(exit_code, log_checkpoint)
    return RunResult(
        exit_code,
        stage=stage,
        retries=uu_retries,
        report_log_path=log_checkpoint.path,
        report_log_offset=log_checkpoint.offset,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Star Rail automation runner")
    parser.add_argument(
        "task",
        choices=["universe", "main"],
        help="task to run",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="hard timeout in seconds for universe only (default: per-task)",
    )
    args = parser.parse_args()

    _setup_logging()

    timeout = args.timeout or DEFAULT_TIMEOUTS.get(args.task, 1800)
    started_at = datetime.now()
    result = run(args.task, timeout)

    if args.task == "main":
        try:
            report_main_run(
                log_path=result.report_log_path,
                offset=result.report_log_offset,
                started_at=started_at,
                exit_code=result.exit_code,
                stage=result.stage,
                retries=result.retries,
            )
        except Exception:
            log.exception("final report service failed")
            if result.exit_code == EXIT_OK:
                notify_starrail_success(result.retries)
            else:
                notify_starrail_failure(result.stage or "未知", result.retries)
    elif result.exit_code == EXIT_OK:
        notify_starrail_success(result.retries)
    else:
        notify_starrail_failure(result.stage or "未知", result.retries)

    log.info("=== finished with code %d ===", result.exit_code)
    sys.exit(result.exit_code)


if __name__ == "__main__":
    main()
