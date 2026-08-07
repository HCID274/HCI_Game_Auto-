"""UU supervisor regression tests."""

from contextlib import nullcontext
from unittest.mock import patch

import pytest

from wuwa_auto.uu.errors import UuStartupError, UuStartupFinalError
from wuwa_auto.uu.service import _locate_verified_wuthering_start, ensure_connected


def test_two_restarts_then_success() -> None:
    outcomes = [
        UuStartupError("one", "missing"),
        UuStartupError("two", "missing"),
        None,
    ]
    with patch("wuwa_auto.uu.service.require_admin"), patch(
        "wuwa_auto.uu.service.minimize_on_exit", return_value=nullcontext()
    ), patch(
        "wuwa_auto.uu.service._run_attempt", side_effect=outcomes
    ), patch("wuwa_auto.uu.service.terminate_uu") as terminate, patch(
        "wuwa_auto.uu.service.time.sleep"
    ):
        assert ensure_connected() == 2
    assert terminate.call_count == 2


def test_restart_budget_is_exact() -> None:
    with patch("wuwa_auto.uu.service.require_admin"), patch(
        "wuwa_auto.uu.service.minimize_on_exit", return_value=nullcontext()
    ), patch(
        "wuwa_auto.uu.service._run_attempt",
        side_effect=UuStartupError("step", "missing"),
    ) as attempt, patch("wuwa_auto.uu.service.terminate_uu") as terminate, patch(
        "wuwa_auto.uu.service.time.sleep"
    ):
        with pytest.raises(UuStartupFinalError):
            ensure_connected()
    assert attempt.call_count == 3
    assert terminate.call_count == 2


def test_generic_start_is_used_only_after_wuthering_card_selection() -> None:
    with patch(
        "wuwa_auto.uu.service.park_cursor_for_detection"
    ) as park, patch(
        "wuwa_auto.uu.service.time.sleep"
    ), patch(
        "wuwa_auto.uu.service.wait_for_image",
        side_effect=[(840, 575), (840, 734)],
    ) as wait, patch(
        "wuwa_auto.uu.service.hover_after_evidence"
    ) as hover:
        assert _locate_verified_wuthering_start() == (840, 734)

    park.assert_called_once_with()
    assert wait.call_args_list[0].kwargs["step_name"] == "locate_wuthering_card"
    hover.assert_called_once_with((840, 575), "wuthering_card")
    assert wait.call_args_list[1].kwargs["step_name"] == "locate_start_acceleration"


def test_generic_start_for_another_game_is_rejected_and_reselected() -> None:
    with patch(
        "wuwa_auto.uu.service.park_cursor_for_detection"
    ) as park, patch(
        "wuwa_auto.uu.service.time.sleep"
    ), patch(
        "wuwa_auto.uu.service.wait_for_image",
        side_effect=[(840, 575), (1133, 734), (840, 575), (840, 734)],
    ), patch(
        "wuwa_auto.uu.service.hover_after_evidence"
    ) as hover, patch(
        "wuwa_auto.uu.service.save_step_screenshot"
    ) as screenshot:
        assert _locate_verified_wuthering_start() == (840, 734)

    assert park.call_count == 2
    assert hover.call_count == 2
    screenshot.assert_called_once_with("uu_start_action_wrong_card")

