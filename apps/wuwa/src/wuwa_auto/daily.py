"""End-to-end Wuthering Waves daily workflow."""

import logging
import re
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from wuwa_auto.cleanup import CleanupResult, cleanup_after_run
from wuwa_auto.client.launcher import stop_client_launchers
from wuwa_auto.input.viiper import managed_virtual_mouse
from wuwa_auto.okww.compatibility import validate_okww_compatibility
from wuwa_auto.okww.config import (
    EXPECTED_REPEAT_FARM_COUNT,
    confirmed_retry_attempt_limit,
    temporary_farm_echo_repeat_count,
)
from wuwa_auto.okww.confirmed_retry import run_confirmed_farm_echo_retry
from wuwa_auto.okww.recovery import (
    FarmEchoRecoveryResult,
    run_world_state_recovery,
)
from wuwa_auto.okww.recovery_flow import maybe_recover_farm_echo_death
from wuwa_auto.okww.runner import (
    OkRunResult,
    run_daily_task,
    run_daily_resume_task,
    run_weekly_garden_task,
    stop_daily_workers,
    stop_wuthering_game,
    write_result,
    write_workflow_failure,
)
from wuwa_auto.reporting.service import report_run
from wuwa_auto.settings import FARM_ECHO_TARGET_REQUEST
from wuwa_auto.uu.desktop import require_admin, save_step_screenshot
from wuwa_auto.uu.service import ensure_connected

log = logging.getLogger(__name__)

DAILY_START_BOOK_FAILURE = (
    "DailyTask:open_daily",
    "can't find gray_book_boss, make sure f2 is the hotkey for book",
)
TACET_DEATH_RECOVERY_FAILURE = (
    "TacetTask:raise_not_in_combat char dead",
    "TacetTask:info_set Revive Failed",
)
# The 0815 morning DailyTask crash (WaitFailedException in walk_to_treasure)
# matched no enumerated marker.  Unknown failures therefore get the same
# bounded recovery instead of a terminal red.
DAILY_GENERIC_RETRY_KIND = "generic-bounded-retry"
# A recurring failure with no observable game-state progress only turns
# fatal after this much wall time: restarting has a real chance of fixing
# transient upstream states and the morning budget can absorb it.
DAILY_NO_PROGRESS_TIMEOUT = 60 * 60.0
# Hard backstop measured from workflow start so the retry ladder can never
# burn through the whole scheduled morning window.
DAILY_RETRY_HARD_DEADLINE = 165 * 60.0
_NIGHTMARE_CLEARED = re.compile(r"已击败残象：(\d+)/(\d+)")
_STAMINA_CURRENT = re.compile(r'"current_stamina": (\d+)')


def _prepare_okww_cold_start() -> None:
    """Leave no pre-opened official client for OK-WW to bind.

    Historical successful runs let OK-WW's ``start_exe=True`` path launch the
    game and enumerate its own fresh window.  The host owns only bounded
    cleanup before that handoff.
    """
    stop_daily_workers()
    stop_wuthering_game()
    stop_client_launchers()
    log.info("official client is closed; OK-WW owns the next game launch")


