from datetime import datetime, timezone
from unittest.mock import patch

from starrail_auto.m7a.config import EXIT_DAILY_VALIDATION_FAILED
from starrail_auto.workflows.daily import _daily_reset_has_passed, run_daily


def test_daily_reset_guard_uses_local_hour() -> None:
    assert not _daily_reset_has_passed(datetime(2026, 8, 11, 4, 59, tzinfo=timezone.utc))
    assert _daily_reset_has_passed(datetime(2026, 8, 11, 5, 0, tzinfo=timezone.utc))


def test_run_daily_fails_before_reset_without_starting_m7a() -> None:
    with patch(
        "starrail_auto.workflows.daily._daily_reset_has_passed",
        return_value=False,
    ), patch("starrail_auto.workflows.daily._setup_logging"), patch(
        "starrail_auto.workflows.daily.execute_task"
    ) as execute:
        result = run_daily(timeout=1800)

    assert result == EXIT_DAILY_VALIDATION_FAILED
    execute.assert_not_called()


def test_run_daily_starts_after_reset() -> None:
    with patch(
        "starrail_auto.workflows.daily._daily_reset_has_passed",
        return_value=True,
    ), patch(
        "starrail_auto.workflows.daily.execute_task",
        return_value=0,
    ) as execute:
        result = run_daily(timeout=1800)

    assert result == 0
    execute.assert_called_once_with("main", 1800)
