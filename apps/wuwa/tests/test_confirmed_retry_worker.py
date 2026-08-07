import json
from unittest.mock import Mock, patch

from wuwa_auto.okww.confirmed_retry_worker import (
    BOSS_PAGE_CONFIRMED_MARKER,
    BOSS_PAGE_RESELECTED_MARKER,
    _open_verified_boss_book,
    _virtual_hid_click,
)


class FakeTask:
    def __init__(self) -> None:
        self.visible_checks = iter([False, True])
        self.logs: list[str] = []
        self.category = object()
        self.category_clicks = 0

    def wait_ocr(self, **_: object) -> object:
        return object() if next(self.visible_checks) else None

    def find_one(self, *_: object, **__: object) -> object:
        return self.category

    def click_box(self, category: object, **_: object) -> None:
        assert category is self.category
        self.category_clicks += 1

    def log_info(self, message: str) -> None:
        self.logs.append(message)


def test_boss_page_is_reselected_and_verified_before_target_click() -> None:
    task = FakeTask()
    opens: list[tuple[str, float]] = []

    def upstream_open(name: str, *, after_sleep: float) -> None:
        opens.append((name, after_sleep))

    _open_verified_boss_book(
        task,
        upstream_open,
        "qiangdi",
        after_sleep=2,
    )

    assert opens == [("qiangdi", 2), ("qiangdi", 2)]
    assert task.category_clicks == 1
    assert task.logs == [
        BOSS_PAGE_RESELECTED_MARKER,
        BOSS_PAGE_CONFIRMED_MARKER,
    ]


def test_other_book_sections_keep_upstream_behavior() -> None:
    task = FakeTask()
    opens: list[tuple[str, float]] = []

    def upstream_open(name: str, *, after_sleep: float) -> None:
        opens.append((name, after_sleep))

    _open_verified_boss_book(
        task,
        upstream_open,
        "wuyin",
        after_sleep=1.5,
    )

    assert opens == [("wuyin", 1.5)]
    assert task.category_clicks == 0


def test_virtual_hid_click_uses_parent_control(monkeypatch) -> None:
    monkeypatch.setenv("WUWA_VIRTUAL_HID_CONTROL_PORT", "43123")
    monkeypatch.setenv("WUWA_VIRTUAL_HID_CONTROL_TOKEN", "secret")
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=None)
    client.recv.side_effect = [b'{"ok": true}\n']
    with patch(
        "wuwa_auto.okww.confirmed_retry_worker.socket.create_connection",
        return_value=client,
    ) as connect:
        _virtual_hid_click(101, 428, hold=0.2)

    connect.assert_called_once_with(("127.0.0.1", 43123), timeout=8)
    sent = json.loads(client.sendall.call_args.args[0].decode("utf-8"))
    assert sent == {
        "token": "secret",
        "action": "click",
        "x": 101,
        "y": 428,
        "hold": 0.2,
        "log_action": True,
    }


def test_virtual_hid_click_accepts_small_accelerated_cursor_miss(monkeypatch) -> None:
    monkeypatch.setenv("WUWA_VIRTUAL_HID_CONTROL_PORT", "43123")
    monkeypatch.setenv("WUWA_VIRTUAL_HID_CONTROL_TOKEN", "secret")

    def client_with(response: bytes) -> Mock:
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=None)
        client.recv.side_effect = [response]
        return client

    first = client_with(
        b'{"ok": false, "error": "virtual mouse could not reach '
        b'(614, 705); cursor=(611, 706)"}\n'
    )
    second = client_with(b'{"ok": true}\n')
    with patch(
        "wuwa_auto.okww.confirmed_retry_worker.socket.create_connection",
        side_effect=[first, second],
    ):
        _virtual_hid_click(614, 705)

    retry = json.loads(second.sendall.call_args.args[0].decode("utf-8"))
    assert (retry["x"], retry["y"]) == (611, 706)
