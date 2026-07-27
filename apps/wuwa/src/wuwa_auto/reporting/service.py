"""Build, archive, preview and optionally send one final run report."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from wuwa_auto.integrations.feishu import build_report_card, send_report_card
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


def report_run(
    result: Any,
    cleanup: Any | None = None,
    *,
    allow_send: bool = True,
) -> Path:
    facts = parse_run(result, cleanup)
    narrative, ai_used = summarize_report(facts)
    title, template = _title(facts.overall_status, result.finished_at)
    card = build_report_card(title=title, template=template, narrative=narrative)
    sent = send_report_card(card) if allow_send else False

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{result.run_id}.json"
    path.write_text(
        json.dumps(
            {
                "run_id": result.run_id,
                "ai_used": ai_used,
                "sent": sent,
                "facts": facts.to_dict(),
                "narrative": asdict(narrative),
                "feishu_card": card,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    log.info("Wuwa report archived: %s", path)
    return path
