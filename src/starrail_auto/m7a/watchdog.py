"""M7A process monitoring and evidence-preserving failure policy."""

import logging
import subprocess
import time

import psutil

from starrail_auto.m7a.config import (
    CPU_IDLE_THRESHOLD,
    CPU_IDLE_WINDOW,
    DAILY_RESULT_POLL_INTERVAL,
    EXIT_DAILY_VALIDATION_FAILED,
    EXIT_M7A_EXIT_NONZERO,
    EXIT_OK,
    EXIT_WATCHDOG_CPU_IDLE,
    EXIT_WATCHDOG_HARD_TIMEOUT,
    EXIT_WATCHDOG_LOG_STALLED,
    GRACE_PERIOD,
    LOG_HEARTBEAT_TIMEOUT,
    M7A_ASSISTANT_PROCESS_NAME,
    M7A_RUNTIME_DISCOVERY_INTERVAL,
    M7A_RUNTIME_DISCOVERY_TIMEOUT,
    WATCHDOG_INTERVAL,
)
from starrail_auto.m7a.logs import (
    capture_failure_evidence,
    daily_run_outcome,
    get_latest_m7a_log,
)
from starrail_auto.m7a.models import M7ALogCheckpoint

log = logging.getLogger(__name__)
ProcessHandle = subprocess.Popen | psutil.Process


def poll_process(proc: ProcessHandle) -> int | None:
    if isinstance(proc, subprocess.Popen):
        return proc.poll()
    try:
        if not proc.is_running():
            return 0
        code = proc.wait(timeout=0)
        return 0 if code is None else code
    except psutil.TimeoutExpired:
        return None
    except psutil.NoSuchProcess:
        return 0


def find_new_assistant(started_after: float) -> psutil.Process | None:
    deadline = time.monotonic() + M7A_RUNTIME_DISCOVERY_TIMEOUT
    while time.monotonic() < deadline:
        candidates = [
            proc
            for proc in psutil.process_iter(["name", "create_time"])
            if (proc.info["name"] or "").casefold() == M7A_ASSISTANT_PROCESS_NAME
            and (proc.info["create_time"] or 0) >= started_after - 2
        ]
        if candidates:
            return max(candidates, key=lambda item: item.create_time())
        time.sleep(M7A_RUNTIME_DISCOVERY_INTERVAL)
    return None


def hard_timeout_for_task(task: str, timeout: int) -> int | None:
    """The main task ends on a log result, not an arbitrary wall-clock cap."""
    return None if task == "main" else timeout


def stop_assistant_for_evidence(proc: ProcessHandle) -> None:
    """Stop only Assistant; leave the game and launcher visible for diagnosis."""
    if not isinstance(proc, psutil.Process):
        log.warning("Assistant PID unavailable; preserving all processes")
        return
    try:
        if proc.name().casefold() != M7A_ASSISTANT_PROCESS_NAME:
            log.warning("watchdog target is not Assistant; preserving all processes")
            return
        proc.kill()
        log.info("stopped M7A Assistant only: pid=%d", proc.pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
        log.warning("cannot stop M7A Assistant: %s", exc)


def watch(
    proc: ProcessHandle,
    hard_timeout: int | None,
    *,
    checkpoint: M7ALogCheckpoint | None = None,
    stop_when_daily_resolved: bool = False,
) -> int:
    start = time.monotonic()
    cpu_idle_since: float | None = None
    while True:
        if stop_when_daily_resolved and checkpoint is not None:
            outcome = daily_run_outcome(checkpoint)
            if outcome == "completed":
                return EXIT_OK
            if outcome == "incomplete":
                return EXIT_DAILY_VALIDATION_FAILED

        code = poll_process(proc)
        if code is not None:
            return EXIT_OK if code == 0 else EXIT_M7A_EXIT_NONZERO

        elapsed = time.monotonic() - start
        if hard_timeout is not None and elapsed >= hard_timeout:
            capture_failure_evidence("hard_timeout", checkpoint)
            stop_assistant_for_evidence(proc)
            return EXIT_WATCHDOG_HARD_TIMEOUT

        if elapsed >= GRACE_PERIOD:
            try:
                cpu = psutil.Process(proc.pid).cpu_percent(interval=1)
                if cpu < CPU_IDLE_THRESHOLD:
                    cpu_idle_since = cpu_idle_since or time.monotonic()
                    if time.monotonic() - cpu_idle_since >= CPU_IDLE_WINDOW:
                        capture_failure_evidence("cpu_idle", checkpoint)
                        stop_assistant_for_evidence(proc)
                        return EXIT_WATCHDOG_CPU_IDLE
                else:
                    cpu_idle_since = None
            except psutil.NoSuchProcess:
                continue

            latest_log = get_latest_m7a_log()
            if latest_log and time.time() - latest_log.stat().st_mtime > LOG_HEARTBEAT_TIMEOUT:
                capture_failure_evidence("log_stalled", checkpoint)
                stop_assistant_for_evidence(proc)
                return EXIT_WATCHDOG_LOG_STALLED

        time.sleep(DAILY_RESULT_POLL_INTERVAL if stop_when_daily_resolved else WATCHDOG_INTERVAL)
