"""Launch OK-WW headlessly and judge the run from only its new log slice."""

from __future__ import annotations

import json
import logging
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import psutil

from wuwa_auto.okww.config import validate_daily_configuration
from wuwa_auto.okww.logs import LogCursor, SUCCESS_MARKER, find_failure
from wuwa_auto.settings import (
    OK_ENTRYPOINT,
    OK_LOG_FILE,
    OK_PYTHONW_EXE,
    OK_WORKING_DIR,
    OK_WW_EXE,
    RUNS_DIR,
)
from wuwa_auto.uu.desktop import require_admin, save_step_screenshot

log = logging.getLogger(__name__)

STARTUP_LOG_TIMEOUT = 180.0
LOG_STALL_TIMEOUT = 1200.0
POLL_INTERVAL = 1.0
OK_INSTALL_ROOT = OK_WW_EXE.parent.resolve()


@dataclass(frozen=True)
class OkRunResult:
    run_id: str
    status: str
    reason: str
    started_at: str
    finished_at: str
    duration_seconds: int
    log_slice_path: str
    evidence_path: str | None
    config: dict[str, object]
    exit_code: int


def _is_ok_process(process: psutil.Process) -> bool:
    try:
        parts = [process.exe(), *process.cmdline()]
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
        return False
    root = str(OK_INSTALL_ROOT).casefold()
    return any(root in (part or "").casefold() for part in parts)


def _running_ok_processes() -> list[psutil.Process]:
    return [process for process in psutil.process_iter() if _is_ok_process(process)]


def stop_pyappify_launchers() -> int:
    """Stop only the graphical PyAppify launcher, never OK's worker or game."""
    require_admin()
    expected = OK_WW_EXE.resolve()
    stopped = 0
    for process in psutil.process_iter(["name"]):
        try:
            if (process.info["name"] or "").casefold() != expected.name.casefold():
                continue
            if Path(process.exe()).resolve() != expected:
                continue
            process.terminate()
            process.wait(timeout=10)
            stopped += 1
            log.info("stopped owned PyAppify launcher pid=%s", process.pid)
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.TimeoutExpired):
            continue
    return 0 if stopped else 1


def stop_daily_workers() -> int:
    """Stop owned headless OK workers while preserving the game process."""
    require_admin()
    expected = OK_PYTHONW_EXE.resolve()
    stopped = 0
    for process in psutil.process_iter(["name"]):
        try:
            if (process.info["name"] or "").casefold() != "pythonw.exe":
                continue
            if Path(process.exe()).resolve() != expected:
                continue
            process.terminate()
            process.wait(timeout=10)
            stopped += 1
            log.info("stopped owned OK-WW worker pid=%s", process.pid)
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.TimeoutExpired):
            continue
    return 0 if stopped else 1


def stop_wuthering_game() -> int:
    """Stop only the verified Wuthering Waves client process."""
    require_admin()
    stopped = 0
    for process in psutil.process_iter(["name"]):
        try:
            if (process.info["name"] or "").casefold() != "client-win64-shipping.exe":
                continue
            process.terminate()
            try:
                process.wait(timeout=8)
            except psutil.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            stopped += 1
            log.info("stopped Wuthering Waves game pid=%s", process.pid)
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    return 0 if stopped else 1


def preflight_daily_task() -> dict[str, object]:
    facts = validate_daily_configuration()
    running = _running_ok_processes()
    if running:
        processes = ", ".join(f"{p.name()}({p.pid})" for p in running)
        raise RuntimeError(f"OK-WW is already running: {processes}")
    return facts


def _write_result(result: OkRunResult, run_dir: Path) -> None:
    (run_dir / "result.json").write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_workflow_failure(
    *,
    started: datetime,
    reason: str,
    evidence_path: Path | None = None,
) -> OkRunResult:
    """Persist a failure that happened before OK-WW could return a result."""
    finished = datetime.now().astimezone()
    base_id = started.strftime("%Y%m%d_%H%M%S")
    run_id = f"{base_id}_workflow_failure"
    run_dir = RUNS_DIR / run_id
    suffix = 1
    while run_dir.exists():
        suffix += 1
        run_id = f"{base_id}_workflow_failure_{suffix}"
        run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True)
    slice_path = run_dir / "ok-current-run.log"
    slice_path.write_text("", encoding="utf-8")
    result = OkRunResult(
        run_id=run_id,
        status="failed",
        reason=reason,
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
        duration_seconds=round((finished - started).total_seconds()),
        log_slice_path=str(slice_path),
        evidence_path=str(evidence_path) if evidence_path else None,
        config={},
        exit_code=1,
    )
    _write_result(result, run_dir)
    return result


