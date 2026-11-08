"""Regression tests for UU retry budget and window lifecycle policy."""

import unittest
from contextlib import nullcontext
from unittest.mock import patch

from starrail_auto.uu.desktop import keep_uu_in_background_on_exit
from starrail_auto.uu.errors import UuStartupError, UuStartupFinalError
from starrail_auto.uu.service import (
    _confirm_starrail_active,
    _hover_until_accelerate_button,
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
            "starrail_auto.uu.service._recover_mandatory_update",
            return_value=False,
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


    def test_cursor_is_parked_before_locating_the_starrail_card(self) -> None:
        # 2026-10-03：鼠标残留在卡片上使其变成悬停样式，卡片原图模板找不到。
        order: list[str] = []
        with patch(
            "starrail_auto.uu.service.require_desktop_ready"
        ), patch(
            "starrail_auto.uu.service.describe_window",
            return_value="test desktop",
        ), patch(
            "starrail_auto.uu.service.require_supported_display"
        ), patch(
            "starrail_auto.uu.service.is_uu_running",
            return_value=False,
        ), patch(
            "starrail_auto.uu.service._ensure_uu_started"
        ), patch(
            "starrail_auto.uu.service.focus_uu_window",
            return_value="UU加速器",
        ), patch(
            "starrail_auto.uu.service._recover_mandatory_update",
            return_value=False,
        ), patch(
            "starrail_auto.uu.service.dismiss_known_popups"
        ), patch(
            "starrail_auto.uu.service.park_cursor_for_detection",
            side_effect=lambda: order.append("park"),
        ), patch(
            "starrail_auto.uu.service.wait_for_image",
            side_effect=lambda *a, **k: order.append("locate") or (1, 2),
        ), patch(
            "starrail_auto.uu.service.move_mouse_to",
            side_effect=RuntimeError("stop after locate"),
        ), patch(
            "starrail_auto.uu.service.time.sleep"
        ):
            with self.assertRaises(RuntimeError):
                _run_startup_attempt(1)

        self.assertEqual(order, ["park", "locate"])


class UuAccelerateHoverTests(unittest.TestCase):
    # 1102/1104/1108：鼠标只移上去一次，UU 没浮出“立即加速”，干等后整轮失败。
    def test_re_hovers_the_card_until_the_accelerate_button_shows(self) -> None:
        with patch(
            "starrail_auto.uu.service.try_locate_image",
            side_effect=[None, None, (840, 734)],
        ), patch("starrail_auto.uu.service.move_mouse_to") as move:
            target = _hover_until_accelerate_button((844, 702))

        self.assertEqual(target, (840, 734))
        self.assertEqual(
            [c.args[0] for c in move.call_args_list], [(844, 642), (874, 582)]
        )

    def test_gives_up_after_the_step_timeout(self) -> None:
        clock = iter([0.0, 6.0, 12.0, 16.0])
        with patch(
            "starrail_auto.uu.service.try_locate_image", return_value=None
        ), patch("starrail_auto.uu.service.move_mouse_to") as move, patch(
            "starrail_auto.uu.service.time.monotonic", side_effect=lambda: next(clock)
        ), patch(
            "starrail_auto.uu.service.startup_error",
            side_effect=lambda step, reason: UuStartupError(step, reason),
        ):
            with self.assertRaises(UuStartupError):
                _hover_until_accelerate_button((844, 702))

        self.assertEqual(move.call_count, 2)


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
