from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw
from wuwa_auto.client.launcher import (
    WindowInfo,
    _ClientRestartRequired,
    _ensure_game_world,
    _locate_network_retry,
    _search_region,
    _world_hud_visible,
    ensure_client_ready,
)
from wuwa_auto.settings import (
    WUWA_CLIENT_NETWORK_RETRY_TEMPLATE,
    WUWA_CLIENT_REMOTE_CONFIG_RETRY_TEMPLATE,
    WUWA_CLIENT_UPDATE_RESTART_CONFIRM_TEMPLATE,
    WUWA_CLIENT_UPDATE_RESTART_NOTICE_TEMPLATE,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeMouse:
    def __init__(self) -> None:
        self.clicks: list[tuple[int, int]] = []

    def click_at(self, x: int, y: int) -> None:
        self.clicks.append((x, y))


def _window(name: str, pid: int) -> WindowInfo:
    return WindowInfo(
        hwnd=pid,
        pid=pid,
        title="鸣潮",
        executable=Path(f"D:/{name}"),
        rect=(0, 0, 1307, 784),
    )


def test_search_region_clamps_borderless_window_to_physical_screen() -> None:
    window = WindowInfo(
        hwnd=1,
        pid=2,
        title="Client",
        executable=Path("D:/Client-Win64-Shipping.exe"),
        rect=(-9, -9, 2569, 1449),
    )
    with patch("wuwa_auto.client.launcher.pyautogui.size", return_value=(2560, 1440)):
        assert _search_region(window) == (0, 0, 2560, 1440)


def test_world_hud_requires_three_distributed_ui_regions() -> None:
    game = _window("Client-Win64-Shipping.exe", 20)
    screenshot = Image.new("RGB", (1307, 784), "black")
    draw = ImageDraw.Draw(screenshot)
    draw.rectangle((0, 0, 100, 100), fill="white")
    draw.rectangle((1000, 0, 1100, 100), fill="white")
    draw.rectangle((1000, 650, 1100, 750), fill="white")
    with patch(
        "wuwa_auto.client.launcher.pyautogui.size", return_value=(1307, 784)
    ), patch(
        "wuwa_auto.client.launcher.pyautogui.screenshot", return_value=screenshot
    ):
        assert _world_hud_visible(game)


def test_real_client_update_dialog_matches_both_templates() -> None:
    fixture = cv2.imread(
        str(Path(__file__).parent / "fixtures" / "client_update_restart_dialog.png")
    )
    assert fixture is not None
    for template_path in (
        WUWA_CLIENT_UPDATE_RESTART_NOTICE_TEMPLATE,
        WUWA_CLIENT_UPDATE_RESTART_CONFIRM_TEMPLATE,
    ):
        template = cv2.imread(str(template_path))
        assert template is not None
        confidence = float(
            np.max(cv2.matchTemplate(fixture, template, cv2.TM_CCOEFF_NORMED))
        )
        assert confidence >= 0.99


def test_existing_game_is_reused_without_opening_launcher() -> None:
    game = _window("Client-Win64-Shipping.exe", 42)
    with patch("wuwa_auto.client.launcher.require_admin"), patch(
        "wuwa_auto.client.launcher._require_templates"
    ), patch("wuwa_auto.client.launcher._game_window", return_value=game), patch(
        "wuwa_auto.client.launcher._ensure_game_world", return_value=game
    ) as ensure_world, patch(
        "wuwa_auto.client.launcher._save_screenshot", return_value=Path("reused.png")
    ), patch("wuwa_auto.client.launcher.stop_client_launchers"), patch(
        "wuwa_auto.client.launcher._launch_launcher"
    ) as launch:
        result = ensure_client_ready(FakeMouse())

    assert result.game_pid == 42
    assert not result.updated
    assert result.launcher_actions == ()
    ensure_world.assert_called_once()
    launch.assert_not_called()


def test_update_state_then_enter_game_is_driven_by_distinct_screenshots() -> None:
    launcher = _window("launcher_main.exe", 10)
    game = _window("Client-Win64-Shipping.exe", 20)
    clock = FakeClock()
    mouse = FakeMouse()

    with patch("wuwa_auto.client.launcher.require_admin"), patch(
        "wuwa_auto.client.launcher._require_templates"
    ), patch(
        "wuwa_auto.client.launcher._game_window",
        side_effect=[None, None, None, game],
    ), patch(
        "wuwa_auto.client.launcher._launcher_window", return_value=launcher
    ), patch(
        "wuwa_auto.client.launcher._locate",
        side_effect=[None, (100, 100), (200, 200)],
    ), patch(
        "wuwa_auto.client.launcher._button_state_hash", return_value="update-v1"
    ), patch("wuwa_auto.client.launcher._focus"), patch(
        "wuwa_auto.client.launcher._ensure_game_world", return_value=game
    ), patch(
        "wuwa_auto.client.launcher._save_screenshot", return_value=Path("screen.png")
    ), patch(
        "wuwa_auto.client.launcher._save_action_crop", return_value=Path("action.png")
    ), patch("wuwa_auto.client.launcher.stop_client_launchers") as stop:
        result = ensure_client_ready(
            mouse,
            update_timeout=120,
            game_timeout=60,
            sleep=clock.sleep,
            clock=clock,
        )

    assert result.updated
    assert result.launcher_actions == ("update_action", "enter_game")
    assert mouse.clicks == [(217, 100), (200, 200)]
    assert result.game_pid == 20
    stop.assert_called_once_with()


def test_monthly_reward_is_claimed_before_world_is_accepted() -> None:
    game = _window("Client-Win64-Shipping.exe", 20)
    clock = FakeClock()
    mouse = FakeMouse()
    evidence: list[str] = []
    actions: list[str] = []

    with patch("wuwa_auto.client.launcher._game_window", return_value=game), patch(
        "wuwa_auto.client.launcher._world_hud_visible",
        side_effect=[False, True, True],
    ), patch(
        "wuwa_auto.client.launcher._client_update_restart_target",
        return_value=None,
    ), patch(
        "wuwa_auto.client.launcher._locate_network_retry",
        return_value=None,
    ), patch(
        "wuwa_auto.client.launcher._locate",
        side_effect=[None, (1200, 700)],
    ), patch("wuwa_auto.client.launcher._restore_game"), patch(
        "wuwa_auto.client.launcher._save_screenshot", return_value=Path("screen.png")
    ), patch(
        "wuwa_auto.client.launcher._save_action_crop", return_value=Path("action.png")
    ):
        ready = _ensure_game_world(
            mouse,
            game,
            timeout=60,
            evidence=evidence,
            actions=actions,
            sleep=clock.sleep,
            clock=clock,
        )

    assert ready is game
    assert mouse.clicks == [(1200, 700)]
    assert actions == ["claim_monthly_reward"]


def test_reward_result_is_closed_before_world_is_accepted() -> None:
    game = _window("Client-Win64-Shipping.exe", 20)
    clock = FakeClock()
    mouse = FakeMouse()
    actions: list[str] = []

    with patch("wuwa_auto.client.launcher._game_window", return_value=game), patch(
        "wuwa_auto.client.launcher._world_hud_visible",
        side_effect=[False, True, True],
    ), patch(
        "wuwa_auto.client.launcher._client_update_restart_target",
        return_value=None,
    ), patch(
        "wuwa_auto.client.launcher._locate_network_retry",
        return_value=None,
    ), patch(
        "wuwa_auto.client.launcher._locate",
        side_effect=[(900, 700)],
    ), patch("wuwa_auto.client.launcher._restore_game"), patch(
        "wuwa_auto.client.launcher._save_screenshot", return_value=Path("screen.png")
    ), patch(
        "wuwa_auto.client.launcher._save_action_crop", return_value=Path("action.png")
    ):
        _ensure_game_world(
            mouse,
            game,
            timeout=60,
            evidence=[],
            actions=actions,
            sleep=clock.sleep,
            clock=clock,
        )

    assert mouse.clicks == [(900, 700)]
    assert actions == ["close_reward_result"]


def test_network_error_is_retried_before_world_is_accepted() -> None:
    game = _window("Client-Win64-Shipping.exe", 20)
    clock = FakeClock()
    mouse = FakeMouse()
    actions: list[str] = []

    with patch("wuwa_auto.client.launcher._game_window", return_value=game), patch(
        "wuwa_auto.client.launcher._world_hud_visible",
        side_effect=[False, True, True],
    ), patch(
        "wuwa_auto.client.launcher._client_update_restart_target",
        return_value=None,
    ), patch(
        "wuwa_auto.client.launcher._locate_network_retry",
        side_effect=[(1000, 600)],
    ), patch("wuwa_auto.client.launcher._restore_game"), patch(
        "wuwa_auto.client.launcher._save_screenshot", return_value=Path("screen.png")
    ), patch(
        "wuwa_auto.client.launcher._save_action_crop", return_value=Path("action.png")
    ):
        _ensure_game_world(
            mouse,
            game,
            timeout=60,
            evidence=[],
            actions=actions,
            sleep=clock.sleep,
            clock=clock,
        )

    assert mouse.clicks == [(1000, 600)]
    assert actions == ["retry_game_network"]


def test_network_retry_recognizes_new_dialog_then_keeps_legacy_fallback() -> None:
    with patch(
        "wuwa_auto.client.launcher._locate",
        side_effect=[None, (1607, 905)],
    ) as locate:
        assert _locate_network_retry(region=(0, 0, 2560, 1440)) == (1607, 905)

    assert locate.call_args_list[0].args[0] == WUWA_CLIENT_REMOTE_CONFIG_RETRY_TEMPLATE
    assert locate.call_args_list[1].args[0] == WUWA_CLIENT_NETWORK_RETRY_TEMPLATE


def test_client_update_complete_is_confirmed_and_requests_restart() -> None:
    game = _window("Client-Win64-Shipping.exe", 20)
    clock = FakeClock()
    mouse = FakeMouse()
    actions: list[str] = []

    with patch("wuwa_auto.client.launcher._game_window", return_value=game), patch(
        "wuwa_auto.client.launcher._world_hud_visible", return_value=False
    ), patch(
        "wuwa_auto.client.launcher._client_update_restart_target",
        return_value=(1000, 700),
    ), patch("wuwa_auto.client.launcher._restore_game"), patch(
        "wuwa_auto.client.launcher._save_screenshot", return_value=Path("screen.png")
    ), patch(
        "wuwa_auto.client.launcher._save_action_crop", return_value=Path("action.png")
    ):
        with pytest.raises(_ClientRestartRequired) as raised:
            _ensure_game_world(
                mouse,
                game,
                timeout=60,
                evidence=[],
                actions=actions,
                sleep=clock.sleep,
                clock=clock,
            )

    assert raised.value.previous_pid == 20
    assert mouse.clicks == [(1000, 700)]
    assert actions == ["confirm_client_update_restart"]


def test_existing_client_update_restart_reenters_world_state_machine() -> None:
    old_game = _window("Client-Win64-Shipping.exe", 20)
    new_game = _window("Client-Win64-Shipping.exe", 21)
    mouse = FakeMouse()
    calls = 0

    def ensure_world(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            kwargs["actions"].append("confirm_client_update_restart")
            raise _ClientRestartRequired(old_game.pid)
        return new_game

    with patch("wuwa_auto.client.launcher.require_admin"), patch(
        "wuwa_auto.client.launcher._require_templates"
    ), patch(
        "wuwa_auto.client.launcher._game_window", return_value=old_game
    ), patch(
        "wuwa_auto.client.launcher._ensure_game_world", side_effect=ensure_world
    ), patch(
        "wuwa_auto.client.launcher._wait_for_restarted_game", return_value=new_game
    ), patch(
        "wuwa_auto.client.launcher._save_screenshot", return_value=Path("screen.png")
    ), patch("wuwa_auto.client.launcher.stop_client_launchers"), patch(
        "wuwa_auto.client.launcher._launch_launcher"
    ) as launch:
        result = ensure_client_ready(mouse)

    assert calls == 2
    assert result.updated
    assert result.game_pid == 21
    assert result.launcher_actions == ("confirm_client_update_restart",)
    launch.assert_not_called()
