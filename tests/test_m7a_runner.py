"""Regression tests for Star Rail launch preconditions."""

import socket
import unittest
from pathlib import Path
from unittest.mock import patch

from m7a_runner import (
    EXIT_OK,
    M7ALogCheckpoint,
    _daily_run_outcome,
    _hard_timeout_for_task,
    _is_game_network_ready,
    _stage_for_exit_code,
    _wait_for_game_ready,
    _watchdog,
)


class GameNetworkPreflightTests(unittest.TestCase):
    def test_rejects_tun_fake_dns_address(self) -> None:
        def resolver(*_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.1.83", 443))]

        self.assertFalse(_is_game_network_ready(resolver=resolver))

    def test_accepts_public_dns_address(self) -> None:
        def resolver(*_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("99.84.48.62", 443))]

        self.assertTrue(_is_game_network_ready(resolver=resolver))


class GameReadyTests(unittest.TestCase):
    def test_process_without_window_is_not_ready(self) -> None:
        self.assertFalse(
            _wait_for_game_ready(
                timeout=0,
                process_check=lambda: True,
                window_check=lambda: False,
            )
        )


class MainRunPolicyTests(unittest.TestCase):
    def test_main_has_no_hard_timeout(self) -> None:
        self.assertIsNone(_hard_timeout_for_task("main", 1800))

    def test_universe_keeps_its_explicit_hard_timeout(self) -> None:
        self.assertEqual(_hard_timeout_for_task("universe", 7200), 7200)

    def test_daily_completion_is_detected_while_m7a_keeps_running(self) -> None:
        checkpoint = M7ALogCheckpoint(path=Path("unused.log"), offset=0)
        with patch(
            "m7a_runner._read_m7a_log_since",
            return_value="2026-07-17 | INFO | 每日实训已完成",
        ):
            self.assertEqual(_daily_run_outcome(checkpoint), "completed")

    def test_main_completion_exits_watchdog_without_touching_process(self) -> None:
        checkpoint = M7ALogCheckpoint(path=Path("unused.log"), offset=0)
        with patch("m7a_runner._daily_run_outcome", return_value="completed"), patch(
            "m7a_runner._poll_process"
        ) as poll_process:
            self.assertEqual(
                _watchdog(
                    object(),
                    None,
                    checkpoint=checkpoint,
                    stop_when_daily_resolved=True,
                ),
                EXIT_OK,
            )
        poll_process.assert_not_called()

    def test_success_stage_does_not_build_failure_summary(self) -> None:
        checkpoint = M7ALogCheckpoint(path=Path("unused.log"), offset=0)
        with patch("m7a_runner._summarize_daily_failure") as summarize:
            self.assertEqual(_stage_for_exit_code(EXIT_OK, checkpoint), "")
        summarize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
