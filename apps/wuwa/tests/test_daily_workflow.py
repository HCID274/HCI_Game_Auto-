from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import pytest
from wuwa_auto.cleanup import CleanupResult
from wuwa_auto.daily import (
    _prepare_okww_cold_start,
    _run_boss_then_daily_task,
    _run_workflow,
)
from wuwa_auto.okww.recovery import FarmEchoRecoveryResult
from wuwa_auto.okww.runner import OkRunResult

DEATH = """
FarmEchoTask:raise_not_in_combat char dead
FarmEchoTask:info_set Revive Failed
Daily Task exception stopped
"""
ABSORPTION = "FarmEchoTask:farm echo walk_find_echo True\n"
RESTORED_TACET_FAILURE = """
DailyTask:open_daily
DailyTask:can't find gray_book_boss, make sure f2 is the hotkey for book
Daily Task exception stopped
"""


@pytest.fixture(autouse=True)
def _validate_compatibility():
    with patch("wuwa_auto.daily.validate_okww_compatibility"), patch(
        "wuwa_auto.daily._prepare_okww_cold_start"
    ):
        yield


def _result(
    root: Path,
    run_id: str,
    text: str,
    *,
    status: str,
    absorbed: int,
) -> OkRunResult:
    run_dir = root / run_id
    run_dir.mkdir(parents=True)
    log = run_dir / "ok-current-run.log"
    log.write_text(text, encoding="utf-8")
    return OkRunResult(
        run_id=run_id,
        status=status,
        reason="test",
        started_at="2026-07-28T05:00:00+09:00",
        finished_at="2026-07-28T05:10:00+09:00",
        duration_seconds=600,
        log_slice_path=str(log),
        evidence_path=None,
        config={
            "workflow_task": "farm_echo_confirmed_retry",
            "repeat_farm_count": 5,
            "target_count": 5,
            "boss_challenge_index": 2,
            "confirmed_farm_echo_absorption_count": absorbed,
        },
        exit_code=0 if status == "success" else 1,
    )


def _safe(reason: str) -> FarmEchoRecoveryResult:
    return FarmEchoRecoveryResult(True, reason, None, "recovery.json")


def _cleanup() -> CleanupResult:
    return CleanupResult(
        completed=True,
        ok_closed=True,
        game_closed=True,
        acceleration_disconnected=True,
        uu_exited=True,
    )


def test_okww_cold_start_closes_every_preopened_client_owner() -> None:
    with patch("wuwa_auto.daily.stop_daily_workers") as stop_workers, patch(
        "wuwa_auto.daily.stop_wuthering_game"
    ) as stop_game, patch(
        "wuwa_auto.daily.stop_client_launchers"
    ) as stop_launchers:
        _prepare_okww_cold_start()

    stop_workers.assert_called_once_with()
    stop_game.assert_called_once_with()
    stop_launchers.assert_called_once_with()


