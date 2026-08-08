"""Host-owned post-claim verification of the OK-WW daily activity reward.

The upstream task performs the work and clicks the reward control once.  The
host layer records the panel before the click and verifies the bottom total
after the click; it never edits the installed OK-WW files.
"""

# OK-WW's bundled OCR/screenshot APIs expose several third-party exception
# types; every boundary below records the error and keeps the evidence path
# alive rather than allowing one optional probe to abort the workflow.
# ruff: noqa: BLE001

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from wuwa_auto.okww.daily_capabilities import compare_activity_panel

try:
    # The workflow-owned control server is shared by all bundled OK-WW
    # workers.  Reuse its already-tested protocol instead of emitting another
    # PostMessage click which the game may ignore on this page.
    from wuwa_auto.okww.confirmed_retry_worker import _virtual_hid_click
except ImportError:  # pragma: no cover - direct bundled-worker execution
    _virtual_hid_click = None  # type: ignore[assignment]

DAILY_ACTIVITY_CLAIM_VERIFIED_MARKER = (
    "HOST_DAILY_ACTIVITY_CLAIM_VERIFIED"
)
DAILY_ACTIVITY_CLAIM_UNVERIFIED_MARKER = (
    "HOST_DAILY_ACTIVITY_CLAIM_UNVERIFIED"
)
DAILY_ACTIVITY_BEFORE_CLAIM_MARKER = "HOST_DAILY_ACTIVITY_BEFORE_CLAIM"
DAILY_ACTIVITY_AFTER_CLAIM_MARKER = "HOST_DAILY_ACTIVITY_AFTER_CLAIM"
DAILY_ACTIVITY_PANEL_MARKER = "HOST_DAILY_ACTIVITY_PANEL"
DAILY_ACTIVITY_COMPLETION_THRESHOLD = 100

_FRACTION_RE = re.compile(r"(?<!\d)(?P<current>\d{1,4})\s*/\s*(?P<target>\d{1,4})(?!\d)")
_NUMBER_RE = re.compile(r"(?<!\d)(?P<value>\d{1,4})(?!\d)")

# The first region is the one used by OK-WW's DailyTask.get_total_daily_points.
# The second one tolerates a small UI/layout shift without scanning unrelated
# parts of the game HUD.  Coordinates are normalized to the captured game
# frame, not absolute desktop pixels.
_OCR_REGIONS: tuple[tuple[float, float, float, float], ...] = (
    (0.19, 0.80, 0.30, 0.93),
    (0.10, 0.70, 0.42, 0.98),
)
_PANEL_OCR_REGION = (0.04, 0.04, 0.78, 0.98)
# This region is intentionally much narrower than the task-list OCR.  The
# total is read only after the claim click, so fractions such as ``15/15`` in
# individual rows cannot be mistaken for the account total.
_TOTAL_OCR_REGIONS: tuple[tuple[float, float, float, float], ...] = (
    # OK-WW's own region, tightened to the yellow total number.
    (0.19, 0.80, 0.30, 0.94),
    (0.12, 0.74, 0.22, 0.95),
)
# The game can display more than the 100-point reward threshold (for example
# 140 after all five completed rows are settled).  Keep the lower milestone
# values as well because OCR may return the whole bottom strip.
_TOTAL_VALUE_RE = re.compile(r"(?<!\d)(?:0|20|40|60|80|100|[1-9]\d{2,3})(?!\d)")
_CLAIM_TEXT_RE = re.compile(r"领取|領取|Claim", re.IGNORECASE)
_CLAIM_REGION = (0.68, 0.04, 0.99, 0.86)
_ACTIVITY_TITLE_RE = re.compile(r"活跃行迹|Activity\s*Trail", re.IGNORECASE)
_ACTIVITY_METRIC_RE = re.compile(
    r"活跃度|daily\s*activity|activity\s*points",
    re.IGNORECASE,
)
_CLAIM_HID_MARKER = "HOST_DAILY_ACTIVITY_VIRTUAL_HID_CLICK"
_MILESTONE_HID_MARKER = "HOST_DAILY_ACTIVITY_MILESTONE_VIRTUAL_HID_CLICK"
_MILESTONE_STATE_MARKER = "HOST_DAILY_ACTIVITY_MILESTONE_STATE"
# OK-WW's upstream DailyTask uses this normalized point for the bottom
# 100-point milestone.  It is a real reward control, not the row-level
# ``领取`` buttons.  Keep the value host-owned so an upstream replacement can
# be adopted without editing its installed files.
_MILESTONE_REWARD_POINT = (0.930, 0.882)
# In the 2560x1440 game frame the five red pending diamonds are centred near
# these normalized x positions and y=1216/1440.  A red diamond becomes a gray
# checkmark after the reward is actually settled.
_MILESTONE_X = (0.406, 0.539, 0.672, 0.806, 0.939)
_MILESTONE_Y = 0.845
_MILESTONE_RED_PIXEL_THRESHOLD = 20
_SCREENSHOT_TIMEOUT_SECONDS = 3.0
_OBSERVATION_TIMEOUT_SECONDS = 12.0
_OBSERVATION_POLL_SECONDS = 0.25


