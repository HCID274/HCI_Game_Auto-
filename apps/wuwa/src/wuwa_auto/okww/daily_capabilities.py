"""Compare the in-game daily-activity panel with local OK-WW capabilities.

The installed OK-WW package is an upstream artifact and must remain
replaceable.  This module therefore contains only host-owned terminology and
evidence mapping.  It never invokes a task; it explains which observed panel
objectives have a corresponding upstream entry point and what the current run
actually attempted.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass

_POINT_RE = re.compile(r"\+\s*(?P<points>\d{1,3})")
_FRACTION_RE = re.compile(
    r"(?<!\d)(?P<current>\d{1,4})\s*/\s*(?P<target>\d{1,4})(?!\d)"
)
_TRACE_LINE_RE = re.compile(r"HOST_OKWW_DAILY_TRACE\s+(\{.*\})")


def _latest_stamina_observation(log_text: str) -> dict[str, object] | None:
    """Return the latest host-captured stamina tuple, if one was logged."""

    latest: dict[str, object] | None = None
    for line in log_text.splitlines():
        match = _TRACE_LINE_RE.search(line)
        if not match:
            continue
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or payload.get("event") != "stamina_end":
            continue
        values = {
            key: payload.get(key)
            for key in ("current_stamina", "back_up_stamina", "total_stamina")
        }
        if all(isinstance(value, (int, float)) for value in values.values()):
            values["ocr_available"] = not all(
                value == -1 for value in values.values()
            )
            latest = values
        else:
            latest = {"raw": payload}
    return latest

# The names are intentionally based on the visible Chinese UI labels rather
# than internal OK-WW identifiers.  This keeps the parser useful across minor
# upstream translations and layout changes.
_RULES: tuple[dict[str, object], ...] = (
    {
        "key": "login",
        "aliases": ("登录游戏",),
        "label": "登录游戏",
        "capability": "supported",
        "entry_point": "启动/登录流程（launcher 与 AutoLogin）",
    },
    {
        "key": "dodge-counter",
        "aliases": ("成功闪避", "逆势回击"),
        "label": "成功闪避或逆势回击",
        "capability": "unsupported",
        "entry_point": "上游没有按每日活跃目标触发闪避/逆势回击的独立任务",
    },
    {
        "key": "daily-quest",
        "aliases": ("完成1次日常任务", "完成 1 次日常任务"),
        "label": "完成1次日常任务",
        "capability": "unsupported",
        "entry_point": "当前上游任务注册表没有日常委托导航/完成器",
    },
    {
        "key": "waveplate",
        "aliases": ("累计消耗180点结晶波片", "累计消耗 180 点结晶波片"),
        "label": "累计消耗180点结晶波片",
        "capability": "supported",
        "entry_point": "DailyTask → Tacet/Forgery/Simulation 的 daily farm",
    },
    {
        "key": "nightmare",
        "aliases": ("梦魇聚落", "残象聚落"),
        "label": "通关1次梦魇聚落或残象聚落",
        "capability": "supported",
        "entry_point": "NightmareNestTask（梦魇/残象聚落）",
    },
    {
        "key": "echo-acquire",
        "aliases": ("获得任意1个声骸", "获得任意 1 个声骸"),
        "label": "获得任意1个声骸",
        "capability": "supported",
        "entry_point": "NightmareNestTask 的捕获/吸收流程",
    },
    {
        "key": "enemy-defeat",
        "aliases": ("累计击败15个敌人", "累计击败 15 个敌人"),
        "label": "累计击败15个敌人",
        "capability": "supported",
        "entry_point": "NightmareNestTask/TacetTask 战斗流程",
    },
)


@dataclass(frozen=True)
class ActivityTaskAssessment:
    """One visible objective and its host/upstream comparison."""

    key: str
    label: str
    points: int
    current: int | None
    target: int | None
    completed: bool
    capability: str
    entry_point: str
    action_observed: bool = False
    state: str = "unknown"
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _rules_for(text: str) -> dict[str, object] | None:
    for rule in _RULES:
        if any(alias in text for alias in rule["aliases"]):
            return rule
    return None


def _blocks(labels: Iterable[str]) -> list[tuple[int, list[str]]]:
    blocks: list[tuple[int, list[str]]] = []
    current: tuple[int, list[str]] | None = None
    for raw in labels:
        label = str(raw).strip()
        point_match = _POINT_RE.search(label)
        if point_match:
            if current is not None:
                blocks.append(current)
            current = (int(point_match.group("points")), [])
            continue
        if current is not None:
            current[1].append(label)
    if current is not None:
        blocks.append(current)
    return blocks


def _last_fraction(labels: Iterable[str]) -> tuple[int | None, int | None]:
    matches: list[tuple[int, int]] = []
    for label in labels:
        for match in _FRACTION_RE.finditer(str(label)):
            matches.append((int(match.group("current")), int(match.group("target"))))
    return matches[-1] if matches else (None, None)


def _action_observed(key: str, log_text: str) -> bool:
    if key == "login":
        return bool(re.search(r"AutoLogin|client world ready|登录", log_text, re.IGNORECASE))
    if key == "waveplate":
        return any(
            marker in log_text
            for marker in (
                "TacetTask:start walk_to_treasure",
                "ForgeryTask:start walk_to_treasure",
                "SimulationTask:start walk_to_treasure",
                "TacetTask:used all stamina",
                "ForgeryTask:used all stamina",
                "SimulationTask:used all stamina",
            )
        )
    if key == "nightmare":
        return any(
            marker in log_text
            for marker in (
                "NightmareNestTask:farm echo walk find true",
                "NightmareNestTask:farm echo yolo find True",
                "HOST_OKWW_DAILY_TRACE {\"event\": \"nightmare_combat_end\"",
            )
        )
    return False


def compare_activity_panel(
    labels: Iterable[str],
    *,
    log_text: str = "",
) -> dict[str, object]:
    """Return a deterministic capability comparison for one OCR panel."""

    assessments: list[ActivityTaskAssessment] = []
    stamina_observation = _latest_stamina_observation(log_text)
    stamina_trace_without_value = bool(
        re.search(r"(?:used all stamina|current stamina:)", log_text, re.IGNORECASE)
    ) and (
        stamina_observation is None
        or stamina_observation.get("ocr_available") is not True
    )
    unknown_index = 0
    for points, raw_labels in _blocks(labels):
        text = "".join(raw_labels)
        rule = _rules_for(text)
        if rule is None:
            # Preserve future/upstream objectives in the comparison instead of
            # silently treating them as impossible.  A fixed capability gap
            # must never be inferred from only the subset this host currently
            # knows how to name.
            if not raw_labels:
                continue
            unknown_index += 1
            current, target = _last_fraction(raw_labels)
            completed = current is not None and target is not None and current >= target
            label = "".join(raw_labels)
            assessments.append(
                ActivityTaskAssessment(
                    key=f"unknown-{unknown_index}",
                    label=label or f"未映射活跃任务{unknown_index}",
                    points=points,
                    current=current,
                    target=target,
                    completed=completed,
                    capability="unknown",
                    entry_point="本地能力映射尚未覆盖该面板任务",
                    state="completed" if completed else "unknown",
                    reason=(
                        "面板进度已达到目标"
                        if completed
                        else "仅记录原始面板证据，暂不判定上游是否可执行"
                    ),
                )
            )
            continue
        current, target = _last_fraction(raw_labels)
        completed = current is not None and target is not None and current >= target
        supported = str(rule["capability"]) == "supported"
        action_seen = _action_observed(str(rule["key"]), log_text)
        if completed:
            state = "completed"
            reason = "面板进度已达到目标"
        elif not supported:
            state = "unsupported"
            reason = str(rule["entry_point"])
        elif str(rule["key"]) == "waveplate" and stamina_observation:
            total_stamina = stamina_observation.get("total_stamina")
            if stamina_observation.get("ocr_available") is False:
                state = "attempted_not_completed"
                reason = "体力 OCR 未识别（上游返回-1哨兵），不能判定为0"
            elif isinstance(total_stamina, (int, float)) and total_stamina <= 0:
                state = "unavailable"
                reason = "体力 OCR 原值确认当前与备用体力合计为0，不能继续消耗"
            elif isinstance(total_stamina, (int, float)) and total_stamina < 60:
                state = "unavailable"
                reason = (
                    "体力 OCR 原值确认当前与备用体力合计为 "
                    f"{int(total_stamina)}，不足一场60体力副本"
                )
            else:
                state = "attempted_not_completed"
                reason = "上游入口已执行，但本轮面板仍未达到目标"
        elif str(rule["key"]) == "waveplate" and re.search(
            r"(?:current_stamina\s+0|current stamina:\s*0|used all stamina)",
            log_text,
            re.IGNORECASE,
        ):
            state = "attempted_not_completed"
            reason = "上游报告 used all stamina，但本轮未记录可验证的体力 OCR 原值"
        elif action_seen:
            state = "attempted_not_completed"
            reason = "上游入口已执行，但面板仍未达到目标"
        else:
            state = "supported_not_observed"
            reason = "上游有入口，当前日志尚未观察到执行"
        assessments.append(
            ActivityTaskAssessment(
                key=str(rule["key"]),
                label=str(rule["label"]),
                points=points,
                current=current,
                target=target,
                completed=completed,
                capability="supported" if supported else "unsupported",
                entry_point=str(rule["entry_point"]),
                action_observed=action_seen,
                state=state,
                reason=reason,
            )
        )

    current_points = sum(item.points for item in assessments if item.completed)
    supported_pending = sum(
        item.points
        for item in assessments
        if not item.completed and item.capability == "supported"
    )
    unavailable_pending = sum(
        item.points
        for item in assessments
        if not item.completed and item.state == "unavailable"
    )
    unsupported_pending = sum(
        item.points
        for item in assessments
        if not item.completed and item.capability == "unsupported"
    )
    unknown_pending = sum(
        item.points
        for item in assessments
        if not item.completed and item.capability == "unknown"
    )
    # This is deliberately optimistic: it answers “can the upstream expose a
    # route?” rather than claiming that a route actually succeeded.
    optimistic_points = current_points + supported_pending
    reachable_now_points = optimistic_points - unavailable_pending
    has_unknown_pending = unknown_pending > 0 or stamina_trace_without_value
    return {
        "tasks": [item.to_dict() for item in assessments],
        "current_points_from_tasks": current_points,
        "supported_pending_points": supported_pending,
        "unavailable_pending_points": unavailable_pending,
        "unsupported_pending_points": unsupported_pending,
        "unknown_pending_points": unknown_pending,
        "optimistic_points": optimistic_points,
        # ``None`` means the visible panel contains an unmapped objective; it
        # is intentionally different from a proven 0/100 capability gap.
        "reachable_now_points": None if has_unknown_pending else reachable_now_points,
        "target": 100,
        "can_reach_target_with_supported_entries": optimistic_points >= 100,
        "can_reach_target_now": None if has_unknown_pending else reachable_now_points >= 100,
        "unsupported_tasks": [
            item.key
            for item in assessments
            if not item.completed and item.capability == "unsupported"
        ],
        "unknown_tasks": [
            item.key
            for item in assessments
            if not item.completed and item.capability == "unknown"
        ],
        "stamina_observation": stamina_observation,
        "stamina_observation_unverified": stamina_trace_without_value,
    }


def capability_matrix() -> list[dict[str, object]]:
    """Expose the stable matrix for trace/config diagnostics and tests."""

    return [
        {
            "key": str(rule["key"]),
            "label": str(rule["label"]),
            "capability": str(rule["capability"]),
            "entry_point": str(rule["entry_point"]),
        }
        for rule in _RULES
    ]