def _read_result_log(result: OkRunResult) -> str:
    path = Path(result.log_slice_path)
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def _compose_recovery_result(
    initial: OkRunResult,
    *,
    retry: OkRunResult | None,
    recovery: FarmEchoRecoveryResult,
    recovery_kind: str,
    run_suffix: str,
    record_extra: dict[str, object] | None = None,
) -> OkRunResult:
    base_dir = Path(initial.log_slice_path).parent.parent
    run_id = f"{initial.run_id}_{run_suffix}"
    run_dir = base_dir / run_id
    suffix = 1
    while run_dir.exists():
        suffix += 1
        run_id = f"{initial.run_id}_{run_suffix}_{suffix}"
        run_dir = base_dir / run_id
    run_dir.mkdir(parents=True)

    parts = [f"=== HOST INITIAL {initial.run_id} ===\n{_read_result_log(initial).rstrip()}"]
    if retry is not None:
        parts.append(f"=== HOST RETRY {retry.run_id} ===\n{_read_result_log(retry).rstrip()}")
    log_path = run_dir / "ok-current-run.log"
    log_path.write_text("\n\n".join(parts) + "\n", encoding="utf-8")

    final = retry or initial
    config = dict(final.config)
    # Workflow-level flags must survive composes even if a future runner
    # change stops re-injecting them; losing daily_resume here would
    # silently degrade a resume ladder into a full daily re-run.
    for flag in ("daily_resume", "workflow_task"):
        if flag in initial.config and flag not in config:
            config[flag] = initial.config[flag]
    recovery_history = list(initial.config.get("daily_state_recoveries") or [])
    record: dict[str, object] = {
        "kind": recovery_kind,
        "triggered": True,
        "success": recovery.success,
        "reason": recovery.reason,
        "initial_run_id": initial.run_id,
        "retry_run_id": retry.run_id if retry is not None else "",
    }
    if record_extra:
        record.update(record_extra)
    recovery_history.append(record)
    config["daily_state_recoveries"] = recovery_history
    started = datetime.fromisoformat(initial.started_at)
    finished = datetime.fromisoformat(final.finished_at)
    if retry is None:
        reason = f"daily world-state recovery failed: {recovery.reason}"
    else:
        reason = final.reason
    composite = OkRunResult(
        run_id=run_id,
        status=final.status if retry is not None else "failed",
        reason=reason,
        started_at=initial.started_at,
        finished_at=final.finished_at,
        duration_seconds=round((finished - started).total_seconds()),
        log_slice_path=str(log_path),
        evidence_path=(
            final.evidence_path
            or recovery.evidence_path
            or initial.evidence_path
        ),
        config=config,
        exit_code=final.exit_code if retry is not None else 1,
    )
    write_result(composite, run_dir)
    return composite


def _maybe_recover_daily_state(
    result: OkRunResult,
    *,
    client_restart: Callable[[], bool] | None = None,
    workflow_started: float | None = None,
    now_fn: Callable[[], float] = time.perf_counter,
) -> OkRunResult:
    """Recover known residual/death states once each before final reporting.

    Unknown failures fall through to the time-budgeted generic retry so the
    next unenumerated crash (0815-style) still gets a full re-run.
    """
    current = result
    recoverable_states = (
        ("restored-tacet-challenge", DAILY_START_BOOK_FAILURE),
        ("tacet-death-teleport", TACET_DEATH_RECOVERY_FAILURE),
    )
    for recovery_kind, markers in recoverable_states:
        if current.status == "success":
            break
        text = _read_result_log(current)
        if not all(marker in text for marker in markers):
            continue

        stop_daily_workers()
        recovery = run_world_state_recovery(
            Path(current.log_slice_path).parent,
            attempt=1,
        )
        if not recovery.success:
            # The failure is settled for this marker, but the bounded
            # generic retry below still owns any remaining recovery budget.
            current = _compose_recovery_result(
                current,
                retry=None,
                recovery=recovery,
                recovery_kind=recovery_kind,
                run_suffix="daily_start_recovery",
            )
            break
        runner = (
            run_daily_resume_task
            if current.config.get("daily_resume") == "after_nightmare"
            else run_daily_task
        )
        retry = runner()
        current = _compose_recovery_result(
            current,
            retry=retry,
            recovery=recovery,
            recovery_kind=recovery_kind,
            run_suffix="daily_start_recovery",
        )
    return _retry_daily_after_any_failure(
        current,
        client_restart=client_restart,
        workflow_started=workflow_started,
        now_fn=now_fn,
    )


