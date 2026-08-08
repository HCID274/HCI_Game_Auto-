"""Build, archive, preview and optionally send one final run report."""

from __future__ import annotations

import logging
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from game_automation_core.reporting.agent import diagnostic_lines, redact_sensitive_data
from game_automation_core.reporting.archive import write_json_archive

from wuwa_auto.integrations.feishu import build_report_card, send_report_card
from wuwa_auto.reporting.models import NarrativeReport, RunFacts
from wuwa_auto.reporting.parser import parse_run
from wuwa_auto.reporting.summarizer import summarize_report
from wuwa_auto.settings import REPORTS_DIR

log = logging.getLogger(__name__)


def _title(status: str, finished_at: str) -> tuple[str, str]:
    timestamp = datetime.fromisoformat(finished_at).strftime("%m-%d %H:%M")
    if status == "completed":
        return f"✅ 鸣潮完成 {timestamp}", "green"
    if status == "partial_success":
        return f"⚠️ 鸣潮部分完成 {timestamp}", "orange"
    return f"❌ 鸣潮失败 {timestamp}", "red"


def _redact_narrative(narrative: NarrativeReport) -> NarrativeReport:
    """Keep card, archive and preview text consistent with redacted evidence."""

    return replace(
        narrative,
        summary=str(redact_sensitive_data(narrative.summary)),
        daily=[str(redact_sensitive_data(item)) for item in narrative.daily],
        weekly=[str(redact_sensitive_data(item)) for item in narrative.weekly],
        followup=[str(redact_sensitive_data(item)) for item in narrative.followup],
        issues=[str(redact_sensitive_data(item)) for item in narrative.issues],
        analysis=redact_sensitive_data(narrative.analysis),
    )


def _should_show_agent_diagnostics(
    facts: RunFacts,
    narrative: NarrativeReport,
) -> bool:
    """Only expose AI diagnostics when program facts establish an impact."""

    return bool(narrative.analysis) and (
        facts.overall_status != "completed" or bool(facts.issues)
    )


def report_run(
    result: Any,
    cleanup: Any | None = None,
    *,
    allow_send: bool = True,
) -> Path:
    facts = parse_run(result, cleanup)
    narrative, ai_used = summarize_report(facts)
    narrative = _redact_narrative(narrative)
    title, template = _title(facts.overall_status, result.finished_at)
    if _should_show_agent_diagnostics(facts, narrative):
        narrative = type(narrative)(
            summary=narrative.summary,
            daily=narrative.daily,
            weekly=narrative.weekly,
            followup=narrative.followup,
            issues=[*narrative.issues, *diagnostic_lines(narrative.analysis)],
            analysis=narrative.analysis,
            token_usage=narrative.token_usage,
        )
    card = build_report_card(title=title, template=template, narrative=narrative)
    sent = send_report_card(card) if allow_send else False

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    suffix = ".json" if allow_send else ".preview.json"
    path = REPORTS_DIR / f"{result.run_id}{suffix}"
    write_json_archive(
        path,
        {
            "run_id": result.run_id,
            "ai_used": ai_used,
            "ai_token_usage": narrative.token_usage,
            "agent_analysis": redact_sensitive_data(narrative.analysis),
            "sent": sent,
            "preview": not allow_send,
            "facts": redact_sensitive_data(facts.to_dict()),
            "narrative": redact_sensitive_data(asdict(narrative)),
            "feishu_card": card,
        },
    )
    log.info("Wuwa report archived: %s", path)
    return path
