"""End-to-end Wuthering Waves daily workflow."""

import logging
from datetime import datetime

from wuwa_auto.cleanup import CleanupResult, cleanup_after_run
from wuwa_auto.input.viiper import managed_virtual_mouse
from wuwa_auto.okww.absorption_flow import ensure_daily_farm_echo_absorptions
from wuwa_auto.okww.runner import (
    OkRunResult,
    run_daily_task,
    run_weekly_garden_task,
    write_workflow_failure,
)
from wuwa_auto.okww.config import (
    EXPECTED_REPEAT_FARM_COUNT,
    confirmed_retry_attempt_limit,
    temporary_farm_echo_repeat_count,
)
from wuwa_auto.okww.confirmed_retry import run_confirmed_farm_echo_retry
from wuwa_auto.okww.recovery_flow import maybe_recover_farm_echo_death
from wuwa_auto.reporting.service import report_run
from wuwa_auto.settings import FARM_ECHO_TARGET_REQUEST
from wuwa_auto.uu.desktop import require_admin, save_step_screenshot
from wuwa_auto.uu.service import ensure_connected

log = logging.getLogger(__name__)


def _settle_business_transaction(
    task_name: str,
    result: OkRunResult,
) -> OkRunResult:
    """Resolve every in-process top-up and recovery before notification."""
    current = result
    try:
        if task_name == "daily":
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