def _daily_progress_fingerprint(result: OkRunResult) -> tuple[object, ...]:
    """Observable game-state progress extracted from one settled attempt.

    Cleared nightmare nests, spent stamina, the completion marker, and boss
    absorptions all advance between attempts while the ladder is making
    progress; a recurring identical fingerprint means the same failure is
    not being worked around and the no-progress clock keeps running.
    """
    text = _read_result_log(result)
    nests = tuple(sorted(set(_NIGHTMARE_CLEARED.findall(text))))
    stamina = tuple(_STAMINA_CURRENT.findall(text))
    completed = "Daily Task Completed" in text
    absorbed = int(result.config.get("confirmed_farm_echo_absorption_count") or 0)
    return (nests, stamina, completed, absorbed)


def _append_terminal_recovery_record(
    current: OkRunResult,
    *,
    why: str,
    minutes: int,
) -> OkRunResult:
    config = dict(current.config)
    history = list(config.get("daily_state_recoveries") or [])
    history.append({
        "kind": "generic-bounded-retry-exhausted",
        "triggered": False,
        "reason": why,
        "minutes_since_progress": minutes,
    })
    config["daily_state_recoveries"] = history
    updated = replace(current, config=config)
    write_result(updated, Path(current.log_slice_path).parent)
    log.info("daily generic retry ladder stopped: %s", why)
    return updated


def _retry_daily_after_any_failure(
    current: OkRunResult,
    *,
    client_restart: Callable[[], bool] | None = None,
    workflow_started: float | None = None,
    now_fn: Callable[[], float] = time.perf_counter,
) -> OkRunResult:
    """Time-budgeted recovery for any DailyTask failure, known or unknown.

    Retries continue while either clock allows: the same failure with no
    observable game-state progress only turns fatal after
    DAILY_NO_PROGRESS_TIMEOUT, and the ladder never crosses the workflow
    hard deadline.  Each attempt resets world state (exiting any
    half-finished challenge); when that recovery itself fails, an OK-owned
    cold start rebuilds a clean entry boundary before the re-run, as often
    as the clocks allow.  Every attempt writes a structured
    ``daily_state_recoveries`` record; nothing is caught or swallowed.
    """
    hard_deadline = (
        (workflow_started if workflow_started is not None else now_fn())
        + DAILY_RETRY_HARD_DEADLINE
    )
    last_progress_at = now_fn()
    previous = _daily_progress_fingerprint(current)
    attempt = 0
    last_tick: float | None = None
    while current.status != "success":
        now = now_fn()
        if last_tick is not None and now <= last_tick:
            # The monotonic clock must advance across a full retry cycle; a
            # frozen one would otherwise spin the ladder forever.
            return _append_terminal_recovery_record(
                current,
                why="retry clock stopped advancing",
                minutes=round((now - last_progress_at) / 60.0),
            )
        last_tick = now
        minutes_since_progress = (now - last_progress_at) / 60.0
        if minutes_since_progress * 60.0 >= DAILY_NO_PROGRESS_TIMEOUT:
            return _append_terminal_recovery_record(
                current,
                why=(
                    "no game-state progress for "
                    f"{minutes_since_progress:.0f} minutes"
                ),
                minutes=round(minutes_since_progress),
            )
        if now >= hard_deadline:
            return _append_terminal_recovery_record(
                current,
                why="workflow hard deadline reached",
                minutes=round(minutes_since_progress),
            )
        attempt += 1
        failure_reason = current.reason
        stop_daily_workers()
        recovery = run_world_state_recovery(
            Path(current.log_slice_path).parent,
            attempt=attempt,
        )
        client_restarted = False
        if not recovery.success and client_restart is not None:
            client_restarted = bool(client_restart())
        runner = (
            run_daily_resume_task
            if current.config.get("daily_resume") == "after_nightmare"
            else run_daily_task
        )
        retry = runner()
        fingerprint = _daily_progress_fingerprint(retry)
        progressed = fingerprint != previous
        if progressed:
            last_progress_at = now_fn()
            previous = fingerprint
        current = _compose_recovery_result(
            current,
            retry=retry,
            recovery=recovery,
            recovery_kind=DAILY_GENERIC_RETRY_KIND,
            run_suffix=f"daily_generic_retry_{attempt}",
            record_extra={
                "attempt": attempt,
                "failure_reason": failure_reason,
                "client_restarted": client_restarted,
                "retry_status": retry.status,
                "progressed": progressed,
                "minutes_since_progress": round(
                    (now_fn() - last_progress_at) / 60.0
                ),
            },
        )
    return current


