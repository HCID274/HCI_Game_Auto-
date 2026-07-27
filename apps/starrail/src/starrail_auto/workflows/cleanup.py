"""Cleanup workflow: close the game and stop UU acceleration."""

import logging
import time
from datetime import datetime
from pathlib import Path

import psutil

from starrail_auto.integrations.feishu import notify_starrail_failure
from starrail_auto.settings import LOGS_DIR
from starrail_auto.uu.service import stop_uu_acceleration

GAME_PROCESS_NAMES = {"starrail.exe"}
M7A_PROCESS_NAMES = {"march7th launcher.exe", "march7th assistant.exe"}

EXIT_OK = 0
EXIT_PROCESS_CLOSE_FAILED = 40
EXIT_UU_DISCONNECT_FAILED = 41

log = logging.getLogger("cleanup_runner")


def _setup_logging(log_file: Path | None = None) -> None:
    """配置收尾任务日志。"""
    LOGS_DIR.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    resolved_log_file = log_file or (LOGS_DIR / f"{today}.log")
    resolved_log_file.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s %(message)s")

    file_handler = logging.FileHandler(resolved_log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console_handler)


def _matching_processes(names: set[str]) -> list[psutil.Process]:
    """按进程名精确匹配目标进程。"""
    matched: list[psutil.Process] = []
    lowered_names = {name.casefold() for name in names}
    current_pid = psutil.Process().pid

    for proc in psutil.process_iter(["name", "pid"]):
        if proc.info["pid"] == current_pid:
            continue
        name = (proc.info["name"] or "").casefold()
        if name in lowered_names:
            matched.append(proc)
    return matched


def _terminate_processes(names: set[str], label: str) -> bool:
    """关闭一组进程；不存在时视为已经关闭。"""
    targets = _matching_processes(names)
    if not targets:
        log.info("no %s processes found", label)
        return True

    for proc in targets:
        try:
            log.info("terminating %s: %s (pid=%d)", label, proc.name(), proc.pid)
            proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            log.warning("cannot terminate %s (pid=%d): %s", label, proc.pid, exc)

    gone, alive = psutil.wait_procs(targets, timeout=10)
    for proc in gone:
        log.info("%s exited: pid=%d", label, proc.pid)

    for proc in alive:
        try:
            log.warning("force-killing %s: %s (pid=%d)", label, proc.name(), proc.pid)
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            log.error("cannot kill %s (pid=%d): %s", label, proc.pid, exc)

    _, still_alive = psutil.wait_procs(alive, timeout=5)
    if still_alive:
        for proc in still_alive:
            log.error("%s still alive after kill: pid=%d", label, proc.pid)
        return False
    return True


def run(delay: int = 0) -> int:
    """执行收尾：可选延迟 -> 关闭游戏/M7A -> 停止 UU 加速。"""
    if delay > 0:
        log.info("cleanup scheduled after %ds", delay)
        time.sleep(delay)

    ok = True
    ok = _terminate_processes(GAME_PROCESS_NAMES, "game") and ok
    ok = _terminate_processes(M7A_PROCESS_NAMES, "M7A") and ok
    if not ok:
        return EXIT_PROCESS_CLOSE_FAILED

    try:
        stop_uu_acceleration()
    except RuntimeError as exc:
        log.error("UU acceleration stop failed: %s", exc)
        return EXIT_UU_DISCONNECT_FAILED

    log.info("cleanup completed")
    return EXIT_OK


def execute_cleanup(delay: int = 0, log_file: Path | None = None) -> int:
    """Configure logging, execute cleanup, and send failure-only notification."""
    _setup_logging(log_file)
    exit_code = run(delay=delay)
    if exit_code == EXIT_PROCESS_CLOSE_FAILED:
        notify_starrail_failure("清理", 0)
    elif exit_code == EXIT_UU_DISCONNECT_FAILED:
        notify_starrail_failure("UU清理", 0)
    return exit_code
