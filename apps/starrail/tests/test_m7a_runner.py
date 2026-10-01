"""Regression tests for Star Rail launch preconditions."""

import socket
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from starrail_auto.m7a.config import EXIT_OK
from starrail_auto.m7a.environment import is_game_network_ready, wait_for_game_ready
from starrail_auto.m7a.logs import (
    battle_in_progress,
    daily_run_outcome,
    game_update_waiting,
    main_run_outcome,
    stage_for_exit_code,
)
from starrail_auto.m7a.models import M7ALogCheckpoint
from starrail_auto.m7a.watchdog import hard_timeout_for_task, watch


class GameNetworkPreflightTests(unittest.TestCase):
    def test_rejects_tun_fake_dns_address(self) -> None:
        def resolver(*_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.1.83", 443))]

        self.assertFalse(is_game_network_ready(resolver=resolver))

    def test_accepts_public_dns_address(self) -> None:
        def resolver(*_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("99.84.48.62", 443))]

        self.assertTrue(is_game_network_ready(resolver=resolver))


class GameReadyTests(unittest.TestCase):
    def test_process_without_window_is_not_ready(self) -> None:
        self.assertFalse(
            wait_for_game_ready(
                timeout=0,
                process_check=lambda: True,
                window_check=lambda: False,
            )
        )


