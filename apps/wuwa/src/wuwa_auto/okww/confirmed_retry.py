"""Host wrapper for an absorption-confirmed, bounded FarmEcho retry."""

from __future__ import annotations

import json
import logging
import subprocess
import time
from datetime import datetime
from pathlib import Path

from wuwa_auto.okww.logs import LogCursor
from wuwa_auto.okww.runner import (
    LOG_STALL_TIMEOUT,
    POLL_INTERVAL,
    STARTUP_LOG_TIMEOUT,
    OkRunResult,
    preflight_farm_echo_task,
    release_active_mouse_buttons,
    resume_active_mouse_control,
    write_result,
)
from wuwa_auto.settings import (
    OK_LOG_FILE,
    OK_PYTHON_EXE,
    OK_WORKING_DIR,
    RUNS_DIR,
)
from wuwa_auto.uu.desktop import require_admin, save_step_screenshot

log = logging.getLogger(__name__)

CONFIRMED_RETRY_WORKER = Path(__file__).with_name("confirmed_retry_worker.py")
MAX_FARM_ECHO_RUNTIME_SECONDS = 3600.0


def _stop_process(process: subprocess.Popen[object]) -> None:
    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    finally:
        # The retry worker talks to the parent HID server.  Release after the
        # child is gone; releasing before terminate can be reasserted by a
        # final queued mouse-up/down packet from the child.
        release_active_mouse_buttons()


def run_confirmed_farm_echo_retry(
    *,
    target_count: int,
    attempt_limit: int,
    runtime_limit_seconds: float = MAX_FARM_ECHO_RUNTIME_SECONDS,
    resume_active_realm: bool = False,
) -> OkRunResult:
    """Retry until N echoes are absorbed, within one bounded wall-clock window."""
    if (
        runtime_limit_seconds <= 0
        or runtime_limit_seconds > MAX_FARM_ECHO_RUNTIME_SECONDS
    ):
        raise ValueError(
            "FarmEcho runtime limit must be within "
            f"(0, {MAX_FARM_ECHO_RUNTIME_SECONDS:.0f}] seconds"
        )
    require_admin()
    facts = preflight_farm_echo_task(attempt_limit)
    facts["configured_attempt_limit"] = facts["repeat_farm_count"]
    facts["repeat_farm_count"] = target_count
    facts.update(
        {
            "workflow_task": "farm_echo_confirmed_retry",
            "target_count": target_count,
            "attempt_limit": attempt_limit,
            "farm_echo_runtime_limit_seconds": runtime_limit_seconds,
            "resume_active_realm": resume_active_realm,
        }
    )
    started = datetime.now().astimezone()
    run_id = started.strftime("%Y%m%d_%H%M%S") + "_farm_echo_confirmed_retry"
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    slice_path = run_dir / "ok-current-run.log"
    worker_result_path = run_dir / "worker-result.json"
    console_path = run_dir / "worker-console.log"
    cursor = LogCursor(OK_LOG_FILE)
    command = [
        str(OK_PYTHON_EXE),
        str(CONFIRMED_RETRY_WORKER),
        str(OK_WORKING_DIR),
        str(worker_result_path),
        str(target_count),
    ]
    if resume_active_realm:
        command.append("resume")
    log.info(
        "starting confirmed FarmEcho retry target=%s attempt_limit=%s",
        target_count,
        attempt_limit,
    )

    collected: list[str] = []
    started_monotonic = time.monotonic()
    last_log_activity = started_monotonic
    saw_log_activity = False
    timeout_reason = ""
    with console_path.open("w", encoding="utf-8") as console:
        resume_active_mouse_control()
        process: subprocess.Popen[object] = subprocess.Popen(
            command,
            cwd=OK_WORKING_DIR,
            stdin=subprocess.DEVNULL,
            stdout=console,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        while process.poll() is None:
            now = time.monotonic()
            chunk = cursor.read_new()
            if chunk:
                collected.append(chunk)
                slice_path.write_text("".join(collected), encoding="utf-8")
                last_log_activity = now
                saw_log_activity = True
                for line in chunk.splitlines():
                    if "HOST_FARM_ECHO_" in line or "Revive Failed" in line:
                        log.info("confirmed retry progress: %s", line)
            if now - started_monotonic >= runtime_limit_seconds:
                timeout_reason = (
                    "FarmEcho absorption target timed out after "
                    f"{runtime_limit_seconds:.0f} seconds"
                )
                break
            if not saw_log_activity and now - started_monotonic > STARTUP_LOG_TIMEOUT:
                timeout_reason = (
                    "confirmed FarmEcho retry produced no log before startup deadline"
                )
                break
            if saw_log_activity and now - last_log_activity > LOG_STALL_TIMEOUT:
                timeout_reason = "confirmed FarmEcho retry log stalled for 45 minutes"
                break
            time.sleep(POLL_INTERVAL)
        if timeout_reason:
            _stop_process(process)
        exit_code = process.poll()

    chunk = cursor.read_new()
    if chunk:
        collected.append(chunk)
    slice_path.write_text("".join(collected), encoding="utf-8")

    payload: dict[str, object] = {}
    if worker_result_path.is_file():
        try:
            loaded = json.loads(worker_result_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        except (OSError, json.JSONDecodeError):
            log.exception("invalid confirmed retry result: %s", worker_result_path)
    absorbed_count = int(payload.get("absorbed_count") or 0)
    if not absorbed_count:
        from wuwa_auto.okww.logs import count_farm_echo_absorptions

        absorbed_count = count_farm_echo_absorptions("".join(collected))
    facts["confirmed_farm_echo_absorption_count"] = absorbed_count
    success = (
        not timeout_reason
        and exit_code == 0
        and payload.get("success") is True
        and absorbed_count >= target_count
    )
    if success:
        reason = (
            f"FarmEcho absorption confirmed {absorbed_count}/{target_count} echoes"
        )
        evidence = save_step_screenshot("ok_farm_echo_confirmed_retry_completed")
    else:
        reason = str(
            timeout_reason
            or payload.get("reason")
            or f"confirmed FarmEcho retry worker exited with code {exit_code}"
        )
        evidence = save_step_screenshot("ok_farm_echo_confirmed_retry_failed")

    finished = datetime.now().astimezone()
    result = OkRunResult(
        run_id=run_id,
        status="success" if success else "failed",
        reason=reason,
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
        duration_seconds=round((finished - started).total_seconds()),
        log_slice_path=str(slice_path),
        evidence_path=str(evidence),
        config=facts,
        exit_code=0 if success else 1,
    )
    write_result(result, run_dir)
    return result
