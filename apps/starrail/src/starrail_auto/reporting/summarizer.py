"""DeepSeek-backed wording with a deterministic fallback."""

import json
import logging
import re
from typing import Any

from openai import OpenAI

from starrail_auto.reporting.models import NarrativeReport, RunReport, StaminaRun
from starrail_auto.reporting.prompting.composer import compose_report_messages
from starrail_auto.settings import get_secret

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
AI_TIMEOUT_SECONDS = 45.0
AI_MAX_TOKENS = 4096

log = logging.getLogger(__name__)


class AISummaryError(RuntimeError):
    """Raised when AI wording is unavailable or invalid."""


def _plan_fully_completed(item: StaminaRun) -> bool:
    if item.status != "completed":
        return False
    if item.remaining_plan_count == 0:
        return True
    return (
        item.planned_count is not None
        and item.rounds is not None
        and item.rewards_per_round is not None
        and item.rounds * item.rewards_per_round >= item.planned_count
    )


def _format_stamina_run(item: StaminaRun) -> str:
    name = item.name.replace(" - ", "·")
    context_match = re.fullmatch(r"用于培养(.+?)（(.+?)）", item.character_context)
    if context_match:
        name = f"{name}（{context_match.group(1)} {context_match.group(2)}）"
    if item.status == "skipped":
        return f"{name}：未执行（{item.reason or '条件不足'}）"

    if item.source == "activity" and item.activity_name:
        name = f"{item.activity_name}：{name}"

    details: list[str] = []
    if item.source == "activity" and item.completed_instances:
        details.append(f"{item.completed_instances}次")
    elif item.rounds == 1 and item.rewards_per_round is not None:
        details.append(f"{item.rewards_per_round}次")
    elif item.rounds is not None and item.rewards_per_round is not None:
        details.append(f"{item.rounds}轮×{item.rewards_per_round}次")
    elif item.rounds is not None:
        details.append(f"{item.rounds}轮")
    elif item.completed_instances:
        details.append(f"{item.completed_instances}次")
    if item.remaining_plan_count is not None and item.remaining_plan_count > 0:
        details.append(f"剩余计划{item.remaining_plan_count}次")
    elif _plan_fully_completed(item):
        details.append("已完成")
    if item.source == "activity" and item.activity_remaining_count is not None:
        details.append(f"活动双倍剩余{item.activity_remaining_count}次")

    text = name
    if details:
        separator = "" if context_match else " "
        text = f"{text}{separator}{'，'.join(details)}"
    return text


def _short_daily_task(task: str) -> str:
    aliases = {
        "派遣委托或收取1次委托奖励": "派遣委托",
        "使用1次「万能合成机」": "万能合成机",
    }
    return aliases.get(task, task)


def _reward_action(label: str) -> str:
    reward = label.removesuffix("完成")
    return reward if reward.startswith("领取") else f"领取{reward}"


def _numbered(items: list[str]) -> str:
    return "\n".join(f"{index}. {item}" for index, item in enumerate(items, start=1))


