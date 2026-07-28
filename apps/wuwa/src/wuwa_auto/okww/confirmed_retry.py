"""Host wrapper for a kill-confirmed, bounded FarmEcho retry."""

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


def _stop_process(process: subprocess.Popen[object]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run_confirmed_farm_echo_retry(
    *,
    target_count: int,
    attempt_limit: int,
) -> OkRunResult:
    """Retry until N causal post-combat facts prove N kills, with a hard cap."""
    require_admin()
    facts = preflight_farm_echo_task(attempt_limit)
    facts["configured_attempt_limit"] = facts["repeat_farm_count"]
    facts["repeat_farm_count"] = target_count
    facts.update(
        {
            "workflow_task": "farm_echo_confirmed_retry",
            "target_count": target_count,
            "attempt_limit": attempt_limit,
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
    confirmed_count = int(payload.get("confirmed_count") or 0)
    facts["confirmed_farm_echo_count"] = confirmed_count
    success = (
        not timeout_reason
        and exit_code == 0
        and payload.get("success") is True
        and confirmed_count >= target_count
    )
    if success:
        reason = (
            f"FarmEcho kill evidence confirmed {confirmed_count}/{target_count} kills"
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
