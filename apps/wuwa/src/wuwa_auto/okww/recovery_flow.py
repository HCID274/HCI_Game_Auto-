"""Bounded FarmEcho death recovery and exact remaining-count retry."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

from wuwa_auto.okww.config import (
    EXPECTED_REPEAT_FARM_COUNT,
    confirmed_retry_attempt_limit,
    temporary_farm_echo_repeat_count,
)
from wuwa_auto.okww.confirmed_retry import (
    MAX_FARM_ECHO_RUNTIME_SECONDS,
    run_confirmed_farm_echo_retry,
)
from wuwa_auto.okww.logs import (
    count_farm_echo_absorptions,
    is_recoverable_farm_echo_death,
    is_recoverable_farm_echo_entry_failure,
    is_recoverable_farm_echo_realm_defeat,
)
from wuwa_auto.okww.recovery import (
    FarmEchoRecoveryResult,
    run_farm_echo_death_recovery,
    run_farm_echo_realm_defeat_recovery,
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


def _failed_recovery(
    reason: str,
    *,
    realm_defeat: bool = False,
) -> FarmEchoRecoveryResult:
    return FarmEchoRecoveryResult(
        success=False,
        reason=reason,
        evidence_path=None,
        worker_result_path="",
        realm_defeat=realm_defeat,
    )


def _recover_safely(
    run_dir: Path,
    *,
    attempt: int,
    realm_defeat: bool = False,
) -> FarmEchoRecoveryResult:
    try:
        stop_daily_workers()
        recovery = (
            run_farm_echo_realm_defeat_recovery
            if realm_defeat
            else run_farm_echo_death_recovery
        )
        return recovery(run_dir, attempt=attempt)
    except Exception as exc:
        log.exception("FarmEcho safety recovery attempt=%s failed", attempt)
        return _failed_recovery(str(exc), realm_defeat=realm_defeat)


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
    attempts: list[OkRunResult],
    attempt_counts: list[int],
    recoveries: list[FarmEchoRecoveryResult],
    retry_error: str = "",
    entry_retry_attempts: int = 0,
) -> OkRunResult:
    initial_completed = attempt_counts[0]
    retry_completed = sum(attempt_counts[1:])
    total_completed = min(target, sum(attempt_counts))
    completed = total_completed >= target and all(
        recovery.success for recovery in recoveries
    )
    config = dict(initial.config)
    config["confirmed_farm_echo_absorption_count"] = total_completed
    config["farm_echo_recovery"] = {
        "triggered": len(attempts) > 1 or bool(recoveries),
        "target_count": target,
        "initial_completed": initial_completed,
        "first_safe_recovery": recoveries[0].success if recoveries else False,
        "first_recovery_reason": recoveries[0].reason if recoveries else "",
        "recovery_attempts": len(recoveries),
        "entry_retry_attempts": entry_retry_attempts,
        "recoveries": [
            {
                "success": recovery.success,
                "reason": recovery.reason,
                "evidence_path": recovery.evidence_path,
                "resume_active_realm": recovery.resume_active_realm,
                "realm_defeat": recovery.realm_defeat,
            }
            for recovery in recoveries
        ],
        "retry_requested": max(0, target - initial_completed),
        "retry_attempted": len(attempts) > 1,
        "retry_completed": retry_completed,
        "retry_status": attempts[-1].status if len(attempts) > 1 else "not_run",
        "retry_error": retry_error,
        "final_safe_recovery": recoveries[-1].success if recoveries else None,
        "final_recovery_reason": recoveries[-1].reason if recoveries else "",
        "total_completed": total_completed,
        "attempt_run_ids": [attempt.run_id for attempt in attempts],
    }

    run_id, run_dir = _unique_composite_dir(initial.run_id)
    combined_path = run_dir / "ok-current-run.log"
    sections = []
    for index, attempt in enumerate(attempts):
        label = "ATTEMPT" if index == 0 else f"RETRY {index}"
        sections.append(
            f"=== HOST {label} {attempt.run_id} ===\n"
            f"{_read_log(attempt).rstrip()}\n"
        )
    combined_path.write_text("\n".join(sections), encoding="utf-8")

    started = datetime.fromisoformat(initial.started_at)
    finished = datetime.now().astimezone()
    if completed:
        reason = f"FarmEcho recovered and completed {total_completed}/{target}"
    else:
        reason = (
            f"FarmEcho recovery incomplete: absorbed {total_completed}/{target}; "
            f"recoveries={len(recoveries)}"
        )
        if retry_error:
            reason += f"; retry={retry_error}"
        elif attempts[-1].status != "success":
            reason += f"; last_attempt={attempts[-1].reason}"
    evidence = next(
        (
            path
            for path in [
                *(recovery.evidence_path for recovery in reversed(recoveries)),
                *(attempt.evidence_path for attempt in reversed(attempts)),
            ]
            if path
        ),
        None,
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
    """Recover known FarmEcho failures within one shared absorption deadline."""
    if result.status == "success":
        return result
    initial_text = _read_log(result)
    if not (
        is_recoverable_farm_echo_death(initial_text)
        or is_recoverable_farm_echo_entry_failure(initial_text)
    ):
        return result

    target = int(
        result.config.get("farm_echo_absorption_target")
        or result.config.get("target_count")
        or result.config.get("repeat_farm_count")
        or EXPECTED_REPEAT_FARM_COUNT
    )
    structured_completed = result.config.get(
        "confirmed_farm_echo_absorption_count"
    )
    if structured_completed is None:
        initial_completed = count_farm_echo_absorptions(initial_text)
    else:
        initial_completed = int(structured_completed)
    initial_completed = min(target, initial_completed)
    remaining = max(0, target - initial_completed)
    log.warning(
        "FarmEcho death detected: completed=%s target=%s remaining=%s",
        initial_completed,
        target,
        remaining,
    )

    explicit_runtime_limit = result.config.get("farm_echo_runtime_limit_seconds")
    retry_deadline = time.monotonic() + MAX_FARM_ECHO_RUNTIME_SECONDS
    if explicit_runtime_limit is not None:
        elapsed_runtime = float(
            result.config.get("farm_echo_runtime_elapsed_seconds")
            or result.duration_seconds
        )
        remaining_runtime = max(
            0.0,
            float(explicit_runtime_limit) - elapsed_runtime,
        )
        retry_deadline = time.monotonic() + remaining_runtime

    attempts = [result]
    attempt_counts = [initial_completed]
    recoveries: list[FarmEchoRecoveryResult] = []
    entry_retry_attempts = 0
    retry_error = ""
    try:
        current = result
        resume_active_realm = False
        while current.status != "success":
            current_text = _read_log(current)
            death_failure = is_recoverable_farm_echo_death(current_text)
            realm_defeat = is_recoverable_farm_echo_realm_defeat(current_text)
            entry_failure = is_recoverable_farm_echo_entry_failure(current_text)
            if not (death_failure or entry_failure):
                break
            if death_failure:
                recovery = _recover_safely(
                    Path(current.log_slice_path).parent,
                    attempt=len(recoveries) + 1,
                    realm_defeat=realm_defeat,
                )
                recoveries.append(recovery)
                if not recovery.success or sum(attempt_counts) >= target:
                    break
                resume_active_realm = recovery.resume_active_realm
            else:
                # The next worker begins with ensure_main and a verified F2
                # boss-page selection, so no death/teleport-heal UI is needed.
                stop_daily_workers()
                entry_retry_attempts += 1
                log.warning(
                    "FarmEcho pre-combat entry failed; restart remaining target "
                    "within shared deadline attempt=%s",
                    entry_retry_attempts,
                )
                resume_active_realm = False

            remaining = target - sum(attempt_counts)
            remaining_runtime = retry_deadline - time.monotonic()
            if remaining_runtime <= 0:
                raise RuntimeError(
                    "FarmEcho one-hour absorption budget exhausted during recovery"
                )
            attempt_limit = confirmed_retry_attempt_limit(remaining)
            retry_kwargs: dict[str, object] = {
                "target_count": remaining,
                "attempt_limit": attempt_limit,
                "runtime_limit_seconds": min(
                    MAX_FARM_ECHO_RUNTIME_SECONDS,
                    remaining_runtime,
                ),
            }
            if resume_active_realm:
                retry_kwargs["resume_active_realm"] = True
            resume_active_realm = False
            with temporary_farm_echo_repeat_count(attempt_limit):
                current = run_confirmed_farm_echo_retry(
                    **retry_kwargs,
                )
            attempts.append(current)
            attempt_counts.append(
                min(
                    remaining,
                    int(
                        current.config.get(
                            "confirmed_farm_echo_absorption_count"
                        )
                        or 0
                    ),
                )
            )
    except Exception as exc:
        retry_error = str(exc)
        log.exception("FarmEcho bounded retry failed before returning a result")
    finally:
        # A returned worker is already exiting; do not let any failed leaf race
        # final cleanup.
        stop_daily_workers()

    return _write_composite(
        result,
        target=target,
        attempts=attempts,
        attempt_counts=attempt_counts,
        recoveries=recoveries,
        retry_error=retry_error,
        entry_retry_attempts=entry_retry_attempts,
    )
