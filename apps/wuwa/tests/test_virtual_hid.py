import pytest

from wuwa_auto.input.viiper import encode_mouse_packet


def test_mouse_packet_matches_viiper_wire_format() -> None:
    assert encode_mouse_packet(buttons=1, dx=2, dy=-3, wheel=4, pan=-5) == (
        b"\x01\x02\x00\xfd\xff\x04\x00\xfb\xff"
    )


def test_mouse_packet_rejects_out_of_range_delta() -> None:
    with pytest.raises(ValueError):
        encode_mouse_packet(dx=32768)
