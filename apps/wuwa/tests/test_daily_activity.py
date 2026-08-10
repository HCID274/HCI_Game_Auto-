from pathlib import Path
from types import SimpleNamespace

import wuwa_auto.okww.daily_activity as daily_activity_module
from wuwa_auto.okww.daily_activity import (
    _TOTAL_OCR_REGIONS,
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


def test_milestone_reward_uses_selected_bottom_tier_via_hid(monkeypatch) -> None:
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
    verifier.click_milestone_reward(0, after_sleep=0)
    verifier.click_milestone_reward(4, after_sleep=0)

    assert calls == [(1033, 1299), (2398, 1299)]


def test_reward_popup_is_closed_before_post_claim_verification(monkeypatch) -> None:
    calls: list[tuple[int, int]] = []

    class Capture:
        def get_abs_cords(self, x: int, y: int) -> tuple[int, int]:
            return x, y

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
    verifier._last_panel_labels = ["获得", "x300", "点击空白区域关闭"]

    assert verifier.dismiss_reward_popup_if_visible(after_sleep=0) is True
    assert calls == [(1280, 1008)]

    verifier._last_panel_labels = ["活跃行迹", "活跃度", "30"]
    assert verifier.dismiss_reward_popup_if_visible(after_sleep=0) is False
    assert calls == [(1280, 1008)]


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


def test_total_ocr_uses_only_upstream_total_region_and_keeps_preclaim_points(
    monkeypatch,
) -> None:
    class TotalTask(_FakeTask):
        def __init__(self, root: Path) -> None:
            super().__init__(root)
            self.ocr_calls: list[tuple[object, ...]] = []

        def ocr(self, *args: object, **kwargs: object) -> list[object]:
            self.ocr_calls.append(args)
            if args[:4] == _TOTAL_OCR_REGIONS[0]:
                return [SimpleNamespace(name="30")]
            return [SimpleNamespace(name="活跃行迹"), SimpleNamespace(name="活跃度")]

    task = TotalTask(Path("."))
    verifier = DailyActivityVerifier(task)
    monkeypatch.setattr(verifier, "_capture", lambda _stage: None)

    verifier.capture_activity_panel(phase="before_claim")
    assert verifier.activity_panel_confirmed is True
    assert verifier._pre_claim_points == 30

    total, _evidence, _source = verifier.capture_total_after_claim()
    observation = verifier.observe_after_claim(
        total_points=total,
        evidence_path=None,
        remaining_claim_buttons=False,
        milestones_pending=False,
    )

    assert total == 30
    assert observation.points == 30
    assert observation.complete is False
    assert task.ocr_calls[-1] == _TOTAL_OCR_REGIONS[0]
    assert _TOTAL_OCR_REGIONS[0][2] - _TOTAL_OCR_REGIONS[0][0] <= 0.12


def test_panel_fallback_rejects_unrelated_large_number() -> None:
    verifier = DailyActivityVerifier(_FakeTask(Path(".")))
    verifier._activity_panel_confirmed = True
    verifier._last_panel_labels = ["活跃行迹", "活跃度", "素材获取", "450"]
    verifier._capture = lambda _stage: None  # type: ignore[method-assign]
    verifier.task.ocr = lambda *_args, **_kwargs: []

    total, _evidence, _source = verifier.capture_total_after_claim()

    assert total is None


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
    assert verifier.pending_milestone_indexes(str(pending_path)) == (0, 1, 2, 3, 4)
    assert verifier.inspect_milestone_state(str(settled_path)) is False
    assert verifier.pending_milestone_indexes(str(settled_path)) == ()


def test_milestone_probe_returns_only_the_claimable_tier(tmp_path: Path) -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (2560, 1440), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    first_x = round(2560 * 0.406)
    y = round(1440 * 0.845)
    draw.rectangle((first_x - 8, y - 8, first_x + 8, y + 8), fill=(220, 40, 45))
    evidence = tmp_path / "only-20-pending.png"
    image.save(evidence)

    verifier = DailyActivityVerifier(_FakeTask(tmp_path))
    verifier._activity_panel_confirmed = True

    assert verifier.pending_milestone_indexes(str(evidence)) == (0,)


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


def test_unconfirmed_post_claim_keeps_preclaim_points_for_diagnostics() -> None:
    verifier = DailyActivityVerifier(_FakeTask(Path(".")))
    verifier._pre_claim_points = 30
    verifier._activity_panel_confirmed = None

    observation = verifier.observe_after_claim(
        total_points=None,
        evidence_path="total.png",
        remaining_claim_buttons=False,
        milestones_pending=None,
    )

    assert observation.points == 30
    assert observation.complete is False
    assert observation.source == "post_claim_activity_panel_unconfirmed"


def test_verifier_observes_ocr_and_saves_evidence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    task = _FakeTask(tmp_path)
    # The upstream reader is the same exact bounded total-region OCR used by
    # the live claim path; a positive value is safe even before the title OCR
    # settles.
    verifier = DailyActivityVerifier(task, points_reader=lambda: 100)

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
    assert states["waveplate"]["state"] == "attempted_not_completed"
    assert "OCR 原值" in states["waveplate"]["reason"]
    assert states["nightmare"]["state"] == "attempted_not_completed"
    assert comparison["reachable_now_points"] is None
    assert comparison["can_reach_target_now"] is None


def test_stamina_capability_uses_raw_trace_instead_of_calling_under_60_zero() -> None:
    comparison = compare_activity_panel(
        ["+60", "累计消耗180点结晶波片", "0/180"],
        log_text=(
            'TacetTask:HOST_OKWW_DAILY_TRACE '
            '{"event":"stamina_end","current_stamina":24,'
            '"back_up_stamina":17,"total_stamina":41,"result":[24,17,41]}\n'
            "TacetTask:used all stamina\n"
        ),
    )

    task = comparison["tasks"][0]
    assert task["state"] == "unavailable"
    assert "41" in task["reason"]
    assert "为0" not in task["reason"]
    assert comparison["stamina_observation"]["total_stamina"] == 41


def test_stamina_ocr_failure_sentinel_is_not_reported_as_zero() -> None:
    comparison = compare_activity_panel(
        ["+60", "累计消耗180点结晶波片", "0/180"],
        log_text=(
            'TacetTask:HOST_OKWW_DAILY_TRACE '
            '{"event":"stamina_end","current_stamina":-1,'
            '"back_up_stamina":-1,"total_stamina":-1,'
            '"ocr_available":false,"result":[-1,-1,-1]}\n'
            "TacetTask:used all stamina\n"
        ),
    )

    task = comparison["tasks"][0]
    assert task["state"] == "attempted_not_completed"
    assert "不能判定为0" in task["reason"]


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
