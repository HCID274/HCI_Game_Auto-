import pytest

from game_automation_core.uu.errors import UuStartupError, UuStartupFinalError
from game_automation_core.uu.supervisor import run_with_restart_budget


def test_restart_budget_is_single_and_exact() -> None:
    attempts: list[int] = []
    restarts: list[bool] = []

    def fail(number: int) -> None:
        attempts.append(number)
        raise UuStartupError("card", "not found")

    with pytest.raises(UuStartupFinalError) as raised:
        run_with_restart_budget(
            attempt=fail,
            restart=lambda: restarts.append(True),
            max_restarts=3,
            restart_delay=0,
            sleep=lambda _: None,
        )

    assert attempts == [1, 2, 3, 4]
    assert len(restarts) == raised.value.restarts_used == 3


def test_non_retryable_error_never_restarts() -> None:
    error = UuStartupError("desktop", "locked", retryable=False)
    restarts: list[bool] = []
    with pytest.raises(UuStartupError) as raised:
        run_with_restart_budget(
            attempt=lambda _: (_ for _ in ()).throw(error),
            restart=lambda: restarts.append(True),
            max_restarts=3,
            restart_delay=0,
        )
    assert raised.value is error
    assert restarts == []
