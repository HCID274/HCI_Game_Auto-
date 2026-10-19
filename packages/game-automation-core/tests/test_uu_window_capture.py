import random
from pathlib import Path

import pytest
from PIL import Image

from game_automation_core.uu import desktop
from game_automation_core.uu.desktop import UuDesktopConfig, UuDesktopController
from game_automation_core.uu.errors import UuStartupError


def _noise(width: int, height: int, seed: int) -> Image.Image:
    rng = random.Random(seed)
    return Image.frombytes("RGB", (width, height), bytes(rng.randrange(256) for _ in range(width * height * 3)))


@pytest.fixture()
def controller(tmp_path: Path, monkeypatch) -> tuple[UuDesktopController, Path]:
    # UU 窗口内容：随机底图，(300, 200) 处贴一张卡片；窗口左上角在屏幕 (1000, 500)。
    card = _noise(80, 40, seed=1)
    window = _noise(600, 400, seed=2)
    window.paste(card, (300, 200))
    template = tmp_path / "card.png"
    card.save(template)
    monkeypatch.setattr(desktop, "capture_window", lambda _hwnd: (window, 1000, 500))
    config = UuDesktopConfig(
        evidence_dir=tmp_path,
        expected_screen_size=(2560, 1440),
        window_keywords=("uu",),
        process_names=frozenset({"uu.exe"}),
        update_notice=tmp_path / "missing.png",
        update_actions=(),
        admin_hint="test",
        image_interval=0.01,
    )
    instance = UuDesktopController(config, is_process_running=lambda: True)
    monkeypatch.setattr(instance, "uu_window_handles", lambda: [42])
    monkeypatch.setattr(instance, "save_screenshot", lambda prefix: tmp_path / f"{prefix}.png")
    return instance, template


def test_template_is_found_in_uu_window_and_mapped_to_screen(controller) -> None:
    instance, template = controller
    instance.cover_at = lambda _point: ""

    # 卡片中心 = 窗口原点 + 贴图位置 + 半个卡片。
    assert instance.try_locate_image(template, timeout=0.05) == (1000 + 340, 500 + 220)


def test_covered_click_point_is_not_clicked_and_is_reported(controller) -> None:
    instance, template = controller
    instance.cover_at = lambda _point: "pid=7 process=PickerHost.exe title=位置已关闭。"

    with pytest.raises(UuStartupError, match="covered by pid=7 process=PickerHost.exe"):
        instance.wait_for_image(template, step_name="locate_card", timeout=0.05)