def _compose_ordered_daily_result(
    boss: OkRunResult,
    daily: OkRunResult,
) -> OkRunResult:
    """Merge the pre-daily boss phase and DailyTask into one report boundary."""
    runs_dir = Path(boss.log_slice_path).parent.parent
    base_run_id = f"{boss.run_id}_daily"
    run_id = base_run_id
    run_dir = runs_dir / run_id
    suffix = 1
    while run_dir.exists():
        suffix += 1
        run_id = f"{base_run_id}_{suffix}"
        run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True)

    combined_path = run_dir / "ok-current-run.log"
    combined_path.write_text(
        f"=== HOST PRE-DAILY BOSS {boss.run_id} ===\n"
        f"{_read_result_log(boss).rstrip()}\n\n"
        f"=== HOST DAILY {daily.run_id} ===\n"
        f"{_read_result_log(daily).rstrip()}\n",
        encoding="utf-8",
    )

    config = dict(boss.config)
    config.update(daily.config)
    config["workflow_task"] = "daily"
    config["confirmed_farm_echo_absorption_count"] = int(
        boss.config.get("confirmed_farm_echo_absorption_count") or 0
    )
    config["farm_echo_absorption_target"] = EXPECTED_REPEAT_FARM_COUNT
    if "farm_echo_recovery" in boss.config:
        config["farm_echo_recovery"] = boss.config["farm_echo_recovery"]
    config["daily_sequence"] = {
        "order": ["farm_echo", "daily"],
        "boss_run_id": boss.run_id,
        "boss_status": boss.status,
        "boss_reason": boss.reason,
        "daily_run_id": daily.run_id,
        "daily_status": daily.status,
        "daily_reason": daily.reason,
        "settled": True,
    }

    completed = boss.status == "success" and daily.status == "success"
    if completed:
        reason = "pre-daily FarmEcho and DailyTask completed"
    else:
        failures = []
        if boss.status != "success":
            failures.append(f"pre-daily FarmEcho failed: {boss.reason}")
        if daily.status != "success":
            failures.append(f"DailyTask failed: {daily.reason}")
        reason = "; ".join(failures)

    started = datetime.fromisoformat(boss.started_at)
    finished = datetime.fromisoformat(daily.finished_at)
    result = OkRunResult(
        run_id=run_id,
        status="success" if completed else "failed",
        reason=reason,
        started_at=boss.started_at,
        finished_at=daily.finished_at,
        duration_seconds=round((finished - started).total_seconds()),
        log_slice_path=str(combined_path),
        evidence_path=(
            daily.evidence_path
            if daily.status != "success"
            else boss.evidence_path
        ),
        config=config,
        exit_code=0 if completed else 1,
    )
    write_result(result, run_dir)
    return result