def build_fallback_narrative(report: RunReport) -> NarrativeReport:
    """Produce a useful report even when DeepSeek is unavailable."""
    daily_items: list[str] = []
    for event in report.daily_events:
        if event.kind == "daily_task":
            daily_items.append(_short_daily_task(event.label))
        elif (
            event.kind == "stamina"
            and event.stamina_index is not None
            and 0 <= event.stamina_index < len(report.stamina_runs)
        ):
            daily_items.append(
                _format_stamina_run(report.stamina_runs[event.stamina_index])
            )
        elif event.kind == "reward":
            daily_items.append(_reward_action(event.label))

    if report.daily_status == "completed":
        daily_items.append(f"每日实训完成 {report.daily_score}")
    elif report.daily_status == "failed":
        if report.daily_unfinished:
            daily_items.extend(
                f"未完成：{_short_daily_task(task)}"
                for task in report.daily_unfinished
            )
        daily_items.append(f"每日实训未达成 {report.daily_score}")
    else:
        daily_items.append(f"每日实训未确认 {report.daily_score}")
    daily = _numbered(daily_items)

    routine_tasks: list[str] = []
    other_tasks = list(report.other_tasks)
    for reward in report.rewards_completed:
        action = _reward_action(reward)
        if any(
            marker in reward
            for marker in ("每日实训奖励", "委托奖励", "无名勋礼奖励")
        ):
            routine_tasks.append(action)
        else:
            other_tasks.append(action)
    other_tasks.extend(
        _format_stamina_run(item)
        for item in report.stamina_runs
        if item.status == "skipped"
    )

    issues: list[str] = []
    if report.run_stage and report.overall_status == "failed":
        issues.append(f"主链路失败阶段：{report.run_stage}")
    if (
        report.recovered_warnings
        and report.stopped_normally
        and report.overall_status != "completed"
    ):
        samples = report.recovered_warnings[:3]
        issues.append(f"过程告警已恢复：{'；'.join(samples)}")
    if report.overall_status == "stalled":
        issues.append(f"疑似卡住：{report.current_reason}")

    current_task = ""
    if not report.stopped_normally and report.current_task:
        if report.overall_status == "failed":
            state = "失败时停在"
        elif report.overall_status == "stalled":
            state = "疑似卡住"
        else:
            state = "仍在进行"
        current_task = f"{state}：{report.current_task}。{report.current_reason}"

    training_plan = report.custom_context.get("training_plan", {})
    completed_goals = training_plan.get("completed_this_run", [])
    other_tasks.extend(
        f"{goal['character']}：{goal['category']}养成计划已完成"
        for goal in completed_goals
        if isinstance(goal, dict) and goal.get("character") and goal.get("category")
    )
    training_todos = [
        f"{goal['character']}：{goal['category']}"
        for goal in training_plan.get("active_goals", [])
        if isinstance(goal, dict) and goal.get("character") and goal.get("category")
    ]

    return NarrativeReport(
        daily=daily,
        routine_tasks=routine_tasks,
        other_tasks=other_tasks,
        current_task=current_task,
        issues=issues,
        training_todos=training_todos,
    )


