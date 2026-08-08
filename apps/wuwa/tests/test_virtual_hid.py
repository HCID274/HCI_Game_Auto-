from unittest.mock import Mock, patch

import pytest
from wuwa_auto.input.viiper import (
    LEFT_BUTTON,
    MIDDLE_BUTTON,
    VirtualHidMouse,
    _VirtualHidControlServer,
    encode_mouse_packet,
)


def test_mouse_packet_matches_viiper_wire_format() -> None:
    assert encode_mouse_packet(buttons=1, dx=2, dy=-3, wheel=4, pan=-5) == (
        b"\x01\x02\x00\xfd\xff\x04\x00\xfb\xff"
    )


def test_mouse_packet_rejects_out_of_range_delta() -> None:
    with pytest.raises(ValueError):
        encode_mouse_packet(dx=32768)


def test_virtual_mouse_emits_requested_button() -> None:
    mouse = VirtualHidMouse()
    mouse.move_to = Mock(return_value=(100, 200))
    mouse.send = Mock()
    with patch("wuwa_auto.input.viiper.time.sleep"):
        mouse.click_at(100, 200, button="middle", log_action=False)

    assert mouse.send.call_args_list[0].kwargs == {"buttons": MIDDLE_BUTTON}
    assert mouse.send.call_args_list[1].kwargs == {}


def test_virtual_mouse_rejects_unknown_button() -> None:
    mouse = VirtualHidMouse()
    with pytest.raises(ValueError, match="unsupported mouse button"):
        mouse.click_at(100, 200, button="side")


def test_virtual_mouse_preserves_other_held_buttons() -> None:
    mouse = VirtualHidMouse()
    mouse.send = Mock()

    mouse.set_button("right", True)
    mouse.set_button("left", True)
    mouse.set_button("right", False)

    assert mouse._buttons == LEFT_BUTTON
    assert mouse.send.call_count == 3


def test_virtual_mouse_release_clears_all_buttons() -> None:
    mouse = VirtualHidMouse()
    mouse.send = Mock()
    mouse.set_button("middle", True)
    mouse.release_buttons()

    assert mouse._buttons == 0
    assert mouse.send.call_count == 2


def test_release_active_mouse_buttons_clears_workflow_state() -> None:
    from wuwa_auto.input import viiper

    mouse = Mock()
    control = Mock()
    with patch.object(viiper, "_active_mouse", mouse), patch.object(
        viiper, "_active_control", control
    ):
        assert viiper.release_active_mouse_buttons()

    control.quiesce_and_release.assert_called_once_with()


def test_release_active_mouse_buttons_falls_back_without_control_server() -> None:
    from wuwa_auto.input import viiper

    mouse = Mock()
    with patch.object(viiper, "_active_mouse", mouse), patch.object(
        viiper, "_active_control", None
    ):
        assert viiper.release_active_mouse_buttons()

    mouse.release_buttons.assert_called_once_with()


def test_release_active_mouse_buttons_is_noop_without_workflow_mouse() -> None:
    from wuwa_auto.input import viiper

    with patch.object(viiper, "_active_mouse", None), patch.object(
        viiper, "_active_control", None
    ):
        assert not viiper.release_active_mouse_buttons()


def test_control_server_quiesces_and_resumes_admission() -> None:
    mouse = Mock()
    control = _VirtualHidControlServer(mouse)
    try:
        control.quiesce_and_release()
        assert control._accepting is False
        mouse.release_buttons.assert_called_once_with()

        control.resume()
        assert control._accepting is True
    finally:
        control.server.server_close()
