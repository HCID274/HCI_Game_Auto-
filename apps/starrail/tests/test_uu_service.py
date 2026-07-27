"""Regression tests for UU retry budget and window lifecycle policy."""

import unittest
from contextlib import nullcontext
from unittest.mock import patch

from starrail_auto.uu.desktop import keep_uu_in_background_on_exit
from starrail_auto.uu.errors import UuStartupError, UuStartupFinalError
from starrail_auto.uu.service import (
    _confirm_starrail_active,
    _run_startup_attempt,
    ensure_uu_connected,
)


class UuIdentityTests(unittest.TestCase):
    def test_generic_stop_button_does_not_prove_starrail_identity(self) -> None:
        with patch(
            "starrail_auto.uu.service.try_locate_image",
            side_effect=[None, (20, 30)],
        ):
            self.assertFalse(_confirm_starrail_active(0.1))

    def test_other_game_acceleration_is_disconnected_and_retried(self) -> None:
        error = UuStartupError(
            "reuse_acceleration_identity",
            "active acceleration was not verified as Star Rail",
        )
        with patch(
            "starrail_auto.uu.service.require_desktop_ready"
        ), patch(
            "starrail_auto.uu.service.describe_window",
            return_value="test desktop",
        ), patch(
            "starrail_auto.uu.service.require_supported_display"
        ), patch(
            "starrail_auto.uu.service.is_uu_running",
            return_value=True,
        ), patch(
            "starrail_auto.uu.service.focus_uu_window",
            return_value="UU加速器",
        ), patch(
            "starrail_auto.uu.service.dismiss_known_popups"
        ), patch(
            "starrail_auto.uu.service._confirm_starrail_active",
            return_value=False,
        ), patch(
            "starrail_auto.uu.service.try_locate_image",
            return_value=(20, 30),
        ), patch(
            "starrail_auto.uu.service.click"
        ) as click, patch(
            "starrail_auto.uu.service.startup_error",
            return_value=error,
        ), patch(
            "starrail_auto.uu.service.time.sleep"
        ), patch(
            "starrail_auto.uu.service.wait_for_image"
        ) as wait_for_image:
            with self.assertRaises(UuStartupError) as raised:
                _run_startup_attempt(1)

        self.assertIs(raised.exception, error)
        click.assert_called_once_with((20, 30))
        wait_for_image.assert_not_called()


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