def test_multiple_recovery_attempts_produce_one_final_report(
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
    cleanup = _cleanup()

    with patch("wuwa_auto.daily.require_admin"), patch(
        "wuwa_auto.daily.managed_virtual_mouse",
        return_value=nullcontext(),
    ), patch("wuwa_auto.daily.ensure_connected"), patch(
        "wuwa_auto.daily.cleanup_after_run",
        return_value=cleanup,
    ), patch(
        "wuwa_auto.okww.recovery_flow.RUNS_DIR",
        runs,
    ), patch(
        "wuwa_auto.okww.recovery_flow._recover_safely",
        side_effect=[_safe("first"), _safe("second")],
    ), patch(
        "wuwa_auto.okww.recovery_flow.temporary_farm_echo_repeat_count",
        side_effect=lambda count: nullcontext(),
    ), patch(
        "wuwa_auto.okww.recovery_flow.run_confirmed_farm_echo_retry",
        side_effect=[first_retry, second_retry],
    ), patch(
        "wuwa_auto.okww.recovery_flow.stop_daily_workers"
    ), patch("wuwa_auto.daily.report_run") as report:
        exit_code = _run_workflow("farm_echo", lambda: initial)

    assert exit_code == 0
    report.assert_called_once()
    final_result, final_cleanup = report.call_args.args
    assert final_cleanup is cleanup
    assert final_result.status == "success"
    assert final_result.config["confirmed_farm_echo_absorption_count"] == 5
    assert final_result.config["farm_echo_recovery"]["attempt_run_ids"] == [
        "initial",
        "retry-1",
        "retry-2",
    ]


def test_terminal_partial_result_produces_one_final_report(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    initial = _result(
        runs,
        "initial",
        ABSORPTION * 4 + DEATH,
        status="failed",
        absorbed=4,
    )
    terminal = _result(
        runs,
        "terminal",
        ABSORPTION * 4,
        status="failed",
        absorbed=4,
    )
    terminal.config["farm_echo_absorption_target"] = 5
    cleanup = _cleanup()

    with patch("wuwa_auto.daily.require_admin"), patch(
        "wuwa_auto.daily.managed_virtual_mouse",
        return_value=nullcontext(),
    ), patch("wuwa_auto.daily.ensure_connected"), patch(
        "wuwa_auto.daily.maybe_recover_farm_echo_death",
        return_value=terminal,
    ) as recover, patch(
        "wuwa_auto.daily.cleanup_after_run",
        return_value=cleanup,
    ), patch("wuwa_auto.daily.report_run") as report:
        exit_code = _run_workflow("farm_echo", lambda: initial)

    assert exit_code == 1
    recover.assert_called_once()
    assert recover.call_args.args == (initial,)
    assert callable(recover.call_args.kwargs["client_restart"])
    report.assert_called_once_with(terminal, cleanup)


def test_settlement_exception_cannot_report_a_stale_success(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    initial = _result(
        runs,
        "initial",
        ABSORPTION * 4,
        status="success",
        absorbed=4,
    )
    initial.config["workflow_task"] = "daily"
    cleanup = _cleanup()

    with patch("wuwa_auto.daily.require_admin"), patch(
        "wuwa_auto.daily.managed_virtual_mouse",
        return_value=nullcontext(),
    ), patch("wuwa_auto.daily.ensure_connected"), patch(
        "wuwa_auto.daily.maybe_recover_farm_echo_death",
        side_effect=RuntimeError("recovery crashed"),
    ), patch(
        "wuwa_auto.daily.save_step_screenshot",
        return_value=None,
    ), patch(
        "wuwa_auto.okww.runner.RUNS_DIR",
        runs,
    ), patch(
        "wuwa_auto.daily.cleanup_after_run",
        return_value=cleanup,
    ), patch("wuwa_auto.daily.report_run") as report:
        exit_code = _run_workflow("daily", lambda: initial)

    assert exit_code == 1
    report.assert_called_once()
    final_result, final_cleanup = report.call_args.args
    assert final_cleanup is cleanup
    assert final_result is not initial
    assert final_result.status == "failed"
    assert final_result.exit_code == 1
    assert final_result.config["workflow_failure_source_run_id"] == "initial"
    assert "recovery crashed" in final_result.reason
    assert ABSORPTION.strip() in Path(final_result.log_slice_path).read_text(
        encoding="utf-8"
    )


def test_daily_and_weekly_are_independent_report_transactions(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    daily = _result(
        runs,
        "daily",
        ABSORPTION * 5,
        status="success",
        absorbed=5,
    )
    daily.config["workflow_task"] = "daily"
    weekly = _result(
        runs,
        "weekly",
        "GardenTask:乐园任务完成, 已达到上限\n",
        status="success",
        absorbed=0,
    )
    weekly.config["workflow_task"] = "weekly_garden"
    cleanup = _cleanup()

    with patch("wuwa_auto.daily.require_admin"), patch(
        "wuwa_auto.daily.managed_virtual_mouse",
        return_value=nullcontext(),
    ), patch("wuwa_auto.daily.ensure_connected"), patch(
        "wuwa_auto.daily.maybe_recover_farm_echo_death",
        side_effect=lambda result: result,
    ), patch(
        "wuwa_auto.daily.cleanup_after_run",
        return_value=cleanup,
    ), patch("wuwa_auto.daily.report_run") as report:
        daily_code = _run_workflow("daily", lambda: daily)
        weekly_code = _run_workflow("weekly_garden", lambda: weekly)

    assert daily_code == 0
    assert weekly_code == 0
    assert [call.args[0].run_id for call in report.call_args_list] == [
        "daily",
        "weekly",
    ]


def test_restored_tacet_challenge_is_exited_and_daily_retried_once(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    initial = _result(
        runs,
        "initial",
        RESTORED_TACET_FAILURE,
        status="failed",
        absorbed=0,
    )
    initial.config["workflow_task"] = "daily"
    retry = _result(
        runs,
        "retry",
        ABSORPTION * 5 + "Daily Task Completed\n",
        status="success",
        absorbed=5,
    )
    retry.config["workflow_task"] = "daily"
    cleanup = _cleanup()
    recovery = FarmEchoRecoveryResult(
        True,
        "HOST_WORLD_STATE_RECOVERY_COMPLETED",
        None,
        "world-recovery.json",
    )

    with patch("wuwa_auto.daily.require_admin"), patch(
        "wuwa_auto.daily.managed_virtual_mouse",
        return_value=nullcontext(),
    ), patch("wuwa_auto.daily.ensure_connected"), patch(
        "wuwa_auto.daily.stop_daily_workers"
    ) as stop_worker, patch(
        "wuwa_auto.daily.run_world_state_recovery",
        return_value=recovery,
    ) as recover_world, patch(
        "wuwa_auto.daily.run_daily_task",
        return_value=retry,
    ) as retry_daily, patch(
        "wuwa_auto.daily.maybe_recover_farm_echo_death",
        side_effect=lambda result: result,
    ), patch(
        "wuwa_auto.daily.cleanup_after_run",
        return_value=cleanup,
    ), patch("wuwa_auto.daily.report_run") as report:
        exit_code = _run_workflow("daily", lambda: initial)

    assert exit_code == 0
    stop_worker.assert_called_once()
    recover_world.assert_called_once()
    retry_daily.assert_called_once()
    report.assert_called_once()
    final_result = report.call_args.args[0]
    assert final_result.status == "success"
    recovery_history = final_result.config["daily_state_recoveries"]
    assert recovery_history[-1]["success"] is True
    assert recovery_history[-1]["kind"] == "restored-tacet-challenge"
    combined = Path(final_result.log_slice_path).read_text(encoding="utf-8")
    assert RESTORED_TACET_FAILURE.strip() in combined
    assert "Daily Task Completed" in combined


def test_tacet_death_waits_for_world_and_retries_daily_once(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    initial = _result(
        runs,
        "initial",
        "TacetTask:raise_not_in_combat char dead\n"
        "TacetTask:info_set Revive Failed\n"
        "Daily Task exception stopped\n",
        status="failed",
        absorbed=0,
    )
    initial.config["workflow_task"] = "daily"
    retry = _result(
        runs,
        "retry",
        "Daily Task Completed\n",
        status="success",
        absorbed=5,
    )
    retry.config["workflow_task"] = "daily"
    cleanup = _cleanup()
    recovery = FarmEchoRecoveryResult(
        True,
        "HOST_WORLD_STATE_RECOVERY_COMPLETED",
        None,
        "world-recovery.json",
    )

    with patch("wuwa_auto.daily.require_admin"), patch(
        "wuwa_auto.daily.managed_virtual_mouse",
        return_value=nullcontext(),
    ), patch("wuwa_auto.daily.ensure_connected"), patch(
        "wuwa_auto.daily.stop_daily_workers"
    ), patch(
        "wuwa_auto.daily.run_world_state_recovery",
        return_value=recovery,
    ) as recover_world, patch(
        "wuwa_auto.daily.run_daily_task",
        return_value=retry,
    ) as retry_daily, patch(
        "wuwa_auto.daily.maybe_recover_farm_echo_death",
        side_effect=lambda result: result,
    ), patch(
        "wuwa_auto.daily.cleanup_after_run",
        return_value=cleanup,
    ), patch("wuwa_auto.daily.report_run") as report:
        exit_code = _run_workflow("daily", lambda: initial)

    assert exit_code == 0
    recover_world.assert_called_once()
    retry_daily.assert_called_once()
    final_result = report.call_args.args[0]
    assert final_result.config["daily_state_recoveries"][-1]["kind"] == (
        "tacet-death-teleport"
    )


def test_daily_workflow_runs_boss_before_daily_and_reports_once(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    boss = _result(
        runs,
        "boss",
        ABSORPTION * 5,
        status="success",
        absorbed=5,
    )
    daily = _result(
        runs,
        "daily-after-boss",
        "DailyTask:Daily Task Completed\n",
        status="success",
        absorbed=0,
    )
    daily.config["workflow_task"] = "daily"
    cleanup = _cleanup()
    order: list[str] = []

    def run_boss(**_: object) -> OkRunResult:
        order.append("boss")
        return boss

    def settle_boss(
        result: OkRunResult,
        *,
        client_restart: object | None = None,
    ) -> OkRunResult:
        order.append("settle-boss")
        return result

    def stop_worker() -> int:
        order.append("stop-worker")
        return 0

    def run_daily() -> OkRunResult:
        order.append("daily")
        return daily

    def settle_daily(result: OkRunResult) -> OkRunResult:
        order.append("settle-daily")
        return result

    with patch("wuwa_auto.daily.require_admin"), patch(
        "wuwa_auto.daily.managed_virtual_mouse",
        return_value=nullcontext(),
    ), patch("wuwa_auto.daily.ensure_connected"), patch(
        "wuwa_auto.daily.temporary_farm_echo_repeat_count",
        side_effect=lambda count: nullcontext(),
    ), patch(
        "wuwa_auto.daily.run_confirmed_farm_echo_retry",
        side_effect=run_boss,
    ), patch(
        "wuwa_auto.daily.maybe_recover_farm_echo_death",
        side_effect=settle_boss,
    ) as recover_boss, patch(
        "wuwa_auto.daily.stop_daily_workers",
        side_effect=stop_worker,
    ), patch(
        "wuwa_auto.daily.run_daily_task",
        side_effect=run_daily,
    ), patch(
        "wuwa_auto.daily._maybe_recover_daily_state",
        side_effect=settle_daily,
    ), patch(
        "wuwa_auto.daily.cleanup_after_run",
        return_value=cleanup,
    ), patch("wuwa_auto.daily.report_run") as report:
        exit_code = _run_workflow("daily", _run_boss_then_daily_task)

    assert exit_code == 0
    assert order == [
        "boss",
        "settle-boss",
        "stop-worker",
        "daily",
        "settle-daily",
    ]
    recover_boss.assert_called_once()
    assert recover_boss.call_args.args == (boss,)
    assert callable(recover_boss.call_args.kwargs["client_restart"])
    report.assert_called_once()
    final_result = report.call_args.args[0]
    assert final_result.status == "success"
    assert final_result.config["daily_sequence"] == {
        "order": ["farm_echo", "daily"],
        "boss_run_id": "boss",
        "boss_status": "success",
        "boss_reason": "test",
        "daily_run_id": "daily-after-boss",
        "daily_status": "success",
        "daily_reason": "test",
        "settled": True,
    }
    combined = Path(final_result.log_slice_path).read_text(encoding="utf-8")
    assert combined.index("HOST PRE-DAILY BOSS") < combined.index("HOST DAILY")
    assert combined.count(ABSORPTION.strip()) == 5
    assert "Daily Task Completed" in combined


def test_daily_waits_for_five_absorptions_and_reports_skip_once(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    boss = _result(
        runs,
        "boss-failed",
        ABSORPTION * 2,
        status="failed",
        absorbed=2,
    )
    daily = _result(
        runs,
        "daily-completed",
        "DailyTask:Daily Task Completed\n",
        status="success",
        absorbed=0,
    )
    daily.config["workflow_task"] = "daily"
    cleanup = _cleanup()

    with patch("wuwa_auto.daily.require_admin"), patch(
        "wuwa_auto.daily.managed_virtual_mouse",
        return_value=nullcontext(),
    ), patch("wuwa_auto.daily.ensure_connected"), patch(
        "wuwa_auto.daily.temporary_farm_echo_repeat_count",
        side_effect=lambda count: nullcontext(),
    ), patch(
        "wuwa_auto.daily.run_confirmed_farm_echo_retry",
        return_value=boss,
    ), patch(
        "wuwa_auto.daily.maybe_recover_farm_echo_death",
        return_value=boss,
    ), patch("wuwa_auto.daily.stop_daily_workers"), patch(
        "wuwa_auto.daily.run_daily_task",
        return_value=daily,
    ) as run_daily, patch(
        "wuwa_auto.daily._maybe_recover_daily_state",
        return_value=daily,
    ), patch(
        "wuwa_auto.daily.cleanup_after_run",
        return_value=cleanup,
    ), patch("wuwa_auto.daily.report_run") as report:
        exit_code = _run_workflow("daily", _run_boss_then_daily_task)

    assert exit_code == 1
    run_daily.assert_not_called()
    report.assert_called_once()
    final_result = report.call_args.args[0]
    assert final_result.config["daily_sequence"]["boss_status"] == "failed"
    assert final_result.config["daily_sequence"]["daily_status"] == "failed"
    assert final_result.config["skipped_after_farm_echo"] is True
