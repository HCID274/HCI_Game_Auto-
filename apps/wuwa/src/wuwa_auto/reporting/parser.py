"""Extract only reportable facts from one OK-WW current-run log slice."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from wuwa_auto.reporting.models import ReportItem, RunFacts
from wuwa_auto.reporting.user_context import load_reporting_context

DAILY_POINTS = re.compile(r"total daily points (?P<points>\d+)")
CURRENT_STAMINA = re.compile(r"current_stamina (?P<value>\d+)")
FINAL_STAMINA = re.compile(r"current stamina: (?P<value>\d+)")
BOSS_TELEPORT = re.compile(r"Teleport to Boss Boss Challenge (?P<index>\d+)")


def _last_int(pattern: re.Pattern[str], text: str) -> int | None:
    matches = list(pattern.finditer(text))
    return int(matches[-1].group("value" if "value" in pattern.groupindex else "points")) if matches else None


def _battle_pass_claim_branch_completed(text: str) -> bool:
    start = text.rfind("DailyTask:battle pass")
    if start < 0:
        return False
    tail = text[start:]
    boundaries = [
        marker for marker in (
            tail.find("current task check weekly garden"),
            tail.find("Daily task completed, start teleport"),
            tail.find("Daily Task Completed"),
        )
        if marker > 0
    ]
    if not boundaries:
        return False
    branch = tail[:min(boundaries)]
    return "can not battle pass" not in branch


def _format_duration(seconds: int) -> str:
    minutes, remainder = divmod(max(0, seconds), 60)
    if minutes:
        return f"{minutes}分{remainder}秒"
    return f"{remainder}秒"


def parse_run(result: Any, cleanup: Any | None = None) -> RunFacts:
    """Parse a run result without consulting logs from any earlier run."""
    path = Path(result.log_slice_path)
    text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    cleanup_data = cleanup.to_dict() if cleanup is not None else {}
    issues: list[ReportItem] = []

    status = "completed" if result.status == "success" else "failed"
    if result.status != "success":
        issues.append(ReportItem("run-failure", f"主流程失败：{result.reason}"))

    daily: list[ReportItem] = []
    stamina_values = [int(match.group("value")) for match in CURRENT_STAMINA.finditer(text)]
    final_stamina = _last_int(FINAL_STAMINA, text)
    tacet_runs = text.count("TacetTask:start walk_to_treasure")
    if tacet_runs:
        index = result.config.get("daily_farm_index")
        label = f"无音区第{index}项" if index else "无音区"
        details = f"{label}清剿{tacet_runs}场"
        if stamina_values and final_stamina is not None:
            consumed = stamina_values[0] - final_stamina
            if consumed > 0:
                details += f"，消耗{consumed}结晶波片"
        daily.append(ReportItem("tacet-suppression", details))

    points = list(DAILY_POINTS.finditer(text))
    if "claim daily reward via  coordinate" in text:
        suffix = f"（检测到{points[-1].group('points')}点）" if points else ""
        daily.append(
            ReportItem("daily-activity-reward", f"领取每日活跃度奖励{suffix}")
        )

    if _battle_pass_claim_branch_completed(text):
        daily.append(ReportItem("battle-pass", "先约电台：已执行奖励领取操作"))

    weekly: list[ReportItem] = []
    garden_started = "weekly garden not completed, run GardenTask" in text
    garden_completed = "乐园任务完成, 已达到上限" in text
    if garden_started and garden_completed:
        weekly.append(ReportItem("weekly-garden", "完成幻梦游园本周目标"))
    elif garden_started and not garden_completed:
        issues.append(ReportItem("weekly-garden-incomplete", "幻梦游园本轮未确认完成"))

    followup: list[ReportItem] = []
    boss_runs = text.count("FarmEchoTask:start wait in combat")
    if boss_runs:
        boss_index = result.config.get("boss_challenge_index")
        if boss_index is None:
            boss_index = result.config.get("which_boss_challenge")
        if boss_index is None:
            teleports = list(BOSS_TELEPORT.finditer(text))
            if teleports:
                # OK logs its internal zero-based list index; the GUI is one-based.
                boss_index = int(teleports[-1].group("index")) + 1
        label = f"讨伐强敌第{boss_index}项" if boss_index else "讨伐强敌"
        followup.append(ReportItem("boss-challenge", f"{label} {boss_runs}次"))

    echo_picked = sum(
        text.count(marker)
        for marker in (
            "farm echo on the face",
            "farm echo yolo find True",
            "farm echo walk_circle_find_echo True",
            "farm echo walk_find_echo True",
        )
    )
    if echo_picked:
        followup.append(ReportItem("echo-picked", f"吸收声骸{echo_picked}次"))

    optional_failures = {
        "GardenTask Failed": "幻梦游园执行异常",
        "NightmareNestTask Failed": "梦魇声骸任务执行异常",
    }
    for marker, wording in optional_failures.items():
        if marker in text and not any(item.text == wording for item in issues):
            issues.append(ReportItem(f"optional-{marker}", wording))

    for index, issue in enumerate(cleanup_data.get("issues", []), start=1):
        issues.append(ReportItem(f"cleanup-{index}", str(issue)))

    if result.status == "success" and issues:
        status = "partial_success"
    if cleanup_data and not cleanup_data.get("completed", False):
        status = "partial_success" if result.status == "success" else "failed"

    return RunFacts(
        overall_status=status,
        reason=result.reason,
        duration_seconds=result.duration_seconds,
        daily=daily,
        weekly=weekly,
        followup=followup,
        issues=issues,
        cleanup=cleanup_data,
        user_context=load_reporting_context(),
    )


def deterministic_summary(facts: RunFacts) -> str:
    status = {
        "completed": "鸣潮日常完成",
        "partial_success": "鸣潮日常部分完成",
        "failed": "鸣潮日常失败",
    }.get(facts.overall_status, "鸣潮日常状态未确认")
    return f"{status}，耗时{_format_duration(facts.duration_seconds)}"
