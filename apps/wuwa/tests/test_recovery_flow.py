from contextlib import nullcontext
from pathlib import Path
from unittest.mock import ANY, patch

from wuwa_auto.okww.recovery import FarmEchoRecoveryResult
from wuwa_auto.okww.recovery_flow import maybe_recover_farm_echo_death
from wuwa_auto.okww.runner import OkRunResult


DEATH = """
FarmEchoTask:raise_not_in_combat char dead
FarmEchoTask:info_set Revive Failed
Daily Task exception stopped
"""
ABSORPTION = "FarmEchoTask:farm echo walk_find_echo True\n"
RESTART_CONFIRMATION = (
    "FarmEchoTask:left_click claim_cancel_button_hcenter_vcenter "
    "(769, 900) after_sleep 0\n"
)


def _result(
    root: Path,
    run_id: str,
    text: str,
    *,
    status: str,
    absorbed: int | None = None,
) -> OkRunResult:
    run_dir = root / run_id
    run_dir.mkdir(parents=True)
    log = run_dir / "ok-current-run.log"
    log.write_text(text, encoding="utf-8")
    config: dict[str, object] = {
        "workflow_task": "daily",
        "repeat_farm_count": 5,
        "boss_challenge_index": 2,
    }
    if absorbed is not None:
        config["confirmed_farm_echo_absorption_count"] = absorbed
    return OkRunResult(
        run_id=run_id,
        status=status,
        reason="test",
        started_at="2026-07-28T05:00:00+09:00",
        finished_at="2026-07-28T05:10:00+09:00",
        duration_seconds=600,
        log_slice_path=str(log),
        evidence_path=None,
        config=config,
        exit_code=0 if status == "success" else 1,
    )


def _safe(reason: str = "healed") -> FarmEchoRecoveryResult:
    return FarmEchoRecoveryResult(True, reason, None, "recovery.json")


