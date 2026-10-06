"""Regression tests for the host-side M7A disclaimer handler."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

import pytest
from game_automation_core.windows.desktop_guard import WindowSnapshot
from PIL import Image, ImageDraw

from starrail_auto.m7a import disclaimer
from starrail_auto.m7a.disclaimer import M7ADisclaimerHandler


@pytest.fixture(autouse=True)
def _no_desktop_screenshot(monkeypatch) -> None:
    # 点击前的取证会截真实桌面存进 runtime/evidence；测试里不该截用户桌面。
    monkeypatch.setattr(disclaimer, "save_screenshot", lambda _prefix: None)


def _window(*, process_name: str = "March7th Assistant.exe") -> WindowSnapshot:
    return WindowSnapshot(
        hwnd=100,
        pid=200,
        process_name=process_name,
        executable=r"D:\M7A\March7th Assistant.exe",
        title="March7th Assistant v2026.7.26",
        command_line="",
        foreground=True,
    )


@dataclass
class _Clock:
    now: float = 0.0

    def __call__(self) -> float:
        return self.now


def test_acknowledgement_waits_for_upstream_ten_second_guard() -> None:
    clock = _Clock()
    clicked: list[tuple[int, int]] = []
    config = [False]
    acknowledged = False
    handler = M7ADisclaimerHandler(
        started_at=0.0,
        windows_getter=lambda: [_window()],
        target_finder=lambda _window: (500, 700) if not clicked else None,
        config_reader=lambda: acknowledged or bool(config[0]),
        activator=lambda _hwnd: None,
        clicker=lambda x, y: clicked.append((x, y)),
        clock=clock,
    )

    assert handler.poll() is False
    clock.now = 9.9
    assert handler.poll() is False
    clock.now = 10.0
    assert handler.poll() is False
    assert clicked == [(500, 700)]
    acknowledged = True
    clock.now = 10.5
    assert handler.poll() is True
    assert clicked == [(500, 700)]


def test_already_acknowledged_disclaimer_never_clicks() -> None:
    clicked: list[tuple[int, int]] = []
    handler = M7ADisclaimerHandler(
        started_at=0.0,
        windows_getter=lambda: [_window()],
        config_reader=lambda: True,
        clicker=lambda x, y: clicked.append((x, y)),
    )

    assert handler.poll() is True
    assert clicked == []


def test_launcher_owned_disclaimer_is_handled_before_assistant_exists() -> None:
    clock = _Clock()
    clicked: list[tuple[int, int]] = []
    acknowledged = False
    handler = M7ADisclaimerHandler(
        started_at=0.0,
        windows_getter=lambda: [_window(process_name="March7th Launcher.exe")],
        target_finder=lambda _window: (500, 700) if not clicked else None,
        config_reader=lambda: acknowledged,
        activator=lambda _hwnd: None,
        clicker=lambda x, y: clicked.append((x, y)),
        clock=clock,
    )

    assert handler.poll() is False
    clock.now = 10.0
    assert handler.poll() is False
    assert clicked == [(500, 700)]
    acknowledged = True
    clock.now = 10.5
    assert handler.poll() is True


def test_unrelated_window_is_never_clicked() -> None:
    clicked: list[tuple[int, int]] = []
    handler = M7ADisclaimerHandler(
        started_at=0.0,
        windows_getter=lambda: [_window(process_name="other.exe")],
        config_reader=lambda: False,
        target_finder=lambda _window: (500, 700),
        clicker=lambda x, y: clicked.append((x, y)),
    )

    assert handler.poll() is True
    assert clicked == []


def test_button_locator_anchors_left_acknowledge_button_to_right_exit_button() -> None:
    image = Image.new("RGB", (1000, 800), (32, 32, 32))
    ImageDraw.Draw(image).rectangle((550, 650, 800, 690), fill=(255, 165, 205))
    window = _window()
    with patch.object(disclaimer, "_window_rect", return_value=(100, 100, 900, 750)), patch.object(
        disclaimer,
        "_virtual_screen_origin",
        return_value=(0, 0),
    ):
        position = disclaimer.find_acknowledge_position(window, screenshot=image)

    assert position is not None
    assert 410 <= position[0] <= 425
    assert 668 <= position[1] <= 672
