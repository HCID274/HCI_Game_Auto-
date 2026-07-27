"""UU supervisor regression tests."""

from contextlib import nullcontext
from unittest.mock import patch

import pytest

from wuwa_auto.uu.errors import UuStartupError, UuStartupFinalError
from wuwa_auto.uu.service import ensure_connected


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

