"""把鸣潮的运行事实翻译成日报：日常、周常、讨伐各一组任务行，异常排最前。"""

from __future__ import annotations

from datetime import datetime

from game_automation_core.reporting.report import FAILED, GameReport, settle_status

from wuwa_auto.reporting.models import RunFacts

GAME_LABELS = {
    "weekly_garden": "鸣潮周常",
    "farm_echo": "鸣潮讨伐",
    "farm_echo_confirmed_retry": "鸣潮讨伐",
}


def build_report(facts: RunFacts, *, finished_at: datetime) -> GameReport:
    daily = [item.line for item in facts.daily]
    if facts.daily_ok is False and not facts.daily_finished:
        daily.append(f"{FAILED} 日常任务：{'中途停止' if daily else '没完成'}")
    tasks = [
        *daily,
        *(item.line for item in facts.weekly),
        *(item.line for item in facts.boss),
    ]
    return GameReport(
        game=GAME_LABELS.get(facts.workflow_task, "鸣潮"),
        status=settle_status(facts.overall_status, tasks, facts.issues),
        finished_at=finished_at,
        tasks=tasks,
        problems=facts.issues,
        duration_seconds=facts.duration_seconds or None,
    )
