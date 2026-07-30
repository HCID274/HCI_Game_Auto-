"""End-to-end Wuthering Waves daily workflow."""

import logging
from datetime import datetime
from pathlib import Path

from wuwa_auto.cleanup import CleanupResult, cleanup_after_run
from wuwa_auto.input.viiper import managed_virtual_mouse
from wuwa_auto.okww.absorption_flow import ensure_daily_farm_echo_absorptions
from wuwa_auto.okww.runner import (
    OkRunResult,
    run_daily_task,
    run_weekly_garden_task,
    stop_daily_workers,
    write_result,
    write_workflow_failure,
)
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


def _read_result_log(result: OkRunResult) -> str:
    path = Path(result.log_slice_path)
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def _compose_daily_start_recovery(
    initial: OkRunResult,
    *,
    retry: OkRunResult | None,
    recovery: FarmEchoRecoveryResult,
    recovery_kind: str,
) -> OkRunResult:
    base_dir = Path(initial.log_slice_path).parent.parent
    run_id = f"{initial.run_id}_daily_start_recovery"
    run_dir = base_dir / run_id
    suffix = 1
    while run_dir.exists():
        suffix += 1
        run_id = f"{initial.run_id}_daily_start_recovery_{suffix}"
        run_dir = base_dir / run_id
    run_dir.mkdir(parents=True)

    parts = [f"=== HOST INITIAL {initial.run_id} ===\n{_read_result_log(initial).rstrip()}"]
    if retry is not None:
        parts.append(f"=== HOST RETRY {retry.run_id} ===\n{_read_result_log(retry).rstrip()}")
    log_path = run_dir / "ok-current-run.log"
    log_path.write_text("\n\n".join(parts) + "\n", encoding="utf-8")

    final = retry or initial
    config = dict(final.config)
    recovery_history = list(initial.config.get("daily_state_recoveries") or [])
    recovery_history.append({
        "kind": recovery_kind,
        "triggered": True,
        "success": recovery.success,
        "reason": recovery.reason,
        "initial_run_id": initial.run_id,
        "retry_run_id": retry.run_id if retry is not None else "",
    })
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


def _maybe_recover_daily_state(result: OkRunResult) -> OkRunResult:
    """Recover known residual/death states once each before final reporting."""
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
            return _compose_daily_start_recovery(
                current,
                retry=None,
                recovery=recovery,
                recovery_kind=recovery_kind,
            )
        retry = run_daily_task()
        current = _compose_daily_start_recovery(
            current,
            retry=retry,
            recovery=recovery,
            recovery_kind=recovery_kind,
        )
    return current


def _settle_business_transaction(
    task_name: str,
    result: OkRunResult,
) -> OkRunResult:
    """Resolve every in-process top-up and recovery before notification."""
    current = result
    try:
        if task_name == "daily":
            current = _maybe_recover_daily_state(current)
            current = ensure_daily_farm_echo_absorptions(current)
        return maybe_recover_farm_echo_death(current)
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
        # Keep a real PnP HID mouse present for game UI input and UU cleanup.
        with managed_virtual_mouse():
            try:
                log.info("%s workflow: local virtual HID mouse is ready", task_name)
                log.info("%s workflow: ensure Wuthering Waves acceleration", task_name)
                ensure_connected()
                acceleration_connected = True
                log.info("%s workflow: start OK-WW task", task_name)
                result = task_runner()
                # A retry is part of this workflow's business transaction, not
                # a new reportable run.  Only the settled composite result may
                # cross the notification boundary below.
                result = _settle_business_transaction(task_name, result)
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
    return _run_workflow("daily", run_daily_task)


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
