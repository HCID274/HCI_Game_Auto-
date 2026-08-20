from datetime import datetime
from unittest.mock import patch

import pytest
from wuwa_auto.version_day import (
    DEFER_CUTOFF_HOUR,
    EVENING_RERUN_HOUR,
    EVENING_RERUN_SCRIPT,
    EVENING_RERUN_TASK_NAME,
    build_registration_script,
    evening_rerun_at,
    register_evening_rerun,
    should_defer_daily_to_evening,
)


def test_weekday_mornings_defer() -> None:
    assert should_defer_daily_to_evening(datetime(2026, 8, 20, 5, 41)) is True


def test_weekday_evenings_continue_directly() -> None:
    assert should_defer_daily_to_evening(datetime(2026, 8, 20, 20, 0)) is False


def test_weekday_cutoff_hour_stops_deferral() -> None:
    assert should_defer_daily_to_evening(datetime(2026, 8, 20, DEFER_CUTOFF_HOUR, 0)) is False


def test_weekends_never_defer() -> None:
    assert should_defer_daily_to_evening(datetime(2026, 8, 22, 6, 0)) is False
    assert should_defer_daily_to_evening(datetime(2026, 8, 23, 12, 0)) is False


def test_evening_rerun_is_same_day_before_the_hour() -> None:
    assert evening_rerun_at(datetime(2026, 8, 20, 5, 41)) == datetime(
        2026, 8, 20, EVENING_RERUN_HOUR, 0
    )


def test_evening_rerun_rolls_to_next_day_after_the_hour() -> None:
    assert evening_rerun_at(datetime(2026, 8, 20, 21, 10)) == datetime(
        2026, 8, 21, EVENING_RERUN_HOUR, 0
    )


def test_registration_script_targets_once_task_with_self_deleting_entry() -> None:
    script = build_registration_script(datetime(2026, 8, 20, 20, 0), "HCID274")
    assert EVENING_RERUN_TASK_NAME in script
    assert "-Once -At '2026-08-20T20:00:00'" in script
    assert "-LogonType Interactive -RunLevel Highest" in script
    assert f"-File {EVENING_RERUN_SCRIPT}" in script


def test_register_evening_rerun_returns_start_time_and_fails_loudly() -> None:
    completed = type("Completed", (), {"returncode": 0, "stderr": "", "stdout": ""})()
    with patch(
        "wuwa_auto.version_day.subprocess.run", return_value=completed
    ) as run:
        rerun_at = register_evening_rerun(datetime(2026, 8, 20, 6, 0))
    assert rerun_at == datetime(2026, 8, 20, 20, 0)
    assert run.call_args.args[0][0:2] == ["powershell", "-NoProfile"]

    failed = type("Completed", (), {"returncode": 1, "stderr": "denied", "stdout": ""})()
    with patch("wuwa_auto.version_day.subprocess.run", return_value=failed):
        with pytest.raises(RuntimeError, match="denied"):
            register_evening_rerun(datetime(2026, 8, 20, 6, 0))
