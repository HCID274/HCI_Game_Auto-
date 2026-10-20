import random
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from game_automation_core.uu import desktop
from game_automation_core.uu.desktop import UuDesktopConfig, UuDesktopController
from game_automation_core.uu.errors import UuStartupError


def _noise(width: int, height: int, seed: int) -> Image.Image:
    rng = random.Random(seed)
    return Image.frombytes("RGB", (width, height), bytes(rng.randrange(256) for _ in range(width * height * 3)))


@pytest.fixture()
def controller(tmp_path: Path, monkeypatch):
    # UU 主窗口（hwnd 42）：屏幕 (1000, 500) 起 600×400，(300, 200) 处是卡片；hwnd 7 是 40×40 的悬浮球。
    card = _noise(80, 40, seed=1)
    window = _noise(600, 400, seed=2)
    window.paste(card, (300, 200))
    template = tmp_path / "card.png"
    card.save(template)
    rects = {42: (1000, 500, 1600, 900), 7: (0, 0, 40, 40)}
    monkeypatch.setattr(desktop, "window_rect", lambda hwnd: rects.get(hwnd))
    monkeypatch.setattr(desktop, "capture_window", lambda hwnd: (window, *rects[hwnd][:2]))
    raised: list[int] = []
    monkeypatch.setattr(desktop, "activate_window", lambda hwnd, timeout: raised.append(hwnd))
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
    monkeypatch.setattr(instance, "uu_window_handles", lambda: [7, 42])
    monkeypatch.setattr(instance, "save_screenshot", lambda prefix: tmp_path / f"{prefix}.png")
    return SimpleNamespace(instance=instance, template=template, raised=raised)


def test_template_is_found_in_uu_window_and_small_windows_are_skipped(controller) -> None:
    # 1020：悬浮球这类比模板还小的窗口曾让 OpenCV 抛错、两个游戏一起崩。
    controller.instance.cover_at = lambda _point: ""

    # 卡片中心 = 窗口原点 + 贴图位置 + 半个卡片。
    assert controller.instance.try_locate_image(controller.template, timeout=0.05) == (1340, 720)


def test_hovered_look_is_found_through_a_dropped_in_variant_image(controller, monkeypatch) -> None:
    # 1020：鼠标停在卡片上时卡片变暗、出现“立即加速”，原模板认不出；
    # 把那次截图裁成 `<模板名>__hover.png` 放进模板目录，流程就认得。
    controller.instance.cover_at = lambda _point: ""
    hovered = _noise(80, 40, seed=9)
    window = _noise(600, 400, seed=2)
    window.paste(hovered, (100, 50))
    monkeypatch.setattr(desktop, "capture_window", lambda _hwnd: (window, 1000, 500))
    assert controller.instance.try_locate_image(controller.template, timeout=0.05) is None

    hovered.save(controller.template.with_name("card__hover.png"))

    assert controller.instance.try_locate_image(controller.template, timeout=0.05) == (1140, 570)


def test_covered_uu_is_brought_forward_and_reported_when_still_covered(controller) -> None:
    controller.instance.cover_at = lambda _point: "pid=7 process=chrome.exe title=抖音"

    with pytest.raises(UuStartupError, match="covered by pid=7 process=chrome.exe"):
        controller.instance.wait_for_image(controller.template, step_name="locate_card", timeout=0.05)
    assert controller.raised == [42]


def test_black_screen_capture_falls_back_to_print_window(monkeypatch) -> None:
    # 1017 内存耗尽后整屏截图全黑；这时才改用 PrintWindow 直接要窗口内容。
    content = _noise(20, 10, seed=3)
    monkeypatch.setattr(desktop, "window_rect", lambda _hwnd: (5, 6, 25, 16))
    monkeypatch.setattr(desktop.ImageGrab, "grab", lambda **_kwargs: Image.new("RGB", (20, 10)))
    monkeypatch.setattr(desktop, "_print_window", lambda _hwnd, _rect: content)

    assert desktop.capture_window(1) == (content, 5, 6)


def test_off_screen_uu_window_is_moved_to_the_primary_screen_center(monkeypatch) -> None:
    moves: list[tuple[int, int]] = []
    user32 = SimpleNamespace(SetWindowPos=lambda _h, _a, x, y, *_rest: moves.append((x, y)))
    monkeypatch.setattr(desktop, "_win32", lambda: (user32, None))
    monkeypatch.setattr(desktop.pyautogui, "size", lambda: SimpleNamespace(width=2560, height=1440))

    monkeypatch.setattr(desktop, "window_rect", lambda _hwnd: (-300, -100, 1000, 800))
    UuDesktopController.ensure_on_primary_screen(1)
    monkeypatch.setattr(desktop, "window_rect", lambda _hwnd: (600, 300, 1900, 1200))
    UuDesktopController.ensure_on_primary_screen(1)

    assert moves == [(630, 270)]