class MainRunPolicyTests(unittest.TestCase):
    def test_main_has_no_hard_timeout(self) -> None:
        self.assertIsNone(hard_timeout_for_task("main", 1800))

    def test_tutorial_recovery_is_limited_to_an_open_battle(self) -> None:
        checkpoint = M7ALogCheckpoint(path=Path("unused.log"), offset=0)
        with patch(
            "starrail_auto.m7a.logs.read_log_since",
            return_value="进入战斗\n识别到未知界面",
        ):
            self.assertTrue(battle_in_progress(checkpoint))
        with patch(
            "starrail_auto.m7a.logs.read_log_since",
            return_value="进入战斗\n战斗完成",
        ):
            self.assertFalse(battle_in_progress(checkpoint))

    def test_watchdog_invokes_obstacle_recovery_without_ending_run(self) -> None:
        recovery = Mock()
        with patch(
            "starrail_auto.m7a.watchdog.poll_process",
            side_effect=[None, 0],
        ), patch("starrail_auto.m7a.watchdog.time.sleep"):
            self.assertEqual(
                watch(object(), None, obstacle_recovery=recovery),
                EXIT_OK,
            )
        self.assertEqual(recovery.call_count, 2)

    def test_universe_keeps_its_explicit_hard_timeout(self) -> None:
        self.assertEqual(hard_timeout_for_task("universe", 7200), 7200)

    def test_daily_completion_is_detected_while_m7a_keeps_running(self) -> None:
        checkpoint = M7ALogCheckpoint(path=Path("unused.log"), offset=0)
        with patch(
            "starrail_auto.m7a.logs.read_log_since",
            return_value="2026-07-17 | INFO | 每日实训已完成",
        ):
            self.assertEqual(daily_run_outcome(checkpoint), "completed")

    def test_daily_completion_does_not_resolve_main_before_stop_marker(self) -> None:
        checkpoint = M7ALogCheckpoint(path=Path("unused.log"), offset=0)
        with patch(
            "starrail_auto.m7a.logs.read_log_since",
            return_value="2026-07-17 | INFO | 每日实训已完成",
        ):
            self.assertIsNone(main_run_outcome(checkpoint))

    def test_daily_completion_resolves_main_at_stop_marker(self) -> None:
        checkpoint = M7ALogCheckpoint(path=Path("unused.log"), offset=0)
        with patch(
            "starrail_auto.m7a.logs.read_log_since",
            return_value="每日实训已完成\n停止运行",
        ):
            self.assertEqual(main_run_outcome(checkpoint), "completed")

    def test_same_cycle_already_settled_resolves_main_at_stop_marker(self) -> None:
        checkpoint = M7ALogCheckpoint(path=Path("unused.log"), offset=0)
        with patch(
            "starrail_auto.m7a.logs.read_log_since",
            return_value="每日实训尚未刷新\n停止运行",
        ):
            self.assertEqual(daily_run_outcome(checkpoint), "completed")
            self.assertEqual(main_run_outcome(checkpoint), "completed")

    def test_same_cycle_already_settled_waits_for_main_stop_marker(self) -> None:
        checkpoint = M7ALogCheckpoint(path=Path("unused.log"), offset=0)
        with patch(
            "starrail_auto.m7a.logs.read_log_since",
            return_value="每日实训尚未刷新",
        ):
            self.assertIsNone(main_run_outcome(checkpoint))

    def test_main_stop_marker_exits_watchdog_without_touching_process(self) -> None:
        checkpoint = M7ALogCheckpoint(path=Path("unused.log"), offset=0)
        with patch(
            "starrail_auto.m7a.watchdog.main_run_outcome",
            return_value="completed",
        ), patch(
            "starrail_auto.m7a.watchdog.poll_process"
        ) as poll_process:
            self.assertEqual(
                watch(
                    object(),
                    None,
                    checkpoint=checkpoint,
                    stop_when_main_resolved=True,
                ),
                EXIT_OK,
            )
        poll_process.assert_not_called()

    def test_launcher_wait_is_detected_until_game_restarts(self) -> None:
        checkpoint = M7ALogCheckpoint(path=Path("unused.log"), offset=0)
        with patch(
            "starrail_auto.m7a.logs.read_log_since",
            return_value="游戏启动：StarRail.exe\n启动器启动：launcher.exe",
        ):
            self.assertTrue(game_update_waiting(checkpoint))
        with patch(
            "starrail_auto.m7a.logs.read_log_since",
            return_value="启动器启动：launcher.exe\n游戏启动：StarRail.exe",
        ):
            self.assertFalse(game_update_waiting(checkpoint))

    def test_launcher_maintenance_suspends_idle_and_log_stall_checks(self) -> None:
        checkpoint = M7ALogCheckpoint(path=Path("unused.log"), offset=0)
        with patch(
            "starrail_auto.m7a.watchdog.main_run_outcome",
            return_value=None,
        ), patch(
            "starrail_auto.m7a.watchdog.poll_process",
            side_effect=[None, 0],
        ), patch(
            "starrail_auto.m7a.watchdog.time.monotonic",
            side_effect=[0.0, 61.0],
        ), patch(
            "starrail_auto.m7a.watchdog.time.sleep"
        ), patch(
            "starrail_auto.m7a.watchdog.psutil.Process"
        ) as process, patch(
            "starrail_auto.m7a.watchdog.get_latest_m7a_log"
        ) as latest_log:
            self.assertEqual(
                watch(
                    object(),
                    None,
                    checkpoint=checkpoint,
                    stop_when_main_resolved=True,
                    maintenance_in_progress=lambda: True,
                ),
                EXIT_OK,
            )
        process.assert_not_called()
        latest_log.assert_not_called()

    def test_active_cpu_prevents_a_stale_log_from_ending_the_run(self) -> None:
        checkpoint = M7ALogCheckpoint(path=Path("unused.log"), offset=0)
        process = Mock()
        process.cpu_percent.return_value = 20.0
        stale_log = Mock()
        stale_log.stat.return_value.st_mtime = 0.0
        with patch(
            "starrail_auto.m7a.watchdog.main_run_outcome",
            return_value=None,
        ), patch(
            "starrail_auto.m7a.watchdog.poll_process",
            side_effect=[None, 0],
        ), patch(
            "starrail_auto.m7a.watchdog.time.monotonic",
            side_effect=[0.0, 61.0],
        ), patch(
            "starrail_auto.m7a.watchdog.time.sleep"
        ), patch(
            "starrail_auto.m7a.watchdog.time.time",
            return_value=10000.0,
        ), patch(
            "starrail_auto.m7a.watchdog.psutil.Process",
            return_value=process,
        ), patch(
            "starrail_auto.m7a.watchdog.get_latest_m7a_log",
            return_value=stale_log,
        ), patch(
            "starrail_auto.m7a.watchdog.capture_failure_evidence"
        ) as capture:
            self.assertEqual(
                watch(
                    Mock(pid=7),
                    None,
                    checkpoint=checkpoint,
                    stop_when_main_resolved=True,
                ),
                EXIT_OK,
            )
        capture.assert_not_called()

    def test_success_stage_does_not_build_failure_summary(self) -> None:
        checkpoint = M7ALogCheckpoint(path=Path("unused.log"), offset=0)
        with patch("starrail_auto.m7a.logs.summarize_daily_failure") as summarize:
            self.assertEqual(stage_for_exit_code(EXIT_OK, checkpoint), "")
        summarize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
