"""Same-day retry planning must pick the smallest safe completion path."""

import json
from dataclasses import asdict
from pathlib import Path

from wuwa_auto.daily import plan_daily_retry
from wuwa_auto.okww.runner import OkRunResult
from wuwa_auto.reporting.day_rollup import same_day_stage_status


def _seed_run(
    runs_dir: Path,
    *,
    run_id: str,
    workflow: str,
    status: str,
    finished_at: str,
    sequence: dict | None = None,
    extra_config: dict | None = None,
) -> None:
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True)
    log_path = run_dir / "ok-current-run.log"
    log_path.write_text(
        "2026-08-14 06:00:00 DailyTask:Daily Task Completed\n",
        encoding="utf-8",
    )
    config: dict = {"workflow_task": workflow}
    if sequence is not None:
        config["daily_sequence"] = sequence
    if extra_config:
        config.update(extra_config)
    result = OkRunResult(
        run_id=run_id,
        status=status,
        reason="completed" if status == "success" else "failed",
        started_at=f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:8]}T05:40:00+09:00",
        finished_at=finished_at,
        duration_seconds=600,
        log_slice_path=str(log_path),
        evidence_path=None,
        config=config,
        exit_code=0 if status == "success" else 1,
    )
    (run_dir / "result.json").write_text(
        json.dumps(asdict(result), ensure_ascii=False),
        encoding="utf-8",
    )


def _plan(tmp_path: Path, day: str = "20260814") -> str:
    import wuwa_auto.reporting.day_rollup as rollup

    original = rollup.RUNS_DIR
    rollup.RUNS_DIR = tmp_path / "runs"
    try:
        return plan_daily_retry(day)
    finally:
        rollup.RUNS_DIR = original


def test_no_runs_today_plans_full(tmp_path: Path) -> None:
    assert _plan(tmp_path) == "full"


def test_green_day_plans_noop(tmp_path: Path) -> None:
    _seed_run(
        tmp_path / "runs",
        run_id="20260814_054107_farm_echo_confirmed_retry_recovery_daily",
        workflow="daily",
        status="success",
        finished_at="2026-08-14T06:20:00+09:00",
        sequence={
            "order": ["farm_echo", "daily"],
            "boss_status": "success",
            "daily_status": "success",
            "settled": True,
        },
    )
    assert _plan(tmp_path) == "noop"


def test_followup_green_daily_failed_plans_resume(tmp_path: Path) -> None:
    _seed_run(
        tmp_path / "runs",
        run_id="20260814_054107_farm_echo_confirmed_retry_recovery_daily",
        workflow="daily",
        status="failed",
        finished_at="2026-08-14T06:20:00+09:00",
        sequence={
            "order": ["farm_echo", "daily"],
            "boss_status": "success",
            "daily_status": "failed",
            "settled": True,
        },
    )
    assert _plan(tmp_path) == "resume"


def test_farm_echo_failed_daily_skipped_plans_full(tmp_path: Path) -> None:
    _seed_run(
        tmp_path / "runs",
        run_id="20260814_054107_farm_echo_confirmed_retry_recovery_daily",
        workflow="daily",
        status="failed",
        finished_at="2026-08-14T05:52:00+09:00",
        sequence={
            "order": ["farm_echo", "daily"],
            "boss_status": "failed",
            "daily_status": "failed",
            "settled": True,
        },
    )
    assert _plan(tmp_path) == "full"


def test_daily_green_followup_failed_plans_farm_echo(tmp_path: Path) -> None:
    _seed_run(
        tmp_path / "runs",
        run_id="20260814_054107_farm_echo_confirmed_retry_recovery_daily",
        workflow="daily",
        status="failed",
        finished_at="2026-08-14T06:20:00+09:00",
        sequence={
            "order": ["farm_echo", "daily"],
            "boss_status": "failed",
            "daily_status": "success",
            "settled": True,
        },
    )
    assert _plan(tmp_path) == "farm-echo"


def test_standalone_resume_success_counts_as_daily_green(tmp_path: Path) -> None:
    _seed_run(
        tmp_path / "runs",
        run_id="20260814_015405_daily_resume",
        workflow="daily",
        status="success",
        finished_at="2026-08-14T02:01:04+09:00",
        extra_config={"daily_resume": "after_nightmare"},
    )
    _seed_run(
        tmp_path / "runs",
        run_id="20260814_060000_farm_echo_confirmed_retry",
        workflow="farm_echo_confirmed_retry",
        status="success",
        finished_at="2026-08-14T06:10:00+09:00",
    )
    assert _plan(tmp_path) == "noop"


def test_latest_failure_is_authoritative_over_earlier_success(
    tmp_path: Path,
) -> None:
    _seed_run(
        tmp_path / "runs",
        run_id="20260814_060000",
        workflow="daily",
        status="success",
        finished_at="2026-08-14T06:10:00+09:00",
    )
    _seed_run(
        tmp_path / "runs",
        run_id="20260814_070000",
        workflow="daily",
        status="failed",
        finished_at="2026-08-14T07:10:00+09:00",
    )
    import wuwa_auto.reporting.day_rollup as rollup

    original = rollup.RUNS_DIR
    rollup.RUNS_DIR = tmp_path / "runs"
    try:
        status = same_day_stage_status("20260814")
    finally:
        rollup.RUNS_DIR = original
    assert status["daily_succeeded"] is False


def test_weekly_garden_and_corrupt_results_are_ignored(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    _seed_run(
        runs,
        run_id="20260814_080000_weekly",
        workflow="weekly_garden",
        status="success",
        finished_at="2026-08-14T08:30:00+09:00",
    )
    broken = runs / "20260814_090000_broken"
    broken.mkdir(parents=True)
    (broken / "result.json").write_text("{not json", encoding="utf-8")
    assert _plan(tmp_path) == "full"
