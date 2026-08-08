from wuwa_auto.okww.farm_echo_state import (
    realm_defeat_visible,
    revive_dialog_visible,
)


class FakeRealmTask:
    def __init__(self, visible: list[bool]) -> None:
        self.visible = iter(visible)
        self.clicked = False

    def wait_ocr(self, *_: object, **__: object) -> object:
        return object() if next(self.visible) else None

    def wait_click_ocr(self, *_: object, **__: object) -> object:
        self.clicked = True
        return object()


def test_realm_defeat_requires_title_and_both_actions() -> None:
    assert realm_defeat_visible(FakeRealmTask([True, True, True]))
    assert not realm_defeat_visible(FakeRealmTask([False]))
    assert not realm_defeat_visible(FakeRealmTask([True, True, False]))


def test_revive_dialog_requires_title_and_confirm_action() -> None:
    assert revive_dialog_visible(FakeRealmTask([True, True]))
    assert not revive_dialog_visible(FakeRealmTask([True, False]))
