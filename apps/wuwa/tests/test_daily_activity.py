from pathlib import Path
from types import SimpleNamespace

import wuwa_auto.okww.daily_activity as daily_activity_module
from wuwa_auto.okww.daily_activity import (
    DAILY_ACTIVITY_CLAIM_UNVERIFIED_MARKER,
    DAILY_ACTIVITY_CLAIM_VERIFIED_MARKER,
    DailyActivityVerifier,
    is_activity_complete,
    parse_activity_marker,
    parse_activity_panel_marker,
    parse_activity_values,
)
from wuwa_auto.okww.daily_capabilities import compare_activity_panel


def test_parse_fraction_and_completion_threshold() -> None:
    points, target, source = parse_activity_values(["100/100"])

    assert (points, target, source) == (100, 100, "fraction")
    assert is_activity_complete(points, target)


def test_parse_upstream_standalone_points() -> None:
    points, target, source = parse_activity_values(["120"])

    assert (points, target, source) == (120, None, "numerator")
    assert is_activity_complete(points, target)


def test_parse_latest_structured_marker() -> None:
    text = (
        f"DailyTask:{DAILY_ACTIVITY_CLAIM_UNVERIFIED_MARKER} "
        '{"points": 80, "reason": "below threshold"}\n'
        f"DailyTask:{DAILY_ACTIVITY_CLAIM_VERIFIED_MARKER} "
        '{"points": 120, "target": 100, "evidence_before": "before.png"}\n'
    )

    marker = parse_activity_marker(text)

    assert marker["state"] == "verified"
    assert marker["points"] == 120
    assert marker["evidence_before"] == "before.png"


class _FakeTask:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.messages: list[str] = []
        self.screenshots: list[str] = []

    def log_info(self, message: str) -> None:
        self.messages.append(message)

    def screenshot(self, name: str) -> None:
        self.screenshots.append(name)
        path = self.root / "screenshots" / f"20260808_{name}_original.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")

    def ocr(self, *_args: object, **_kwargs: object) -> list[object]:
        return [SimpleNamespace(name="100/100")]


def test_claim_button_uses_workflow_hid_and_capture_origin(monkeypatch) -> None:
    calls: list[tuple[int, int]] = []

    class Capture:
        def get_abs_cords(self, x: int, y: int) -> tuple[int, int]:
            return x + 17, y + 29

    class Task(_FakeTask):
        executor = SimpleNamespace(
            interaction=SimpleNamespace(capture=Capture()),
        )

        def sleep(self, _seconds: float) -> None:
            return None

    monkeypatch.setattr(
        daily_activity_module,
        "_virtual_hid_click",
        lambda x, y, **_kwargs: calls.append((x, y)),
    )
    verifier = DailyActivityVerifier(Task(Path(".")))
    verifier._click_claim_button(SimpleNamespace(center=lambda: (2255, 354)))

    assert calls == [(2272, 383)]


def test_milestone_reward_uses_upstream_bottom_point_via_hid(monkeypatch) -> None:
    calls: list[tuple[int, int]] = []

    class Capture:
        def get_abs_cords(self, x: int, y: int) -> tuple[int, int]:
            return x + 17, y + 29

    class Task(_FakeTask):
        executor = SimpleNamespace(
            interaction=SimpleNamespace(capture=Capture()),
        )

        def sleep(self, _seconds: float) -> None:
            return None

    monkeypatch.setattr(
        daily_activity_module,
        "_virtual_hid_click",
        lambda x, y, **_kwargs: calls.append((x, y)),
    )
    verifier = DailyActivityVerifier(Task(Path(".")))
    verifier.click_milestone_reward(after_sleep=0)

    assert calls == [(2398, 1299)]


def test_total_above_threshold_stays_unverified_while_claim_button_remains() -> None:
    verifier = DailyActivityVerifier(_FakeTask(Path(".")))
    verifier._activity_panel_confirmed = True

    observation = verifier.observe_after_claim(
        total_points=140,
        evidence_path="total.png",
        remaining_claim_buttons=True,
    )

    assert observation.complete is False
    assert observation.source == "post_claim_total_region_pending_buttons"