def _run_boss_then_daily_task(
    client_restart: Callable[[], bool] | None = None,
    daily_client_restart: Callable[[], bool] | None = None,
    workflow_started: float | None = None,
) -> OkRunResult:
    """Run five confirmed boss absorptions before the pure daily task."""
    target = EXPECTED_REPEAT_FARM_COUNT
    attempt_limit = confirmed_retry_attempt_limit(target)
    with temporary_farm_echo_repeat_count(attempt_limit):
        boss = run_confirmed_farm_echo_retry(
            target_count=target,
            attempt_limit=attempt_limit,
        )
    boss = maybe_recover_farm_echo_death(
        boss,
        client_restart=client_restart,
    )

    stop_daily_workers()
    if boss.status != "success":
        skipped_log = Path(boss.log_slice_path).parent / "daily-skipped.log"
        skipped_log.write_text(
            "DailyTask was not started because FarmEcho did not reach the "
            f"required {target}/{target} absorption boundary.\n",
            encoding="utf-8",
        )
        skipped_at = datetime.now().astimezone().isoformat()
        daily = OkRunResult(
            run_id=f"{boss.run_id}_daily_skipped",
            status="failed",
            reason="DailyTask skipped until FarmEcho reaches 5/5",
            started_at=skipped_at,
            finished_at=skipped_at,
            duration_seconds=0,
            log_slice_path=str(skipped_log),
            evidence_path=boss.evidence_path,
            config={"workflow_task": "daily", "skipped_after_farm_echo": True},
            exit_code=1,
        )
        return _compose_ordered_daily_result(boss, daily)
    daily = _maybe_recover_daily_state(
        run_daily_task(),
        client_restart=daily_client_restart,
        workflow_started=workflow_started,
    )
    return _compose_ordered_daily_result(boss, daily)


def _settle_business_transaction(
    task_name: str,
    result: OkRunResult,
    *,
    client_restart: Callable[[], bool] | None = None,
    workflow_started: float | None = None,
) -> OkRunResult:
    """Resolve every in-process top-up and recovery before notification."""
    current = result
    try:
        if task_name == "daily":
            sequence = current.config.get("daily_sequence") or {}
            if isinstance(sequence, dict) and sequence.get("settled") is True:
                return current
            current = _maybe_recover_daily_state(
                current,
                client_restart=client_restart,
                workflow_started=workflow_started,
            )
            return maybe_recover_farm_echo_death(current)
        if task_name == "farm_echo":
            if client_restart is None:
                return maybe_recover_farm_echo_death(current)
            return maybe_recover_farm_echo_death(
                current,
                client_restart=client_restart,
            )
        return current
    except Exception as exc:
        reason = f"{task_name} business transaction settlement exception: {exc}"
        log.exception(reason)
        evidence = None
        try:
            evidence = save_step_screenshot("wuwa_transaction_settlement_failed")
        except Exception:
            log.exception("could not save transaction settlement failure screenshot")
        return write_workflow_failure(
            started=datetime.fromisoformat(current.started_at),
            reason=reason,
            evidence_path=evidence,
            source_result=current,
        )


