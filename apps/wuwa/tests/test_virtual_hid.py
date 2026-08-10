import json
from unittest.mock import Mock, patch

from wuwa_auto.okww.virtual_hid import virtual_hid_click


def test_virtual_hid_click_uses_parent_control(monkeypatch) -> None:
    monkeypatch.setenv("WUWA_VIRTUAL_HID_CONTROL_PORT", "43123")
    monkeypatch.setenv("WUWA_VIRTUAL_HID_CONTROL_TOKEN", "secret")
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=None)
    client.recv.side_effect = [b'{"ok": true}\n']
    with patch(
        "wuwa_auto.okww.virtual_hid.socket.create_connection",
        return_value=client,
    ) as connect:
        virtual_hid_click(101, 428, hold=0.2)

    connect.assert_called_once_with(("127.0.0.1", 43123), timeout=8)
    sent = json.loads(client.sendall.call_args.args[0].decode("utf-8"))
    assert sent == {
        "token": "secret",
        "action": "click",
        "x": 101,
        "y": 428,
        "button": "left",
        "hold": 0.2,
        "log_action": True,
    }


def test_virtual_hid_click_accepts_small_accelerated_cursor_miss(
    monkeypatch,
) -> None:
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
        "wuwa_auto.okww.virtual_hid.socket.create_connection",
        side_effect=[first, second],
    ):
        virtual_hid_click(614, 705)

    retry = json.loads(second.sendall.call_args.args[0].decode("utf-8"))
    assert (retry["x"], retry["y"]) == (611, 706)
