"""Extract only reportable facts from one OK-WW current-run log slice."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from wuwa_auto.okww.logs import (
    count_farm_echo_absorptions,
    count_farm_echo_kill_confirmations,
)
from wuwa_auto.reporting.models import ReportItem, RunFacts
from wuwa_auto.reporting.user_context import load_reporting_context

DAILY_POINTS = re.compile(r"total daily points (?P<points>\d+)")
BOSS_TELEPORT = re.compile(r"Teleport to Boss Boss Challenge (?P<index>\d+)")


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
    tacet_attempts = text.count("TacetTask:start walk_to_treasure")
    tacet_unclaimed = text.count("TacetTask:is not claim treasure, restart challenge")
    tacet_runs = max(0, tacet_attempts - tacet_unclaimed)
    if tacet_runs:
        index = result.config.get("daily_farm_index")
        label = f"无音区第{index}项" if index else "无音区"
        # OK-WW v3.5.18 fixes one Tacet claim at 60 waveplates. Its
        # ``current stamina`` value is a must-use budget that may become
        # negative, not the final in-game balance, so subtraction is unsafe.
        details = f"{label}清剿{tacet_runs}场，消耗{tacet_runs * 60}结晶波片"
        daily.append(ReportItem("tacet-suppression", details))

    points = list(DAILY_POINTS.finditer(text))
    if "claim daily reward via  coordinate" in text:
        detected_points = int(points[-1].group("points")) if points else None
        if detected_points is not None and detected_points >= 100:
            daily.append(
                ReportItem(
                    "daily-activity-reward",
                    f"领取每日活跃度奖励（活跃度{detected_points}点）",
                )
            )
        else:
            daily.append(
                ReportItem(
                    "daily-activity-claim-action",
                    "每日活跃度：已执行奖励领取操作（最终活跃度未从日志确认）",
                )
            )

    nightmare_echoes = sum(
        text.count(marker)
        for marker in (
            "NightmareNestTask:Captured echo during combat, skipping search.",
            "NightmareNestTask:farm echo yolo find True",
            "NightmareNestTask:farm echo walk find true",
        )
    )
    if nightmare_echoes:
        daily.append(
            ReportItem(
                "nightmare-nest-echo",
                f"梦魇巢穴吸收声骸{nightmare_echoes}次",
            )
        )

    if _battle_pass_claim_branch_completed(text):
        daily.append(ReportItem("battle-pass", "先约电台：已执行奖励领取操作"))

    weekly: list[ReportItem] = []
    garden_completed = "乐园任务完成, 已达到上限" in text
    garden_started = (
        "weekly garden not completed, run GardenTask" in text
        or "GardenTask:garden end" in text
    )
    if garden_completed:
        weekly.append(ReportItem("weekly-garden", "完成幻梦游园本周目标"))
    elif garden_started and not garden_completed:
        issues.append(ReportItem("weekly-garden-incomplete", "幻梦游园本轮未确认完成"))

    followup: list[ReportItem] = []
    recovery = result.config.get("farm_echo_recovery") or {}
    boss_runs = count_farm_echo_kill_confirmations(text)
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

    if recovery.get("triggered"):
        retry_completed = int(recovery.get("retry_completed") or 0)
        recovery_attempts = int(recovery.get("recovery_attempts") or 1)
        total_completed = int(recovery.get("total_completed") or boss_runs)
        target = int(recovery.get("target_count") or total_completed)
        if result.status == "success":
            if retry_completed:
                recovery_wording = (
                    f"讨伐中途倒地{recovery_attempts}次，"
                    "已自动退本回血并补吸收声骸"
                    f"{retry_completed}次"
                )
            else:
                recovery_wording = (
                    f"讨伐中途倒地{recovery_attempts}次，已自动退本回血"
                )
            followup.append(
                ReportItem(
                    "boss-death-recovered",
                    recovery_wording,
                )
            )
        else:
            first_safe = recovery.get("first_safe_recovery") is True
            final_safe = recovery.get("final_safe_recovery")
            safe = first_safe and final_safe is not False
            recovery_state = "已退本回血" if safe else "安全恢复未完成"
            issues.append(
                ReportItem(
                    "boss-death-recovery-incomplete",
                    f"讨伐中途倒地{recovery_attempts}次，{recovery_state}；"
                    "声骸累计吸收"
                    f"{total_completed}/{target}次",
                )
            )

    echo_picked = count_farm_echo_absorptions(text)
    structured_absorptions = result.config.get(
        "confirmed_farm_echo_absorption_count"
    )
    if structured_absorptions is not None:
        echo_picked = int(structured_absorptions)
    if recovery.get("triggered"):
        echo_picked = int(recovery.get("total_completed") or echo_picked)
    if echo_picked:
        followup.append(ReportItem("echo-picked", f"吸收声骸{echo_picked}次"))

    absorption_target = result.config.get("farm_echo_absorption_target")
    if absorption_target is None and (
        result.config.get("workflow_task") == "farm_echo_confirmed_retry"
    ):
        absorption_target = result.config.get("target_count")
    if (
        absorption_target is not None
        and echo_picked < int(absorption_target)
    ):
        issues.append(
            ReportItem(
                "echo-absorption-incomplete",
                f"声骸吸收目标仅完成{echo_picked}/{int(absorption_target)}次",
            )
        )

    optional_failures = {
        "GardenTask Failed": "幻梦游园执行异常",
        "NightmareNestTask Failed": "梦魇声骸任务执行异常",
    }
    for marker, wording in optional_failures.items():
        if marker in text and not any(item.text == wording for item in issues):
            issues.append(ReportItem(f"optional-{marker}", wording))

    host_unconfirmed_travel = text.count("HOST_NIGHTMARE_TRAVEL_NOT_CONFIRMED")
    legacy_unconfirmed_travel = text.count(
        "NightmareNestTask:nightmare nest unreachable, skip this run"
    )
    unconfirmed_travel = host_unconfirmed_travel or legacy_unconfirmed_travel
    if unconfirmed_travel:
        issues.append(
            ReportItem(
                "nightmare-nest-travel-unconfirmed",
                f"梦魇巢穴传送未确认生效，已跳过{unconfirmed_travel}处",
            )
        )

    for index, issue in enumerate(cleanup_data.get("issues", []), start=1):
        issues.append(ReportItem(f"cleanup-{index}", str(issue)))

    if result.status == "success" and issues:
        status = "partial_success"
    elif result.status != "success":
        sequence = result.config.get("daily_sequence") or {}
        sequence_has_success = isinstance(sequence, dict) and any(
            sequence.get(key) == "success"
            for key in ("boss_status", "daily_status")
        )
        recovery_has_progress = recovery.get("triggered") and (
            int(recovery.get("total_completed") or 0) > 0
            or recovery.get("first_safe_recovery") is True
        )
        if sequence_has_success or recovery_has_progress:
            status = "partial_success"
    if cleanup_data and not cleanup_data.get("completed", False):
        if status == "completed":
            status = "partial_success"

    return RunFacts(
        overall_status=status,
        reason=result.reason,
        duration_seconds=result.duration_seconds,
        workflow_task=str(result.config.get("workflow_task", "daily")),
        daily=daily,
        weekly=weekly,
        followup=followup,
        issues=issues,
        cleanup=cleanup_data,
        user_context=load_reporting_context(),
    )


def deterministic_summary(facts: RunFacts) -> str:
    subject = {
        "weekly_garden": "鸣潮周常",
        "farm_echo": "鸣潮后续任务",
        "farm_echo_confirmed_retry": "鸣潮后续任务",
    }.get(facts.workflow_task, "鸣潮日常")
    status = {
        "completed": f"{subject}完成",
        "partial_success": f"{subject}部分完成",
        "failed": f"{subject}失败",
    }.get(facts.overall_status, f"{subject}状态未确认")
    return f"{status}，耗时{_format_duration(facts.duration_seconds)}"
