from contextlib import nullcontext
from pathlib import Path
from unittest.mock import ANY, patch

from wuwa_auto.okww.recovery import FarmEchoRecoveryResult
from wuwa_auto.okww.recovery_flow import (
    _recover_safely,
    maybe_recover_farm_echo_death,
)
from wuwa_auto.okww.runner import OkRunResult


class _FakeClock:
    """Deterministic perf_counter stand-in that advances per call."""

    def __init__(self, *, start: float = 0.0, step: float) -> None:
        self._now = start
        self._step = step

    def __call__(self) -> float:
        value = self._now
        self._now += self._step
        return value

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
REALM_DEFEAT = "FarmEchoTask:HOST_FARM_ECHO_REALM_DEFEAT_CONFIRMED\n"
PARTY_MEMBER_UNAVAILABLE = (
    "FarmEchoTask:HOST_FARM_ECHO_PARTY_MEMBER_UNAVAILABLE_CONFIRMED\n"
)
CURRENT_CHAR_BIND_FAILURE = (
    "FarmEchoTask:could not find char 0 please check current char\n"
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


def _safe_in_place() -> FarmEchoRecoveryResult:
    return FarmEchoRecoveryResult(
        True,
        "revived in place",
        None,
        "recovery.json",
        resume_active_realm=True,
    )


def _failed_safe_recovery(
    reason: str = "safe state not confirmed",
) -> FarmEchoRecoveryResult:
    return FarmEchoRecoveryResult(
        False,
        reason,
        None,
        "recovery.json",
        kind="party_member_unavailable_recovery",
    )


def test_live_degradation_rebinds_fresh_worker_in_active_realm(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    initial = _result(
        runs,
        "initial",
        CURRENT_CHAR_BIND_FAILURE,
        status="failed",
    )
    initial.config["farm_echo_live_combat_degradation"] = True
    retry = _result(
        runs,
        "retry",
        ABSORPTION,
        status="success",
        absorbed=1,
    )
    initial.config["target_count"] = 1

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
    ) as run_retry, patch(
        "wuwa_auto.okww.recovery_flow.stop_daily_workers"
    ):
        result = maybe_recover_farm_echo_death(initial)

    assert result.status == "success"
    recover.assert_not_called()
    assert run_retry.call_args.kwargs["resume_active_realm"] is True
    recovery = result.config["farm_echo_recovery"]
    assert recovery["combat_rebind_attempts"] == 1
    assert recovery["recovery_attempts"] == 0


def test_failed_active_realm_bind_restarts_client_then_retries_normally(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    initial = _result(
        runs,
        "initial",
        CURRENT_CHAR_BIND_FAILURE,
        status="failed",
    )
    initial.config.update(
        target_count=1,
        farm_echo_live_combat_degradation=True,
    )
    bind_failure = _result(
        runs,
        "bind-failure",
        CURRENT_CHAR_BIND_FAILURE,
        status="failed",
    )
    bind_failure.config["farm_echo_live_combat_degradation"] = True
    success = _result(
        runs,
        "success",
        ABSORPTION,
        status="success",
        absorbed=1,
    )
    restarts: list[str] = []

    with patch(
        "wuwa_auto.okww.recovery_flow.RUNS_DIR", runs
    ), patch(
        "wuwa_auto.okww.recovery_flow._recover_safely"
    ) as recover, patch(
        "wuwa_auto.okww.recovery_flow.temporary_farm_echo_repeat_count",
        side_effect=lambda count: nullcontext(),
    ), patch(
        "wuwa_auto.okww.recovery_flow.run_confirmed_farm_echo_retry",
        side_effect=[bind_failure, success],
    ) as run_retry, patch(
        "wuwa_auto.okww.recovery_flow.stop_daily_workers"
    ):
        result = maybe_recover_farm_echo_death(
            initial,
            client_restart=lambda: not restarts.append("restart"),
        )

    assert result.status == "success"
    assert restarts == ["restart"]
    assert recover.call_count == 0
    assert run_retry.call_count == 2
    assert run_retry.call_args_list[0].kwargs["resume_active_realm"] is True
    assert "resume_active_realm" not in run_retry.call_args_list[1].kwargs
    recovery = result.config["farm_echo_recovery"]
    assert recovery["combat_rebind_attempts"] == 1
    assert recovery["client_restart_triggered"] is True
    assert recovery["retry_runs"] == 2


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
    override.assert_called_once_with(5)
    run_retry.assert_called_once_with(
        target_count=2,
        attempt_limit=5,
        runtime_limit_seconds=ANY,
    )


def test_party_member_unavailable_exits_and_heals_before_retry(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    initial = _result(
        runs,
        "initial",
        PARTY_MEMBER_UNAVAILABLE,
        status="failed",
    )
    initial.config["target_count"] = 1
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
        "wuwa_auto.okww.recovery_flow._recover_safely",
        return_value=_safe("party healed"),
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
    assert recover.call_args.kwargs["party_member_unavailable"] is True
    assert recover.call_args.kwargs["realm_defeat"] is False


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
    override.assert_called_once_with(5)
    run_retry.assert_called_once_with(
        target_count=5,
        attempt_limit=5,
        runtime_limit_seconds=ANY,
    )


def test_realm_defeat_uses_confirmed_retry_recovery(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    initial = _result(
        runs,
        "initial",
        REALM_DEFEAT,
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
        "wuwa_auto.okww.recovery_flow._recover_safely", return_value=_safe()
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
    assert result.config["farm_echo_recovery"]["total_completed"] == 5
    recover.assert_called_once_with(
        Path(initial.log_slice_path).parent,
        attempt=1,
        realm_defeat=True,
    )


def test_failed_realm_recovery_keeps_realm_classification(tmp_path: Path) -> None:
    with patch(
        "wuwa_auto.okww.recovery_flow.stop_daily_workers"
    ), patch(
        "wuwa_auto.okww.recovery_flow.run_farm_echo_realm_defeat_recovery",
        side_effect=RuntimeError("recovery worker failed"),
    ):
        result = _recover_safely(
            tmp_path,
            attempt=1,
            realm_defeat=True,
        )

    assert result.success is False
    assert result.realm_defeat is True


def test_in_place_recovery_passes_one_shot_realm_handoff(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    initial = _result(runs, "initial", DEATH, status="failed", absorbed=3)
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
        "wuwa_auto.okww.recovery_flow._recover_safely",
        return_value=_safe_in_place(),
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
    assert run_retry.call_args.kwargs["resume_active_realm"] is True


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
    override.assert_called_once_with(5)
    run_retry.assert_called_once_with(
        target_count=1,
        attempt_limit=5,
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


def test_recovery_retry_uses_a_full_no_progress_window(
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
        result = maybe_recover_farm_echo_death(
            initial,
            now_fn=_FakeClock(step=100.0),
            sleep_fn=lambda _seconds: None,
        )

    assert result.status == "success"
    run_retry.assert_called_once_with(
        target_count=1,
        attempt_limit=5,
        runtime_limit_seconds=3600.0,
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
        }
    )

    def clock() -> float:
        values = iter([0.0, 3600.5, 3601.5])
        return lambda: next(values)

    with patch(
        "wuwa_auto.okww.recovery_flow.RUNS_DIR", runs
    ), patch(
        "wuwa_auto.okww.recovery_flow._recover_safely", return_value=_safe()
    ), patch(
        "wuwa_auto.okww.recovery_flow.run_confirmed_farm_echo_retry"
    ) as run_retry, patch(
        "wuwa_auto.okww.recovery_flow.stop_daily_workers"
    ):
        result = maybe_recover_farm_echo_death(
            initial,
            now_fn=clock(),
            sleep_fn=lambda _seconds: None,
        )

    assert result.status == "failed"
    assert "no-progress window exhausted" in result.config["farm_echo_recovery"]["retry_error"]
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

DEGRADATION = (
    "FarmEchoTask:clicked liberation but no effect\n"
    "FarmEchoTask:Target enemy failed, please disable Nvidia/AMD Filter or "
    "Sharpening!\n"
)


def test_combat_degradation_retriggers_upstream_before_client_restart(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    initial = _result(
        runs,
        "initial",
        DEGRADATION + DEATH,
        status="failed",
        absorbed=0,
    )
    retry = _result(
        runs,
        "retry",
        ABSORPTION * 5,
        status="success",
        absorbed=5,
    )
    calls: list[str] = []

    def restart_client() -> bool:
        calls.append("restart")
        return True

    with patch(
        "wuwa_auto.okww.recovery_flow.RUNS_DIR", runs
    ), patch(
        "wuwa_auto.okww.recovery_flow._recover_safely",
        return_value=_safe(),
    ) as recover, patch(
        "wuwa_auto.okww.recovery_flow.temporary_farm_echo_repeat_count",
        side_effect=lambda count: nullcontext(),
    ), patch(
        "wuwa_auto.okww.recovery_flow.run_confirmed_farm_echo_retry",
        return_value=retry,
    ) as run_retry, patch(
        "wuwa_auto.okww.recovery_flow.stop_daily_workers"
    ):
        result = maybe_recover_farm_echo_death(
            initial,
            client_restart=restart_client,
        )

    assert result.status == "success"
    recovery = result.config["farm_echo_recovery"]
    assert recovery["client_restart_triggered"] is False
    assert calls == []
    recover.assert_called_once()
    run_retry.assert_called_once_with(
        target_count=5,
        attempt_limit=5,
        runtime_limit_seconds=ANY,
    )


def test_client_restart_not_triggered_without_degradation(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    initial = _result(
        runs,
        "initial",
        ABSORPTION * 3 + DEATH,
        status="failed",
        absorbed=3,
    )
    retry = _result(
        runs,
        "retry",
        ABSORPTION * 2,
        status="success",
        absorbed=2,
    )
    calls: list[str] = []

    def restart_client() -> bool:
        calls.append("restart")
        return True

    with patch(
        "wuwa_auto.okww.recovery_flow.RUNS_DIR", runs
    ), patch(
        "wuwa_auto.okww.recovery_flow._recover_safely",
        return_value=_safe(),
    ) as recover, patch(
        "wuwa_auto.okww.recovery_flow.temporary_farm_echo_repeat_count",
        side_effect=lambda count: nullcontext(),
    ), patch(
        "wuwa_auto.okww.recovery_flow.run_confirmed_farm_echo_retry",
        return_value=retry,
    ), patch(
        "wuwa_auto.okww.recovery_flow.stop_daily_workers"
    ):
        result = maybe_recover_farm_echo_death(
            initial,
            client_restart=restart_client,
        )

    assert result.status == "success"
    recovery = result.config["farm_echo_recovery"]
    assert recovery["client_restart_triggered"] is False
    assert calls == []
    recover.assert_called_once()


def test_client_restart_only_once_across_degraded_retries(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    initial = _result(
        runs,
        "initial",
        DEGRADATION + DEATH,
        status="failed",
        absorbed=0,
    )
    first_retry = _result(
        runs,
        "retry-1",
        DEGRADATION + DEATH,
        status="failed",
        absorbed=0,
    )
    second_retry = _result(
        runs,
        "retry-2",
        ABSORPTION * 5,
        status="success",
        absorbed=5,
    )
    calls: list[str] = []

    def restart_client() -> bool:
        calls.append("restart")
        return True

    with patch(
        "wuwa_auto.okww.recovery_flow.RUNS_DIR", runs
    ), patch(
        "wuwa_auto.okww.recovery_flow._recover_safely",
        return_value=_safe(),
    ) as recover, patch(
        "wuwa_auto.okww.recovery_flow.temporary_farm_echo_repeat_count",
        side_effect=lambda count: nullcontext(),
    ), patch(
        "wuwa_auto.okww.recovery_flow.run_confirmed_farm_echo_retry",
        side_effect=[first_retry, second_retry],
    ), patch(
        "wuwa_auto.okww.recovery_flow.stop_daily_workers"
    ):
        result = maybe_recover_farm_echo_death(
            initial,
            client_restart=restart_client,
        )

    assert result.status == "success"
    recovery = result.config["farm_echo_recovery"]
    assert recovery["client_restart_triggered"] is True
    assert calls == ["restart"]
    assert recover.call_count == 2
    assert recovery["retry_runs"] == 2
    assert recovery["retry_limit"] is None
    assert recovery["progress_driven_retries"] is True


def test_zero_progress_workers_continue_while_the_window_stays_open(
    tmp_path: Path,
) -> None:
    """Dual-clock policy: no fixed count may abandon a run with budget left."""

    runs = tmp_path / "runs"
    initial = _result(
        runs,
        "initial",
        DEGRADATION + DEATH,
        status="failed",
        absorbed=0,
    )
    initial.config["farm_echo_no_progress_timeout_seconds"] = 600
    worker_count = 0

    def more_zero_progress_workers(**_kwargs: object) -> OkRunResult:
        nonlocal worker_count
        worker_count += 1
        return _result(
            runs,
            f"retry-{worker_count}",
            DEGRADATION + DEATH,
            status="failed",
            absorbed=0,
        )

    with patch(
        "wuwa_auto.okww.recovery_flow.RUNS_DIR", runs
    ), patch(
        "wuwa_auto.okww.recovery_flow._recover_safely",
        return_value=_safe(),
    ) as recover, patch(
        "wuwa_auto.okww.recovery_flow.temporary_farm_echo_repeat_count",
        side_effect=lambda count: nullcontext(),
    ), patch(
        "wuwa_auto.okww.recovery_flow.run_confirmed_farm_echo_retry",
        side_effect=more_zero_progress_workers,
    ) as run_retry, patch(
        "wuwa_auto.okww.recovery_flow.stop_daily_workers"
    ):
        result = maybe_recover_farm_echo_death(
            initial,
            client_restart=lambda: True,
            now_fn=_FakeClock(step=50.0),
            sleep_fn=lambda _seconds: None,
        )

    recovery = result.config["farm_echo_recovery"]
    assert result.status == "failed"
    # Far beyond the abolished count cap of 3 while the window was open.
    assert run_retry.call_count > 3
    assert recover.call_count > 3
    assert recovery["consecutive_no_progress_retries"] > 3
    assert recovery["no_progress_window_seconds"] == 600
    assert "no-progress window exhausted" in recovery["retry_error"]


def test_window_open_and_recent_progress_survives_count_beyond_three(
    tmp_path: Path,
) -> None:
    """The 0818 counterexample: 3 no-progress retries must not abandon."""

    runs = tmp_path / "runs"
    initial = _result(runs, "initial", DEATH, status="failed", absorbed=0)
    outcomes = [
        (2, "failed"),
        (0, "failed"),
        (0, "failed"),
        (0, "failed"),
        (3, "success"),
    ]
    retries = [
        _result(
            runs,
            f"retry-{index}",
            ABSORPTION * absorbed + (DEATH if status == "failed" else ""),
            status=status,
            absorbed=absorbed,
        )
        for index, (absorbed, status) in enumerate(outcomes, start=1)
    ]

    with patch(
        "wuwa_auto.okww.recovery_flow.RUNS_DIR", runs
    ), patch(
        "wuwa_auto.okww.recovery_flow._recover_safely", return_value=_safe()
    ), patch(
        "wuwa_auto.okww.recovery_flow.temporary_farm_echo_repeat_count",
        side_effect=lambda count: nullcontext(),
    ), patch(
        "wuwa_auto.okww.recovery_flow.run_confirmed_farm_echo_retry",
        side_effect=retries,
    ) as run_retry, patch(
        "wuwa_auto.okww.recovery_flow.stop_daily_workers"
    ):
        result = maybe_recover_farm_echo_death(
            initial,
            now_fn=_FakeClock(step=100.0),
            sleep_fn=lambda _seconds: None,
        )

    recovery = result.config["farm_echo_recovery"]
    assert result.status == "success"
    assert run_retry.call_count == 5
    assert recovery["total_completed"] == 5
    assert recovery["consecutive_no_progress_retries"] == 0
    assert recovery["no_progress_window_seconds"] == 3600.0


def test_failed_safety_recovery_restarts_once_then_completes_remaining_target(
    tmp_path: Path,
) -> None:
    """A stale party-state recovery may fail once without poisoning 5/5."""

    runs = tmp_path / "runs"
    initial = _result(
        runs,
        "initial",
        PARTY_MEMBER_UNAVAILABLE,
        status="failed",
        absorbed=2,
    )
    retry = _result(
        runs,
        "retry",
        ABSORPTION * 3,
        status="success",
        absorbed=3,
    )
    restarts: list[str] = []

    with patch(
        "wuwa_auto.okww.recovery_flow.RUNS_DIR", runs
    ), patch(
        "wuwa_auto.okww.recovery_flow._recover_safely",
        return_value=_failed_safe_recovery(),
    ) as recover, patch(
        "wuwa_auto.okww.recovery_flow.temporary_farm_echo_repeat_count",
        side_effect=lambda count: nullcontext(),
    ), patch(
        "wuwa_auto.okww.recovery_flow.run_confirmed_farm_echo_retry",
        return_value=retry,
    ) as run_retry, patch(
        "wuwa_auto.okww.recovery_flow.stop_daily_workers"
    ):
        result = maybe_recover_farm_echo_death(
            initial,
            client_restart=lambda: restarts.append("restart") or True,
        )

    assert result.status == "success"
    assert restarts == ["restart"]
    recover.assert_called_once()
    run_retry.assert_called_once_with(
        target_count=3,
        attempt_limit=5,
        runtime_limit_seconds=ANY,
    )
    recovery = result.config["farm_echo_recovery"]
    assert recovery["total_completed"] == 5
    assert recovery["client_restart_triggered"] is True
    assert recovery["consecutive_no_progress_retries"] == 0
    assert recovery["first_safe_recovery"] is False
    assert recovery["final_safe_recovery"] is True
    assert recovery["final_state_safe"] is True


def test_failed_safety_recovery_never_starts_unsafe_worker_until_window_ends(
    tmp_path: Path,
) -> None:
    """Without a restart adapter, failed recoveries retry inside the window
    but never launch a Worker from an unconfirmed unsafe screen."""

    runs = tmp_path / "runs"
    initial = _result(
        runs,
        "initial",
        PARTY_MEMBER_UNAVAILABLE,
        status="failed",
        absorbed=2,
    )

    with patch(
        "wuwa_auto.okww.recovery_flow.RUNS_DIR", runs
    ), patch(
        "wuwa_auto.okww.recovery_flow._recover_safely",
        return_value=_failed_safe_recovery(),
    ) as recover, patch(
        "wuwa_auto.okww.recovery_flow.run_confirmed_farm_echo_retry"
    ) as run_retry, patch(
        "wuwa_auto.okww.recovery_flow.stop_daily_workers"
    ):
        result = maybe_recover_farm_echo_death(
            initial,
            now_fn=_FakeClock(step=100.0),
            sleep_fn=lambda _seconds: None,
        )

    assert result.status == "failed"
    recoveries = result.config["farm_echo_recovery"]
    # The abolished cap was 3; the window keeps retrying well past it.
    assert recover.call_count > 3
    run_retry.assert_not_called()
    assert (
        recoveries["consecutive_no_progress_retries"] == recover.call_count
    )
    assert "no-progress window exhausted" in recoveries["retry_error"]


def test_frozen_recovery_clock_stops_the_ladder(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    initial = _result(runs, "initial", DEATH, status="failed", absorbed=0)
    first_retry = _result(
        runs,
        "retry-1",
        DEATH,
        status="failed",
        absorbed=0,
    )

    with patch(
        "wuwa_auto.okww.recovery_flow.RUNS_DIR", runs
    ), patch(
        "wuwa_auto.okww.recovery_flow._recover_safely", return_value=_safe()
    ), patch(
        "wuwa_auto.okww.recovery_flow.temporary_farm_echo_repeat_count",
        side_effect=lambda count: nullcontext(),
    ), patch(
        "wuwa_auto.okww.recovery_flow.run_confirmed_farm_echo_retry",
        return_value=first_retry,
    ) as run_retry, patch(
        "wuwa_auto.okww.recovery_flow.stop_daily_workers"
    ):
        result = maybe_recover_farm_echo_death(
            initial,
            now_fn=_FakeClock(step=0.0),
            sleep_fn=lambda _seconds: None,
        )

    assert result.status == "failed"
    assert (
        result.config["farm_echo_recovery"]["retry_error"]
        == "FarmEcho recovery clock stopped advancing"
    )
    assert run_retry.call_count == 1


def test_workerless_fast_fail_iterations_are_paced(
    tmp_path: Path,
) -> None:
    """A tight loop of instantly failing recoveries is paced so the window,
    not the loop rate, bounds it; Worker iterations are never paced."""

    runs = tmp_path / "runs"
    initial = _result(
        runs,
        "initial",
        PARTY_MEMBER_UNAVAILABLE,
        status="failed",
        absorbed=2,
    )
    initial.config["farm_echo_no_progress_timeout_seconds"] = 60
    sleeps: list[float] = []

    with patch(
        "wuwa_auto.okww.recovery_flow.RUNS_DIR", runs
    ), patch(
        "wuwa_auto.okww.recovery_flow._recover_safely",
        return_value=_failed_safe_recovery(),
    ), patch(
        "wuwa_auto.okww.recovery_flow.run_confirmed_farm_echo_retry"
    ) as run_retry, patch(
        "wuwa_auto.okww.recovery_flow.stop_daily_workers"
    ):
        result = maybe_recover_farm_echo_death(
            initial,
            now_fn=_FakeClock(step=5.0),
            sleep_fn=sleeps.append,
        )

    assert result.status == "failed"
    assert sleeps == [20.0, 20.0, 20.0]
    assert run_retry.call_count == 0
    assert "no-progress window exhausted" in (
        result.config["farm_echo_recovery"]["retry_error"]
    )


def test_progress_allows_more_than_three_worker_retries_until_five(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    initial = _result(runs, "initial", DEATH, status="failed", absorbed=0)
    retries = [
        _result(
            runs,
            f"retry-{index}",
            ABSORPTION + (DEATH if index < 5 else ""),
            status="failed" if index < 5 else "success",
            absorbed=1,
        )
        for index in range(1, 6)
    ]

    with patch(
        "wuwa_auto.okww.recovery_flow.RUNS_DIR", runs
    ), patch(
        "wuwa_auto.okww.recovery_flow._recover_safely",
        return_value=_safe(),
    ) as recover, patch(
        "wuwa_auto.okww.recovery_flow.temporary_farm_echo_repeat_count",
        side_effect=lambda count: nullcontext(),
    ), patch(
        "wuwa_auto.okww.recovery_flow.run_confirmed_farm_echo_retry",
        side_effect=retries,
    ) as run_retry, patch(
        "wuwa_auto.okww.recovery_flow.stop_daily_workers"
    ):
        result = maybe_recover_farm_echo_death(initial)

    recovery = result.config["farm_echo_recovery"]
    assert result.status == "success"
    assert recovery["total_completed"] == 5
    assert recovery["retry_runs"] == 5
    assert recovery["retry_limit"] is None
    assert recovery["consecutive_no_progress_retries"] == 0
    assert run_retry.call_count == 5
    assert recover.call_count == 5


def test_startup_network_exhaustion_restarts_ok_owned_client_once(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    initial = _result(runs, "initial", "startup network error\n", status="failed")
    initial.config.update(
        {
            "target_count": 1,
            "farm_echo_startup_network_retry_exhausted": True,
        }
    )
    retry = _result(runs, "retry", ABSORPTION, status="success", absorbed=1)
    restarts: list[bool] = []

    with patch(
        "wuwa_auto.okww.recovery_flow.RUNS_DIR", runs
    ), patch(
        "wuwa_auto.okww.recovery_flow.temporary_farm_echo_repeat_count",
        side_effect=lambda count: nullcontext(),
    ), patch(
        "wuwa_auto.okww.recovery_flow.run_confirmed_farm_echo_retry",
        return_value=retry,
    ), patch(
        "wuwa_auto.okww.recovery_flow.stop_daily_workers"
    ):
        result = maybe_recover_farm_echo_death(
            initial,
            client_restart=lambda: restarts.append(True) or True,
        )

    assert result.status == "success"
    assert restarts == [True]
    recovery = result.config["farm_echo_recovery"]
    assert recovery["client_restart_triggered"] is True
    assert recovery["recoveries"][0]["kind"] == "client_restart"