def _parse_narrative_response(data: Any) -> NarrativeReport:
    if not isinstance(data, dict) or not isinstance(data.get("daily"), str):
        raise AISummaryError("DeepSeek response is missing the daily field")

    def string_list(name: str) -> list[str]:
        value = data.get(name, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise AISummaryError(f"DeepSeek response field {name} is invalid")
        return [item.strip() for item in value if item.strip()]

    current_task = data.get("current_task", "")
    if not isinstance(current_task, str):
        raise AISummaryError("DeepSeek response field current_task is invalid")

    return NarrativeReport(
        daily=data["daily"].strip(),
        routine_tasks=string_list("routine_tasks"),
        other_tasks=string_list("other_tasks"),
        current_task=current_task.strip(),
        issues=string_list("issues"),
        training_todos=string_list("training_todos"),
    )


def _stamina_fact(item: StaminaRun) -> dict[str, Any]:
    return {
        "name": item.name.replace(" - ", "·"),
        "source": item.source,
        "activity_name": item.activity_name,
        "activity_start_remaining": item.activity_start_remaining,
        "activity_remaining_count": item.activity_remaining_count,
        "completed_instances": item.completed_instances,
        "rounds": item.rounds,
        "count_per_round": item.rewards_per_round,
        "remaining_plan_count": item.remaining_plan_count,
        "plan_fully_completed": _plan_fully_completed(item),
        "status": item.status,
        "reason": item.reason,
        "character_context": item.character_context,
    }


def _build_ai_input(report: RunReport) -> dict[str, Any]:
    ordered_daily_events: list[dict[str, Any]] = []
    for event in report.daily_events:
        if event.kind == "stamina" and event.stamina_index is not None:
            if 0 <= event.stamina_index < len(report.stamina_runs):
                ordered_daily_events.append(
                    {
                        "type": "stamina",
                        **_stamina_fact(report.stamina_runs[event.stamina_index]),
                    }
                )
        elif event.kind in {"daily_task", "reward"}:
            ordered_daily_events.append(
                {
                    "type": event.kind,
                    "name": (
                        _reward_action(event.label)
                        if event.kind == "reward"
                        else _short_daily_task(event.label)
                    ),
                }
            )
    ordered_daily_events.append(
        {
            "type": "daily_result",
            "status": report.daily_status,
            "score": report.daily_score,
        }
    )

    return {
        "overall_status": report.overall_status,
        "ordered_daily_events": ordered_daily_events,
        "routine_rewards": [
            _reward_action(reward)
            for reward in report.rewards_completed
        ],
        "followup_plans": [
            _stamina_fact(item)
            for item in report.stamina_runs
            if item.status == "skipped"
        ],
        "other_tasks": report.other_tasks,
        "daily_unfinished": (
            report.daily_unfinished if report.daily_status == "failed" else []
        ),
        "current_task": report.current_task,
        "current_reason": report.current_reason,
        "run_stage": report.run_stage,
        "retries": report.retries,
        "stopped_normally": report.stopped_normally,
        "recovered_warnings": report.recovered_warnings,
        "detected_training_target": report.detected_training_target,
        "detected_training_dungeons": report.detected_training_dungeons,
        "custom_context": report.custom_context,
    }


def summarize_with_ai(report: RunReport) -> NarrativeReport:
    """Use DeepSeek V4 Flash high-thinking mode to word structured facts."""
    api_key = get_secret("DEEPSEEK_API_KEY")
    if not api_key:
        raise AISummaryError("DEEPSEEK_API_KEY is not configured")

    base_url = get_secret("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL
    model = get_secret("DEEPSEEK_MODEL") or DEFAULT_MODEL
    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=AI_TIMEOUT_SECONDS,
        max_retries=1,
    )
    messages = compose_report_messages(_build_ai_input(report))
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        stream=False,
        reasoning_effort="high",
        extra_body={"thinking": {"type": "enabled"}},
        response_format={"type": "json_object"},
        max_tokens=AI_MAX_TOKENS,
    )
    content = response.choices[0].message.content
    if not content:
        raise AISummaryError("DeepSeek returned empty content")
    try:
        narrative = _parse_narrative_response(json.loads(content))
    except json.JSONDecodeError as exc:
        raise AISummaryError(f"DeepSeek returned invalid JSON: {exc}") from exc
    _validate_stamina_wording(report, narrative)
    return narrative


def _validate_stamina_wording(
    report: RunReport,
    narrative: NarrativeReport,
) -> None:
    """Reject AI wording that drops authoritative activity or plan counters."""
    lines = narrative.daily.splitlines()
    for event in report.daily_events:
        if event.kind != "stamina" or event.stamina_index is None:
            continue
        if not 0 <= event.stamina_index < len(report.stamina_runs):
            continue
        item = report.stamina_runs[event.stamina_index]
        name = item.name.replace(" - ", "·")
        line = next((value for value in lines if name in value), "")
        required: list[str] = []
        if item.source == "activity":
            if item.activity_name:
                required.append(item.activity_name)
            if item.completed_instances:
                required.append(f"{item.completed_instances}次")
            if item.activity_remaining_count is not None:
                required.append(f"活动双倍剩余{item.activity_remaining_count}次")
        if item.remaining_plan_count == 0:
            required.append("已完成")
        elif item.remaining_plan_count is not None:
            required.append(f"剩余计划{item.remaining_plan_count}次")
        if not line or any(token not in line for token in required):
            raise AISummaryError(
                f"DeepSeek wording dropped stamina facts for {name}: {required}"
            )


def summarize_report(report: RunReport) -> tuple[NarrativeReport, bool]:
    """Return AI wording when possible, otherwise deterministic wording."""
    try:
        return summarize_with_ai(report), True
    except Exception as exc:
        log.warning("AI report summary unavailable; using deterministic fallback: %s", exc)
        return build_fallback_narrative(report), False
