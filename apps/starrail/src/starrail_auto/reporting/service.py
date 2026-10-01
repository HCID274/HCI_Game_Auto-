"""Turn one finished M7A run into the final Feishu card and a local archive."""

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from game_automation_core.reporting.archive import write_json_archive
from game_automation_core.reporting.feishu import card_text
from game_automation_core.reporting.redact import redact_sensitive_data
from game_automation_core.reporting.report import GameReport

from starrail_auto.integrations.feishu import send_card
from starrail_auto.m7a.power_plan import load_power_plan_remaining
from starrail_auto.reporting.models import RunReport
from starrail_auto.reporting.parser import parse_m7a_run
from starrail_auto.reporting.reminders import format_active_reminders
from starrail_auto.reporting.report import build_report
from starrail_auto.reporting.training_plan import (
    load_training_plan,
    reconcile_training_plan,
)
from starrail_auto.settings import REPORTS_DIR

TIMESTAMP_PATTERN = re.compile(r"^(?P<value>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})")

log = logging.getLogger(__name__)


def read_log_slice(path: Path, offset: int, *, until: datetime | None = None) -> str:
    """Read this run's bytes; ``until`` cuts off later same-day runs on replay."""
    if not path.exists():
        return ""
    data = path.read_bytes()
    content = data[offset if len(data) >= offset else 0 :].decode("utf-8", errors="replace")
    if until is None:
        return content
    selected: list[str] = []
    for line in content.splitlines():
        match = TIMESTAMP_PATTERN.match(line)
        if match and datetime.strptime(match.group("value"), "%Y-%m-%d %H:%M:%S,%f") > until:
            break
        selected.append(line)
    return "\n".join(selected)


def build_main_report(
    content: str,
    *,
    exit_code: int,
    stage: str,
    finished_at: datetime,
    duration_seconds: int | None,
    persist_plan: bool = True,
) -> tuple[RunReport, GameReport]:
    run = parse_m7a_run(
        content,
        now=finished_at,
        training_goals=load_training_plan().goals,
        run_stage=stage,
        force_failed=exit_code != 0,
        power_plan_remaining=load_power_plan_remaining(),
    )
    plan = reconcile_training_plan(
        run.stamina_runs,
        completed_at=finished_at,
        persist=persist_plan,
    )
    report = build_report(
        run,
        plan=plan,
        reminders=format_active_reminders(finished_at.date()),
        finished_at=finished_at,
        duration_seconds=duration_seconds,
    )
    return run, report


def report_main_run(
    *,
    log_path: Path | None,
    offset: int,
    exit_code: int,
    stage: str,
    retries: int,
    duration_seconds: int | None = None,
) -> Path:
    """Send exactly one final card without changing the run result."""
    finished_at = datetime.now()
    content = read_log_slice(log_path, offset) if log_path is not None else ""
    run, report = build_main_report(
        content,
        exit_code=exit_code,
        stage=stage,
        finished_at=finished_at,
        duration_seconds=duration_seconds,
    )
    card = report.to_card()
    sent = send_card(card)
    stem = f"{log_path.stem}_{offset}" if log_path is not None else f"{finished_at:%Y-%m-%d}_preflight"
    archive = write_json_archive(
        REPORTS_DIR / f"{stem}.json",
        {
            "source": {"path": str(log_path) if log_path else None, "offset": offset},
            "exit_code": exit_code,
            "stage": stage,
            "retries": retries,
            "sent": sent,
            "facts": redact_sensitive_data(run.to_dict()),
            "report": report.to_dict(),
            "feishu_card": card,
        },
    )
    log.info("final report handled: sent=%s status=%s archive=%s", sent, report.status, archive)
    return archive


def preview_archived_run(name: str = "latest") -> tuple[Path, str]:
    """Rebuild the card of an archived run without sending or touching user plans."""
    if name == "latest":
        candidates = sorted(
            (path for path in REPORTS_DIR.glob("*.json") if not path.name.endswith(".preview.json")),
            key=lambda path: path.stat().st_mtime,
        )
        if not candidates:
            raise SystemExit("no archived Star Rail report was found")
        archive_path = candidates[-1]
    else:
        archive_path = REPORTS_DIR / f"{name}.json"
    if not archive_path.is_file():
        raise SystemExit(f"archived report not found: {archive_path}")
    data = json.loads(archive_path.read_text(encoding="utf-8"))
    source = data.get("source") or {}
    facts = data.get("facts") or {}
    last_log_at = facts.get("last_log_at")
    content = ""
    if source.get("path"):
        content = read_log_slice(
            Path(source["path"]),
            int(source.get("offset") or 0),
            until=datetime.fromisoformat(last_log_at) if last_log_at else None,
        )
    # 旧归档没有退出码和完成时间：以当时程序判定的整体状态还原成功/失败，
    # 以归档文件的写入时间还原汇报时刻（卡住判定依赖它）。
    exit_code = int(data.get("exit_code", 0 if facts.get("overall_status") == "completed" else 1))
    stage = str(data.get("stage", facts.get("run_stage", "")))
    archived = data.get("report") or {}
    finished_at = (
        datetime.fromisoformat(archived["finished_at"])
        if archived.get("finished_at")
        else datetime.fromtimestamp(archive_path.stat().st_mtime)
    )
    _, report = build_main_report(
        content,
        exit_code=exit_code,
        stage=stage,
        finished_at=finished_at,
        duration_seconds=archived.get("duration_seconds"),
        persist_plan=False,
    )
    card = report.to_card()
    preview = write_json_archive(
        archive_path.with_name(f"{archive_path.stem}.preview.json"),
        {"source": source, "preview": True, "report": report.to_dict(), "feishu_card": card},
    )
    return preview, card_text(card)


def send_short_report(
    *,
    game: str,
    problems: list[str],
    status: str | None = None,
    duration_seconds: int | None = None,
) -> bool:
    """Card for paths that have no M7A log to describe; no problems means success.

    兜底路径上调用，任何异常都只记日志，不能改变任务退出码。
    """
    try:
        report = GameReport(
            game=game,
            status=status or ("failed" if problems else "completed"),
            finished_at=datetime.now(),
            problems=problems,
            duration_seconds=duration_seconds,
        )
        return send_card(report.to_card())
    except Exception:
        log.exception("short report failed")
        return False
