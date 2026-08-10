"""DeepSeek wording over immutable, program-derived report facts."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any

from game_automation_core.reporting.agent import (
    AgentResponseError,
    TokenUsage,
    redact_sensitive_data,
    token_usage_from_response,
)
from openai import OpenAI

from wuwa_auto.reporting.models import NarrativeReport, RunFacts
from wuwa_auto.reporting.parser import deterministic_summary
from wuwa_auto.reporting.prompting import compose_report_messages
from wuwa_auto.settings import get_secret

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
AI_READ_TIMEOUT_SECONDS = 180
AI_SUCCESS_MAX_TOKENS = 2048
AI_DIAGNOSTIC_MAX_TOKENS = 4096
log = logging.getLogger(__name__)
def _safe_summary(summary: str, facts: RunFacts) -> str:
    """Keep one minimal guard: the headline cannot reverse the run status."""

    summary = summary.strip()
    if not summary or len(summary) > 160 or "邮件" in summary:
        return deterministic_summary(facts)
    if facts.overall_status == "completed":
        if "完成" not in summary or any(
            marker in summary for marker in ("失败", "未确认", "未完成", "部分完成")
        ):
            return deterministic_summary(facts)
    elif facts.overall_status == "partial_success":
        if "部分完成" not in summary:
            return deterministic_summary(facts)
    elif facts.overall_status == "failed":
        if "未失败" in summary or not any(
            marker in summary for marker in ("失败", "中止", "未成功", "未完成")
        ):
            return deterministic_summary(facts)
    else:
        required = {
            "in_progress": "进行",
            "stalled": "卡",
            "unknown": "未确认",
        }.get(facts.overall_status)
        if required and required not in summary:
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


def _string_list(value: Any, *, limit: int = 12) -> list[str]:
    if not isinstance(value, list):
        raise TypeError("AI report section is not a list")
    result: list[str] = []
    for item in value[:limit]:
        if not isinstance(item, str):
            raise TypeError("AI report item is not text")
        text = item.strip()[:360]
        if text and "邮件" not in text:
            result.append(text)
    return result


def _parse_agent_report(
    data: Any,
    facts: RunFacts,
    *,
    token_usage: dict[str, Any],
) -> NarrativeReport:
    """Apply only the schema and business-status safety boundary."""

    if not isinstance(data, dict):
        raise TypeError("AI report is not a JSON object")
    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise TypeError("AI report has no summary")
    daily = _string_list(data.get("daily"))
    weekly = _string_list(data.get("weekly"))
    followup = _string_list(data.get("followup"))
    issues = _string_list(data.get("issues"))
    if facts.overall_status == "completed" and not facts.issues:
        issues = []
    elif facts.issues and not issues:
        # The model may write naturally, but it may not erase a failed step.
        issues = [item.text for item in facts.issues]
    return NarrativeReport(
        summary=_safe_summary(summary, facts),
        daily=daily,
        weekly=weekly,
        followup=followup,
        issues=issues,
        analysis={},
        token_usage=token_usage,
    )


def _consume_completion(response: Any, *, model: str) -> tuple[str, dict[str, Any]]:
    """Read either a real streamed response or a compact test response."""

    if hasattr(response, "choices"):
        content = response.choices[0].message.content or ""
        usage = token_usage_from_response(response, model=model).to_dict()
        return str(content), usage

    parts: list[str] = []
    usage_value: Any = None
    finish_reason = ""
    for chunk in response:
        if getattr(chunk, "usage", None) is not None:
            usage_value = chunk.usage
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            continue
        choice = choices[0]
        delta = getattr(choice, "delta", None)
        content = getattr(delta, "content", None) if delta is not None else None
        if content:
            parts.append(str(content))
        if getattr(choice, "finish_reason", None):
            finish_reason = str(choice.finish_reason)
    usage = token_usage_from_response(
        SimpleNamespace(usage=usage_value),
        model=model,
    ).to_dict()
    usage["finish_reason"] = finish_reason
    return "".join(parts), usage


def summarize_with_ai(facts: RunFacts) -> NarrativeReport:
    api_key = get_secret("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")
    client = OpenAI(
        api_key=api_key,
        base_url=get_secret("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL,
        timeout=AI_READ_TIMEOUT_SECONDS,
        max_retries=1,
    )
    model = get_secret("DEEPSEEK_MODEL") or DEFAULT_MODEL
    payload = {
        "agent_schema_version": "game-report-agent.v2",
        "game": "wuwa",
        "overall_status": facts.overall_status,
        "reason": facts.reason,
        "duration_seconds": facts.duration_seconds,
        "confirmed_results": {
            "daily_activity": facts.daily_activity,
            "daily": [item.text for item in facts.daily],
            "weekly": [item.text for item in facts.weekly],
            "followup": [item.text for item in facts.followup],
            "failed_steps": [item.text for item in facts.issues],
        },
        "ordered_log": facts.evidence,
    }
    payload = redact_sensitive_data(payload)
    response = client.chat.completions.create(
        model=model,
        messages=compose_report_messages(payload),
        stream=True,
        stream_options={"include_usage": True},
        reasoning_effort=("low" if facts.overall_status == "completed" else "high"),
        extra_body={"thinking": {"type": "enabled"}},
        response_format={"type": "json_object"},
        max_tokens=(
            AI_SUCCESS_MAX_TOKENS
            if facts.overall_status == "completed"
            else AI_DIAGNOSTIC_MAX_TOKENS
        ),
    )
    content, usage = _consume_completion(response, model=model)
    try:
        if not content:
            raise ValueError("DeepSeek returned empty content")
        data = json.loads(content)
        narrative = _parse_agent_report(data, facts, token_usage=usage)
    except Exception as exc:
        raise AgentResponseError(str(exc), token_usage=usage) from exc
    log.info(
        "DeepSeek Wuwa report usage: input_tokens=%s output_tokens=%s "
        "total_tokens=%s ratio=%s available=%s",
        usage.get("input_tokens"),
        usage.get("output_tokens"),
        usage.get("total_tokens"),
        usage.get("output_input_ratio"),
        usage.get("available"),
    )
    return narrative


def summarize_report(facts: RunFacts) -> tuple[NarrativeReport, bool]:
    try:
        return summarize_with_ai(facts), True
    except AgentResponseError as exc:
        log.warning("AI response invalid; preserving usage and using fallback: %s", exc)
        return build_fallback_narrative(facts, token_usage=exc.token_usage), False
    except Exception as exc:  # noqa: BLE001 - AI is an optional wording layer
        log.warning("AI summary unavailable; using deterministic wording: %s", exc)
        return build_fallback_narrative(facts), False
