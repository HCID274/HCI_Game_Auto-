import pytest

from wuwa_auto.okww.farm_echo_state import (
    TUTORIAL_OVERLAY_MARKER,
    click_realm_defeat_exit,
    dismiss_tutorial_overlay,
    party_member_unavailable,
    realm_defeat_visible,
    revival_item_unavailable_visible,
    revive_dialog_visible,
)


class FakeRealmTask:
    def __init__(
        self,
        visible: list[bool],
        team_state: tuple[bool, int, int] = (True, 0, 3),
    ) -> None:
        self.visible = iter(visible)
        self.clicked = False
        self.team_state = team_state

    def wait_ocr(self, *_: object, **__: object) -> object:
        return object() if next(self.visible) else None

    def wait_click_ocr(self, *_: object, **__: object) -> object:
        self.clicked = True
        return object()

    def in_team(self) -> tuple[bool, int, int]:
        return self.team_state


def test_realm_defeat_requires_title_and_both_actions() -> None:
    assert realm_defeat_visible(FakeRealmTask([True, True, True]))
    assert not realm_defeat_visible(FakeRealmTask([False]))
    assert not realm_defeat_visible(FakeRealmTask([True, True, False]))


def test_revive_dialog_requires_title_and_confirm_action() -> None:
    assert revive_dialog_visible(FakeRealmTask([True, True]))
    assert not revive_dialog_visible(FakeRealmTask([True, False]))


def test_realm_defeat_exit_clicks_the_left_action() -> None:
    task = FakeRealmTask([True, True, True])

    click_realm_defeat_exit(task)

    assert task.clicked is True


def test_party_member_unavailable_requires_blocked_switch_and_party_hud() -> None:
    assert party_member_unavailable(
        FakeRealmTask([], (True, 0, 3)),
        "failed switch chars",
    )
    assert not party_member_unavailable(
        FakeRealmTask([], (False, 0, 0)),
        "failed switch chars",
    )
    assert not party_member_unavailable(
        FakeRealmTask([], (True, 0, 3)),
        "sleep check not in combat",
    )


def test_revival_item_prompt_requires_prompt_and_party_hud() -> None:
    assert revival_item_unavailable_visible(
        FakeRealmTask([True], team_state=(True, 0, 3))
    )
    assert not revival_item_unavailable_visible(
        FakeRealmTask([False], team_state=(True, 0, 3))
    )
    assert not revival_item_unavailable_visible(
        FakeRealmTask([True], team_state=(False, 0, 3))
    )


class FakeTutorialTask:
    def __init__(self, visible: list[bool]) -> None:
        self.visible = iter(visible)
        self.keys: list[str] = []
        self.logs: list[object] = []
        self.ensured = False

    def wait_ocr(self, *_: object, **__: object) -> object:
        return object() if next(self.visible) else None

    def send_key(self, key: str, **_: object) -> None:
        self.keys.append(key)

    def ensure_main(self, **_: object) -> None:
        self.ensured = True

    def log_info(self, message: object) -> None:
        self.logs.append(message)


def test_tutorial_overlay_absent_does_nothing() -> None:
    task = FakeTutorialTask([False])

    assert dismiss_tutorial_overlay(task) is False
    assert task.keys == [] and not task.ensured


def test_tutorial_overlay_pages_forward_then_returns_to_main() -> None:
    # 进入检测、翻第一页后仍在、翻第二页后提示消失、收尾复查已清除
    task = FakeTutorialTask([True, True, False, False])

    assert dismiss_tutorial_overlay(task) is True
    assert task.keys == ["d", "d"]
    assert task.ensured
    assert task.logs == [TUTORIAL_OVERLAY_MARKER]


def test_tutorial_overlay_that_survives_is_an_error() -> None:
    task = FakeTutorialTask([True] * 20)

    with pytest.raises(RuntimeError, match="tutorial overlay"):
        dismiss_tutorial_overlay(task)
