import ast
import inspect
import textwrap
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from wuwa_auto.okww import confirmed_retry_worker


def _farm_echo_method_names() -> set[str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(confirmed_retry_worker.main)))
    task_class = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "FarmEchoTask"
    )
    return {
        node.name
        for node in task_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_confirmed_worker_never_overrides_upstream_combat_or_input() -> None:
    methods = _farm_echo_method_names()

    assert methods.isdisjoint(
        {
            "click",
            "mouse_down",
            "mouse_up",
            "combat_once",
            "in_realm",
            "teleport_to_boss_enabled",
            "open_boss_book",
        }
    )


def test_confirmed_worker_keeps_only_host_accounting_and_death_boundary() -> None:
    methods = _farm_echo_method_names()

    assert methods == {
        "__init__",
        "manage_boss_interactions",
        "raise_not_in_combat",
        "teleport_to_configured_boss_and_prepare",
        "host_record_absorption",
        "incr_drop",
        "run",
    }


def test_virtual_hid_is_scoped_to_entry_and_restores_upstream_methods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clicks: list[tuple[int, int]] = []
    upstream: list[str] = []

    class Task:
        width = 2560
        height = 1440

        def __init__(self) -> None:
            self.executor = SimpleNamespace(
                interaction=SimpleNamespace(
                    capture=SimpleNamespace(
                        get_abs_cords=lambda x, y: (x, y),
                    )
                ),
                reset_scene=lambda: None,
            )

        def check_interval(self, _interval: float) -> bool:
            return True

        def click(self, *_args: object, **_kwargs: object) -> str:
            upstream.append("click")
            return "upstream"

        def open_boss_book(self, *_args: object, **_kwargs: object) -> None:
            upstream.append("open")

        def log_info(self, _message: str) -> None:
            pass

        def sleep(self, _seconds: float) -> None:
            pass

    monkeypatch.setattr(
        "wuwa_auto.okww.virtual_hid.virtual_hid_click",
        lambda x, y, **_kwargs: clicks.append((x, y)),
    )
    task = Task()
    original_click = task.click
    original_open = task.open_boss_book

    with confirmed_retry_worker._scoped_entry_navigation_hid(task):
        assert task.click(101, 428, name="gray_book_boss") is True
        assert task.click(1280, 720, name=None) == "upstream"

    assert clicks == [(101, 428)]
    assert task.click.__func__ is original_click.__func__
    assert task.open_boss_book.__func__ is original_open.__func__
    assert task.click(2431, 771, name="boss_proceed") == "upstream"


def test_active_realm_resume_only_initializes_host_state() -> None:
    task = Mock()

    confirmed_retry_worker._prepare_active_realm_resume(task)

    task.ensure_main.assert_called_once_with(time_out=30)
    task.init_parameters.assert_called_once_with()
    task.log_info.assert_called_once_with(
        confirmed_retry_worker.ACTIVE_REALM_RESUME_MARKER
    )
    assert task._teleport_walk_result == "realm"
    assert task._in_realm is True
    assert task.treat_as_not_in_realm is False
    assert task._has_treasure is False
    assert task._just_entered_boss_realm is True
    called_methods = {item[0] for item in task.method_calls}
    for forbidden in ("click", "send_key", "mouse_down", "mouse_up", "combat_once"):
        assert forbidden not in called_methods
