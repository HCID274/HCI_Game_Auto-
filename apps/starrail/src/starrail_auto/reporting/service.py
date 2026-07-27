"""Summarize one completed M7A run and send one Feishu card."""

import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from game_automation_core.reporting.archive import write_json_archive

from starrail_auto.integrations.feishu import send_starrail_report_card
from starrail_auto.m7a.power_plan import load_power_plan_remaining
from starrail_auto.reporting.models import RunReport
from starrail_auto.reporting.parser import parse_m7a_run
from starrail_auto.reporting.reminders import format_active_reminders
from starrail_auto.reporting.summarizer import summarize_report
from starrail_auto.reporting.training_plan import reconcile_training_plan
from starrail_auto.reporting.user_context import load_reporting_context
from starrail_auto.settings import REPORTS_DIR

REPORT_ARCHIVE_DIR = REPORTS_DIR

log = logging.getLogger(__name__)


def read_log_since(path: Path, offset: int) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as handle:
        current_size = path.stat().st_size
        handle.seek(offset if current_size >= offset else 0)
        return handle.read().decode("utf-8", errors="replace")


def _archive_report(
    report: RunReport,
    *,
    narrative: object,
    ai_used: bool,
    log_path: Path,
    offset: int,
) -> None:
    REPORT_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = REPORT_ARCHIVE_DIR / f"{log_path.stem}_{offset}.json"
    payload = {
        "source": {"path": str(log_path), "offset": offset},
        "ai_used": ai_used,
        "facts": report.to_prompt_dict(),
        "narrative": asdict(narrative),
    }
    write_json_archive(archive_path, payload)
    log.info("report evidence archived: %s", archive_path)


def _title_for(report: RunReport, finished_at: datetime) -> tuple[str, str]:
    timestamp = finished_at.strftime("%m-%d %H:%M")
    if report.overall_status not in {"failed", "stalled"} and report.daily_status == "completed":
        return f"✅️ 星铁完成 {timestamp} 重试{report.retries}", (
            "orange" if report.overall_status in {"stalled", "in_progress"} else "green"
        )
    return f"❌️ 星铁失败 {timestamp} 重试{report.retries}", "red"


def report_main_run(
    *,
    log_path: Path | None,
    offset: int,
    exit_code: int,
    stage: str,
    retries: int,
) -> None:
    """Send exactly one final main-run report without changing the run result."""
    preferences = load_reporting_context()
    content = ""
    report_time = datetime.now()

    if log_path is not None:
        content = read_log_since(log_path, offset)

    report = parse_m7a_run(
        content,
        now=report_time,
        preferences=preferences,
        run_stage=stage,
        retries=retries,
        force_failed=(exit_code != 0),
        power_plan_remaining=load_power_plan_remaining(),
    )
    training_plan = reconcile_training_plan(
        report.stamina_runs,
        completed_at=report_time,
    )
    report.custom_context["training_plan"] = training_plan.to_context()
    narrative, ai_used = summarize_report(report)
    title, template = _title_for(report, report_time)
    reminders = format_active_reminders(report_time.date())
    sent = send_starrail_report_card(
        title=title,
        template=template,
        daily=narrative.daily,
        routine_tasks=narrative.routine_tasks,
        other_tasks=narrative.other_tasks,
        current_task=narrative.current_task,
        issues=narrative.issues,
        training_todos=narrative.training_todos,
        reminders=reminders,
    )
    log.info(
        "final report handled: sent=%s ai_used=%s status=%s",
        sent,
        ai_used,
        report.overall_status,
    )
    if log_path is not None:
        _archive_report(
            report,
            narrative=narrative,
            ai_used=ai_used,
            log_path=log_path,
            offset=offset,
        )