class DailyActivityVerificationError(RuntimeError):
    """Raised when the daily reward cannot be proven from the current UI."""


@dataclass(frozen=True)
class DailyActivityObservation:
    points: int | None
    target: int
    complete: bool
    evidence_path: str | None = None
    source: str = "unknown"
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _box_names(boxes: Any) -> list[str]:
    if boxes is None:
        return []
    if not isinstance(boxes, Iterable) or isinstance(boxes, (str, bytes)):
        boxes = [boxes]
    names: list[str] = []
    for box in boxes:
        name = getattr(box, "name", box)
        if name is not None:
            names.append(str(name))
    return names


def parse_activity_values(names: Iterable[str]) -> tuple[int | None, int | None, str]:
    """Extract a likely activity value from OCR names.

    A visible fraction is preferred.  Some OK-WW versions expose only the
    numerator (for example ``120``), so a standalone value is retained and
    uses the game's 100-point completion threshold as its implicit target.
    """

    values = [str(name) for name in names]
    for value in values:
        match = _FRACTION_RE.search(value)
        if match:
            return (
                int(match.group("current")),
                int(match.group("target")),
                "fraction",
            )

    standalone: list[int] = []
    for value in values:
        standalone.extend(int(match.group("value")) for match in _NUMBER_RE.finditer(value))
    if standalone:
        # The activity panel's total is normally the largest number in the
        # narrow OCR region; retaining the maximum avoids selecting a tiny
        # task index when the UI renders several labels together.
        return max(standalone), None, "numerator"
    return None, None, "unknown"


def is_activity_complete(points: int | None, target: int | None = None) -> bool:
    if points is None:
        return False
    required = max(
        DAILY_ACTIVITY_COMPLETION_THRESHOLD,
        int(target or DAILY_ACTIVITY_COMPLETION_THRESHOLD),
    )
    return points >= required


def parse_activity_marker(text: str) -> dict[str, Any]:
    """Read the latest structured host marker from one current-run log slice."""

    latest: dict[str, Any] = {}
    for line in text.splitlines():
        if DAILY_ACTIVITY_CLAIM_VERIFIED_MARKER in line:
            marker = DAILY_ACTIVITY_CLAIM_VERIFIED_MARKER
            state = "verified"
        elif DAILY_ACTIVITY_CLAIM_UNVERIFIED_MARKER in line:
            marker = DAILY_ACTIVITY_CLAIM_UNVERIFIED_MARKER
            state = "unverified"
        else:
            continue
        payload = line.split(marker, 1)[1].strip()
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            data = {"reason": payload}
        if not isinstance(data, dict):
            data = {"reason": str(data)}
        latest = {"state": state, **data}
    return latest


def parse_activity_panel_marker(text: str) -> dict[str, Any]:
    """Read the latest OCR panel marker and attach the capability matrix."""

    latest: dict[str, Any] = {}
    for line in text.splitlines():
        if DAILY_ACTIVITY_PANEL_MARKER not in line:
            continue
        payload = line.split(DAILY_ACTIVITY_PANEL_MARKER, 1)[1].strip()
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            data = {"labels": [], "evidence_path": None, "reason": payload}
        if not isinstance(data, dict):
            data = {"labels": [], "evidence_path": None, "reason": str(data)}
        labels = data.get("labels")
        if not isinstance(labels, list):
            labels = []
        latest = {
            **data,
            "labels": [str(label) for label in labels],
            "comparison": compare_activity_panel([str(label) for label in labels]),
        }
    return latest


