from unittest.mock import patch

from starrail_auto.workflows.cleanup import EXIT_OK, EXIT_UU_DISCONNECT_FAILED, run


def test_cleanup_disconnects_then_exits_uu() -> None:
    with patch("starrail_auto.workflows.cleanup._terminate_processes", return_value=True), patch(
        "starrail_auto.workflows.cleanup.stop_uu_acceleration"
    ) as disconnect, patch("starrail_auto.workflows.cleanup.kill_uu", return_value=True) as stop:
        assert run() == EXIT_OK
    disconnect.assert_called_once_with()
    stop.assert_called_once_with()


def test_complete_uu_exit_recovers_unverified_disconnect() -> None:
    with patch("starrail_auto.workflows.cleanup._terminate_processes", return_value=True), patch(
        "starrail_auto.workflows.cleanup.stop_uu_acceleration",
        side_effect=RuntimeError("template missing"),
    ), patch("starrail_auto.workflows.cleanup.kill_uu", return_value=True):
        assert run() == EXIT_OK


def test_remaining_uu_process_blocks_handoff() -> None:
    with patch("starrail_auto.workflows.cleanup._terminate_processes", return_value=True), patch(
        "starrail_auto.workflows.cleanup.stop_uu_acceleration"
    ), patch("starrail_auto.workflows.cleanup.kill_uu", return_value=False):
        assert run() == EXIT_UU_DISCONNECT_FAILED
