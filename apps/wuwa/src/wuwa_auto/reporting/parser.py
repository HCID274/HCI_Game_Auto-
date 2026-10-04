"""把一次 OK-WW 运行的日志切片和结构化结果解析成任务清单。

每个任务一个函数，只看本轮日志切片和 ``result.config``，不读其他运行的日志；
同一天多次运行的合并在 ``day_rollup`` 里做。只写本轮真正执行过的任务。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from game_automation_core.reporting.report import DONE, FAILED, WARN

from wuwa_auto.okww.daily_activity import (
    parse_activity_marker,
    parse_activity_panel_marker,
)
from wuwa_auto.okww.daily_capabilities import compare_activity_panel
from wuwa_auto.okww.logs import (
    count_farm_echo_absorptions,
    count_farm_echo_kill_confirmations,
    is_farm_echo_world_team_blocked,
)
from wuwa_auto.okww.tacet_skip import TACET_UNREACHABLE_MARKER
from wuwa_auto.reporting.models import ReportItem, RunFacts
from wuwa_auto.reporting.reasons import explain_failure

FARM_ECHO_WORKFLOWS = frozenset({"farm_echo", "farm_echo_confirmed_retry"})
# 日常任务行按游戏里的执行顺序排列；同日合并后也按这个顺序。
DAILY_ITEM_ORDER = ("tacet", "nightmare-nest", "daily-activity", "battle-pass")
DAILY_POINTS = re.compile(r"total daily points (?P<points>\d+)")
HOST_CLAIM_ACTION = re.compile(r'HOST_DAILY_ACTIVITY_CLAIM_ACTION .*"host_clicks":\s*[1-9]')
BOSS_TELEPORT = re.compile(r"Teleport to Boss Boss Challenge (?P<index>\d+)")
# 每次日常重试都会重撞同一个传送不了的巢穴，按巢穴去重计数（1004 曾报成 35 处）。
NIGHTMARE_UNREACHABLE = re.compile(
    r"(?:HOST_NIGHTMARE_TRAVEL_NOT_CONFIRMED target=|nightmare nest unreachable, skip this run: )(\S+)"
)
# OCR 面板标签会把进度和档位数字粘在任务名后面，例如“击败1次怒涛级敌人0/1”。
PANEL_PROGRESS_SUFFIX = re.compile(r"\s*\d+\s*/\s*\d+.*$")
NIGHTMARE_ECHO_MARKERS = (
    "NightmareNestTask:Captured echo during combat, skipping search.",
    "NightmareNestTask:farm echo yolo find True",
    "NightmareNestTask:farm echo walk find true",
)
# OK-WW v3.5.18 每次领取无音区奖励固定消耗 60 结晶波片；日志里的 current stamina
# 是“必须用掉的预算”，可能为负，不能拿来相减。
TACET_WAVEPLATES = 60
ACTIVITY_TARGET = 100
WORLD_TEAM_BLOCKED = (
    "讨伐没进入战斗：游戏可能停在剧情、特殊模式或单人队伍里，需要手动切回常规队伍"
)


def _phase_ok(result: Any, phase: str) -> bool | None:
    """读取讨伐（boss）或日常（daily）阶段是否成功；本轮没跑该阶段返回 None。"""

    sequence = result.config.get("daily_sequence")
    if isinstance(sequence, dict) and sequence.get(f"{phase}_status"):
        return sequence[f"{phase}_status"] == "success"
    workflow = str(result.config.get("workflow_task", "daily"))
    if (phase == "daily" and workflow == "daily") or (
        phase == "boss" and workflow in FARM_ECHO_WORKFLOWS
    ):
        return result.status == "success"
    return None


def _tacet(text: str, config: Mapping[str, Any]) -> list[ReportItem]:
    runs = text.count("TacetTask:start walk_to_treasure") - text.count(
        "TacetTask:is not claim treasure, restart challenge"
    )
    index = config.get("daily_farm_index")
    label = f"无音区第{index}项" if index else "无音区"
    if TACET_UNREACHABLE_MARKER in text:
        return [ReportItem("tacet", WARN, f"{label}：附近信标无法快速到达，已跳过，体力没花")]
    if runs <= 0:
        return []
    return [
        ReportItem(
            "tacet",
            DONE,
            f"{label}：清剿 {runs} 场，消耗 {runs * TACET_WAVEPLATES} 结晶波片",
        )
    ]


def _nightmare(text: str) -> list[ReportItem]:
    echoes = sum(text.count(marker) for marker in NIGHTMARE_ECHO_MARKERS)
    skipped = len(set(NIGHTMARE_UNREACHABLE.findall(text)))
    failed = "NightmareNestTask Failed" in text
    if not (echoes or skipped or failed):
        return []
    parts = [f"吸收声骸 {echoes} 次"] if echoes else []
    if skipped:
        parts.append(f"{skipped} 处没传送过去，已跳过")
    if failed:
        parts.append("中途出错")
    mark = DONE if not (skipped or failed) else WARN if echoes else FAILED
    return [ReportItem("nightmare-nest", mark, f"梦魇巢穴：{'，'.join(parts)}")]


def _panel_label(task: Mapping[str, Any]) -> str:
    raw = str(task.get("label") or task.get("key") or "未知任务")
    return PANEL_PROGRESS_SUFFIX.sub("", raw).strip(" :：") or raw


def _activity(text: str) -> tuple[list[ReportItem], list[str]]:
    """每日活跃度一行；没拿满时把面板上还差的任务列进异常。"""

    marker = parse_activity_marker(text)
    panel = parse_activity_panel_marker(text)
    comparison = (
        compare_activity_panel(panel.get("labels") or [], log_text=text) if panel else {}
    )
    verified = marker.get("state") == "verified"
    points = marker.get("points")
    claimed = "claim daily reward via  coordinate" in text or bool(
        HOST_CLAIM_ACTION.search(text)
    )

    items: list[ReportItem] = []
    if verified:
        detail = f"{points} 点，" if points is not None else ""
        items.append(ReportItem("daily-activity", DONE, f"每日活跃度：{detail}奖励已领取"))
    elif claimed:
        if points is None and (matches := DAILY_POINTS.findall(text)):
            points = int(matches[-1])
        # 领取前读到的低分可能是旧值，只有已达标的分数才值得写出来。
        detail = (
            f"{points} 点，点了领取但没确认到账"
            if points is not None and points >= ACTIVITY_TARGET
            else "点了领取，但没读到最终活跃度"
        )
        items.append(ReportItem("daily-activity", WARN, f"每日活跃度：{detail}"))
    else:
        progress = points if points is not None else comparison.get("current_points_from_tasks")
        if progress is not None and progress < ACTIVITY_TARGET:
            items.append(
                ReportItem("daily-activity", FAILED, f"每日活跃度：{progress}/100，没达标")
            )
        elif progress is not None:
            items.append(
                ReportItem("daily-activity", WARN, f"每日活跃度：{progress} 点，奖励没确认领取")
            )
        elif marker.get("state") == "unverified":
            items.append(ReportItem("daily-activity", WARN, "每日活跃度：没确认"))

    issues: list[str] = []
    # 达标和领完奖励是两件事：领取后总分已确认达标时，面板上剩下的可选任务不算缺口。
    total_reached = (
        isinstance(points, int)
        and points >= ACTIVITY_TARGET
        and panel.get("active_panel_confirmed") is True
        and str(marker.get("source", "")).startswith("post_claim_total_region")
    )
    if comparison and not verified and not total_reached:
        tasks = [task for task in comparison.get("tasks") or [] if isinstance(task, dict)]
        missing = [
            f"{_panel_label(task)}(+{int(task.get('points') or 0)})"
            for task in tasks
            if not task.get("completed")
        ]
        if missing:
            issues.append(f"每日活跃度还差：{'、'.join(missing)}")
        # 面板上有没识别的任务时，不能只凭已知部分推出“最多只能到多少分”。
        if (
            tasks
            and comparison.get("can_reach_target_now") is False
            and not comparison.get("unknown_tasks")
        ):
            issues.append(
                "按脚本现有功能，每日活跃度最多只能做到 "
                f"{comparison.get('reachable_now_points')}/100"
            )
    return items, issues


def _battle_pass(text: str) -> list[ReportItem]:
    """先约电台只在真正进入领取分支时汇报。"""

    start = text.rfind("DailyTask:battle pass")
    if start < 0:
        return []
    tail = text[start:]
    boundaries = [
        position
        for position in (
            tail.find("current task check weekly garden"),
            tail.find("Daily task completed, start teleport"),
            tail.find("Daily Task Completed"),
        )
        if position > 0
    ]
    if not boundaries or "can not battle pass" in tail[: min(boundaries)]:
        return []
    return [ReportItem("battle-pass", DONE, "先约电台：已执行领取")]


def _garden(text: str) -> list[ReportItem]:
    if "乐园任务完成, 已达到上限" in text:
        return [ReportItem("weekly-garden", DONE, "幻梦游园：本周目标已完成")]
    if "GardenTask Failed" in text:
        return [ReportItem("weekly-garden", FAILED, "幻梦游园：执行出错")]
    if "weekly garden not completed, run GardenTask" in text or "GardenTask:garden end" in text:
        return [ReportItem("weekly-garden", WARN, "幻梦游园：本轮没确认完成")]
    return []


def _boss_label(text: str, config: Mapping[str, Any], boss_names: Mapping[int, str]) -> str:
    index = config.get("boss_challenge_index")
    if index is None:
        index = config.get("which_boss_challenge")
    if index is None and (teleports := BOSS_TELEPORT.findall(text)):
        # OK-WW 日志里是从 0 开始的列表下标，游戏界面从 1 开始数。
        index = int(teleports[-1]) + 1
    if not index:
        return "讨伐强敌"
    name = boss_names.get(int(index))
    return f"讨伐强敌第{index}项" + (f"（{name}）" if name else "")


def _is_client_restart(item: Mapping[str, Any]) -> bool:
    return (
        item.get("kind") == "client_restart"
        or "client restart" in str(item.get("reason", "")).casefold()
    )


def _recovery_events(recovery: Mapping[str, Any]) -> list[str]:
    history = [item for item in recovery.get("recoveries") or [] if isinstance(item, dict)]
    in_game = [item for item in history if not _is_client_restart(item)]
    defeats = sum(1 for item in in_game if item.get("realm_defeat") is True)
    # 旧结果没有逐次记录，只有恢复总次数，全部按中途倒地算。
    deaths = len(in_game) - defeats if history else int(recovery.get("recovery_attempts") or 0)
    restarts = max(
        len(history) - len(in_game),
        int(bool(recovery.get("client_restart_triggered"))),
    )
    counts = (
        ("团灭", defeats),
        ("倒地", deaths),
        ("重启游戏", restarts),
        ("重新识别角色", int(recovery.get("combat_rebind_attempts") or 0)),
        ("续跑", int(recovery.get("retry_runs") or 0)),
    )
    return [f"{label} {count} 次" for label, count in counts if count]


def _boss(
    text: str,
    config: Mapping[str, Any],
    *,
    boss_ok: bool | None,
    boss_names: Mapping[int, str],
) -> list[ReportItem]:
    """讨伐强敌一行：吸收声骸进度为准，击败次数和自动恢复经过写在后面。"""

    recovery = config.get("farm_echo_recovery") or {}
    kills = count_farm_echo_kill_confirmations(text)
    absorbed = count_farm_echo_absorptions(text)
    structured = config.get("confirmed_farm_echo_absorption_count")
    if structured is not None:
        # 确认重试吸收完上一只的声骸才开下一场，结构化吸收数同时证明了击败次数。
        absorbed = int(structured)
        kills = max(kills, absorbed)
    if recovery.get("triggered"):
        total = int(recovery.get("total_completed") or 0)
        absorbed = total or absorbed
        kills = max(kills, total)
    target = config.get("farm_echo_absorption_target")
    if target is None and config.get("workflow_task") == "farm_echo_confirmed_retry":
        target = config.get("target_count")
    if target is None and recovery.get("triggered"):
        target = recovery.get("target_count")
    target = int(target or 0)
    if not (kills or absorbed or target or boss_ok is not None):
        return []

    done = absorbed >= target if target else boss_ok is not False and (kills or absorbed) > 0
    parts = []
    if target:
        parts.append(f"吸收声骸 {absorbed}/{target}")
    elif absorbed:
        parts.append(f"吸收声骸 {absorbed} 次")
    if kills > absorbed:
        parts.append(f"击败 {kills} 次")
    line = f"{_boss_label(text, config, boss_names)}：{'，'.join(parts) or '没完成'}"
    events = _recovery_events(recovery) if recovery.get("triggered") else []
    if events:
        line += f"；途中{'、'.join(events)}" + ("，已自动恢复" if done else "")
    mark = DONE if done else WARN if kills or absorbed else FAILED
    return [ReportItem("boss", mark, line)]


def parse_run(
    result: Any,
    cleanup: Mapping[str, Any] | None = None,
    *,
    boss_names: Mapping[int, str] | None = None,
) -> RunFacts:
    """解析一次运行；``cleanup`` 是收尾结果的字典形式。"""

    path = Path(result.log_slice_path)
    text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    config = result.config
    workflow = str(config.get("workflow_task", "daily"))
    daily_ok = _phase_ok(result, "daily")
    boss_ok = _phase_ok(result, "boss")
    activity, activity_issues = _activity(text)
    # 没有阶段前缀的失败原因归到本轮工作流自己的阶段。
    own_phase = (
        "boss" if workflow in FARM_ECHO_WORKFLOWS else "daily" if workflow == "daily" else None
    )
    issues: dict[str | None, list[str]] = {"boss": [], "daily": [], None: []}

    if result.status == "success":
        status = "completed"
    else:
        if config.get("farm_echo_world_team_blocked") or is_farm_echo_world_team_blocked(text):
            issues["boss"].append(WORLD_TEAM_BLOCKED)
        else:
            for phase, line in explain_failure(result.reason):
                issues[phase or own_phase].append(line)
        recovery = config.get("farm_echo_recovery") or {}
        recovered_some = recovery.get("triggered") and (
            int(recovery.get("total_completed") or 0) > 0
            or recovery.get("first_safe_recovery") is True
        )
        status = "partial" if daily_ok or boss_ok or recovered_some else "failed"
    issues["daily"].extend(activity_issues)

    return RunFacts(
        overall_status=status,
        workflow_task=workflow,
        reason=str(result.reason),
        duration_seconds=int(result.duration_seconds or 0),
        daily_ok=daily_ok,
        boss_ok=boss_ok,
        daily=[*_tacet(text, config), *_nightmare(text), *activity, *_battle_pass(text)],
        weekly=_garden(text),
        boss=_boss(text, config, boss_ok=boss_ok, boss_names=boss_names or {}),
        boss_issues=issues["boss"],
        daily_issues=issues["daily"],
        other_issues=issues[None],
        cleanup=dict(cleanup or {}),
        sources=[str(getattr(result, "run_id", ""))],
    )
