"""Host-owned execution tracing for the installed OK-WW daily task.

The bundled OK-WW files are treated as replaceable upstream artifacts.  This
module adds observability by wrapping methods in memory inside the worker
process; it does not write to the installation or change task behavior.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any

from wuwa_auto.okww.daily_capabilities import capability_matrix

TRACE_MARKER = "HOST_OKWW_DAILY_TRACE"
CAPABILITIES_MARKER = "HOST_OKWW_DAILY_CAPABILITIES"
STAMINA_OCR_REGION = (0.49, 0.0, 0.92, 0.10)
STAMINA_MAX_SAMPLES = 3
STAMINA_REQUIRED_AGREEMENT = 2
STAMINA_ZERO_MIN_CONFIDENCE = 0.60
STAMINA_PANEL_REFRESH_ATTEMPTS = 1
_STAMINA_SAMPLES_ATTR = "__wuwa_host_stamina_ocr_samples__"
_STAMINA_RATIO_RE = re.compile(r"\d+\s*/\s*\d+")
_BOOK_TAB_OCR_REGION = (0.03, 0.10, 0.36, 0.98)
_BOOK_FORWARD_VIEW_REGION = (0.60, 0.60, 0.99, 0.98)
_BOOK_FORWARD_VIEW_RE = re.compile(
    r"前往查看|前往檢視|Go\s+to\s+view",
    re.IGNORECASE,
)
_BOOK_TAB_PATTERNS = {
    "wuyin": re.compile(r"无音清剿|無音清剿|Tacet\s+Suppression", re.IGNORECASE),
    "canxiang": re.compile(
        r"残象聚落|殘象聚落|Tacet\s+Discord\s+Nest",
        re.IGNORECASE,
    ),
}


def _json_value(value: Any, *, limit: int = 240) -> Any:
    """Keep trace payloads small and free of opaque runtime objects."""

    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "..."
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item, limit=limit) for item in list(value)[:40]]
    if isinstance(value, dict):
        return {
            str(key): _json_value(item, limit=limit)
            for key, item in list(value.items())[:40]
        }
    name = getattr(value, "name", None)
    if name:
        return str(name)
    cache_key = getattr(value, "cache_key", None)
    if cache_key:
        return str(cache_key)
    return type(value).__name__


def _log(task: Any, event: str, **fields: Any) -> None:
    log_info = getattr(task, "log_info", None)
    if not callable(log_info):
        return
    payload = {
        "event": event,
        **{key: _json_value(value) for key, value in fields.items()},
    }
    log_info(f"{TRACE_MARKER} {json.dumps(payload, ensure_ascii=False)}")


def _wrap_method(
    task_class: type[Any],
    method_name: str,
    *,
    event: str,
    before: Callable[[Any, tuple[Any, ...], dict[str, Any]], dict[str, Any]]
    | None = None,
    after: Callable[[Any, Any], dict[str, Any]] | None = None,
) -> None:
    original = getattr(task_class, method_name, None)
    if not callable(original) or getattr(original, "__wuwa_host_trace__", False):
        return

    @wraps(original)
    def traced(self: Any, *args: Any, **kwargs: Any) -> Any:
        fields = before(self, args, kwargs) if before else {}
        _log(self, f"{event}_start", **fields)
        try:
            result = original(self, *args, **kwargs)
        except Exception as exc:
            _log(
                self,
                f"{event}_error",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise
        _log(
            self,
            f"{event}_end",
            **(after(self, result) if after else {"result": result}),
        )
        return result

    traced.__wuwa_host_trace__ = True
    setattr(task_class, method_name, traced)


def _task_config(task: Any) -> dict[str, Any]:
    config = getattr(task, "config", {})
    return config if isinstance(config, dict) else {}


def _daily_run_before(task: Any, _args: tuple[Any, ...], _kwargs: dict[str, Any]) -> dict[str, Any]:
    config = _task_config(task)
    executor = getattr(task, "executor", None)
    onetime_tasks = getattr(executor, "onetime_tasks", []) if executor else []
    available = [
        getattr(candidate, "__class__", type(candidate)).__name__
        for candidate in onetime_tasks or []
    ]
    _log(
        task,
        "capabilities",
        configured_daily={
            "which_to_farm": config.get("Which to Farm"),
            "which_tacet": config.get("Which Tacet Suppression to Farm"),
            "which_forgery": config.get("Which Forgery Challenge to Farm"),
            "farm_nightmare_for_daily_echo": config.get(
                "Farm Nightmare Nest for Daily Echo"
            ),
            "additional_tasks": config.get(
                "Additional Tasks to Run After Daily Task"
            ),
        },
        supported_tasks=getattr(task, "support_tasks", []),
        activity_capability_matrix=capability_matrix(),
        configured_onetime_tasks=available,
    )
    return {"config": config}


def _run_task_before(_task: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    selected = args[0] if args else kwargs.get("task")
    if isinstance(selected, type):
        selected_name = selected.__name__
    else:
        selected_name = (
            getattr(selected, "__class__", type(selected)).__name__
            if selected is not None
            else None
        )
    return {
        "selected_task": selected_name,
    }


def _open_daily_after(_task: Any, result: Any) -> dict[str, Any]:
    if isinstance(result, (list, tuple)) and len(result) == 2:
        return {"used_stamina": result[0], "daily_reward_ready": result[1]}
    return {"result": result}


def _farm_before(_task: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    used_stamina = kwargs.get("used_stamina")
    if used_stamina is None and len(args) >= 2:
        used_stamina = args[1]
    daily = kwargs.get("daily")
    if daily is None and args:
        daily = args[0]
    return {"daily": daily, "used_stamina": used_stamina}


def _stamina_result_after(_task: Any, result: Any) -> dict[str, Any]:
    """Expose the upstream stamina tuple without changing its semantics."""

    if isinstance(result, (list, tuple)) and len(result) >= 3:
        current, back_up, total = result[:3]
        return {
            "current_stamina": current,
            "back_up_stamina": back_up,
            "total_stamina": total,
            "ocr_available": not (
                current == -1 and back_up == -1 and total == -1
            ),
            "result": result,
        }
    return {"result": result}


def _same_stamina_region(
    args: tuple[Any, ...],
    kwargs: dict[str, Any] | None = None,
) -> bool:
    kwargs = kwargs or {}
    coordinates = (
        args[0] if len(args) > 0 else kwargs.get("x"),
        args[1] if len(args) > 1 else kwargs.get("y"),
        args[2] if len(args) > 2 else kwargs.get("to_x"),
        args[3] if len(args) > 3 else kwargs.get("to_y"),
    )
    if any(value is None for value in coordinates):
        return False
    try:
        return all(
            abs(float(actual) - expected) < 0.001
            for actual, expected in zip(coordinates, STAMINA_OCR_REGION)
        )
    except (TypeError, ValueError):
        return False


def _raw_ocr_box(box: Any) -> dict[str, Any]:
    """Keep the OCR value and bounded geometry for post-run verification."""

    payload: dict[str, Any] = {"name": str(getattr(box, "name", box))}
    for key in ("x", "y", "width", "height", "confidence"):
        value = getattr(box, key, None)
        if isinstance(value, (int, float)):
            payload[key] = value
    return payload


def _install_stamina_ocr_trace(task_class: type[Any]) -> None:
    """Record the exact raw OCR boxes used by BaseWWTask.get_stamina()."""

    original = getattr(task_class, "ocr", None)
    if not callable(original) or getattr(original, "__wuwa_host_stamina_ocr_trace__", False):
        return

    @wraps(original)
    def traced(self: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            result = original(self, *args, **kwargs)
        except Exception as exc:
            if _same_stamina_region(args, kwargs):
                _log(
                    self,
                    "stamina_ocr_error",
                    region=STAMINA_OCR_REGION,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
            raise
        if _same_stamina_region(args, kwargs):
            boxes = list(result or []) if not isinstance(result, (str, bytes)) else [result]
            match = kwargs.get("match")
            patterns = []
            for item in match if isinstance(match, (list, tuple, set)) else [match]:
                pattern = getattr(item, "pattern", None)
                patterns.append(str(pattern if pattern is not None else item))
            observation = {
                "region": STAMINA_OCR_REGION,
                "raw_names": [
                    str(getattr(box, "name", box)) for box in boxes[:40]
                ],
                "raw_boxes": [_raw_ocr_box(box) for box in boxes[:40]],
                "match_patterns": patterns,
            }
            samples = getattr(self, _STAMINA_SAMPLES_ATTR, None)
            if not isinstance(samples, list):
                samples = []
                setattr(self, _STAMINA_SAMPLES_ATTR, samples)
            samples.append(observation)
            del samples[:-12]
            _log(self, "stamina_ocr", **observation)
        return result

    traced.__wuwa_host_stamina_ocr_trace__ = True
    task_class.ocr = traced


def _stamina_read_is_trustworthy(
    result: Any,
    observations: list[dict[str, Any]],
) -> bool:
    """Reject a lone low-confidence zero without changing valid readings."""

    if not isinstance(result, (list, tuple)) or len(result) < 3:
        return False
    try:
        total = int(result[2])
    except (TypeError, ValueError):
        return False
    if total < 0:
        return False
    if total > 0:
        return True

    ratio_confidences: list[float] = []
    for observation in observations:
        for box in observation.get("raw_boxes", []):
            name = str(box.get("name", ""))
            if not _STAMINA_RATIO_RE.search(name):
                continue
            confidence = box.get("confidence")
            if isinstance(confidence, (int, float)):
                normalized = float(confidence)
                if normalized > 1:
                    normalized /= 100
                ratio_confidences.append(normalized)
            else:
                ratio_confidences.append(1.0)
    return bool(ratio_confidences) and max(ratio_confidences) >= STAMINA_ZERO_MIN_CONFIDENCE


def _capture_stamina_evidence(task: Any, stage: str) -> str | None:
    """Persist the exact desktop state when the bounded stamina OCR is doubtful."""

    try:
        from PIL import ImageGrab

        evidence_dir = Path(__file__).resolve().parents[3] / "runtime" / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
        path = evidence_dir / f"ok_stamina_{stage}_{timestamp}.png"
        ImageGrab.grab(all_screens=True).save(path)
        return str(path.resolve())
    except Exception as exc:  # noqa: BLE001 - evidence must not alter the task
        _log(
            task,
            "stamina_evidence_error",
            stage=stage,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return None


def _box_center(box: Any) -> tuple[int, int] | None:
    center = getattr(box, "center", None)
    point = center() if callable(center) else None
    if (
        isinstance(point, tuple)
        and len(point) == 2
        and all(isinstance(value, (int, float)) for value in point)
    ):
        return int(point[0]), int(point[1])
    x = getattr(box, "x", None)
    y = getattr(box, "y", None)
    width = getattr(box, "width", 0)
    height = getattr(box, "height", 0)
    if all(isinstance(value, (int, float)) for value in (x, y, width, height)):
        return int(x + width / 2), int(y + height / 2)
    return None


def _refresh_stamina_panel_with_hid(task: Any) -> bool:
    """Re-select the proven F2 boss tab when PostMessage left a stale page open."""

    find_one = getattr(task, "find_one", None)
    if not callable(find_one):
        return False
    try:
        feature = find_one("gray_book_boss")
        point = _box_center(feature) if feature is not None else None
        if point is None:
            return False
        capture = getattr(
            getattr(getattr(task, "executor", None), "interaction", None),
            "capture",
            None,
        )
        get_abs_cords = getattr(capture, "get_abs_cords", None)
        if not callable(get_abs_cords):
            return False
        from wuwa_auto.okww.virtual_hid import _virtual_hid_click

        absolute_x, absolute_y = get_abs_cords(*point)
        evidence_before = _capture_stamina_evidence(task, "panel_before_refresh")
        _virtual_hid_click(
            int(absolute_x),
            int(absolute_y),
            hold=0.2,
            log_action=True,
        )
        sleeper = getattr(task, "sleep", None)
        if callable(sleeper):
            sleeper(1.5)
        else:
            time.sleep(1.5)
        _log(
            task,
            "stamina_panel_hid_refresh",
            feature="gray_book_boss",
            frame_point=point,
            absolute_point=(absolute_x, absolute_y),
            evidence_before=evidence_before,
        )
        return True
    except Exception as exc:  # noqa: BLE001 - bounded recovery falls back safely
        _log(
            task,
            "stamina_panel_hid_refresh_error",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return False


def _install_book_tab_hid_override(task_class: type[Any]) -> None:
    """Use a bounded OCR label after game updates reorder the F2 menu.

    OK-WW's feature boxes encode the old vertical position.  The 2026-08-09
    client added a menu entry, so those boxes now select the neighbouring
    page.  Only the two daily tabs observed in this workflow are overridden;
    an unrecognized UI falls back to the upstream method.
    """

    original = getattr(task_class, "open_boss_book", None)
    if not callable(original) or getattr(original, "__wuwa_host_book_tab_hid__", False):
        return

    @wraps(original)
    def open_boss_book(self: Any, name: str, after_sleep: float = 2) -> Any:
        pattern = _BOOK_TAB_PATTERNS.get(name)
        ocr = getattr(self, "ocr", None)
        if pattern is not None and callable(ocr):
            try:
                boxes = ocr(*_BOOK_TAB_OCR_REGION, match=pattern, log=False)
                refreshed_root = False
                if not boxes and _refresh_stamina_panel_with_hid(self):
                    refreshed_root = True
                    boxes = ocr(*_BOOK_TAB_OCR_REGION, match=pattern, log=False)
                names = [str(getattr(box, "name", box)) for box in boxes or []]
                _log(
                    self,
                    "book_tab_ocr",
                    tab=name,
                    region=_BOOK_TAB_OCR_REGION,
                    raw_names=names,
                    raw_boxes=[_raw_ocr_box(box) for box in boxes or []],
                    refreshed_root=refreshed_root,
                )
                box = (boxes or [None])[0]
                point = _box_center(box) if box is not None else None
                if point is not None:
                    capture = getattr(
                        getattr(getattr(self, "executor", None), "interaction", None),
                        "capture",
                        None,
                    )
                    get_abs_cords = getattr(capture, "get_abs_cords", None)
                    if callable(get_abs_cords):
                        from wuwa_auto.okww.virtual_hid import (
                            _virtual_hid_click,
                        )

                        absolute_x, absolute_y = get_abs_cords(*point)
                        evidence_before = _capture_stamina_evidence(
                            self,
                            f"book_tab_{name}_before",
                        )
                        _virtual_hid_click(
                            int(absolute_x),
                            int(absolute_y),
                            hold=0.2,
                            log_action=True,
                        )
                        sleeper = getattr(self, "sleep", None)
                        if callable(sleeper):
                            sleeper(max(1.5, after_sleep))
                        else:
                            time.sleep(max(1.5, after_sleep))
                        _log(
                            self,
                            "book_tab_hid_click",
                            tab=name,
                            frame_point=point,
                            absolute_point=(absolute_x, absolute_y),
                            evidence_before=evidence_before,
                        )

                        # The updated “无音清剿” page is a cultivation-plan
                        # landing page.  Its localized “前往查看” opens the
                        # legacy target list consumed by TacetTask.
                        if name == "wuyin":
                            forward_boxes = ocr(
                                *_BOOK_FORWARD_VIEW_REGION,
                                match=_BOOK_FORWARD_VIEW_RE,
                                log=False,
                            )
                            forward = (forward_boxes or [None])[0]
                            forward_point = (
                                _box_center(forward) if forward is not None else None
                            )
                            if forward_point is not None:
                                forward_x, forward_y = get_abs_cords(*forward_point)
                                _virtual_hid_click(
                                    int(forward_x),
                                    int(forward_y),
                                    hold=0.2,
                                    log_action=True,
                                )
                                if callable(sleeper):
                                    sleeper(max(1.5, after_sleep))
                                else:
                                    time.sleep(max(1.5, after_sleep))
                                evidence_after = _capture_stamina_evidence(
                                    self,
                                    "book_tab_wuyin_forward_view_after",
                                )
                                _log(
                                    self,
                                    "book_tab_forward_view_hid_click",
                                    tab=name,
                                    region=_BOOK_FORWARD_VIEW_REGION,
                                    raw_names=[
                                        str(getattr(item, "name", item))
                                        for item in forward_boxes or []
                                    ],
                                    frame_point=forward_point,
                                    absolute_point=(forward_x, forward_y),
                                    evidence_after=evidence_after,
                                )
                            else:
                                _log(
                                    self,
                                    "book_tab_forward_view_absent",
                                    tab=name,
                                    region=_BOOK_FORWARD_VIEW_REGION,
                                )
                        return None
            except Exception as exc:  # noqa: BLE001 - retain upstream fallback
                _log(
                    self,
                    "book_tab_hid_error",
                    tab=name,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
        _log(self, "book_tab_upstream_fallback", tab=name)
        return original(self, name, after_sleep=after_sleep)

    open_boss_book.__wuwa_host_book_tab_hid__ = True
    task_class.open_boss_book = open_boss_book


def _install_stamina_guard(task_class: type[Any]) -> None:
    """Require two consistent semantic reads before stamina controls a task."""

    original = getattr(task_class, "get_stamina", None)
    if not callable(original) or getattr(original, "__wuwa_host_stamina_guard__", False):
        return

    @wraps(original)
    def guarded(self: Any, *args: Any, **kwargs: Any) -> Any:
        trustworthy: list[Any] = []
        total_attempt = 0
        for refresh_round in range(STAMINA_PANEL_REFRESH_ATTEMPTS + 1):
            for attempt in range(1, STAMINA_MAX_SAMPLES + 1):
                total_attempt += 1
                samples = getattr(self, _STAMINA_SAMPLES_ATTR, [])
                before = len(samples) if isinstance(samples, list) else 0
                result = original(self, *args, **kwargs)
                samples = getattr(self, _STAMINA_SAMPLES_ATTR, [])
                observations = (
                    list(samples[before:]) if isinstance(samples, list) else []
                )
                accepted = _stamina_read_is_trustworthy(result, observations)
                _log(
                    self,
                    "stamina_read_attempt",
                    attempt=total_attempt,
                    refresh_round=refresh_round,
                    result=result,
                    semantic_read=accepted,
                    raw_observations=observations,
                )
                if accepted:
                    trustworthy.append(result)
                    agreements = sum(candidate == result for candidate in trustworthy)
                    if agreements >= STAMINA_REQUIRED_AGREEMENT:
                        _log(
                            self,
                            "stamina_read_consensus",
                            attempt=total_attempt,
                            refresh_round=refresh_round,
                            result=result,
                        )
                        return result
                if attempt < STAMINA_MAX_SAMPLES:
                    sleep = getattr(self, "sleep", None)
                    if callable(sleep):
                        sleep(0.35)
            if (
                refresh_round < STAMINA_PANEL_REFRESH_ATTEMPTS
                and not _refresh_stamina_panel_with_hid(self)
            ):
                break

        evidence_path = _capture_stamina_evidence(self, "ocr_unverified")
        _log(
            self,
            "stamina_read_unverified",
            attempts=total_attempt,
            trustworthy_results=trustworthy,
            fallback_result=(-1, -1, -1),
            evidence_path=evidence_path,
        )
        return -1, -1, -1

    guarded.__wuwa_host_stamina_guard__ = True
    task_class.get_stamina = guarded


def _nest_init_after(task: Any, _result: Any) -> dict[str, Any]:
    return {"queue": [getattr(action, "__name__", str(action)) for action in task.queues]}


def _nest_target_after(_task: Any, result: Any) -> dict[str, Any]:
    return {
        "target": getattr(result, "cache_key", None),
        "target_type": type(result).__name__ if result is not None else None,
    }


def _install_nightmare_ocr_trace(task_class: type[Any]) -> None:
    """Record the exact count OCR used to select a nightmare target."""

    original = getattr(task_class, "ocr", None)
    if not callable(original) or getattr(original, "__wuwa_host_ocr_trace__", False):
        return

    @wraps(original)
    def traced(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = original(self, *args, **kwargs)
        try:
            x, y = float(args[0]), float(args[1])
        except (IndexError, TypeError, ValueError):
            x = y = -1
        match = kwargs.get("match")
        is_count_region = abs(x - 0.35) < 0.01 and abs(y - 0.13) < 0.01
        if is_count_region and isinstance(match, re.Pattern):
            names = [str(getattr(item, "name", item)) for item in result or []]
            _log(self, "nightmare_count_ocr", names=names, pattern=match.pattern)
        return result

    traced.__wuwa_host_ocr_trace__ = True
    task_class.ocr = traced


def install_daily_trace(
    daily_task_class: type[Any],
    *,
    nightmare_task_class: type[Any] | None = None,
    tacet_task_class: type[Any] | None = None,
    forgery_task_class: type[Any] | None = None,
    simulation_task_class: type[Any] | None = None,
) -> None:
    """Install idempotent, behavior-preserving trace wrappers."""

    _wrap_method(
        daily_task_class,
        "run",
        event="daily_run",
        before=_daily_run_before,
    )
    _wrap_method(
        daily_task_class,
        "open_daily",
        event="open_daily",
        after=_open_daily_after,
    )
    _wrap_method(
        daily_task_class,
        "run_task_by_class",
        event="dispatch_subtask",
        before=_run_task_before,
        after=lambda _task, result: {"result": result},
    )
    for name in ("claim_daily", "claim_mail", "claim_battle_pass", "run_additional_tasks"):
        _wrap_method(daily_task_class, name, event=name)

    if nightmare_task_class is not None:
        _install_book_tab_hid_override(nightmare_task_class)
        _install_nightmare_ocr_trace(nightmare_task_class)
        _wrap_method(nightmare_task_class, "run", event="nightmare_run")
        _wrap_method(nightmare_task_class, "run_capture_mode", event="nightmare_capture")
        _wrap_method(nightmare_task_class, "_init_queue", event="nightmare_init_queue", after=_nest_init_after)
        _wrap_method(nightmare_task_class, "get_nest_to_go", event="nightmare_find_target", after=_nest_target_after)
        _wrap_method(nightmare_task_class, "combat_nest", event="nightmare_combat")

    farm_methods = (
        (tacet_task_class, "farm_tacet", "tacet"),
        (forgery_task_class, "farm_forgery", "forgery"),
        (simulation_task_class, "farm_simulation", "simulation"),
    )
    for task_class, method_name, event in farm_methods:
        if task_class is not None:
            _install_book_tab_hid_override(task_class)
            _install_stamina_ocr_trace(task_class)
            _install_stamina_guard(task_class)
            _wrap_method(
                task_class,
                "get_stamina",
                event="stamina",
                after=_stamina_result_after,
            )
            _wrap_method(task_class, method_name, event=event, before=_farm_before)