class DailyActivityVerifier:
    """Read, capture and report the daily activity state through OK's task API."""

    def __init__(
        self,
        task: Any,
        *,
        points_reader: Callable[[], Any] | None = None,
    ) -> None:
        self.task = task
        self.points_reader = points_reader
        self._last_panel_labels: list[str] = []
        self._last_panel_comparison: dict[str, Any] = {}
        self._activity_panel_confirmed: bool | None = None

    @property
    def screenshot_dir(self) -> Path:
        return Path.cwd() / "screenshots"

    def _capture(self, stage: str) -> str | None:
        prefix = f"HOST_DAILY_ACTIVITY_{stage}"
        before = {
            path.resolve()
            for path in self.screenshot_dir.glob(f"*_{prefix}_original.png")
        }
        try:
            self.task.screenshot(prefix)
        except Exception as exc:
            self._log(f"daily activity screenshot failed stage={stage}: {exc}")
            return None

        deadline = time.monotonic() + _SCREENSHOT_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            candidates = [
                path
                for path in self.screenshot_dir.glob(f"*_{prefix}_original.png")
                if path.resolve() not in before
            ]
            if candidates:
                return str(max(candidates, key=lambda path: path.stat().st_mtime).resolve())
            time.sleep(0.05)
        # The headless OK worker may not start its Qt screenshot consumer, so
        # the signal emitted by ``task.screenshot`` can be dropped.  Keep the
        # evidence boundary host-owned and fall back to the same desktop
        # capture used by the runner.  This does not alter OK-WW files or its
        # screenshot configuration.
        try:
            # The bundled OK-WW interpreter does not inherit the host package
            # dependencies, so importing the host desktop controller here is
            # not reliable.  Pillow is already part of OK-WW's runtime and
            # gives us a host-independent full-screen evidence fallback.
            from PIL import ImageGrab

            evidence_dir = Path(__file__).resolve().parents[3] / "runtime" / "evidence"
            evidence_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
            path = evidence_dir / f"ok_daily_activity_{stage.lower()}_{timestamp}.png"
            ImageGrab.grab(all_screens=True).save(path)
            return str(path.resolve())
        except Exception as exc:
            self._log(f"daily activity fallback screenshot failed stage={stage}: {exc}")
            return None

    def _log(self, message: str) -> None:
        log_info = getattr(self.task, "log_info", None)
        if callable(log_info):
            log_info(message)

    def _crop_total_evidence(self, source_path: str | None) -> str | None:
        """Save a narrow bottom-total crop without altering the source proof."""

        if not source_path:
            return None
        try:
            from PIL import Image

            source = Path(source_path)
            with Image.open(source) as image:
                width, height = image.size
                # Pillow fallback captures the complete multi-monitor desktop;
                # OK's own screenshot normally contains only the game frame.
                if width >= 3000:
                    box = (
                        int(width * 0.02),
                        int(height * 0.40),
                        int(width * 0.70),
                        int(height * 0.62),
                    )
                else:
                    box = (
                        int(width * 0.02),
                        int(height * 0.72),
                        int(width * 0.70),
                        int(height * 0.995),
                    )
                cropped = image.crop(box)
                evidence_dir = Path(__file__).resolve().parents[3] / "runtime" / "evidence"
                evidence_dir.mkdir(parents=True, exist_ok=True)
                timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
                target = evidence_dir / f"ok_daily_activity_total_{timestamp}.png"
                cropped.save(target)
                return str(target.resolve())
        except Exception as exc:
            self._log(f"daily activity total crop failed: {exc}")
            return None

    def _read_ocr(self, *, match: re.Pattern[str]) -> list[str]:
        names: list[str] = []
        ocr = getattr(self.task, "ocr", None)
        if not callable(ocr):
            return names
        for region in _OCR_REGIONS:
            try:
                names.extend(_box_names(ocr(*region, match=match)))
            except Exception as exc:
                self._log(f"daily activity OCR region failed {region}: {exc}")
        return names

    def capture_activity_panel(self) -> tuple[list[str], str | None]:
        """Capture the visible daily panel and retain its OCR task labels."""

        names: list[str] = []
        ocr = getattr(self.task, "ocr", None)
        if callable(ocr):
            try:
                names = _box_names(ocr(*_PANEL_OCR_REGION, match=None, log=False))
            except Exception as exc:
                self._log(f"daily activity panel OCR failed: {exc}")
        evidence_path = self._capture("ACTIVITY_PANEL")
        comparison = compare_activity_panel(names)
        self._last_panel_labels = names
        self._last_panel_comparison = comparison
        joined_labels = " ".join(names)
        self._activity_panel_confirmed = bool(
            _ACTIVITY_TITLE_RE.search(joined_labels)
            and _ACTIVITY_METRIC_RE.search(joined_labels)
        )
        self._log(
            f"{DAILY_ACTIVITY_PANEL_MARKER} "
            f"{json.dumps({'labels': names, 'evidence_path': evidence_path, 'comparison': comparison, 'active_panel_confirmed': self._activity_panel_confirmed}, ensure_ascii=False)}"
        )
        return names, evidence_path

    @property
    def activity_panel_confirmed(self) -> bool | None:
        """Whether the last OCR capture proves the active-activity page."""

        return self._activity_panel_confirmed

    def capture_total_after_claim(self) -> tuple[int | None, str | None, str | None]:
        """Capture/OCR only the post-claim total region.

        The full screenshot remains available as an audit source; the returned
        crop is the compact evidence used in the final report.
        """

        source = self._capture("TOTAL_AFTER_CLAIM")
        evidence = self._crop_total_evidence(source)
        values: list[int] = []
        ocr = getattr(self.task, "ocr", None)
        if callable(ocr) and self._activity_panel_confirmed is True:
            for region in _TOTAL_OCR_REGIONS:
                try:
                    names = _box_names(ocr(*region, match=_TOTAL_VALUE_RE, log=False))
                    for name in names:
                        match = _TOTAL_VALUE_RE.search(name)
                        if match:
                            values.append(int(match.group(0)))
                except Exception as exc:
                    self._log(f"daily activity total OCR region failed {region}: {exc}")

        total: int | None
        totals = [value for value in values if value >= DAILY_ACTIVITY_COMPLETION_THRESHOLD]
        if not totals and self._activity_panel_confirmed is True:
            # OCR of the narrow crop can miss the large yellow number while
            # the panel OCR has already captured it as a standalone label
            # (e.g. ``140``).  Ignore ``+100`` point labels and fractions so
            # this fallback cannot promote an unclaimed row to completion.
            panel_values = [
                int(label)
                for label in self._last_panel_labels
                if re.fullmatch(r"\d{3,4}", label)
            ]
            if panel_values:
                values.extend(panel_values)
                totals = panel_values
        if totals:
            total = max(totals)
        elif values and 0 in values:
            total = 0
        else:
            total = None
        if self._activity_panel_confirmed is not True:
            total = None
        self._log(
            "HOST_DAILY_ACTIVITY_TOTAL_REGION "
            f"{json.dumps({'points': total, 'ocr_values': values, 'evidence_path': evidence, 'active_panel_confirmed': self._activity_panel_confirmed}, ensure_ascii=False)}"
        )
        return total, evidence, source

    def _find_claim_button(self) -> Any | None:
        """Find a visible activity-row claim control without fixed coordinates."""
        # Do not use the generic ``boss_proceed`` template here.  On the
        # already-claimed panel it can match an unrelated “前往”/resource
        # control and navigate away from 活跃行迹.  The localized OCR label is
        # the only accepted claim affordance.
        ocr = getattr(self.task, "ocr", None)
        if callable(ocr):
            try:
                boxes = ocr(*_CLAIM_REGION, match=_CLAIM_TEXT_RE, log=False)
                if boxes:
                    return boxes[0]
            except Exception as exc:
                self._log(f"daily activity claim OCR search failed: {exc}")
        return None

    def _frame_dimensions(self) -> tuple[int, int]:
        """Return the captured game-frame size used by normalized OK points."""

        for owner in (
            self.task,
            getattr(getattr(getattr(self.task, "executor", None), "method", None), "__self__", None),
            getattr(
                getattr(getattr(self.task, "executor", None), "interaction", None),
                "capture",
                None,
            ),
        ):
            if owner is None:
                continue
            try:
                width = int(getattr(owner, "width", 0) or 0)
                height = int(getattr(owner, "height", 0) or 0)
            except (TypeError, ValueError):
                continue
            if width > 0 and height > 0:
                return width, height
        # Wuthering Waves' OK-WW layout is authored for this frame.  This is
        # only a test/direct-call fallback; the live task exposes width/height.
        return 2560, 1440

    def _hid_click_frame_point(
        self,
        frame_x: int,
        frame_y: int,
        *,
        marker: str,
        kind: str,
        after_sleep: float = 1.0,
    ) -> None:
        """Emit one local HID click after converting game-frame coordinates."""

        if _virtual_hid_click is None:
            raise RuntimeError("host virtual HID control is unavailable for daily activity claim")
        capture = getattr(
            getattr(getattr(self.task, "executor", None), "interaction", None),
            "capture",
            None,
        )
        get_abs_cords = getattr(capture, "get_abs_cords", None)
        if not callable(get_abs_cords):
            raise TypeError("OK-WW capture origin is unavailable for daily activity HID click")
        absolute_x, absolute_y = get_abs_cords(int(frame_x), int(frame_y))
        _virtual_hid_click(
            int(absolute_x),
            int(absolute_y),
            hold=0.2,
            log_action=True,
        )
        self._log(
            f"{marker} "
            f"{json.dumps({'kind': kind, 'frame_point': [int(frame_x), int(frame_y)], 'absolute_point': [int(absolute_x), int(absolute_y)]}, ensure_ascii=False)}"
        )
        if after_sleep > 0:
            sleeper = getattr(self.task, "sleep", None)
            if callable(sleeper):
                sleeper(after_sleep)
            else:
                time.sleep(after_sleep)

    def _milestone_pending_from_evidence(self, source_path: str | None) -> bool | None:
        """Detect red, claimable milestone diamonds in a captured panel.

        The total can already read 140 while the five milestone rewards are
        still red/claimable.  OCR cannot distinguish that state, so this
        small host-owned visual probe is deliberately limited to the known
        milestone diamonds and never clicks on an unknown screen.
        """

        if self._activity_panel_confirmed is not True:
            self._log(
                f"{_MILESTONE_STATE_MARKER} "
                f"{json.dumps({'pending': None, 'reason': 'active activity panel unconfirmed'}, ensure_ascii=False)}"
            )
            return None
        if not source_path:
            self._log(
                f"{_MILESTONE_STATE_MARKER} "
                f"{json.dumps({'pending': None, 'reason': 'evidence unavailable'}, ensure_ascii=False)}"
            )
            return None
        try:
            from PIL import Image

            with Image.open(source_path) as image:
                rgb = image.convert("RGB")
                image_width, image_height = rgb.size
                # Host fallback screenshots include the whole desktop, with
                # the game frame at the top-left.  OK task screenshots are
                # already the game frame.
                frame_width, frame_height = self._frame_dimensions()
                if image_width < 3000:
                    frame_width, frame_height = image_width, image_height
                else:
                    frame_width = min(frame_width, image_width)
                    frame_height = min(frame_height, image_height)

                red_counts: list[int] = []
                for fraction in _MILESTONE_X:
                    center_x = round(frame_width * fraction)
                    center_y = round(frame_height * _MILESTONE_Y)
                    left = max(0, center_x - int(frame_width * 0.014))
                    top = max(0, center_y - int(frame_height * 0.020))
                    right = min(image_width, center_x + int(frame_width * 0.014) + 1)
                    bottom = min(image_height, center_y + int(frame_height * 0.020) + 1)
                    count = 0
                    crop = rgb.crop((left, top, right, bottom))
                    for pixel_y in range(crop.height):
                        for pixel_x in range(crop.width):
                            red, green, blue = crop.getpixel((pixel_x, pixel_y))
                            if red > 150 and red > green * 1.5 and red > blue * 1.15:
                                count += 1
                    red_counts.append(count)
                pending = any(count >= _MILESTONE_RED_PIXEL_THRESHOLD for count in red_counts)
                self._log(
                    f"{_MILESTONE_STATE_MARKER} "
                    f"{json.dumps({'pending': pending, 'red_counts': red_counts, 'evidence_path': str(Path(source_path).resolve())}, ensure_ascii=False)}"
                )
                return pending
        except Exception as exc:
            self._log(
                f"{_MILESTONE_STATE_MARKER} "
                f"{json.dumps({'pending': None, 'reason': str(exc), 'evidence_path': source_path}, ensure_ascii=False)}"
            )
            return None

    def inspect_milestone_state(self, source_path: str | None) -> bool | None:
        """Expose the bounded milestone probe for the workflow wrapper/tests."""

        return self._milestone_pending_from_evidence(source_path)

    def click_milestone_reward(self, *, after_sleep: float = 1.0) -> None:
        """Click OK-WW's actual bottom milestone reward through local HID."""

        width, height = self._frame_dimensions()
        frame_x = round(width * _MILESTONE_REWARD_POINT[0])
        frame_y = round(height * _MILESTONE_REWARD_POINT[1])
        self._hid_click_frame_point(
            frame_x,
            frame_y,
            marker=_MILESTONE_HID_MARKER,
            kind="daily_activity_milestone_reward",
            after_sleep=after_sleep,
        )

    def _click_claim_button(self, button: Any, *, after_sleep: float = 1.0) -> None:
        """Click a detected reward button through the real workflow HID.

        OK-WW's configured ``PostMessage`` interaction is useful for many
        screens but is ignored by the current activity panel.  The host owns
        a local virtual USB mouse for exactly this case.  OCR/feature boxes
        are in the captured game frame, so convert them through OK-WW's
        capture origin before sending the HID request.
        """

        center = getattr(button, "center", None)
        point = center() if callable(center) else (
            getattr(button, "x", None),
            getattr(button, "y", None),
        )
        if not (
            isinstance(point, tuple)
            and len(point) == 2
            and isinstance(point[0], (int, float))
            and isinstance(point[1], (int, float))
        ):
            raise RuntimeError(f"daily activity claim button has no usable center: {point!r}")
        frame_x, frame_y = int(point[0]), int(point[1])

        self._hid_click_frame_point(
            frame_x,
            frame_y,
            marker=_CLAIM_HID_MARKER,
            kind="daily_activity_row_claim",
            after_sleep=after_sleep,
        )

    def _panel_has_pending_claim_buttons(self) -> bool:
        """Determine pending rewards from the freshly captured panel labels."""

        return any(_CLAIM_TEXT_RE.search(label) for label in self._last_panel_labels)

    def click_visible_claim_buttons(self, *, max_clicks: int = 1) -> tuple[int, bool | None]:
        """Click actual visible row controls, bounded and evidence-logged.

        The installed OK-WW version uses a stale fixed coordinate for this
        page.  We retain that upstream call for compatibility, then use the
        local visual control only when a row still exposes ``领取``.  Usually
        one click batches all rows; if the game presents rows individually,
        the bounded loop finishes the same set without blind screen clicks.
        """

        clicks = 0
        for attempt in range(1, max_clicks + 1):
            button = self._find_claim_button()
            if not button:
                break
            name = getattr(button, "name", "claim")
            center = getattr(button, "center", None)
            point = center() if callable(center) else (getattr(button, "x", None), getattr(button, "y", None))
            self._log(
                "HOST_DAILY_ACTIVITY_CLAIM_BUTTON "
                f"{json.dumps({'attempt': attempt, 'name': str(name), 'point': str(point)}, ensure_ascii=False)}"
            )
            self._click_claim_button(button, after_sleep=1)
            clicks += 1
        # Do not query the feature matcher immediately: after a real HID click
        # OK-WW may still expose the previous frame for a moment.  The caller
        # captures a fresh panel and derives the authoritative pending state
        # from those labels.
        remaining: bool | None = None if clicks else self._find_claim_button() is not None
        self._log(
            "HOST_DAILY_ACTIVITY_CLAIM_BUTTON_SUMMARY "
            f"{json.dumps({'clicks': clicks, 'remaining': remaining}, ensure_ascii=False)}"
        )
        return clicks, remaining

    def observe(self, *, points_hint: int | None = None) -> DailyActivityObservation:
        fraction_names = self._read_ocr(match=_FRACTION_RE)
        points, target, source = parse_activity_values(fraction_names)
        if points is None:
            number_names = self._read_ocr(match=_NUMBER_RE)
            points, target, source = parse_activity_values(number_names)

        if points_hint is not None and points_hint > 0 and (
            points is None or points_hint > points
        ):
            points = points_hint
            source = "upstream_points"
        effective_target = max(
            DAILY_ACTIVITY_COMPLETION_THRESHOLD,
            int(target or DAILY_ACTIVITY_COMPLETION_THRESHOLD),
        )
        return DailyActivityObservation(
            points=points,
            target=effective_target,
            complete=is_activity_complete(points, effective_target),
            source=source,
            reason=("activity threshold reached" if points is not None and is_activity_complete(points, effective_target)
                    else "activity value not confirmed"),
        )

    def observe_after_claim(
        self,
        *,
        total_points: int | None,
        evidence_path: str | None,
        remaining_claim_buttons: bool | None = None,
        milestones_pending: bool | None = None,
    ) -> DailyActivityObservation:
        """Use post-click total evidence, then the completed task panel as fallback."""

        if self._activity_panel_confirmed is not True:
            return DailyActivityObservation(
                points=total_points,
                target=DAILY_ACTIVITY_COMPLETION_THRESHOLD,
                complete=False,
                evidence_path=evidence_path,
                source="post_claim_activity_panel_unconfirmed",
                reason="活跃行迹/活跃度面板未确认，拒绝把其他页面数字当作总分",
            )

        if total_points is not None:
            if remaining_claim_buttons is True:
                return DailyActivityObservation(
                    points=total_points,
                    target=DAILY_ACTIVITY_COMPLETION_THRESHOLD,
                    complete=False,
                    evidence_path=evidence_path,
                    source="post_claim_total_region_pending_buttons",
                    reason="post-claim panel still exposes a领取 button",
                )
            if milestones_pending is not False:
                return DailyActivityObservation(
                    points=total_points,
                    target=DAILY_ACTIVITY_COMPLETION_THRESHOLD,
                    complete=False,
                    evidence_path=evidence_path,
                    source=(
                        "post_claim_total_region_pending_milestones"
                        if milestones_pending is True
                        else "post_claim_total_region_milestones_unconfirmed"
                    ),
                    reason=(
                        "post-claim panel still exposes red milestone rewards"
                        if milestones_pending is True
                        else "post-claim milestone reward state was not confirmed"
                    ),
                )
            return DailyActivityObservation(
                points=total_points,
                target=DAILY_ACTIVITY_COMPLETION_THRESHOLD,
                complete=is_activity_complete(total_points),
                evidence_path=evidence_path,
                source="post_claim_total_region",
                reason=("activity threshold reached" if is_activity_complete(total_points)
                        else "post-claim total below threshold"),
            )

        comparison = self._last_panel_comparison
        task_points = comparison.get("current_points_from_tasks")
        if isinstance(task_points, int) and remaining_claim_buttons is False:
            capped = min(DAILY_ACTIVITY_COMPLETION_THRESHOLD, task_points)
            return DailyActivityObservation(
                points=capped,
                target=DAILY_ACTIVITY_COMPLETION_THRESHOLD,
                complete=capped >= DAILY_ACTIVITY_COMPLETION_THRESHOLD,
                evidence_path=evidence_path,
                source="post_claim_panel_tasks",
                reason=("activity threshold reached" if capped >= DAILY_ACTIVITY_COMPLETION_THRESHOLD
                        else "post-claim completed task points below threshold"),
            )
        if isinstance(task_points, int) and remaining_claim_buttons is True:
            capped = min(DAILY_ACTIVITY_COMPLETION_THRESHOLD, task_points)
            return DailyActivityObservation(
                points=capped,
                target=DAILY_ACTIVITY_COMPLETION_THRESHOLD,
                complete=False,
                evidence_path=evidence_path,
                source="post_claim_panel_tasks_pending_buttons",
                reason="post-claim panel still exposes a领取 button",
            )
        if milestones_pending is not False:
            observation = self.observe()
            return DailyActivityObservation(
                **{
                    **observation.to_dict(),
                    "complete": False,
                    "evidence_path": evidence_path,
                    "source": (
                        "post_claim_panel_milestones_unconfirmed"
                        if milestones_pending is None
                        else "post_claim_panel_pending_milestones"
                    ),
                    "reason": (
                        "post-claim panel still exposes red milestone rewards"
                        if milestones_pending is True
                        else "post-claim milestone reward state was not confirmed"
                    ),
                }
            )
        observation = self.observe()
        return DailyActivityObservation(
            **{
                **observation.to_dict(),
                "evidence_path": evidence_path,
                "source": "post_claim_ocr_fallback",
            }
        )

    def wait_until_complete(self) -> DailyActivityObservation:
        deadline = time.monotonic() + _OBSERVATION_TIMEOUT_SECONDS
        last = DailyActivityObservation(
            points=None,
            target=DAILY_ACTIVITY_COMPLETION_THRESHOLD,
            complete=False,
            reason="activity value not observed",
        )
        while time.monotonic() < deadline:
            hint: int | None = None
            if self.points_reader is not None:
                try:
                    value = self.points_reader()
                    hint = int(value) if value is not None else None
                except Exception as exc:
                    self._log(f"daily activity upstream points read failed: {exc}")
            last = self.observe(points_hint=hint)
            if last.complete:
                return last
            time.sleep(_OBSERVATION_POLL_SECONDS)
        return last

    def log_unverified(
        self,
        observation: DailyActivityObservation,
        *,
        reason: str,
        evidence_path: str | None,
    ) -> None:
        payload = {
            **observation.to_dict(),
            "reason": reason,
            "evidence_path": evidence_path,
        }
        self._log(f"{DAILY_ACTIVITY_CLAIM_UNVERIFIED_MARKER} {json.dumps(payload, ensure_ascii=False)}")

    def verify_claim_transition(
        self,
        observation: DailyActivityObservation,
        *,
        evidence_before: str | None,
        evidence_after: str | None,
        milestones_pending: bool | None,
    ) -> None:
        payload = {
            **observation.to_dict(),
            "state": "verified",
            "evidence_before": evidence_before,
            "evidence_after": evidence_after,
            "milestones_pending": milestones_pending,
            "transition": "returned_to_world",
        }
        self._log(
            f"{DAILY_ACTIVITY_CLAIM_VERIFIED_MARKER} "
            f"{json.dumps(payload, ensure_ascii=False)}"
        )