def _run_workflow(task_name: str, task_runner) -> int:
    require_admin()
    started = datetime.now().astimezone()
    result: OkRunResult | None = None
    cleanup: CleanupResult | None = None
    acceleration_connected = False
    failure_reason = ""
    failure_evidence = None

    try:
        validate_okww_compatibility()
        # Keep a real PnP HID mouse present for game UI input and UU cleanup.
        with managed_virtual_mouse():
            try:
                log.info("%s workflow: local virtual HID mouse is ready", task_name)
                log.info("%s workflow: ensure Wuthering Waves acceleration", task_name)
                ensure_connected()
                acceleration_connected = True
                log.info("%s workflow: prepare OK-WW cold start", task_name)
                _prepare_okww_cold_start()
                log.info("%s workflow: start OK-WW task", task_name)
                workflow_started_monotonic = time.perf_counter()
                restart_state: dict[str, bool] = {"done": False}

                def restart_client_once() -> bool:
                    if restart_state["done"]:
                        return False
                    log.info(
                        "%s workflow: close Wuthering Waves so the next "
                        "OK-WW worker can relaunch and rebind it",
                        task_name,
                    )
                    stop_daily_workers()
                    stop_wuthering_game()
                    stop_client_launchers()
                    restart_state["done"] = True
                    return True

                def restart_client_for_daily() -> bool:
                    # The daily retry ladder may rebuild a clean entry
                    # boundary as often as its no-progress clock allows;
                    # the shared once-per-run budget stays with FarmEcho.
                    log.info(
                        "%s workflow: close Wuthering Waves so the daily "
                        "retry ladder can relaunch a clean entry boundary",
                        task_name,
                    )
                    stop_daily_workers()
                    stop_wuthering_game()
                    stop_client_launchers()
                    return True

                if task_runner is _run_boss_then_daily_task:
                    result = _run_boss_then_daily_task(
                        client_restart=restart_client_once,
                        daily_client_restart=restart_client_for_daily,
                        workflow_started=workflow_started_monotonic,
                    )
                else:
                    result = task_runner()
                # A retry is part of this workflow's business transaction, not
                # a new reportable run.  Only the settled composite result may
                # cross the notification boundary below.
                result = _settle_business_transaction(
                    task_name,
                    result,
                    client_restart=(
                        restart_client_once
                        if task_name == "farm_echo"
                        else restart_client_for_daily
                        if task_name == "daily"
                        else None
                    ),
                    workflow_started=workflow_started_monotonic,
                )
            except Exception as exc:
                failure_reason = f"{task_name} workflow exception: {exc}"
                log.exception(failure_reason)
                # Never let a result captured before an exception cross the
                # final notification boundary as if the workflow had settled.
                result = None
                try:
                    failure_evidence = save_step_screenshot(
                        "wuwa_daily_workflow_failed"
                    )
                except Exception:
                    log.exception("could not save workflow failure screenshot")
            finally:
                cleanup = cleanup_after_run(
                    acceleration_was_connected=acceleration_connected
                )
    except Exception as exc:
        if not failure_reason:
            failure_reason = f"virtual HID or cleanup exception: {exc}"
            log.exception(failure_reason)

    if result is None:
        result = write_workflow_failure(
            started=started,
            reason=failure_reason or "daily workflow ended without a result",
            evidence_path=failure_evidence,
        )
    if cleanup is None:
        cleanup = cleanup_after_run(
            acceleration_was_connected=acceleration_connected
        )

    try:
        report_run(result, cleanup)
    except Exception:
        log.exception("final Wuwa report service failed")

    if result.exit_code != 0:
        return result.exit_code
    return 0 if cleanup.completed else 2


def run_daily_workflow() -> int:
    return _run_workflow("daily", _run_boss_then_daily_task)


def run_daily_only_workflow() -> int:
    """Run and report DailyTask without the optional pre-daily boss phase."""
    return _run_workflow("daily", run_daily_task)


def run_daily_resume_workflow() -> int:
    """Resume DailyTask after settled Nightmare work without replaying it."""

    return _run_workflow("daily", run_daily_resume_task)


def run_farm_echo_workflow() -> int:
    target_count = EXPECTED_REPEAT_FARM_COUNT
    if FARM_ECHO_TARGET_REQUEST.is_file():
        requested = FARM_ECHO_TARGET_REQUEST.read_text(encoding="utf-8").strip()
        FARM_ECHO_TARGET_REQUEST.unlink()
        try:
            target_count = int(requested)
        except ValueError as exc:
            raise RuntimeError(
                f"invalid one-shot FarmEcho target: {requested!r}"
            ) from exc
        if not 1 <= target_count <= EXPECTED_REPEAT_FARM_COUNT:
            raise RuntimeError(
                "one-shot FarmEcho target must be between 1 and "
                f"{EXPECTED_REPEAT_FARM_COUNT}; actual={target_count}"
            )

    def run_confirmed_target() -> OkRunResult:
        attempt_limit = confirmed_retry_attempt_limit(target_count)
        with temporary_farm_echo_repeat_count(attempt_limit):
            return run_confirmed_farm_echo_retry(
                target_count=target_count,
                attempt_limit=attempt_limit,
            )

    return _run_workflow("farm_echo", run_confirmed_target)


def run_weekly_garden_workflow() -> int:
    return _run_workflow("weekly_garden", run_weekly_garden_task)