def run_daily_task() -> OkRunResult:
    require_admin()
    facts = preflight_daily_task()
    started = datetime.now().astimezone()
    run_id = started.strftime("%Y%m%d_%H%M%S")
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    slice_path = run_dir / "ok-current-run.log"
    cursor = LogCursor(OK_LOG_FILE)

    # ok-ww.exe is a graphical PyAppify application manager.  The real CLI is
    # the installed app's bundled interpreter plus working/main.py.
    command = [
        str(OK_PYTHONW_EXE),
        str(OK_ENTRYPOINT),
        # The first OK logger parser reserves short ``-h`` for help.  The
        # framework's long form survives parse_known_args and reaches OK.
        "--headless",
        "-t",
        "1",
        "-e",
    ]
    log.info("starting OK-WW DailyTask: %s", command)
    launcher = subprocess.Popen(
        command,
        cwd=OK_WORKING_DIR,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    collected: list[str] = []
    started_monotonic = time.monotonic()
    last_log_activity = started_monotonic
    saw_log_activity = False
    status = "failed"
    reason = "unknown"
    evidence: Path | None = None

    while True:
        now = time.monotonic()
        chunk = cursor.read_new()
        if chunk:
            collected.append(chunk)
            slice_path.write_text("".join(collected), encoding="utf-8")
            last_log_activity = now
            saw_log_activity = True
            for line in chunk.splitlines():
                if any(
                    marker in line
                    for marker in (
                        "run one-time task without ui",
                        "Daily task completed, start teleport",
                        "start farming ",
                        SUCCESS_MARKER,
                    )
                ):
                    log.info("OK-WW progress: %s", line)

        current_text = "".join(collected)
        failure = find_failure(current_text)
        if failure:
            reason = f"OK-WW failure marker: {failure}"
            evidence = save_step_screenshot("ok_daily_failed")
            break
        if SUCCESS_MARKER in current_text:
            status = "success"
            reason = SUCCESS_MARKER
            evidence = save_step_screenshot("ok_daily_completed")
            break
        if not saw_log_activity and now - started_monotonic > STARTUP_LOG_TIMEOUT:
            reason = "OK-WW produced no current-run log before startup deadline"
            evidence = save_step_screenshot("ok_daily_startup_timeout")
            break
        worker_exit_code = launcher.poll()
        if worker_exit_code is not None and now - started_monotonic > 5:
            boundary = (
                "before producing current-run log"
                if not saw_log_activity
                else "before the completion marker"
            )
            reason = f"OK-WW worker exited {boundary} (code={worker_exit_code})"
            evidence = save_step_screenshot("ok_daily_worker_exited")
            break
        if saw_log_activity and now - last_log_activity > LOG_STALL_TIMEOUT:
            reason = "OK-WW current-run log stalled for 20 minutes"
            evidence = save_step_screenshot("ok_daily_log_stalled")
            break
        time.sleep(POLL_INTERVAL)

    finished = datetime.now().astimezone()
    if not slice_path.exists():
        slice_path.write_text("".join(collected), encoding="utf-8")
    result = OkRunResult(
        run_id=run_id,
        status=status,
        reason=reason,
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
        duration_seconds=round((finished - started).total_seconds()),
        log_slice_path=str(slice_path),
        evidence_path=str(evidence) if evidence else None,
        config=facts,
        exit_code=0 if status == "success" else 1,
    )
    _write_result(result, run_dir)
    log.info(
        "OK-WW DailyTask finished status=%s reason=%s launcher_pid=%s",
        status,
        reason,
        launcher.pid,
    )
    return result