def test_death_recovers_and_retries_only_remaining_count(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    initial = _result(runs, "initial", ABSORPTION * 3 + DEATH, status="failed")
    retry = _result(
        runs,
        "retry",
        ABSORPTION * 2,
        status="success",
        absorbed=2,
    )

    with patch(
        "wuwa_auto.okww.recovery_flow.RUNS_DIR", runs
    ), patch(
        "wuwa_auto.okww.recovery_flow._recover_safely", return_value=_safe()
    ) as recover, patch(
        "wuwa_auto.okww.recovery_flow.temporary_farm_echo_repeat_count",
        side_effect=lambda count: nullcontext(),
    ) as override, patch(
        "wuwa_auto.okww.recovery_flow.run_confirmed_farm_echo_retry",
        return_value=retry,
    ) as run_retry, patch(
        "wuwa_auto.okww.recovery_flow.stop_daily_workers"
    ):
        result = maybe_recover_farm_echo_death(initial)

    assert result.status == "success"
    assert result.exit_code == 0
    recovery = result.config["farm_echo_recovery"]
    assert recovery["initial_completed"] == 3
    assert recovery["retry_requested"] == 2
    assert recovery["retry_completed"] == 2
    assert recovery["total_completed"] == 5
    recover.assert_called_once()
    override.assert_called_once_with(24)
    run_retry.assert_called_once_with(
        target_count=2,
        attempt_limit=24,
        runtime_limit_seconds=ANY,
    )


def test_death_before_first_completion_retries_full_target(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    initial = _result(runs, "initial", DEATH, status="failed")
    retry = _result(
        runs,
        "retry",
        ABSORPTION * 5,
        status="success",
        absorbed=5,
    )

    with patch(
        "wuwa_auto.okww.recovery_flow.RUNS_DIR", runs
    ), patch(
        "wuwa_auto.okww.recovery_flow._recover_safely", return_value=_safe()
    ), patch(
        "wuwa_auto.okww.recovery_flow.temporary_farm_echo_repeat_count",
        side_effect=lambda count: nullcontext(),
    ) as override, patch(
        "wuwa_auto.okww.recovery_flow.run_confirmed_farm_echo_retry",
        return_value=retry,
    ) as run_retry, patch(
        "wuwa_auto.okww.recovery_flow.stop_daily_workers"
    ):
        result = maybe_recover_farm_echo_death(initial)

    assert result.status == "success"
    recovery = result.config["farm_echo_recovery"]
    assert recovery["initial_completed"] == 0
    assert recovery["retry_requested"] == 5
    assert recovery["retry_completed"] == 5
    assert recovery["total_completed"] == 5
    override.assert_called_once_with(60)
    run_retry.assert_called_once_with(
        target_count=5,
        attempt_limit=60,
        runtime_limit_seconds=ANY,
    )


def test_structured_zero_does_not_count_stale_restart_click(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    initial = _result(
        runs,
        "initial",
        RESTART_CONFIRMATION + DEATH,
        status="failed",
        absorbed=0,
    )
    initial.config["repeat_farm_count"] = 1
    retry = _result(
        runs,
        "retry",
        "HOST_FARM_ECHO_ABSORPTION_CONFIRMED 1/1\n",
        status="success",
        absorbed=1,
    )

    with patch(
        "wuwa_auto.okww.recovery_flow.RUNS_DIR", runs
    ), patch(
        "wuwa_auto.okww.recovery_flow._recover_safely", return_value=_safe()
    ), patch(
        "wuwa_auto.okww.recovery_flow.temporary_farm_echo_repeat_count",
        side_effect=lambda count: nullcontext(),
    ) as override, patch(
        "wuwa_auto.okww.recovery_flow.run_confirmed_farm_echo_retry",
        return_value=retry,
    ) as run_retry, patch(
        "wuwa_auto.okww.recovery_flow.stop_daily_workers"
    ):
        result = maybe_recover_farm_echo_death(initial)

    recovery = result.config["farm_echo_recovery"]
    assert result.status == "success"
    assert recovery["initial_completed"] == 0
    assert recovery["retry_requested"] == 1
    assert recovery["total_completed"] == 1
    override.assert_called_once_with(12)
    run_retry.assert_called_once_with(
        target_count=1,
        attempt_limit=12,
        runtime_limit_seconds=ANY,
    )


def test_second_death_retries_again_within_the_shared_deadline(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    initial = _result(runs, "initial", ABSORPTION * 3 + DEATH, status="failed")
    first_retry = _result(
        runs,
        "retry-1",
        ABSORPTION + DEATH,
        status="failed",
        absorbed=1,
    )
    second_retry = _result(
        runs,
        "retry-2",
        ABSORPTION,
        status="success",
        absorbed=1,
    )

    with patch(
        "wuwa_auto.okww.recovery_flow.RUNS_DIR", runs
    ), patch(
        "wuwa_auto.okww.recovery_flow._recover_safely",
        side_effect=[_safe("first"), _safe("second")],
    ) as recover, patch(
        "wuwa_auto.okww.recovery_flow.temporary_farm_echo_repeat_count",
        side_effect=lambda count: nullcontext(),
    ), patch(
        "wuwa_auto.okww.recovery_flow.run_confirmed_farm_echo_retry",
        side_effect=[first_retry, second_retry],
    ) as run_retry, patch(
        "wuwa_auto.okww.recovery_flow.stop_daily_workers"
    ):
        result = maybe_recover_farm_echo_death(initial)

    assert result.status == "success"
    recovery = result.config["farm_echo_recovery"]
    assert recovery["total_completed"] == 5
    assert recovery["recovery_attempts"] == 2
    assert recovery["final_safe_recovery"] is True
    assert recover.call_count == 2
    assert run_retry.call_count == 2
    assert run_retry.call_args_list[0].kwargs["target_count"] == 2
    assert run_retry.call_args_list[1].kwargs["target_count"] == 1


def test_recovery_retry_shares_the_one_hour_absorption_budget(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    initial = _result(
        runs,
        "initial",
        DEATH,
        status="failed",
        absorbed=0,
    )
    initial.config.update(
        {
            "repeat_farm_count": 1,
            "target_count": 1,
            "farm_echo_runtime_limit_seconds": 3600,
        }
    )
    retry = _result(
        runs,
        "retry",
        ABSORPTION,
        status="success",
        absorbed=1,
    )

    with patch(
        "wuwa_auto.okww.recovery_flow.RUNS_DIR", runs
    ), patch(
        "wuwa_auto.okww.recovery_flow.time.monotonic",
        side_effect=[0.0, 100.0, 200.0],
    ), patch(
        "wuwa_auto.okww.recovery_flow._recover_safely", return_value=_safe()
    ), patch(
        "wuwa_auto.okww.recovery_flow.temporary_farm_echo_repeat_count",
        side_effect=lambda count: nullcontext(),
    ), patch(
        "wuwa_auto.okww.recovery_flow.run_confirmed_farm_echo_retry",
        return_value=retry,
    ) as run_retry, patch(
        "wuwa_auto.okww.recovery_flow.stop_daily_workers"
    ):
        result = maybe_recover_farm_echo_death(initial)

    assert result.status == "success"
    run_retry.assert_called_once_with(
        target_count=1,
        attempt_limit=12,
        runtime_limit_seconds=2900.0,
    )


def test_deadline_exhaustion_recovers_but_starts_no_more_battles(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    initial = _result(
        runs,
        "initial",
        DEATH,
        status="failed",
        absorbed=0,
    )
    initial.config.update(
        {
            "target_count": 1,
            "farm_echo_runtime_limit_seconds": 3600,
            "farm_echo_runtime_elapsed_seconds": 3600,
        }
    )

    with patch(
        "wuwa_auto.okww.recovery_flow.RUNS_DIR", runs
    ), patch(
        "wuwa_auto.okww.recovery_flow.time.monotonic",
        side_effect=[0.0, 100.0, 100.0],
    ), patch(
        "wuwa_auto.okww.recovery_flow._recover_safely", return_value=_safe()
    ), patch(
        "wuwa_auto.okww.recovery_flow.run_confirmed_farm_echo_retry"
    ) as run_retry, patch(
        "wuwa_auto.okww.recovery_flow.stop_daily_workers"
    ):
        result = maybe_recover_farm_echo_death(initial)

    assert result.status == "failed"
    assert "budget exhausted" in result.config["farm_echo_recovery"]["retry_error"]
    assert result.config["farm_echo_recovery"]["first_safe_recovery"] is True
    run_retry.assert_not_called()


def test_entry_failure_restarts_without_claiming_death_recovery(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    initial = _result(
        runs,
        "initial",
        "FarmEchoTask:info_set app Teleport to boss failed\n"
        "RuntimeError: Teleport to boss failed\n",
        status="failed",
        absorbed=0,
    )
    retry = _result(
        runs,
        "retry",
        "HOST_FARM_ECHO_ABSORPTION_CONFIRMED 5/5\n",
        status="success",
        absorbed=5,
    )

    with patch(
        "wuwa_auto.okww.recovery_flow.RUNS_DIR", runs
    ), patch(
        "wuwa_auto.okww.recovery_flow._recover_safely"
    ) as recover, patch(
        "wuwa_auto.okww.recovery_flow.temporary_farm_echo_repeat_count",
        side_effect=lambda count: nullcontext(),
    ), patch(
        "wuwa_auto.okww.recovery_flow.run_confirmed_farm_echo_retry",
        return_value=retry,
    ), patch(
        "wuwa_auto.okww.recovery_flow.stop_daily_workers"
    ):
        result = maybe_recover_farm_echo_death(initial)

    assert result.status == "success"
    recovery = result.config["farm_echo_recovery"]
    assert recovery["entry_retry_attempts"] == 1
    assert recovery["recovery_attempts"] == 0
    assert recovery["total_completed"] == 5
    recover.assert_not_called()
