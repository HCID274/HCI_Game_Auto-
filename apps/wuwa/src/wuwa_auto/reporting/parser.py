"""Extract only reportable facts from one OK-WW current-run log slice."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from game_automation_core.reporting.agent import build_evidence_bundle

from wuwa_auto.okww.daily_activity import (
    parse_activity_marker,
    parse_activity_panel_marker,
)
from wuwa_auto.okww.daily_capabilities import compare_activity_panel
from wuwa_auto.okww.logs import (
    count_farm_echo_absorptions,
    count_farm_echo_kill_confirmations,
)
from wuwa_auto.reporting.models import ReportItem, RunFacts
from wuwa_auto.reporting.noise import known_upstream_noise_lines
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
    daily_activity = parse_activity_marker(text)
    panel = parse_activity_panel_marker(text)
    if panel:
        labels = panel.get("labels") or []
        panel["comparison"] = compare_activity_panel(labels, log_text=text)
        daily_activity["panel"] = panel
    if daily_activity.get("state") == "unverified":
        reason = str(daily_activity.get("reason") or "状态未确认")
        issues.append(
            ReportItem(
                "daily-activity-unverified",
                f"每日活跃度奖励未确认：{reason}",
            )
        )
    comparison = panel.get("comparison") if panel else None
    if isinstance(comparison, dict):
        tasks = comparison.get("tasks") or []
        observed_points = daily_activity.get("points")
        if observed_points is None:
            observed_points = comparison.get("current_points_from_tasks")
        if observed_points is not None and daily_activity.get("state") != "verified":
            daily.append(
                ReportItem(
                    "daily-activity-progress",
                    f"每日活跃度当前{observed_points}/{comparison.get('target', 100)}，仅记录取证结果",
                )
            )
        # Once the post-claim total is verified at/above 100, the game has
        # settled the completed rows.  The panel may then intentionally show
        # other optional “前往” tasks (for example +40 daily quest); those
        # are not failures of today's reward and must not downgrade a real
        # success to “部分完成”.
        activity_verified = daily_activity.get("state") == "verified"
        if not activity_verified:
            for task in tasks:
                if not isinstance(task, dict) or task.get("completed"):
                    continue
                key = str(task.get("key") or "task")
                points_value = int(task.get("points") or 0)
                label = str(task.get("label") or key)
                state = str(task.get("state") or "unknown")
                reason = str(task.get("reason") or "")
                if state == "unsupported":
                    issues.append(
                        ReportItem(
                            f"daily-activity-unsupported-{key}",
                            f"每日活跃任务未完成：{label}(+{points_value})；{reason}",
                        )
                    )
                elif state == "unavailable":
                    issues.append(
                        ReportItem(
                            f"daily-activity-unavailable-{key}",
                            f"每日活跃任务未完成：{label}(+{points_value})；{reason}",
                        )
                    )
                elif state == "unknown":
                    issues.append(
                        ReportItem(
                            f"daily-activity-unknown-{key}",
                            f"每日活跃任务未完成：{label}(+{points_value})；"
                            "本地能力映射未覆盖，暂不判定可达上限",
                        )
                    )
            # An unknown visible objective makes a global upper-bound claim
            # unsound; report the objective itself instead of saying the
            # known subset can reach only 0/100.
            if (
                comparison.get("can_reach_target_now") is False
                and not comparison.get("unknown_tasks")
            ):
                reachable = comparison.get("reachable_now_points")
                issues.append(
                    ReportItem(
                        "daily-activity-capability-gap",
                        f"按本次面板与日志，现有可用 OK-WW 入口最多可达{reachable}/100；"
                        "剩余任务中存在上游未提供或本次资源不可用的项目",
                    )
                )
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
    claim_action = "claim daily reward via  coordinate" in text or bool(
        re.search(r"HOST_DAILY_ACTIVITY_CLAIM_ACTION .*\"host_clicks\":\s*[1-9]", text)
    )
    if daily_activity.get("state") == "verified" and not claim_action:
        verified_points = daily_activity.get("points")
        daily.append(
            ReportItem(
                "daily-activity-verified",
                f"每日活跃度已确认达到100（当前{verified_points}点，奖励状态已结算）"
                if verified_points is not None
                else "每日活跃度已确认达到100，奖励状态已结算",
            )
        )
    if claim_action:
        detected_points = daily_activity.get("points")
        if detected_points is None and points:
            detected_points = int(points[-1].group("points"))
        if daily_activity.get("state") == "verified":
            suffix = ""
            if detected_points is not None:
                suffix = f"（活跃度{detected_points}点，已确认100%）"
            daily.append(
                ReportItem(
                    "daily-activity-reward",
                    f"领取每日活跃度奖励{suffix}",
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
    structured_absorptions = result.config.get(
        "confirmed_farm_echo_absorption_count"
    )
    if structured_absorptions is not None:
        # Confirmed-retry starts the next challenge only after the defeated
        # boss's echo has been absorbed.  A structured 5/5 absorption result
        # therefore proves five completed boss clears even if an upstream UI
        # click marker was missed in the log.
        boss_runs = max(boss_runs, int(structured_absorptions))
    if recovery.get("triggered"):
        boss_runs = max(boss_runs, int(recovery.get("total_completed") or 0))
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
        recovery_attempts = int(recovery.get("recovery_attempts") or 0)
        total_completed = int(recovery.get("total_completed") or boss_runs)
        target = int(recovery.get("target_count") or total_completed)
        recovery_history = recovery.get("recoveries") or []
        realm_defeat_attempts = sum(
            1
            for item in recovery_history
            if isinstance(item, dict) and item.get("realm_defeat") is True
        )
        death_attempts = max(0, recovery_attempts - realm_defeat_attempts)

        def recovery_summary(action: str) -> str:
            events: list[str] = []
            if realm_defeat_attempts:
                events.append(f"讨伐副本团灭{realm_defeat_attempts}次")
            if death_attempts:
                events.append(f"讨伐中途倒地{death_attempts}次")
            events.append(action)
            return "，".join(events)

        if recovery_attempts and result.status == "success":
            if retry_completed:
                recovery_wording = recovery_summary(
                    f"已自动恢复并补吸收声骸{retry_completed}次"
                )
            else:
                recovery_wording = recovery_summary(
                    "已自动恢复并继续挑战"
                )
            followup.append(
                ReportItem(
                    "boss-death-recovered",
                    recovery_wording,
                )
            )
        elif recovery_attempts:
            first_safe = recovery.get("first_safe_recovery") is True
            final_safe = recovery.get("final_safe_recovery")
            safe = first_safe and final_safe is not False
            recovery_state = "已自动恢复并重试" if safe else "自动恢复未完成"
            issues.append(
                ReportItem(
                    "boss-death-recovery-incomplete",
                    f"{recovery_summary(recovery_state)}；"
                    "声骸累计吸收"
                    f"{total_completed}/{target}次",
                )
            )

    echo_picked = count_farm_echo_absorptions(text)
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
    if (
        cleanup_data
        and not cleanup_data.get("completed", False)
        and status == "completed"
    ):
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
        daily_activity=daily_activity,
        cleanup=cleanup_data,
        user_context=load_reporting_context(),
        evidence=build_evidence_bundle(
            game="wuwa",
            log_text=text,
            source=str(path),
            ignored_line_numbers=known_upstream_noise_lines(text),
        ),
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
        "in_progress": f"{subject}仍在进行",
        "stalled": f"{subject}疑似卡住",
    }.get(facts.overall_status, f"{subject}状态未确认")
    return f"{status}，耗时{_format_duration(facts.duration_seconds)}"
