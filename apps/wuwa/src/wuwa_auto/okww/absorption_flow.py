"""Enforce the daily FarmEcho absorption postcondition before cleanup."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from wuwa_auto.okww.config import (
    EXPECTED_REPEAT_FARM_COUNT,
    confirmed_retry_attempt_limit,
    temporary_farm_echo_repeat_count,
)
from wuwa_auto.okww.confirmed_retry import run_confirmed_farm_echo_retry
from wuwa_auto.okww.logs import count_farm_echo_absorptions
from wuwa_auto.okww.runner import (
    OkRunResult,
    stop_daily_workers,
    write_result,
)
from wuwa_auto.settings import RUNS_DIR

log = logging.getLogger(__name__)


def _read_log(result: OkRunResult) -> str:
    path = Path(result.log_slice_path)
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def _unique_run_dir(base_run_id: str) -> tuple[str, Path]:
    base = f"{base_run_id}_absorption"
    run_id = base
    run_dir = RUNS_DIR / run_id
    suffix = 1
    while run_dir.exists():
        suffix += 1
        run_id = f"{base}_{suffix}"
        run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True)
    return run_id, run_dir


def _annotate_satisfied_result(
    result: OkRunResult,
    *,
    target: int,
    absorbed: int,
) -> OkRunResult:
    config = dict(result.config)
    config["farm_echo_absorption_target"] = target
    config["confirmed_farm_echo_absorption_count"] = absorbed
    annotated = replace(result, config=config)
    write_result(annotated, Path(result.log_slice_path).parent)
    return annotated


def ensure_daily_farm_echo_absorptions(
    result: OkRunResult,
    *,
    target: int = EXPECTED_REPEAT_FARM_COUNT,
) -> OkRunResult:
    """Top up a successful daily run until N echoes were actually absorbed."""
    if result.status != "success":
        return result

    initial_absorbed = count_farm_echo_absorptions(_read_log(result))
    if initial_absorbed >= target:
        return _annotate_satisfied_result(
            result,
            target=target,
            absorbed=initial_absorbed,
        )

    remaining = target - initial_absorbed
    attempt_limit = confirmed_retry_attempt_limit(remaining)
    log.info(
        "daily FarmEcho absorption postcondition incomplete: "
        "absorbed=%s target=%s remaining=%s",
        initial_absorbed,
        target,
        remaining,
    )
    stop_daily_workers()
    with temporary_farm_echo_repeat_count(attempt_limit):
        retry = run_confirmed_farm_echo_retry(
            target_count=remaining,
            attempt_limit=attempt_limit,
        )

    retry_absorbed = min(
        remaining,
        int(retry.config.get("confirmed_farm_echo_absorption_count") or 0),
    )
    total_absorbed = min(target, initial_absorbed + retry_absorbed)
    completed = retry.status == "success" and total_absorbed >= target
    run_id, run_dir = _unique_run_dir(result.run_id)
    combined_path = run_dir / "ok-current-run.log"
    combined_path.write_text(
        f"=== HOST DAILY {result.run_id} ===\n{_read_log(result).rstrip()}\n\n"
        f"=== HOST ABSORPTION TOPUP {retry.run_id} ===\n{_read_log(retry).rstrip()}\n",
        encoding="utf-8",
    )

    config = dict(result.config)
    config.update(
        {
            "farm_echo_absorption_target": target,
            "confirmed_farm_echo_absorption_count": total_absorbed,
            "farm_echo_runtime_limit_seconds": retry.config.get(
                "farm_echo_runtime_limit_seconds"
            ),
            "farm_echo_runtime_elapsed_seconds": retry.duration_seconds,
            "farm_echo_absorption_topup": {
                "triggered": True,
                "initial_absorbed": initial_absorbed,
                "retry_requested": remaining,
                "retry_absorbed": retry_absorbed,
                "total_absorbed": total_absorbed,
                "target_count": target,
                "retry_run_id": retry.run_id,
                "retry_status": retry.status,
            },
        }
    )
    finished = datetime.now().astimezone()
    started = datetime.fromisoformat(result.started_at)
    if completed:
        reason = f"Daily completed; FarmEcho absorbed {total_absorbed}/{target} echoes"
    else:
        reason = (
            f"Daily completed but FarmEcho absorption incomplete: "
            f"{total_absorbed}/{target}; retry={retry.reason}"
        )
    composite = OkRunResult(
        run_id=run_id,
        status="success" if completed else "failed",
        reason=reason,
        started_at=result.started_at,
        finished_at=finished.isoformat(),
        duration_seconds=round((finished - started).total_seconds()),
        log_slice_path=str(combined_path),
        evidence_path=retry.evidence_path or result.evidence_path,
        config=config,
        exit_code=0 if completed else 1,
    )
    write_result(composite, run_dir)
    return composite
