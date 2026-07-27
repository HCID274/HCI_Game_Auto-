"""End-to-end Wuthering Waves daily workflow."""

import logging
from datetime import datetime

from wuwa_auto.cleanup import CleanupResult, cleanup_after_run
from wuwa_auto.input.viiper import managed_virtual_mouse
from wuwa_auto.okww.runner import (
    OkRunResult,
    run_daily_task,
    run_farm_echo_task,
    run_weekly_garden_task,
    write_workflow_failure,
)
from wuwa_auto.reporting.service import report_run
from wuwa_auto.uu.desktop import require_admin, save_step_screenshot
from wuwa_auto.uu.service import ensure_connected

log = logging.getLogger(__name__)


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
            except Exception as exc:
                failure_reason = f"{task_name} workflow exception: {exc}"
                log.exception(failure_reason)
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
    return _run_workflow("farm_echo", run_farm_echo_task)


def run_weekly_garden_workflow() -> int:
    return _run_workflow("weekly_garden", run_weekly_garden_task)
