"""Launch OK-WW headlessly and judge the run from only its new log slice."""

from __future__ import annotations

import json
import logging
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import psutil

from wuwa_auto.okww.daily_worker import TRAVEL_NOT_CONFIRMED_MARKER
from wuwa_auto.okww.config import (
    EXPECTED_REPEAT_FARM_COUNT,
    validate_daily_configuration,
    validate_farm_echo_configuration,
    validate_weekly_garden_configuration,
)
from wuwa_auto.okww.logs import SUCCESS_MARKER, LogCursor, find_failure
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
LOG_STALL_TIMEOUT = 2700.0
POLL_INTERVAL = 1.0
DAILY_WORKER_ENTRYPOINT = Path(__file__).with_name("daily_worker.py")


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
        executable = Path(process.exe()).resolve()
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
        return False
    # Do not inspect arbitrary command-line arguments here. A diagnostic shell
    # that merely reads OK's log file contains the install path too and must
    # never be treated as an owned OK process.
    return executable in {OK_WW_EXE.resolve(), OK_PYTHONW_EXE.resolve()}


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


def _preflight_task(
    validate_configuration: Callable[[], dict[str, object]],
) -> dict[str, object]:
    facts = validate_configuration()
    running = _running_ok_processes()
    if running:
        processes = ", ".join(f"{p.name()}({p.pid})" for p in running)
        raise RuntimeError(f"OK-WW is already running: {processes}")
    return facts


def preflight_daily_task() -> dict[str, object]:
    return _preflight_task(validate_daily_configuration)


def preflight_farm_echo_task(
    expected_repeat_count: int = EXPECTED_REPEAT_FARM_COUNT,
) -> dict[str, object]:
    return _preflight_task(
        lambda: validate_farm_echo_configuration(expected_repeat_count)
    )


def preflight_weekly_garden_task() -> dict[str, object]:
    return _preflight_task(validate_weekly_garden_configuration)


def write_result(result: OkRunResult, run_dir: Path) -> None:
    (run_dir / "result.json").write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_workflow_failure(
    *,
    started: datetime,
    reason: str,
    evidence_path: Path | None = None,
    source_result: OkRunResult | None = None,
) -> OkRunResult:
    """Persist a workflow failure, optionally retaining its latest result."""
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
    source_text = ""
    config: dict[str, object] = {}
    evidence = evidence_path
    if source_result is not None:
        source_path = Path(source_result.log_slice_path)
        if source_path.is_file():
            source_text = (
                f"=== HOST WORKFLOW SOURCE {source_result.run_id} ===\n"
                f"{source_path.read_text(encoding='utf-8', errors='replace').rstrip()}\n"
            )
        config = dict(source_result.config)
        config["workflow_failure_source_run_id"] = source_result.run_id
        if evidence is None and source_result.evidence_path:
            evidence = Path(source_result.evidence_path)
    slice_path.write_text(source_text, encoding="utf-8")
    result = OkRunResult(
        run_id=run_id,
        status="failed",
        reason=reason,
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
        duration_seconds=round((finished - started).total_seconds()),
        log_slice_path=str(slice_path),
        evidence_path=str(evidence) if evidence else None,
        config=config,
        exit_code=1,
    )
    write_result(result, run_dir)
    return result


def _build_task_command(task_index: int, task_label: str) -> list[str]:
    if task_label == "daily":
        return [
            str(OK_PYTHONW_EXE),
            str(DAILY_WORKER_ENTRYPOINT),
            str(OK_WORKING_DIR),
        ]
    return [
        str(OK_PYTHONW_EXE),
        str(OK_ENTRYPOINT),
        "--headless",
        "-t",
        str(task_index),
        "-e",
    ]


def _run_task(
    *,
    task_index: int,
    task_label: str,
    success_marker: str,
    preflight: Callable[[], dict[str, object]],
    run_suffix: str = "",
) -> OkRunResult:
    require_admin()
    facts = preflight()
    facts["workflow_task"] = task_label
    started = datetime.now().astimezone()
    run_id = started.strftime("%Y%m%d_%H%M%S") + run_suffix
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    slice_path = run_dir / "ok-current-run.log"
    cursor = LogCursor(OK_LOG_FILE)

    # ok-ww.exe is a graphical PyAppify application manager.  The real CLI is
    # the installed app's bundled interpreter plus working/main.py.
    command = _build_task_command(task_index, task_label)
    log.info("starting OK-WW %s: %s", task_label, command)
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
    captured_nightmare_transition = False

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
                        success_marker,
                    )
                ):
                    log.info("OK-WW progress: %s", line)
                if (
                    TRAVEL_NOT_CONFIRMED_MARKER in line
                    and not captured_nightmare_transition
                ):
                    try:
                        save_step_screenshot(
                            "ok_nightmare_travel_not_confirmed"
                        )
                    except Exception:
                        log.exception(
                            "could not capture nightmare travel evidence"
                        )
                    captured_nightmare_transition = True

        current_text = "".join(collected)
        failure = find_failure(current_text)
        if failure:
            reason = f"OK-WW failure marker: {failure}"
            evidence = save_step_screenshot("ok_daily_failed")
            break
        if success_marker in current_text:
            status = "success"
            reason = success_marker
            evidence = save_step_screenshot(f"ok_{task_label}_completed")
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
            reason = "OK-WW current-run log stalled for 45 minutes"
            evidence = save_step_screenshot(f"ok_{task_label}_log_stalled")
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
    write_result(result, run_dir)
    log.info(
        "OK-WW %s finished status=%s reason=%s launcher_pid=%s",
        task_label,
        status,
        reason,
        launcher.pid,
    )
    return result


def run_daily_task() -> OkRunResult:
    return _run_task(
        task_index=1,
        task_label="daily",
        success_marker=SUCCESS_MARKER,
        preflight=preflight_daily_task,
    )


def run_farm_echo_task(
    *,
    expected_repeat_count: int = EXPECTED_REPEAT_FARM_COUNT,
    run_suffix: str = "_farm_echo",
) -> OkRunResult:
    return _run_task(
        task_index=3,
        task_label="farm_echo",
        success_marker="Successfully Executed Task, Exiting Game and App!",
        preflight=lambda: preflight_farm_echo_task(expected_repeat_count),
        run_suffix=run_suffix,
    )


def run_weekly_garden_task() -> OkRunResult:
    return _run_task(
        task_index=12,
        task_label="weekly_garden",
        success_marker="Successfully Executed Task, Exiting Game and App!",
        preflight=preflight_weekly_garden_task,
        run_suffix="_weekly_garden",
    )
