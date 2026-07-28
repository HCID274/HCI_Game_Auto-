"""Bounded FarmEcho death recovery and exact remaining-count retry."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from wuwa_auto.okww.config import (
    EXPECTED_REPEAT_FARM_COUNT,
    confirmed_retry_attempt_limit,
    temporary_farm_echo_repeat_count,
)
from wuwa_auto.okww.confirmed_retry import run_confirmed_farm_echo_retry
from wuwa_auto.okww.logs import (
    count_farm_echo_kill_confirmations,
    is_recoverable_farm_echo_death,
)
from wuwa_auto.okww.recovery import (
    FarmEchoRecoveryResult,
    run_farm_echo_death_recovery,
)
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


def _failed_recovery(reason: str) -> FarmEchoRecoveryResult:
    return FarmEchoRecoveryResult(
        success=False,
        reason=reason,
        evidence_path=None,
        worker_result_path="",
    )


def _recover_safely(
    run_dir: Path,
    *,
    attempt: int,
) -> FarmEchoRecoveryResult:
    try:
        stop_daily_workers()
        return run_farm_echo_death_recovery(run_dir, attempt=attempt)
    except Exception as exc:
        log.exception("FarmEcho safety recovery attempt=%s failed", attempt)
        return _failed_recovery(str(exc))


def _unique_composite_dir(base_run_id: str) -> tuple[str, Path]:
    base = f"{base_run_id}_recovery"
    run_id = base
    run_dir = RUNS_DIR / run_id
    suffix = 1
    while run_dir.exists():
        suffix += 1
        run_id = f"{base}_{suffix}"
        run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True)
    return run_id, run_dir


def _write_composite(
    initial: OkRunResult,
    *,
    target: int,
    initial_completed: int,
    first_recovery: FarmEchoRecoveryResult,
    retry: OkRunResult | None,
    retry_requested: int,
    retry_completed: int,
    final_recovery: FarmEchoRecoveryResult | None,
    retry_error: str = "",
) -> OkRunResult:
    total_completed = min(target, initial_completed + retry_completed)
    completed = (
        first_recovery.success
        and total_completed >= target
        and (retry_requested == 0 or (retry is not None and retry.status == "success"))
    )
    config = dict(initial.config)
    config["farm_echo_recovery"] = {
        "triggered": True,
        "target_count": target,
        "initial_completed": initial_completed,
        "first_safe_recovery": first_recovery.success,
        "first_recovery_reason": first_recovery.reason,
        "retry_requested": retry_requested,
        "retry_attempted": retry is not None,
        "retry_completed": retry_completed,
        "retry_status": retry.status if retry is not None else "not_run",
        "retry_error": retry_error,
        "final_safe_recovery": (
            final_recovery.success if final_recovery is not None else None
        ),
        "final_recovery_reason": (
            final_recovery.reason if final_recovery is not None else ""
        ),
        "total_completed": total_completed,
        "attempt_run_ids": [
            initial.run_id,
            *([retry.run_id] if retry is not None else []),
        ],
    }

    run_id, run_dir = _unique_composite_dir(initial.run_id)
    combined_path = run_dir / "ok-current-run.log"
    sections = [
        f"=== HOST ATTEMPT {initial.run_id} ===\n{_read_log(initial).rstrip()}\n"
    ]
    if retry is not None:
        sections.append(
            f"=== HOST RETRY {retry.run_id} ===\n{_read_log(retry).rstrip()}\n"
        )
    combined_path.write_text("\n".join(sections), encoding="utf-8")

    started = datetime.fromisoformat(initial.started_at)
    finished = datetime.now().astimezone()
    if completed:
        reason = f"FarmEcho recovered and completed {total_completed}/{target}"
    else:
        reason = (
            f"FarmEcho recovery incomplete: completed {total_completed}/{target}; "
            f"first_recovery={first_recovery.reason}"
        )
        if retry_error:
            reason += f"; retry={retry_error}"
        elif retry is not None and retry.status != "success":
            reason += f"; retry={retry.reason}"
        if final_recovery is not None:
            reason += f"; final_recovery={final_recovery.reason}"
    evidence = (
        (final_recovery.evidence_path if final_recovery is not None else None)
        or (retry.evidence_path if retry is not None else None)
        or first_recovery.evidence_path
        or initial.evidence_path
    )
    result = OkRunResult(
        run_id=run_id,
        status="success" if completed else "failed",
        reason=reason,
        started_at=initial.started_at,
        finished_at=finished.isoformat(),
        duration_seconds=round((finished - started).total_seconds()),
        log_slice_path=str(combined_path),
        evidence_path=evidence,
        config=config,
        exit_code=0 if completed else 1,
    )
    write_result(result, run_dir)
    log.info("FarmEcho composite result: %s", result)
    return result


def maybe_recover_farm_echo_death(result: OkRunResult) -> OkRunResult:
    """Recover one death and retry only the exact remaining count once."""
    if result.status == "success":
        return result
    initial_text = _read_log(result)
    if not is_recoverable_farm_echo_death(initial_text):
        return result

    target = int(
        result.config.get("repeat_farm_count") or EXPECTED_REPEAT_FARM_COUNT
    )
    structured_completed = result.config.get("confirmed_farm_echo_count")
    if structured_completed is None:
        initial_completed = count_farm_echo_kill_confirmations(initial_text)
    else:
        initial_completed = int(structured_completed)
    initial_completed = min(target, initial_completed)
    remaining = max(0, target - initial_completed)
    initial_run_dir = Path(result.log_slice_path).parent
    log.warning(
        "FarmEcho death detected: completed=%s target=%s remaining=%s",
        initial_completed,
        target,
        remaining,
    )

    first_recovery = _recover_safely(initial_run_dir, attempt=1)
    if not first_recovery.success or remaining == 0:
        return _write_composite(
            result,
            target=target,
            initial_completed=initial_completed,
            first_recovery=first_recovery,
            retry=None,
            retry_requested=remaining,
            retry_completed=0,
            final_recovery=None,
        )

    retry: OkRunResult | None = None
    retry_completed = 0
    retry_error = ""
    final_recovery: FarmEchoRecoveryResult | None = None
    try:
        attempt_limit = confirmed_retry_attempt_limit(remaining)
        with temporary_farm_echo_repeat_count(attempt_limit):
            retry = run_confirmed_farm_echo_retry(
                target_count=remaining,
                attempt_limit=attempt_limit,
            )
        retry_text = _read_log(retry)
        retry_completed = min(
            remaining,
            int(retry.config.get("confirmed_farm_echo_count") or 0),
        )
        if retry.status != "success" and is_recoverable_farm_echo_death(retry_text):
            final_recovery = _recover_safely(
                Path(retry.log_slice_path).parent,
                attempt=2,
            )
    except Exception as exc:
        retry_error = str(exc)
        log.exception("FarmEcho bounded retry failed before returning a result")
    finally:
        # A successful worker is already exiting; a failed worker must not race
        # final cleanup or a second safety recovery.
        stop_daily_workers()

    return _write_composite(
        result,
        target=target,
        initial_completed=initial_completed,
        first_recovery=first_recovery,
        retry=retry,
        retry_requested=remaining,
        retry_completed=retry_completed,
        final_recovery=final_recovery,
        retry_error=retry_error,
    )
