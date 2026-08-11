"""Bounded FarmEcho death recovery and exact remaining-count retry."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
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
    has_farm_echo_combat_degradation,
    is_recoverable_farm_echo_death,
    is_recoverable_farm_echo_entry_failure,
    is_recoverable_farm_echo_party_member_unavailable,
    is_recoverable_farm_echo_realm_defeat,
)
from wuwa_auto.okww.recovery import (
    FarmEchoRecoveryResult,
    run_farm_echo_death_recovery,
    run_farm_echo_party_member_recovery,
    run_farm_echo_realm_defeat_recovery,
)
from wuwa_auto.okww.runner import (
    OkRunResult,
    stop_daily_workers,
    write_result,
)
from wuwa_auto.settings import RUNS_DIR

log = logging.getLogger(__name__)

MAX_CONSECUTIVE_NO_PROGRESS_RETRIES = 3
DEGRADED_RUNS_BEFORE_CLIENT_RESTART = 2


def _read_log(result: OkRunResult) -> str:
    path = Path(result.log_slice_path)
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def _failed_recovery(
    reason: str,
    *,
    realm_defeat: bool = False,
    kind: str = "death_recovery",
) -> FarmEchoRecoveryResult:
    return FarmEchoRecoveryResult(
        success=False,
        reason=reason,
        evidence_path=None,
        worker_result_path="",
        realm_defeat=realm_defeat,
        kind=kind,
    )


def _recover_safely(
    run_dir: Path,
    *,
    attempt: int,
    realm_defeat: bool = False,
    party_member_unavailable: bool = False,
) -> FarmEchoRecoveryResult:
    try:
        stop_daily_workers()
        if party_member_unavailable:
            recovery = run_farm_echo_party_member_recovery
        elif realm_defeat:
            recovery = run_farm_echo_realm_defeat_recovery
        else:
            recovery = run_farm_echo_death_recovery
        return recovery(run_dir, attempt=attempt)
    except Exception as exc:
        log.exception("FarmEcho safety recovery attempt=%s failed", attempt)
        return _failed_recovery(
            str(exc),
            realm_defeat=realm_defeat,
            kind=(
                "party_member_unavailable_recovery"
                if party_member_unavailable
                else "realm_defeat_recovery"
                if realm_defeat
                else "death_recovery"
            ),
        )


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
    client_restart_triggered: bool = False,
    combat_rebind_attempts: int = 0,
    consecutive_no_progress_retries: int = 0,
) -> OkRunResult:
    game_recoveries = [
        recovery for recovery in recoveries
        if recovery.kind != "client_restart"
    ]
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
        "first_safe_recovery": (
            game_recoveries[0].success if game_recoveries else False
        ),
        "first_recovery_reason": (
            game_recoveries[0].reason if game_recoveries else ""
        ),
        "recovery_attempts": len(game_recoveries),
        "entry_retry_attempts": entry_retry_attempts,
        "combat_rebind_attempts": combat_rebind_attempts,
        "client_restart_triggered": client_restart_triggered,
        "retry_limit": None,
        "progress_driven_retries": True,
        "no_progress_retry_limit": MAX_CONSECUTIVE_NO_PROGRESS_RETRIES,
        "consecutive_no_progress_retries": consecutive_no_progress_retries,
        "retry_runs": max(0, len(attempts) - 1),
        "recoveries": [
            {
                "success": recovery.success,
                "reason": recovery.reason,
                "evidence_path": recovery.evidence_path,
                "resume_active_realm": recovery.resume_active_realm,
                "realm_defeat": recovery.realm_defeat,
                "kind": recovery.kind,
            }
            for recovery in recoveries
        ],
        "retry_requested": max(0, target - initial_completed),
        "retry_attempted": len(attempts) > 1,
        "retry_completed": retry_completed,
        "retry_status": attempts[-1].status if len(attempts) > 1 else "not_run",
        "retry_error": retry_error,
        "final_safe_recovery": (
            game_recoveries[-1].success if game_recoveries else None
        ),
        "final_recovery_reason": (
            game_recoveries[-1].reason if game_recoveries else ""
        ),
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
    retry_runs = max(0, len(attempts) - 1)
    if completed:
        reason = (
            f"FarmEcho recovered and completed {total_completed}/{target}; "
            f"worker_retries={retry_runs} (progress-driven); "
            f"combat_rebinds={combat_rebind_attempts}; "
            f"client_restarts={int(client_restart_triggered)}"
        )
    else:
        reason = (
            f"FarmEcho recovery incomplete: absorbed {total_completed}/{target}; "
            f"worker_retries={retry_runs} (progress-driven); "
            f"combat_rebinds={combat_rebind_attempts}; "
            f"client_restarts={int(client_restart_triggered)}; "
            f"recoveries={len(game_recoveries)}"
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


def maybe_recover_farm_echo_death(
    result: OkRunResult,
    *,
    client_restart: Callable[[], bool] | None = None,
) -> OkRunResult:
    """Recover until target, bounding consecutive retries that make no progress."""
    if result.status == "success":
        return result
    initial_text = _read_log(result)
    confirmed_worker = result.config.get("workflow_task") == (
        "farm_echo_confirmed_retry"
    )
    if not (
        is_recoverable_farm_echo_death(initial_text)
        or is_recoverable_farm_echo_entry_failure(initial_text)
        or has_farm_echo_combat_degradation(initial_text)
        or bool(result.config.get("farm_echo_startup_network_retry_exhausted"))
        or confirmed_worker
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
        "FarmEcho recoverable failure detected: completed=%s target=%s remaining=%s",
        initial_completed,
        target,
        remaining,
    )

    no_progress_window = min(
        MAX_FARM_ECHO_RUNTIME_SECONDS,
        float(
            result.config.get("farm_echo_no_progress_timeout_seconds")
            or result.config.get("farm_echo_runtime_limit_seconds")
            or MAX_FARM_ECHO_RUNTIME_SECONDS
        ),
    )
    retry_deadline = time.monotonic() + no_progress_window

    attempts = [result]
    attempt_counts = [initial_completed]
    recoveries: list[FarmEchoRecoveryResult] = []
    entry_retry_attempts = 0
    client_restart_done = False
    degraded_runs = 0
    combat_rebind_attempts = 0
    consecutive_no_progress_retries = 0
    retry_error = ""
    try:
        current = result
        resume_active_realm = False
        while sum(attempt_counts) < target:
            current_text = _read_log(current)
            death_failure = is_recoverable_farm_echo_death(current_text)
            realm_defeat = is_recoverable_farm_echo_realm_defeat(current_text)
            party_member_unavailable = (
                is_recoverable_farm_echo_party_member_unavailable(current_text)
            )
            entry_failure = is_recoverable_farm_echo_entry_failure(current_text)
            degraded = has_farm_echo_combat_degradation(current_text)
            live_degraded = bool(
                current.config.get("farm_echo_live_combat_degradation")
            )
            startup_network_failure = bool(
                current.config.get("farm_echo_startup_network_retry_exhausted")
            )
            confirmed_worker_failure = current.config.get("workflow_task") == (
                "farm_echo_confirmed_retry"
            )
            if not (
                death_failure
                or entry_failure
                or degraded
                or startup_network_failure
                or confirmed_worker_failure
            ):
                break
            if death_failure:
                recovery_kwargs: dict[str, object] = {
                    "attempt": (
                        sum(
                            item.kind != "client_restart"
                            for item in recoveries
                        )
                        + 1
                    ),
                    "realm_defeat": realm_defeat,
                }
                if party_member_unavailable:
                    recovery_kwargs["party_member_unavailable"] = True
                recovery = _recover_safely(
                    Path(current.log_slice_path).parent,
                    **recovery_kwargs,
                )
                recoveries.append(recovery)
                if not recovery.success or sum(attempt_counts) >= target:
                    break
                resume_active_realm = recovery.resume_active_realm

            if (
                consecutive_no_progress_retries
                >= MAX_CONSECUTIVE_NO_PROGRESS_RETRIES
            ):
                retry_error = (
                    "maximum consecutive FarmEcho no-progress retries exhausted: "
                    f"{MAX_CONSECUTIVE_NO_PROGRESS_RETRIES}"
                )
                break

            if entry_failure:
                entry_retry_attempts += 1

            if degraded:
                degraded_runs += 1
            if live_degraded and not death_failure:
                resume_active_realm = True

            restart_for_network = startup_network_failure and not client_restart_done
            restart_for_degradation = (
                degraded
                and degraded_runs >= DEGRADED_RUNS_BEFORE_CLIENT_RESTART
                and not client_restart_done
            )
            if startup_network_failure and client_restart_done:
                retry_error = (
                    "FarmEcho startup network retry failed again after one clean "
                    "OK-owned client restart"
                )
                break
            if restart_for_network or restart_for_degradation:
                if client_restart is None:
                    retry_error = (
                        "FarmEcho requires a clean client restart but no restart "
                        "adapter is available"
                    )
                    break
                client_restart_done = True
                restart_reason = (
                    "startup network retry exhausted"
                    if restart_for_network
                    else "upstream combat degradation detected"
                )
                log.warning("FarmEcho %s; restarting the client once", restart_reason)
                try:
                    restarted = bool(client_restart())
                except Exception as exc:
                    retry_error = f"client restart failed: {exc}"
                    log.exception("FarmEcho client restart failed")
                    break
                if not restarted:
                    retry_error = "client restart adapter returned false"
                    break
                recoveries.append(
                    FarmEchoRecoveryResult(
                        success=True,
                        reason=f"client restarted once after {restart_reason}",
                        evidence_path=None,
                        worker_result_path="",
                        kind="client_restart",
                    )
                )
                resume_active_realm = False

            remaining = target - sum(attempt_counts)
            remaining_runtime = retry_deadline - time.monotonic()
            if remaining_runtime <= 0:
                raise RuntimeError(
                    "FarmEcho no-progress window exhausted during recovery"
                )
            attempt_limit = confirmed_retry_attempt_limit(remaining)
            retry_kwargs: dict[str, object] = {
                "target_count": remaining,
                "attempt_limit": attempt_limit,
                "runtime_limit_seconds": no_progress_window,
            }
            if resume_active_realm:
                retry_kwargs["resume_active_realm"] = True
                combat_rebind_attempts += 1
            resume_active_realm = False
            with temporary_farm_echo_repeat_count(attempt_limit):
                current = run_confirmed_farm_echo_retry(
                    **retry_kwargs,
                )
            attempts.append(current)
            completed_this_run = min(
                remaining,
                int(
                    current.config.get("confirmed_farm_echo_absorption_count")
                    or 0
                ),
            )
            attempt_counts.append(completed_this_run)
            if completed_this_run > 0:
                consecutive_no_progress_retries = 0
                degraded_runs = 0
                retry_deadline = time.monotonic() + no_progress_window
                log.info(
                    "FarmEcho total progress advanced to %s/%s; Worker recovery "
                    "remains unlimited and the no-progress window was reset",
                    min(target, sum(attempt_counts)),
                    target,
                )
            else:
                consecutive_no_progress_retries += 1
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
        client_restart_triggered=client_restart_done,
        combat_rebind_attempts=combat_rebind_attempts,
        consecutive_no_progress_retries=consecutive_no_progress_retries,
    )
