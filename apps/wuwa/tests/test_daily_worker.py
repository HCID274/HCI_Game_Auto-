import pytest

from wuwa_auto.okww.daily_worker import (
    ADDITIONAL_TASKS,
    AUTO_FARM_NIGHTMARE_NEST,
    DAILY_RESUME_MARKER,
    FARM_NIGHTMARE_FOR_DAILY_ECHO,
    install_daily_resume_after_nightmare,
    install_nightmare_override,
)


@pytest.mark.parametrize(
    ("initial", "team_ready", "next_action", "expected", "calls"),
    [
        ("gray_teleport", False, None, "gray_teleport", 1),
        ("team_close", True, None, "team_close", 1),
        ("team_close", False, "gray_teleport", "gray_teleport", 2),
        ("team_close", False, "team_start_challenge", "team_close", 2),
    ],
)
@pytest.mark.parametrize("use_keyword", [False, True])
def test_nest_entry_needs_action_not_just_close_icon(initial, team_ready, next_action, expected, calls, use_keyword):
    from types import SimpleNamespace

    class Nest:
        def __init__(self):
            self.waits = []

        def _travel_to_nest_or_skip(self, nest):
            return True

        def ensure_main(self, **kwargs):
            pass

        def log_info(self, message):
            pass

        def find_one(self, name):
            assert name == "team_start_challenge"
            return team_ready

        def wait_feature(self, features, **kwargs):
            self.waits.append((features, kwargs))
            return SimpleNamespace(name=initial if len(self.waits) == 1 else next_action)

    install_nightmare_override(Nest)
    task = Nest()
    features = ["fast_travel_custom", "gray_teleport", "remove_custom", "team_close"]
    if use_keyword:
        result = task.wait_feature(feature=features, time_out=10, raise_if_not_found=True)
    else:
        result = task.wait_feature(features, time_out=10, raise_if_not_found=True)
    assert result.name == expected
    assert len(task.waits) == calls
    if calls == 2:
        assert "team_close" not in task.waits[1][0]
        assert task.waits[1][1] == {"time_out": 10, "raise_if_not_found": True}


def test_daily_resume_skips_nightmare_only_in_process() -> None:
    original_additional = [AUTO_FARM_NIGHTMARE_NEST, "Check Weekly Garden"]

    class Daily:
        def __init__(self) -> None:
            self.config = {
                FARM_NIGHTMARE_FOR_DAILY_ECHO: True,
                ADDITIONAL_TASKS: original_additional,
            }
            self.messages: list[str] = []
            self.observed: dict[str, object] = {}

        def log_info(self, message: str) -> None:
            self.messages.append(message)

        def run(self) -> str:
            self.observed = dict(self.config)
            return "completed"

    install_daily_resume_after_nightmare(Daily)
    task = Daily()

    assert task.run() == "completed"
    assert task.observed[FARM_NIGHTMARE_FOR_DAILY_ECHO] is False
    assert task.observed[ADDITIONAL_TASKS] == ["Check Weekly Garden"]
    assert task.config[FARM_NIGHTMARE_FOR_DAILY_ECHO] is True
    assert task.config[ADDITIONAL_TASKS] is original_additional
    assert task.messages == [DAILY_RESUME_MARKER]


def test_daily_resume_restores_config_when_daily_raises() -> None:
    class Daily:
        def __init__(self) -> None:
            self.config = {
                FARM_NIGHTMARE_FOR_DAILY_ECHO: True,
                ADDITIONAL_TASKS: [AUTO_FARM_NIGHTMARE_NEST],
            }

        def log_info(self, _message: str) -> None:
            return None

        def run(self) -> None:
            assert self.config[FARM_NIGHTMARE_FOR_DAILY_ECHO] is False
            assert self.config[ADDITIONAL_TASKS] == []
            raise RuntimeError("daily failure")

    install_daily_resume_after_nightmare(Daily)
    task = Daily()

    with pytest.raises(RuntimeError, match="daily failure"):
        task.run()

    assert task.config == {
        FARM_NIGHTMARE_FOR_DAILY_ECHO: True,
        ADDITIONAL_TASKS: [AUTO_FARM_NIGHTMARE_NEST],
    }
