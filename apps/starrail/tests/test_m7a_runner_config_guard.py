"""Runner integration tests for the M7A configuration gate."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

from starrail_auto.m7a.config import (
    EXIT_M7A_CONFIG_FAILED,
    EXIT_M7A_EXIT_NONZERO,
    EXIT_OK,
)
from starrail_auto.m7a.config_guard import M7AConfigProtectionError
from starrail_auto.m7a.models import M7ALogCheckpoint
from starrail_auto.m7a.runner import run_m7a


def _checkpoint() -> M7ALogCheckpoint:
    return M7ALogCheckpoint(Path("m7a.log"), 17)


def test_preflight_failure_blocks_launcher_creation() -> None:
    with patch(
        "starrail_auto.m7a.runner.capture_log_checkpoint",
        return_value=_checkpoint(),
    ), patch(
        "starrail_auto.m7a.runner.prepare_m7a_config",
        side_effect=M7AConfigProtectionError("broken"),
    ), patch("starrail_auto.m7a.runner.capture_failure_evidence"), patch(
        "starrail_auto.m7a.runner.subprocess.Popen"
    ) as popen:
        result = run_m7a("main", 1800)

    assert result.exit_code == EXIT_M7A_CONFIG_FAILED
    assert result.stage == "M7A配置保护"
    popen.assert_not_called()


def test_unsafe_startup_mutation_stops_only_new_m7a_processes() -> None:
    session = Mock()
    session.verify_live.side_effect = M7AConfigProtectionError("reset")
    launcher = Mock(pid=1234)

    def wait_for_ready(*, startup_check: object) -> bool:
        startup_check()
        return True

    with patch(
        "starrail_auto.m7a.runner.capture_log_checkpoint",
        return_value=_checkpoint(),
    ), patch(
        "starrail_auto.m7a.runner.prepare_m7a_config",
        return_value=session,
    ), patch(
        "starrail_auto.m7a.runner.subprocess.Popen",
        return_value=launcher,
    ), patch(
        "starrail_auto.m7a.runner.wait_for_game_ready",
        side_effect=wait_for_ready,
    ), patch(
        "starrail_auto.m7a.runner.M7ADisclaimerHandler.poll",
        return_value=True,
    ), patch(
        "starrail_auto.m7a.runner.capture_failure_evidence"
    ), patch(
        "starrail_auto.m7a.runner._stop_new_m7a_processes"
    ) as stop:
        result = run_m7a("main", 1800)

    assert result.exit_code == EXIT_M7A_CONFIG_FAILED
    stop.assert_called_once()


def test_successful_run_finalizes_last_known_good() -> None:
    session = Mock()
    launcher = Mock(pid=1234)

    with patch(
        "starrail_auto.m7a.runner.capture_log_checkpoint",
        return_value=_checkpoint(),
    ), patch(
        "starrail_auto.m7a.runner.prepare_m7a_config",
        return_value=session,
    ), patch(
        "starrail_auto.m7a.runner.subprocess.Popen",
        return_value=launcher,
    ), patch(
        "starrail_auto.m7a.runner.wait_for_game_ready",
        return_value=True,
    ), patch(
        "starrail_auto.m7a.runner.M7ADisclaimerHandler.poll",
        return_value=True,
    ), patch(
        "starrail_auto.m7a.runner.find_new_assistant",
        return_value=None,
    ), patch(
        "starrail_auto.m7a.runner.watch",
        return_value=EXIT_OK,
    ), patch(
        "starrail_auto.m7a.runner.stage_for_exit_code",
        return_value="",
    ):
        result = run_m7a("universe", 1800)

    assert result.exit_code == EXIT_OK
    session.finalize.assert_called_once()


def test_failed_run_verifies_without_finalizing_last_known_good() -> None:
    session = Mock()
    launcher = Mock(pid=1234)

    with patch(
        "starrail_auto.m7a.runner.capture_log_checkpoint",
        return_value=_checkpoint(),
    ), patch(
        "starrail_auto.m7a.runner.prepare_m7a_config",
        return_value=session,
    ), patch(
        "starrail_auto.m7a.runner.subprocess.Popen",
        return_value=launcher,
    ), patch(
        "starrail_auto.m7a.runner.wait_for_game_ready",
        return_value=True,
    ), patch(
        "starrail_auto.m7a.runner.M7ADisclaimerHandler.poll",
        return_value=True,
    ), patch(
        "starrail_auto.m7a.runner.find_new_assistant",
        return_value=None,
    ), patch(
        "starrail_auto.m7a.runner.watch",
        return_value=EXIT_M7A_EXIT_NONZERO,
    ), patch(
        "starrail_auto.m7a.runner.stage_for_exit_code",
        return_value="",
    ):
        result = run_m7a("universe", 1800)

    assert result.exit_code == EXIT_M7A_EXIT_NONZERO
    session.finalize.assert_not_called()
    session.verify_live.assert_called_once()
