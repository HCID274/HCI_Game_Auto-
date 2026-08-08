"""DeepSeek wording over immutable, program-derived report facts."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from game_automation_core.reporting.agent import (
    AgentResponseError,
    TokenUsage,
    diagnostics_match_status,
    redact_sensitive_data,
    token_usage_from_response,
    validate_diagnostics,
)
from openai import OpenAI

from wuwa_auto.reporting.models import NarrativeReport, ReportItem, RunFacts
from wuwa_auto.reporting.parser import deterministic_summary
from wuwa_auto.reporting.prompting import compose_report_messages
from wuwa_auto.settings import get_secret

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
AI_MAX_TOKENS = 8192
log = logging.getLogger(__name__)
_DAILY_ACTIVITY_NEGATIVE_IDS = (
    "daily-activity-claim-action",
    "daily-activity-progress",
    "daily-activity-unverified",
    "daily-activity-capability-gap",
    "daily-activity-unsupported-",
    "daily-activity-unavailable-",
    "daily-activity-unknown-",
    "run-failure",
)
_DAILY_ACTIVITY_CONTRADICTION_RE = re.compile(
    r"奖励(?:已|已经)?领取|已领取奖励|已确认达到\s*100|"
    r"每日活跃(?:度)?(?:任务)?已完成|主流程(?:已|已经)?完成|"
    r"本轮已完成|本次已完成|(?:已|已经)完成[^，。；]{0,16}(?:任务|目标|奖励)",
    re.IGNORECASE,
)
_SUMMARY_CONTRADICTION_RE = re.compile(
    r"本轮(?:已|已经)?完成但|本轮(?:已|已经)?完成，.*失败|"
    r"(?:未|没有|并未)失败|未确认.*(?:已|已经)?完成|部分完成但.*全部完成",
    re.IGNORECASE,
)
_UNOBSERVED_REWARD_RE = re.compile(
    r"(?:额外(?:获得|领取|奖励)|"
    r"(?:获得|掉落)[^，。；]{0,12}(?:武器|角色|声骸|材料|奖励)|"
    r"限定(?:角色|武器)|五星(?:角色|武器)|新增(?:奖励|任务))",
    re.IGNORECASE,
)
_DURATION_CLAUSE_RE = re.compile(
    r"(?:耗时|用时|历时|运行(?:总)?时长)\s*"
    r"(?:\d+\s*(?:小时|时|分钟|分|秒)\s*)+",
    re.IGNORECASE,
)
_DURATION_PREFIX_RE = re.compile(
    r"^(?:耗时|用时|历时|运行(?:总)?时长)\s*",
    re.IGNORECASE,
)
_DURATION_KEYWORD_RE = re.compile(
    r"(?:耗时|用时|历时|运行(?:总)?时长)",
    re.IGNORECASE,
)

def _all_items(facts: RunFacts) -> list[ReportItem]:
    return [*facts.daily, *facts.weekly, *facts.followup, *facts.issues]


def _has_unobserved_reward_claim(text: str, facts: RunFacts) -> bool:
    """Reject reward/drop claims absent from facts or explicit user context."""

    observed = "\n".join(item.text for item in _all_items(facts))
    user_context = "\n".join(str(value) for value in facts.user_context.values())
    return any(
        match.group(0) not in observed and match.group(0) not in user_context
        for match in _UNOBSERVED_REWARD_RE.finditer(text)
    )


def _has_unobserved_summary_numbers(text: str, facts: RunFacts) -> bool:
    """Allow only fact counts (plus the measured duration) in the headline."""

    observed = "\n".join(item.text for item in _all_items(facts))
    allowed = set(re.findall(r"\d+", observed))
    duration = max(0, facts.duration_seconds)
    minutes, seconds = divmod(duration, 60)
    expected_duration = f"{minutes}分{seconds}秒" if minutes else f"{seconds}秒"
    duration_matches = list(_DURATION_CLAUSE_RE.finditer(text))
    if _DURATION_KEYWORD_RE.search(text) and not duration_matches:
        return True
    for match in duration_matches:
        actual_duration = _DURATION_PREFIX_RE.sub("", match.group(0)).strip()
        if actual_duration != expected_duration:
            return True
    without_duration = _DURATION_CLAUSE_RE.sub("", text)
    return any(number not in allowed for number in re.findall(r"\d+", without_duration))


def _safe_summary(summary: str, facts: RunFacts) -> str:
    """Accept fluent AI headlines only when their status semantics are safe."""

    summary = summary.strip()
    if not summary or len(summary) > 120:
        return deterministic_summary(facts)
    required = {
        "completed": "完成",
        "partial_success": "部分完成",
        "failed": "失败",
        "in_progress": "进行",
        "stalled": "卡住",
        "unknown": "未确认",
    }.get(facts.overall_status)
    if required and required not in summary:
        return deterministic_summary(facts)
    if _SUMMARY_CONTRADICTION_RE.search(summary):
        return deterministic_summary(facts)
    if _has_unobserved_reward_claim(summary, facts):
        return deterministic_summary(facts)
    if _has_unobserved_summary_numbers(summary, facts):
        return deterministic_summary(facts)
    if facts.overall_status == "completed" and any(
        marker in summary
        for marker in (
            "失败",
            "未领取",
            "未确认",
            "未完成",
            "未达成",
            "未成功",
            "部分完成",
        )
    ):
        return deterministic_summary(facts)
    if facts.overall_status == "failed" and any(
        marker in summary
        for marker in (
            "未失败",
            "成功",
            "已完成",
            "全部完成",
            "完全完成",
            "已领取",
            "领取成功",
        )
    ):
        return deterministic_summary(facts)
    if facts.overall_status == "partial_success" and any(
        marker in summary
        for marker in ("成功", "全部完成", "完全完成", "本轮已完成")
    ):
        return deterministic_summary(facts)
    if facts.overall_status in {"unknown", "in_progress", "stalled"} and any(
        marker in summary
        for marker in ("成功", "已完成", "全部完成", "奖励", "领取成功")
    ):
        return deterministic_summary(facts)
    return summary


def build_fallback_narrative(
    facts: RunFacts,
    *,
    token_usage: dict[str, Any] | None = None,
) -> NarrativeReport:
    return NarrativeReport(
        summary=str(redact_sensitive_data(deterministic_summary(facts))),
        daily=[str(redact_sensitive_data(item.text)) for item in facts.daily],
        weekly=[str(redact_sensitive_data(item.text)) for item in facts.weekly],
        followup=[str(redact_sensitive_data(item.text)) for item in facts.followup],
        issues=[str(redact_sensitive_data(item.text)) for item in facts.issues],
        token_usage=token_usage or TokenUsage().to_dict(),
    )


def _validate_wording(
    data: Any,
    facts: RunFacts,
) -> tuple[str, dict[str, str]]:
    if not isinstance(data, dict) or not isinstance(data.get("summary"), str):
        raise TypeError("AI response has no summary")
    if "邮件" in data["summary"]:
        raise ValueError("AI summary mentioned excluded mail content")
    wording = data.get("wording")
    if not isinstance(wording, dict):
        raise TypeError("AI response has no wording object")
    expected = {item.item_id: item for item in _all_items(facts)}
    if set(wording) != set(expected):
        raise ValueError("AI response changed the fact id set")

    validated: dict[str, str] = {}
    required_phrases = {
        "daily-activity-reward": ("领取每日活跃度奖励", "已确认100%"),
        "daily-activity-verified": ("每日活跃度已确认达到100", "奖励状态已结算"),
        "daily-activity-claim-action": ("每日活跃度", "已执行奖励领取操作", "未从日志确认"),
        "daily-activity-progress": ("每日活跃度当前", "仅记录取证结果"),
        "daily-activity-unverified": ("每日活跃度奖励未确认",),
        "daily-activity-capability-gap": ("最多可达", "剩余任务"),
        "run-failure": ("主流程失败",),
        "echo-absorption-incomplete": ("声骸吸收目标仅完成",),
        "weekly-garden-incomplete": ("幻梦游园本轮未确认完成",),
        "nightmare-nest-travel-unconfirmed": ("梦魇巢穴传送未确认",),
        "nightmare-nest-echo": ("梦魇巢穴", "吸收声骸"),
        "battle-pass": ("先约电台", "已执行奖励领取操作"),
        "weekly-garden": ("完成幻梦游园本周目标",),
        "echo-picked": ("吸收声骸",),
    }
    for item_id, item in expected.items():
        value = wording[item_id]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"AI wording is invalid for {item_id}")
        value = value.strip()
        if "邮件" in value:
            raise ValueError("AI response mentioned excluded mail content")
        if (
            item_id.startswith(_DAILY_ACTIVITY_NEGATIVE_IDS)
            and _DAILY_ACTIVITY_CONTRADICTION_RE.search(value)
        ):
            # A negative fact cannot be softened by appending a contradictory
            # success clause (for example “未确认，但奖励已领取”).
            value = item.text
        anchors = list(required_phrases.get(item_id, ()))
        if item_id.startswith(
            (
                "daily-activity-unsupported-",
                "daily-activity-unavailable-",
                "daily-activity-unknown-",
            )
        ):
            anchors.append("每日活跃任务未完成")
        anchors.extend(re.findall(r"(?:讨伐强敌|无音区)第\d+项", item.text))
        if any(anchor not in value for anchor in anchors):
            # Preserve the deterministic fact instead of accepting an AI
            # implication that is stronger or weaker than the observed action.
            value = item.text
        for number in re.findall(r"\d+", item.text):
            if number not in value:
                value = item.text
                break
        expected_numbers = set(re.findall(r"\d+", item.text))
        actual_numbers = set(re.findall(r"\d+", value))
        if not actual_numbers.issubset(expected_numbers):
            # The wording layer may polish punctuation, but it must not add
            # a score, count, index or reward total absent from program facts.
            value = item.text
        if _has_unobserved_reward_claim(value, facts):
            # Keep the wording layer from inventing drops, characters,
            # weapons, materials, or extra rewards that the parser did not
            # observe.  Explicit Markdown context remains an allowed suffix.
            value = item.text
        validated[item_id] = value
    return data["summary"].strip(), validated


def summarize_with_ai(facts: RunFacts) -> NarrativeReport:
    api_key = get_secret("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")
    client = OpenAI(
        api_key=api_key,
        base_url=get_secret("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL,
        timeout=45,
        max_retries=1,
    )
    model = get_secret("DEEPSEEK_MODEL") or DEFAULT_MODEL
    payload = {
        "agent_schema_version": "game-report-agent.v1",
        "game": "wuwa",
        "status": facts.overall_status,
        "duration_seconds": facts.duration_seconds,
        "facts": {
            "daily_activity": facts.daily_activity,
            "daily": [item.__dict__ for item in facts.daily],
            "weekly": [item.__dict__ for item in facts.weekly],
            "followup": [item.__dict__ for item in facts.followup],
            "issues": [item.__dict__ for item in facts.issues],
        },
        "required_fact_ids": [item.item_id for item in _all_items(facts)],
        "fact_contract": "wording keys must exactly equal required_fact_ids",
        "evidence": facts.evidence,
        "user_markdown": facts.user_context,
    }
    payload = redact_sensitive_data(payload)
    response = client.chat.completions.create(
        model=model,
        messages=compose_report_messages(payload),
        stream=False,
        reasoning_effort="high",
        extra_body={"thinking": {"type": "enabled"}},
        response_format={"type": "json_object"},
        max_tokens=AI_MAX_TOKENS,
    )
    usage = token_usage_from_response(response, model=model)
    try:
        content = response.choices[0].message.content
        if not content:
            raise ValueError("DeepSeek returned empty content")
        data = json.loads(content)
        summary, wording = _validate_wording(data, facts)
        analysis = validate_diagnostics(data.get("analysis"), facts.evidence)
    except Exception as exc:
        raise AgentResponseError(str(exc), token_usage=usage.to_dict()) from exc
    log.info(
        "DeepSeek Wuwa report usage: input_tokens=%s output_tokens=%s "
        "total_tokens=%s ratio=%s available=%s",
        usage.input_tokens,
        usage.output_tokens,
        usage.total_tokens,
        usage.output_input_ratio,
        usage.available,
    )

    def render(items: list[ReportItem]) -> list[str]:
        return [wording[item.item_id] for item in items]

    summary = _safe_summary(summary, facts)
    if facts.overall_status == "completed" and not facts.issues:
        # A clean deterministic success is the notification boundary.  The
        # Agent may still notice optional panel capabilities, but those did
        # not affect this run and must not become user-visible anomalies.
        analysis = {}
    elif not diagnostics_match_status(analysis, facts.overall_status):
        analysis = {}
    return NarrativeReport(
        summary=summary,
        daily=render(facts.daily),
        weekly=render(facts.weekly),
        followup=render(facts.followup),
        issues=render(facts.issues),
        analysis=analysis,
        token_usage=usage.to_dict(),
    )


def summarize_report(facts: RunFacts) -> tuple[NarrativeReport, bool]:
    try:
        return summarize_with_ai(facts), True
    except AgentResponseError as exc:
        log.warning("AI response invalid; preserving usage and using fallback: %s", exc)
        return build_fallback_narrative(facts, token_usage=exc.token_usage), False
    except Exception as exc:  # noqa: BLE001 - AI is an optional wording layer
        log.warning("AI summary unavailable; using deterministic wording: %s", exc)
        return build_fallback_narrative(facts), False
