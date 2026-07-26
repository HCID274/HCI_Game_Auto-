"""Regression tests for UU retry budget and window lifecycle policy."""

import unittest
from contextlib import nullcontext
from unittest.mock import patch

from starrail_auto.uu.desktop import keep_uu_in_background_on_exit
from starrail_auto.uu.errors import UuStartupError, UuStartupFinalError
from starrail_auto.uu.service import ensure_uu_connected


class UuSupervisorTests(unittest.TestCase):
    def test_two_failed_attempts_use_two_restarts_then_succeed(self) -> None:
        errors = [
            UuStartupError("step1", "not found"),
            UuStartupError("step2", "not found"),
            None,
        ]
        with patch("starrail_auto.uu.service.require_admin"), patch(
            "starrail_auto.uu.service.keep_uu_in_background_on_exit",
            return_value=nullcontext(),
        ), patch(
            "starrail_auto.uu.service._run_startup_attempt",
            side_effect=errors,
        ), patch("starrail_auto.uu.service.kill_uu") as kill, patch(
            "starrail_auto.uu.service.time.sleep"
        ):
            retries = ensure_uu_connected()

        self.assertEqual(retries, 2)
        self.assertEqual(kill.call_count, 2)

    def test_restart_budget_is_exactly_three(self) -> None:
        with patch("starrail_auto.uu.service.require_admin"), patch(
            "starrail_auto.uu.service.keep_uu_in_background_on_exit",
            return_value=nullcontext(),
        ), patch(
            "starrail_auto.uu.service._run_startup_attempt",
            side_effect=UuStartupError("step", "not found"),
        ) as attempt, patch("starrail_auto.uu.service.kill_uu") as kill, patch(
            "starrail_auto.uu.service.time.sleep"
        ):
            with self.assertRaises(UuStartupFinalError) as raised:
                ensure_uu_connected()

        self.assertEqual(attempt.call_count, 4)
        self.assertEqual(kill.call_count, 3)
        self.assertEqual(raised.exception.restarts_used, 3)


class UuWindowLifecycleTests(unittest.TestCase):
    def test_failure_exit_still_minimizes_uu(self) -> None:
        with patch("starrail_auto.uu.desktop.minimize_best_effort") as minimize:
            with self.assertRaisesRegex(RuntimeError, "boom"):
                with keep_uu_in_background_on_exit("test failure"):
                    raise RuntimeError("boom")

        minimize.assert_called_once_with("test failure")


if __name__ == "__main__":
    unittest.main()
