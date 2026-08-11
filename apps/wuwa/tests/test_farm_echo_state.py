from wuwa_auto.okww.farm_echo_state import (
    click_realm_defeat_exit,
    party_member_unavailable,
    realm_defeat_visible,
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
