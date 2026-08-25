"""Launch M7A and translate its observed lifecycle into a run result."""

import logging
import subprocess
import time

import psutil

from starrail_auto.m7a.config import (
    EXIT_DAILY_VALIDATION_FAILED,
    EXIT_GAME_READY_TIMEOUT,
    EXIT_M7A_CONFIG_FAILED,
    EXIT_M7A_LAUNCH_FAILED,
    EXIT_OK,
    M7A_ASSISTANT_PROCESS_NAME,
    M7A_LAUNCHER,
    M7A_LAUNCHER_PROCESS_NAME,
)
from starrail_auto.m7a.config_guard import (
    M7AConfigProtectionError,
    M7AConfigSession,
    prepare_m7a_config,
)
from starrail_auto.m7a.disclaimer import M7ADisclaimerHandler
from starrail_auto.m7a.environment import wait_for_game_ready
from starrail_auto.m7a.logs import (
    capture_failure_evidence,
    capture_log_checkpoint,
    daily_run_outcome,
    stage_for_exit_code,
    summarize_daily_failure,
    wait_for_daily_completion,
)
from starrail_auto.m7a.models import M7ALogCheckpoint, RunResult
from starrail_auto.m7a.watchdog import find_new_assistant, hard_timeout_for_task, watch

log = logging.getLogger(__name__)


def _stop_new_m7a_processes(started_at: float) -> None:
    names = {M7A_ASSISTANT_PROCESS_NAME, M7A_LAUNCHER_PROCESS_NAME}
    targets: list[psutil.Process] = []
    for proc in psutil.process_iter(["name", "create_time"]):
        if (proc.info["name"] or "").casefold() not in names:
            continue
        if (proc.info["create_time"] or 0) < started_at - 2:
            continue
        targets.append(proc)
    for proc in targets:
        try:
            proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    _, alive = psutil.wait_procs(targets, timeout=5)
    for proc in alive:
        try:
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


def _config_failure_result(
    checkpoint: M7ALogCheckpoint,
    *,
    retries: int,
) -> RunResult:
    return RunResult(
        EXIT_M7A_CONFIG_FAILED,
        stage="M7A配置保护",
        retries=retries,
        report_log_path=checkpoint.path,
        report_log_offset=checkpoint.offset,
    )


def _startup_ready(
    disclaimer: M7ADisclaimerHandler,
    config_session: M7AConfigSession,
) -> bool:
    ready = disclaimer.poll()
    config_session.verify_live()
    return ready


def run_m7a(task: str, timeout: int, *, uu_retries: int = 0) -> RunResult:
    hard_timeout = hard_timeout_for_task(task, timeout)
    checkpoint = capture_log_checkpoint()
    try:
        config_session = prepare_m7a_config()
    except M7AConfigProtectionError as exc:
        log.error("M7A launch blocked by config preflight: %s", exc)
        capture_failure_evidence("m7a_config_preflight_failed", checkpoint)
        return _config_failure_result(checkpoint, retries=uu_retries)
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

    disclaimer = M7ADisclaimerHandler(started_at=launch_started_at)
    try:
        game_ready = wait_for_game_ready(
            startup_check=lambda: _startup_ready(disclaimer, config_session)
        )
    except M7AConfigProtectionError as exc:
        log.error("M7A stopped after unsafe config mutation: %s", exc)
        capture_failure_evidence("m7a_config_unsafe_change", checkpoint)
        _stop_new_m7a_processes(launch_started_at)
        return _config_failure_result(checkpoint, retries=uu_retries)
    if not game_ready:
        reason = disclaimer.failure_reason or "game_window_timeout"
        capture_failure_evidence(reason, checkpoint)
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
        stop_when_main_resolved=(task == "main"),
    )
    if exit_code == EXIT_OK and task == "main":
        if daily_run_outcome(checkpoint) != "completed" and not wait_for_daily_completion(checkpoint):
            exit_code = EXIT_DAILY_VALIDATION_FAILED
            stage = summarize_daily_failure(checkpoint)
        else:
            stage = ""
    else:
        stage = stage_for_exit_code(exit_code, checkpoint)

    try:
        if exit_code == EXIT_OK:
            config_session.finalize()
        else:
            config_session.verify_live()
    except M7AConfigProtectionError as exc:
        log.error("M7A result rejected by config protection: %s", exc)
        capture_failure_evidence("m7a_config_finalize_failed", checkpoint)
        _stop_new_m7a_processes(launch_started_at)
        return _config_failure_result(checkpoint, retries=uu_retries)

    return RunResult(
        exit_code,
        stage=stage,
        retries=uu_retries,
        report_log_path=checkpoint.path,
        report_log_offset=checkpoint.offset,
    )
