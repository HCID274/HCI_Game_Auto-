"""Replay real historical M7A runs through DeepSeek and Feishu."""

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from feishu_notify import send_starrail_report_card
from reporting.ai_summarizer import summarize_report
from reporting.config import load_reporting_context
from reporting.log_parser import parse_m7a_run
from reporting.models import NarrativeReport, RunReport
from reporting.report_service import _title_for


M7A_LOG_DIR = Path(
    r"D:\2_Software\4_Games\StarRail\Auto\March7thAssistant_full\logs"
)
TIMESTAMP_PATTERN = re.compile(r"^(?P<value>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})")


@dataclass(frozen=True)
class HistoricalSample:
    name: str
    log_name: str
    offset: int
    ended_at: datetime
    stage: str
    force_failed: bool


@dataclass(frozen=True)
class PreparedSample:
    sample: HistoricalSample
    report: RunReport
    narrative: NarrativeReport
    title: str
    template: str


CORE_SAMPLES = (
    HistoricalSample(
        name="成功",
        log_name="2026-07-25.log",
        offset=414,
        ended_at=datetime(2026, 7, 25, 6, 8, 22),
        stage="",
        force_failed=False,
    ),
    HistoricalSample(
        name="失败",
        log_name="2026-07-17.log",
        offset=1410,
        ended_at=datetime(2026, 7, 17, 13, 44, 41),
        stage="每日实训未达标：截图失败，未找到游戏窗口",
        force_failed=True,
    ),
    HistoricalSample(
        name="超时",
        log_name="2026-07-17.log",
        offset=7137,
        ended_at=datetime(2026, 7, 17, 14, 19, 46),
        stage="旧版硬超时：差分宇宙仍在运行，但达到30分钟上限",
        force_failed=True,
    ),
)

ADDITIONAL_SUCCESS_SAMPLES = (
    HistoricalSample(
        name="07-22成功",
        log_name="2026-07-22.log",
        offset=0,
        ended_at=datetime(2026, 7, 22, 6, 11, 25, 377000),
        stage="",
        force_failed=False,
    ),
    HistoricalSample(
        name="07-21成功",
        log_name="2026-07-21.log",
        offset=0,
        ended_at=datetime(2026, 7, 21, 6, 9, 20, 369000),
        stage="",
        force_failed=False,
    ),
)

RANDOM_REVIEW_SAMPLES = (
    HistoricalSample(
        name="随机07-11成功",
        log_name="2026-07-11.log",
        offset=0,
        ended_at=datetime(2026, 7, 11, 6, 7, 36, 93000),
        stage="",
        force_failed=False,
    ),
    HistoricalSample(
        name="随机07-15成功",
        log_name="2026-07-15.log",
        offset=0,
        ended_at=datetime(2026, 7, 15, 6, 7, 29, 132000),
        stage="",
        force_failed=False,
    ),
    HistoricalSample(
        name="随机07-08成功",
        log_name="2026-07-08.log",
        offset=0,
        ended_at=datetime(2026, 7, 8, 6, 7, 50, 594000),
        stage="",
        force_failed=False,
    ),
)

SAMPLE_SETS = {
    "core": CORE_SAMPLES,
    "additional-success": ADDITIONAL_SUCCESS_SAMPLES,
    "random-review": RANDOM_REVIEW_SAMPLES,
}


def read_historical_run(sample: HistoricalSample) -> str:
    """Read one checkpoint range without including later same-day runs."""
    path = M7A_LOG_DIR / sample.log_name
    content = path.read_bytes()[sample.offset :].decode("utf-8", errors="replace")
    selected: list[str] = []
    for line in content.splitlines():
        match = TIMESTAMP_PATTERN.match(line)
        if match:
            timestamp = datetime.strptime(match.group("value"), "%Y-%m-%d %H:%M:%S,%f")
            if timestamp > sample.ended_at:
                break
        selected.append(line)
    return "\n".join(selected)


def prepare_sample(sample: HistoricalSample) -> PreparedSample:
    context = load_reporting_context()
    report = parse_m7a_run(
        read_historical_run(sample),
        now=sample.ended_at,
        preferences=context,
        run_stage=sample.stage,
        retries=0,
        force_failed=sample.force_failed,
    )
    narrative, ai_used = summarize_report(report)
    if not ai_used:
        raise RuntimeError(f"{sample.name}样本未使用 DeepSeek，停止发送")

    title, template = _title_for(report, sample.ended_at)
    title = f"{title}（历史{sample.name}测试）"
    return PreparedSample(sample, report, narrative, title, template)


def send_sample(prepared: PreparedSample) -> None:
    if not send_starrail_report_card(
        title=prepared.title,
        template=prepared.template,
        daily=prepared.narrative.daily,
        routine_tasks=prepared.narrative.routine_tasks,
        other_tasks=prepared.narrative.other_tasks,
        current_task=prepared.narrative.current_task,
        issues=prepared.narrative.issues,
        training_todos=prepared.narrative.training_todos,
    ):
        raise RuntimeError(f"{prepared.sample.name}样本飞书发送失败")


def print_sample(prepared: PreparedSample, *, sent: bool) -> None:
    action = "已发送" if sent else "仅验证"
    print(
        f"{prepared.sample.name}: {action}; ai_used=True; "
        f"overall={prepared.report.overall_status}; "
        f"daily={prepared.report.daily_status}; title={prepared.title}"
    )
    print(f"  每日实训: {prepared.narrative.daily}")
    for index, item in enumerate(prepared.narrative.routine_tasks, start=1):
        print(f"  日常 {index}. {item}")
    for index, item in enumerate(prepared.narrative.other_tasks, start=1):
        print(f"  后续 {index}. {item}")
    if prepared.narrative.current_task:
        print(f"  当前任务: {prepared.narrative.current_task}")
    for item in prepared.narrative.issues:
        print(f"  异常: {item}")
    for index, item in enumerate(prepared.narrative.training_todos, start=1):
        print(f"  养成待办 {index}. {item}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--send",
        action="store_true",
        help="send all validated historical cards to the configured Feishu bot",
    )
    parser.add_argument(
        "--set",
        choices=tuple(SAMPLE_SETS),
        default="core",
        dest="sample_set",
        help="historical sample group to replay",
    )
    args = parser.parse_args()
    prepared_samples = [
        prepare_sample(sample)
        for sample in SAMPLE_SETS[args.sample_set]
    ]
    if args.send:
        for prepared in prepared_samples:
            send_sample(prepared)
    for prepared in prepared_samples:
        print_sample(prepared, sent=args.send)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
