"""Wait for the M7A run boundary, summarize it, and send one Feishu card."""

import logging
import time
from dataclasses import asdict
from datetime import datetime, timedelta
from datetime import time as clock_time
from pathlib import Path
from typing import Callable

from game_automation_core.reporting.archive import write_json_archive

from starrail_auto.integrations.feishu import send_starrail_report_card
from starrail_auto.reporting.models import RunReport
from starrail_auto.reporting.parser import (
    DAILY_INCOMPLETE_MARKER,
    RUN_STOP_MARKER,
    parse_m7a_run,
)
from starrail_auto.reporting.reminders import format_active_reminders
from starrail_auto.reporting.summarizer import summarize_report
from starrail_auto.reporting.training_plan import reconcile_training_plan
from starrail_auto.reporting.user_context import load_reporting_context
from starrail_auto.settings import REPORTS_DIR

REPORT_ARCHIVE_DIR = REPORTS_DIR
REPORT_POLL_SECONDS = 5
DAILY_CUTOFF = clock_time(hour=8)
MANUAL_RUN_WINDOW = timedelta(hours=2)

log = logging.getLogger(__name__)


def read_log_since(path: Path, offset: int) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as handle:
        current_size = path.stat().st_size
        handle.seek(offset if current_size >= offset else 0)
        return handle.read().decode("utf-8", errors="replace")


def report_cutoff(started_at: datetime) -> datetime:
    """Use 08:00 for the scheduled run and two hours for later manual reruns."""
    scheduled_cutoff = datetime.combine(started_at.date(), DAILY_CUTOFF)
    if started_at < scheduled_cutoff:
        return scheduled_cutoff
    return started_at + MANUAL_RUN_WINDOW


def wait_for_report_boundary(
    path: Path,
    offset: int,
    *,
    started_at: datetime,
    now_fn: Callable[[], datetime] = datetime.now,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> tuple[str, bool, datetime]:
    cutoff = report_cutoff(started_at)
    while True:
        content = read_log_since(path, offset)
        if RUN_STOP_MARKER in content:
            return content, False, now_fn()

        now = now_fn()
        if now >= cutoff:
            return content, True, now
        sleep_fn(min(REPORT_POLL_SECONDS, max(0.0, (cutoff - now).total_seconds())))


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
    started_at: datetime,
    exit_code: int,
    stage: str,
    retries: int,
) -> None:
    """Send exactly one final main-run report without changing the run result."""
    preferences = load_reporting_context()
    content = ""
    cutoff_reached = False
    report_time = datetime.now()

    if log_path is not None:
        initial_content = read_log_since(log_path, offset)
        should_wait_for_m7a = exit_code == 0 or DAILY_INCOMPLETE_MARKER in initial_content
        if should_wait_for_m7a:
            content, cutoff_reached, report_time = wait_for_report_boundary(
                log_path,
                offset,
                started_at=started_at,
            )
        else:
            content = initial_content

    report = parse_m7a_run(
        content,
        now=report_time,
        preferences=preferences,
        run_stage=stage,
        retries=retries,
        force_failed=(exit_code != 0),
    )
    training_plan = reconcile_training_plan(
        report.stamina_runs,
        completed_at=report_time,
    )
    report.custom_context["training_plan"] = training_plan.to_context()
    if cutoff_reached and not report.stopped_normally and report.overall_status == "completed":
        # The daily objective is complete, but a later M7A task is still active.
        if report.current_reason.startswith("日志仍在更新"):
            report.overall_status = "in_progress"

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
        "final report handled: sent=%s ai_used=%s cutoff=%s status=%s",
        sent,
        ai_used,
        cutoff_reached,
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