def install_daily_activity_override(task_class: type[Any]) -> None:
    """Wrap DailyTask.claim_daily while retaining the upstream implementation."""

    claim_daily = getattr(task_class, "claim_daily", None)
    open_book = getattr(task_class, "openF2Book", None)
    get_points = getattr(task_class, "get_total_daily_points", None)
    if not all(callable(method) for method in (claim_daily, open_book, get_points)):
        raise RuntimeError(
            "OK-WW DailyTask is incompatible: daily activity methods missing"
        )

    def host_claim_daily(self: Any) -> Any:
        verifier = DailyActivityVerifier(
            self,
            points_reader=lambda: get_points(self),
        )
        # Record the task list before the host-owned clicks for audit purposes.
        # The installed upstream method uses PostMessage and a fixed bottom
        # coordinate.  On this panel that input can be ignored, so the
        # replaceable host layer performs both localized row and milestone
        # clicks through the workflow's real virtual HID device.
        self.info_set("current task", "claim daily")
        open_book(self, "gray_book_quest")
        self.click(0.17, 0.12, after_sleep=1)
        _panel_labels, panel_evidence = verifier.capture_activity_panel()
        evidence_before = verifier._capture("BEFORE_CLAIM")
        pending_before = bool(
            verifier.activity_panel_confirmed is True
            and (
                verifier._panel_has_pending_claim_buttons()
                or verifier._find_claim_button() is not None
            )
        )
        if verifier.activity_panel_confirmed is not True:
            verifier._log(
                "HOST_DAILY_ACTIVITY_PANEL_UNCONFIRMED "
                f"{json.dumps({'source': 'pre_claim_ocr'}, ensure_ascii=False)}"
            )
        milestone_evidence = panel_evidence or evidence_before
        row_clicks = 0
        milestone_clicks = 0
        try:
            if pending_before:
                row_clicks, _ = verifier.click_visible_claim_buttons(max_clicks=1)
                if row_clicks:
                    # Re-read after the row claim.  The total often changes to
                    # 140 at this point and the red milestone diamonds become
                    # available only in the new frame.
                    _, refreshed_evidence = verifier.capture_activity_panel()
                    if refreshed_evidence:
                        milestone_evidence = refreshed_evidence
            else:
                # The reward rows were already settled by an earlier attempt.
                # Do not invoke the upstream fixed coordinate: when no row
                # exposes “领取”, it can navigate to an unrelated resource
                # page.  A pending red bottom milestone is handled below.
                verifier._log(
                    "HOST_DAILY_ACTIVITY_ALREADY_SETTLED "
                    f"{json.dumps({'source': 'pre_claim_panel_labels'}, ensure_ascii=False)}"
                )
            milestones_pending = verifier.inspect_milestone_state(milestone_evidence)
            if milestones_pending is True:
                verifier.click_milestone_reward(after_sleep=1)
                milestone_clicks = 1
            elif milestones_pending is None:
                verifier._log(
                    "HOST_DAILY_ACTIVITY_MILESTONE_CLICK_SKIPPED "
                    f"{json.dumps({'reason': 'milestone state unconfirmed'}, ensure_ascii=False)}"
                )
        except Exception as exc:
            observation = DailyActivityObservation(
                points=None,
                target=DAILY_ACTIVITY_COMPLETION_THRESHOLD,
                complete=False,
                evidence_path=evidence_before or panel_evidence,
                reason="claim action failed",
            )
            verifier.log_unverified(
                observation,
                reason=f"host daily activity claim failed: {exc}",
                evidence_path=evidence_before or panel_evidence,
            )
            raise

        result = None
        # Keep the panel open for a fresh post-condition capture.  No blind
        # fixed-coordinate click is performed here.
        self.info_set("current task", "verify daily activity after claim")
        verifier._log(
            "HOST_DAILY_ACTIVITY_CLAIM_ACTION "
            f"{json.dumps({'upstream_click': False, 'host_clicks': row_clicks + milestone_clicks, 'row_clicks': row_clicks, 'milestone_clicks': milestone_clicks}, ensure_ascii=False)}"
        )
        _, post_panel_evidence = verifier.capture_activity_panel()
        if post_panel_evidence:
            milestone_evidence = post_panel_evidence
        remaining_claim_buttons = verifier._panel_has_pending_claim_buttons()
        verifier._log(
            "HOST_DAILY_ACTIVITY_CLAIM_BUTTON_STATE "
            f"{json.dumps({'remaining': remaining_claim_buttons, 'source': 'post_claim_panel_labels'}, ensure_ascii=False)}"
        )
        milestones_pending = verifier.inspect_milestone_state(milestone_evidence)
        total_points, total_evidence, full_after = verifier.capture_total_after_claim()
        observation = verifier.observe_after_claim(
            total_points=total_points,
            evidence_path=total_evidence or full_after,
            remaining_claim_buttons=remaining_claim_buttons,
            milestones_pending=milestones_pending,
        )
        evidence_after = total_evidence or full_after
        if not observation.complete:
            reason = (
                f"daily activity verification failed: points={observation.points!r}, "
                f"target={observation.target}, source={observation.source}, "
                f"detail={observation.reason}"
            )
            verifier.log_unverified(
                observation,
                reason=reason,
                evidence_path=evidence_after or evidence_before or panel_evidence,
            )
            try:
                self.ensure_main(time_out=10)
            finally:
                raise DailyActivityVerificationError(reason)

        self.ensure_main(time_out=10)
        verifier.verify_claim_transition(
            observation,
            evidence_before=evidence_before,
            evidence_after=evidence_after,
            milestones_pending=milestones_pending,
        )
        return result

    host_claim_daily.__name__ = claim_daily.__name__
    host_claim_daily.__qualname__ = claim_daily.__qualname__
    task_class.claim_daily = host_claim_daily
