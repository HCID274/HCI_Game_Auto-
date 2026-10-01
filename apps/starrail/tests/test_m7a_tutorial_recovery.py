"""Regression tests for the narrow Star Rail tutorial recovery."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from starrail_auto.m7a.tutorial_recovery import (
    CONTENT_ROI,
    NEXT_CONFIDENCE,
    NEXT_ROI,
    NEXT_TEMPLATE,
    TITLE_CONFIDENCE,
    TITLE_ROI,
    TITLE_TEMPLATE,
    GameWindow,
    GluttonyTutorialRecovery,
    _match_in_roi,
    _normalized_frame,
    _read_template,
)

FIXTURE = Path(__file__).parent / "fixtures" / "gluttony_tutorial.png"
WINDOW = GameWindow(hwnd=77, left=0, top=0, width=1920, height=1080)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _recovery(
    images: list[Image.Image],
    clicks: list[tuple[int, int]],
    tmp_path: Path,
    monkeypatch: object,
) -> GluttonyTutorialRecovery:
    clock = FakeClock()
    remaining = iter(images)
    last = images[-1]

    def screenshotter(**_kwargs: object) -> Image.Image:
        nonlocal last
        last = next(remaining, last)
        return last.copy()

    monkeypatch.setattr(
        "starrail_auto.m7a.tutorial_recovery.EVIDENCE_DIR",
        tmp_path,
    )
    return GluttonyTutorialRecovery(
        window_getter=lambda: WINDOW,
        screenshotter=screenshotter,
        activator=lambda *_args, **_kwargs: True,
        clicker=lambda x, y: clicks.append((x, y)),
        clock=clock,
        sleeper=clock.sleep,
    )


def test_real_failure_fixture_matches_both_scoped_anchors() -> None:
    frame = _normalized_frame(Image.open(FIXTURE))

    title = _match_in_roi(
        frame,
        _read_template(TITLE_TEMPLATE),
        TITLE_ROI,
        TITLE_CONFIDENCE,
    )
    action = _match_in_roi(
        frame,
        _read_template(NEXT_TEMPLATE),
        NEXT_ROI,
        NEXT_CONFIDENCE,
    )

    assert title is not None
    assert action is not None
    assert title.score >= TITLE_CONFIDENCE
    assert action.score >= NEXT_CONFIDENCE


def test_missing_overlay_never_clicks(tmp_path: Path, monkeypatch: object) -> None:
    blank = Image.new("RGB", (1920, 1080), "black")
    clicks: list[tuple[int, int]] = []
    recovery = _recovery([blank], clicks, tmp_path, monkeypatch)

    assert recovery.poll() is False
    assert clicks == []


def test_unchanged_page_is_clicked_only_once(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    page = Image.open(FIXTURE).convert("RGB")
    clicks: list[tuple[int, int]] = []
    recovery = _recovery([page], clicks, tmp_path, monkeypatch)

    assert recovery.poll() is False
    assert recovery.poll() is False
    assert len(clicks) == 1


def test_changed_page_allows_one_more_click(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    page_one = Image.open(FIXTURE).convert("RGB")
    page_two = page_one.copy()
    draw = ImageDraw.Draw(page_two)
    left, top, right, bottom = CONTENT_ROI
    draw.rectangle((left, top, right, bottom), fill=(24, 80, 150))
    dismissed = Image.new("RGB", (1920, 1080), "black")
    clicks: list[tuple[int, int]] = []
    recovery = _recovery(
        [page_one, page_one, page_two, page_two, page_two, dismissed],
        clicks,
        tmp_path,
        monkeypatch,
    )

    assert recovery.poll() is True
    assert recovery.poll() is True
    assert len(clicks) == 2


def test_anchors_outside_their_rois_do_not_authorize_click(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    image = Image.new("RGB", (1920, 1080), "black")
    title = Image.open(TITLE_TEMPLATE).convert("RGB")
    action = Image.open(NEXT_TEMPLATE).convert("RGB")
    image.paste(title, (0, 0))
    image.paste(action, (300, 500))
    clicks: list[tuple[int, int]] = []
    recovery = _recovery([image], clicks, tmp_path, monkeypatch)

    assert recovery.poll() is False
    assert clicks == []


def test_page_signatures_are_meaningfully_different() -> None:
    page = _normalized_frame(Image.open(FIXTURE))
    changed = page.copy()
    left, top, right, bottom = CONTENT_ROI
    changed[top:bottom, left:right] = np.zeros_like(changed[top:bottom, left:right])

    assert not np.array_equal(page, changed)
