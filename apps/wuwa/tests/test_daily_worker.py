import pytest

from wuwa_auto.okww.daily_worker import (
    ADDITIONAL_TASKS,
    AUTO_FARM_NIGHTMARE_NEST,
    DAILY_RESUME_MARKER,
    FARM_NIGHTMARE_FOR_DAILY_ECHO,
    install_daily_resume_after_nightmare,
)


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
