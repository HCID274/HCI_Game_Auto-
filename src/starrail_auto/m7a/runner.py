"""Launch M7A and translate its observed lifecycle into a run result."""

import logging
import subprocess
import time

from starrail_auto.m7a.config import (
    EXIT_DAILY_VALIDATION_FAILED,
    EXIT_GAME_READY_TIMEOUT,
    EXIT_M7A_LAUNCH_FAILED,
    EXIT_OK,
    M7A_LAUNCHER,
)
from starrail_auto.m7a.environment import wait_for_game_ready
from starrail_auto.m7a.logs import (
    capture_failure_evidence,
    capture_log_checkpoint,
    daily_run_outcome,
    stage_for_exit_code,
    summarize_daily_failure,
    wait_for_daily_completion,
)
from starrail_auto.m7a.models import RunResult
from starrail_auto.m7a.watchdog import find_new_assistant, hard_timeout_for_task, watch

log = logging.getLogger(__name__)


def run_m7a(task: str, timeout: int, *, uu_retries: int = 0) -> RunResult:
    hard_timeout = hard_timeout_for_task(task, timeout)
    checkpoint = capture_log_checkpoint()
    command = [str(M7A_LAUNCHER), task, "-e"]
    launch_started_at = time.time()
    try:
        launcher = subprocess.Popen(command)
    except OSError as exc:
        log.error("failed to launch M7A: %s", exc)
        return RunResult(
            EXIT_M7A_LAUNCH_FAILED,
            stage="M7A启动",
            retries=uu_retries,
            report_log_path=checkpoint.path,
            report_log_offset=checkpoint.offset,
        )
    log.info("M7A launcher started: pid=%d task=%s", launcher.pid, task)

    if not wait_for_game_ready():
        capture_failure_evidence("game_window_timeout", checkpoint)
        return RunResult(
            EXIT_GAME_READY_TIMEOUT,
            stage="游戏检测",
            retries=uu_retries,
            report_log_path=checkpoint.path,
            report_log_offset=checkpoint.offset,
        )

    assistant = find_new_assistant(launch_started_at)
    target = assistant or launcher
    if assistant:
        log.info("watchdog switched to Assistant pid=%d", assistant.pid)
    else:
        log.warning("Assistant was not discovered; watchdog uses launcher pid=%d", launcher.pid)

    exit_code = watch(
        target,
        hard_timeout,
        checkpoint=checkpoint,
        stop_when_daily_resolved=(task == "main"),
    )
    if exit_code == EXIT_OK and task == "main":
        if daily_run_outcome(checkpoint) != "completed" and not wait_for_daily_completion(checkpoint):
            exit_code = EXIT_DAILY_VALIDATION_FAILED
            stage = summarize_daily_failure(checkpoint)
        else:
            stage = ""
    else:
        stage = stage_for_exit_code(exit_code, checkpoint)

    return RunResult(
        exit_code,
        stage=stage,
        retries=uu_retries,
        report_log_path=checkpoint.path,
        report_log_offset=checkpoint.offset,
    )
