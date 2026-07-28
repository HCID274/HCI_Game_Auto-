from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from wuwa_auto.cleanup import CleanupResult
from wuwa_auto.daily import _run_workflow
from wuwa_auto.okww.recovery import FarmEchoRecoveryResult
from wuwa_auto.okww.runner import OkRunResult


DEATH = """
FarmEchoTask:raise_not_in_combat char dead
FarmEchoTask:info_set Revive Failed
Daily Task exception stopped
"""
ABSORPTION = "FarmEchoTask:farm echo walk_find_echo True\n"


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
    recover.assert_called_once_with(initial)
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
        "wuwa_auto.daily.ensure_daily_farm_echo_absorptions",
        return_value=initial,
    ), patch(
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
        "wuwa_auto.daily.ensure_daily_farm_echo_absorptions",
        return_value=daily,
    ) as ensure_absorptions, patch(
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
    ensure_absorptions.assert_called_once_with(daily)
    assert [call.args[0].run_id for call in report.call_args_list] == [
        "daily",
        "weekly",
    ]
