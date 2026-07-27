"""DeepSeek wording over immutable, program-derived report facts."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from openai import OpenAI

from wuwa_auto.reporting.models import NarrativeReport, ReportItem, RunFacts
from wuwa_auto.reporting.parser import deterministic_summary
from wuwa_auto.settings import get_secret

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
log = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是鸣潮自动化日报编辑。程序已经决定了所有事实、分类和完成状态。
你只能精简措辞，以及依据用户Markdown中明确存在的内容补充Boss名称、角色和养成用途；“待填写”不算有效信息。
不得新增、删除、合并或移动事实项；不得提及邮件；不得把检查、跳过或早已完成写成本轮完成。
必须保留“讨伐强敌第N项”“无音区第N项”等原始编号，补充信息只能附加在其后。
只返回JSON：{"summary":"一句话", "wording":{"事实id":"改写文字"}}。
wording必须包含输入中的全部事实id，且不能出现额外id。"""


def _all_items(facts: RunFacts) -> list[ReportItem]:
    return [*facts.daily, *facts.weekly, *facts.followup, *facts.issues]


def build_fallback_narrative(facts: RunFacts) -> NarrativeReport:
    return NarrativeReport(
        summary=deterministic_summary(facts),
        daily=[item.text for item in facts.daily],
        weekly=[item.text for item in facts.weekly],
        followup=[item.text for item in facts.followup],
        issues=[item.text for item in facts.issues],
    )


def _validate_wording(
    data: Any,
    facts: RunFacts,
) -> tuple[str, dict[str, str]]:
    if not isinstance(data, dict) or not isinstance(data.get("summary"), str):
        raise ValueError("AI response has no summary")
    if "邮件" in data["summary"]:
        raise ValueError("AI summary mentioned excluded mail content")
    wording = data.get("wording")
    if not isinstance(wording, dict):
        raise ValueError("AI response has no wording object")
    expected = {item.item_id: item for item in _all_items(facts)}
    if set(wording) != set(expected):
        raise ValueError("AI response changed the fact id set")

    validated: dict[str, str] = {}
    required_phrases = {
        "daily-activity-reward": ("领取每日活跃度奖励",),
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
        anchors = list(required_phrases.get(item_id, ()))
        for anchor in re.findall(r"(?:讨伐强敌|无音区)第\d+项", item.text):
            anchors.append(anchor)
        if any(anchor not in value for anchor in anchors):
            # Preserve the deterministic fact instead of accepting an AI
            # implication that is stronger or weaker than the observed action.
            value = item.text
        for number in re.findall(r"\d+", item.text):
            if number not in value:
                value = item.text
                break
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
    payload = {
        "status": facts.overall_status,
        "duration_seconds": facts.duration_seconds,
        "facts": {
            "daily": [item.__dict__ for item in facts.daily],
            "weekly": [item.__dict__ for item in facts.weekly],
            "followup": [item.__dict__ for item in facts.followup],
            "issues": [item.__dict__ for item in facts.issues],
        },
        "user_markdown": facts.user_context,
    }
    response = client.chat.completions.create(
        model=get_secret("DEEPSEEK_MODEL") or DEFAULT_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False),
            },
        ],
        stream=False,
        reasoning_effort="high",
        extra_body={"thinking": {"type": "enabled"}},
        response_format={"type": "json_object"},
        max_tokens=2048,
    )
    content = response.choices[0].message.content
    if not content:
        raise ValueError("DeepSeek returned empty content")
    summary, wording = _validate_wording(json.loads(content), facts)

    def render(items: list[ReportItem]) -> list[str]:
        return [wording[item.item_id] for item in items]

    # Keep the headline deterministic; AI is only allowed to enrich item wording.
    return NarrativeReport(
        summary=deterministic_summary(facts),
        daily=render(facts.daily),
        weekly=render(facts.weekly),
        followup=render(facts.followup),
        issues=render(facts.issues),
    )


def summarize_report(facts: RunFacts) -> tuple[NarrativeReport, bool]:
    try:
        return summarize_with_ai(facts), True
    except Exception as exc:
        log.warning("AI summary unavailable; using deterministic wording: %s", exc)
        return build_fallback_narrative(facts), False
