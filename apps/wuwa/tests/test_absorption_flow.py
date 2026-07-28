from pathlib import Path
from unittest.mock import patch

from wuwa_auto.okww.absorption_flow import ensure_daily_farm_echo_absorptions
from wuwa_auto.okww.runner import OkRunResult


PICKUP = "FarmEchoTask:farm echo walk_find_echo True\n"


def _result(
    root: Path,
    run_id: str,
    text: str,
    *,
    status: str = "success",
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
        config["farm_echo_runtime_limit_seconds"] = 3600
    result = OkRunResult(
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
    (run_dir / "result.json").write_text("{}", encoding="utf-8")
    return result


def test_satisfied_daily_absorption_target_does_not_retry(tmp_path: Path) -> None:
    result = _result(tmp_path, "daily", PICKUP * 5)

    with patch(
        "wuwa_auto.okww.absorption_flow.run_confirmed_farm_echo_retry"
    ) as retry:
        completed = ensure_daily_farm_echo_absorptions(result)

    assert completed.status == "success"
    assert completed.config["confirmed_farm_echo_absorption_count"] == 5
    assert completed.config["farm_echo_absorption_target"] == 5
    retry.assert_not_called()


def test_daily_absorption_target_retries_only_missing_pickups(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    daily = _result(runs, "daily", PICKUP * 3)
    retry = _result(
        runs,
        "retry",
        PICKUP * 2,
        absorbed=2,
    )

    with patch(
        "wuwa_auto.okww.absorption_flow.RUNS_DIR", runs
    ), patch(
        "wuwa_auto.okww.absorption_flow.stop_daily_workers"
    ), patch(
        "wuwa_auto.okww.absorption_flow.temporary_farm_echo_repeat_count"
    ) as override, patch(
        "wuwa_auto.okww.absorption_flow.run_confirmed_farm_echo_retry",
        return_value=retry,
    ) as run_retry:
        override.return_value.__enter__.return_value = None
        completed = ensure_daily_farm_echo_absorptions(daily)

    assert completed.status == "success"
    assert completed.config["confirmed_farm_echo_absorption_count"] == 5
    topup = completed.config["farm_echo_absorption_topup"]
    assert topup["initial_absorbed"] == 3
    assert topup["retry_requested"] == 2
    assert topup["retry_absorbed"] == 2
    override.assert_called_once_with(24)
    run_retry.assert_called_once_with(target_count=2, attempt_limit=24)