def test_total_requires_settled_milestones() -> None:
    verifier = DailyActivityVerifier(_FakeTask(Path(".")))
    verifier._activity_panel_confirmed = True

    pending = verifier.observe_after_claim(
        total_points=140,
        evidence_path="total.png",
        remaining_claim_buttons=False,
        milestones_pending=True,
    )
    settled = verifier.observe_after_claim(
        total_points=140,
        evidence_path="total.png",
        remaining_claim_buttons=False,
        milestones_pending=False,
    )
    unknown = verifier.observe_after_claim(
        total_points=140,
        evidence_path="total.png",
        remaining_claim_buttons=False,
    )

    assert pending.complete is False
    assert pending.source == "post_claim_total_region_pending_milestones"
    assert settled.complete is True
    assert unknown.complete is False
    assert unknown.source == "post_claim_total_region_milestones_unconfirmed"


def test_milestone_visual_probe_distinguishes_red_diamonds_and_checks(tmp_path: Path) -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (2560, 1440), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    for fraction in (0.406, 0.539, 0.672, 0.806, 0.939):
        x = round(2560 * fraction)
        y = round(1440 * 0.845)
        draw.rectangle((x - 8, y - 8, x + 8, y + 8), fill=(220, 40, 45))
    pending_path = tmp_path / "pending.png"
    image.save(pending_path)

    settled = Image.new("RGB", (2560, 1440), (0, 0, 0))
    settled_path = tmp_path / "settled.png"
    settled.save(settled_path)

    verifier = DailyActivityVerifier(_FakeTask(tmp_path))
    verifier._activity_panel_confirmed = True
    assert verifier.inspect_milestone_state(str(pending_path)) is True
    assert verifier.inspect_milestone_state(str(settled_path)) is False


def test_wrong_page_number_cannot_become_daily_total() -> None:
    class WrongPageTask(_FakeTask):
        def ocr(self, *_args: object, **_kwargs: object) -> list[object]:
            return [SimpleNamespace(name="450")]

    verifier = DailyActivityVerifier(WrongPageTask(Path(".")))
    verifier._activity_panel_confirmed = False

    total, _evidence, _source = verifier.capture_total_after_claim()
    assert total is None

    observation = verifier.observe_after_claim(
        total_points=450,
        evidence_path="material.png",
        remaining_claim_buttons=False,
        milestones_pending=False,
    )

    assert observation.complete is False
    assert observation.source == "post_claim_activity_panel_unconfirmed"


def test_verifier_observes_ocr_and_saves_evidence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    task = _FakeTask(tmp_path)
    verifier = DailyActivityVerifier(task, points_reader=lambda: 0)

    observation = verifier.wait_until_complete()
    before = verifier._capture("BEFORE_CLAIM")

    assert observation.complete
    assert observation.points == 100
    assert before is not None
    assert Path(before).is_file()


def test_panel_capability_comparison_keeps_supported_and_unsupported_tasks() -> None:
    labels = [
        "+20", "登录游戏", "1/1",
        "+10", "成功闪避或逆势回击1次", "1/1",
        "+40", "完成1次日常任务", "0/1",
        "+60", "累计消耗180点结晶波片", "0/180",
        "+20", "通关1次梦魇聚落或残象聚落", "0/1",
    ]

    comparison = compare_activity_panel(
        labels,
        log_text="TacetTask:used all stamina\nNightmareNestTask:farm echo walk find true",
    )

    states = {item["key"]: item for item in comparison["tasks"]}
    assert states["login"]["state"] == "completed"
    assert states["dodge-counter"]["state"] == "completed"
    assert states["daily-quest"]["state"] == "unsupported"
    assert states["waveplate"]["state"] == "unavailable"
    assert states["nightmare"]["state"] == "attempted_not_completed"
    assert comparison["reachable_now_points"] == 50
    assert comparison["can_reach_target_now"] is False


def test_unknown_panel_task_does_not_create_global_capability_gap() -> None:
    comparison = compare_activity_panel(
        ["+100", "完成1个危行任务", "0/1"],
    )

    assert comparison["tasks"][0]["capability"] == "unknown"
    assert comparison["unknown_tasks"] == ["unknown-1"]
    assert comparison["reachable_now_points"] is None
    assert comparison["can_reach_target_now"] is None


def test_parse_panel_marker_preserves_screenshot_and_raw_labels() -> None:
    text = (
        'DailyTask:HOST_DAILY_ACTIVITY_PANEL '
        '{"labels": ["+20", "登录游戏", "1/1"], '
        '"evidence_path": "D:/evidence/panel.png"}\n'
    )

    marker = parse_activity_panel_marker(text)

    assert marker["evidence_path"].endswith("panel.png")
    assert marker["labels"] == ["+20", "登录游戏", "1/1"]
    assert marker["comparison"]["tasks"][0]["key"] == "login"
