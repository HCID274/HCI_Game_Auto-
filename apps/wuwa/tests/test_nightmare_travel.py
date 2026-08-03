from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from wuwa_auto.okww.daily_worker import (
    TRAVEL_CONFIRMED_MARKER,
    TRAVEL_NOT_CONFIRMED_MARKER,
    TRAVEL_RECOVERED_MARKER,
    TRAVEL_RETRY_MARKER,
    _probe_transition,
    confirmed_nightmare_travel,
    install_nightmare_override,
)


def _task(*wait_results: object) -> Mock:
    task = Mock()
    task.wait_until.side_effect = list(wait_results)
    task.in_team_and_world.return_value = True
    task._unreachable_nests = set()
    return task


def _messages(task: Mock) -> list[str]:
    return [call.args[0] for call in task.log_info.call_args_list]


def test_confirmation_popup_is_clicked_without_declaring_transition() -> None:
    task = Mock()
    task.in_team_and_world.return_value = False
    confirm = SimpleNamespace(name="confirm")
    task._find_first_feature.return_value = confirm

    assert _probe_transition(task, "gray_teleport") is None
    task.click.assert_called_once_with(confirm, after_sleep=1)
    task.find_one.assert_not_called()


def test_button_disappearance_is_a_transition_candidate() -> None:
    task = Mock()
    task.in_team_and_world.return_value = False
    task._find_first_feature.return_value = None
    task.find_one.return_value = None

    assert _probe_transition(task, "gray_teleport") == "button_gone"


def test_delayed_transition_waits_for_world_before_success() -> None:
    travel = SimpleNamespace(name="gray_teleport")
    task = _task(travel, "button_gone")
    task.wait_in_team_and_world.return_value = True

    assert confirmed_nightmare_travel(
        task,
        SimpleNamespace(cache_key="go_nest:48:28"),
        upstream_ensure_main=Mock(),
    )
    task.click.assert_called_once_with(travel, after_sleep=1)
    assert any(TRAVEL_CONFIRMED_MARKER in message for message in _messages(task))


def test_missing_travel_button_recovers_instead_of_throwing_book_error() -> None:
    task = _task(None)
    upstream_ensure_main = Mock()
    nest = SimpleNamespace(cache_key="go_nest:48:28")

    assert not confirmed_nightmare_travel(
        task,
        nest,
        upstream_ensure_main=upstream_ensure_main,
    )
    upstream_ensure_main.assert_called_once_with(task, time_out=60)
    assert task._unreachable_nests == {"go_nest:48:28"}
    assert any("travel_button_missing" in message for message in _messages(task))


def test_still_visible_button_is_clicked_a_second_time() -> None:
    first = SimpleNamespace(name="gray_teleport")
    second = SimpleNamespace(name="gray_teleport")
    task = _task(first, None, second, "world")

    assert confirmed_nightmare_travel(
        task,
        SimpleNamespace(cache_key="go_nest:48:28"),
        upstream_ensure_main=Mock(),
    )
    assert task.click.call_count == 2
    assert any(TRAVEL_RETRY_MARKER in message for message in _messages(task))


def test_failed_retry_recovers_world_and_caches_only_that_target() -> None:
    first = SimpleNamespace(name="gray_teleport")
    second = SimpleNamespace(name="gray_teleport")
    task = _task(first, None, second, None)
    upstream_ensure_main = Mock()
    nest = SimpleNamespace(cache_key="go_nest:48:28")

    assert not confirmed_nightmare_travel(
        task,
        nest,
        upstream_ensure_main=upstream_ensure_main,
    )
    upstream_ensure_main.assert_called_once_with(task, time_out=60)
    assert task._unreachable_nests == {"go_nest:48:28"}
    assert any(TRAVEL_NOT_CONFIRMED_MARKER in message for message in _messages(task))
    assert any(TRAVEL_RECOVERED_MARKER in message for message in _messages(task))


def test_failed_world_recovery_is_explicit() -> None:
    travel = SimpleNamespace(name="gray_teleport")
    task = _task(travel, None, travel, None)
    task.in_team_and_world.return_value = False

    with pytest.raises(RuntimeError, match="returned outside open world"):
        confirmed_nightmare_travel(
            task,
            SimpleNamespace(cache_key="go_nest:48:28"),
            upstream_ensure_main=Mock(),
        )


def test_override_rejects_changed_upstream_signature() -> None:
    class ChangedTask:
        def ensure_main(self, time_out: float) -> None:
            pass

        def _travel_to_nest_or_skip(self, nest: object, extra: object) -> bool:
            return True

    with pytest.raises(RuntimeError, match="incompatible"):
        install_nightmare_override(ChangedTask)


def test_override_uses_unbound_upstream_ensure_main() -> None:
    ensured: list[float] = []

    class CompatibleTask:
        def ensure_main(self, time_out: float) -> None:
            ensured.append(time_out)

        def _travel_to_nest_or_skip(self, nest: object) -> bool:
            return True

    install_nightmare_override(CompatibleTask)
    task = CompatibleTask()
    task.ensure_main = Mock(side_effect=AssertionError("instance shadow used"))
    with patch(
        "wuwa_auto.okww.daily_worker.confirmed_nightmare_travel",
        return_value=True,
    ) as travel:
        assert task._travel_to_nest_or_skip(SimpleNamespace(cache_key="target"))

    upstream = travel.call_args.kwargs["upstream_ensure_main"]
    upstream(task, time_out=60)
    assert ensured == [60]
    task.ensure_main.assert_not_called()
