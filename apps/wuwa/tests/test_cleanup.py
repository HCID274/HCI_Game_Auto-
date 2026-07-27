from unittest.mock import patch

from wuwa_auto.cleanup import cleanup_after_run


def test_cleanup_closes_every_owned_component() -> None:
    with patch("wuwa_auto.cleanup.stop_daily_workers"), patch(
        "wuwa_auto.cleanup.stop_pyappify_launchers"
    ), patch("wuwa_auto.cleanup._running_ok_processes", return_value=[]), patch(
        "wuwa_auto.cleanup.stop_wuthering_game"
    ), patch("wuwa_auto.cleanup._game_running", return_value=False), patch(
        "wuwa_auto.cleanup.is_uu_running", return_value=True
    ), patch("wuwa_auto.cleanup.disconnect") as disconnect, patch(
        "wuwa_auto.cleanup.terminate_uu", return_value=True
    ), patch("wuwa_auto.cleanup.is_any_uu_process_running", return_value=False):
        result = cleanup_after_run(acceleration_was_connected=True)

    assert result.completed
    assert result.ok_closed
    assert result.game_closed
    assert result.acceleration_disconnected
    assert result.uu_exited
    assert result.issues == []
    disconnect.assert_called_once_with()


def test_disconnect_failure_is_preserved_but_uu_is_still_exited() -> None:
    with patch("wuwa_auto.cleanup.stop_daily_workers"), patch(
        "wuwa_auto.cleanup.stop_pyappify_launchers"
    ), patch("wuwa_auto.cleanup._running_ok_processes", return_value=[]), patch(
        "wuwa_auto.cleanup.stop_wuthering_game"
    ), patch("wuwa_auto.cleanup._game_running", return_value=False), patch(
        "wuwa_auto.cleanup.is_uu_running", return_value=True
    ), patch(
        "wuwa_auto.cleanup.disconnect", side_effect=RuntimeError("not verified")
    ), patch("wuwa_auto.cleanup.terminate_uu", return_value=True), patch(
        "wuwa_auto.cleanup.is_any_uu_process_running", return_value=False
    ):
        result = cleanup_after_run(acceleration_was_connected=True)

    assert result.completed
    assert result.uu_exited
    assert result.issues == ["鸣潮加速未确认断开：not verified"]
