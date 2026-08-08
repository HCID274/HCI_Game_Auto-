"""Deterministic parser for one M7A run."""

import re
from collections import Counter
from datetime import datetime
from typing import Any

from game_automation_core.reporting.agent import build_evidence_bundle

from starrail_auto.reporting.models import RunEvent, RunReport, StaminaRun

LOG_LINE_PATTERN = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})"
    r" \| (?P<level>INFO|WARNING|ERROR|CRITICAL) \| (?P<message>.*)$"
)
SECTION_PATTERN = re.compile(r"^\|\s*(?P<label>[^|]+?)\s*\|$")
SEPARATOR_LABEL_PATTERN = re.compile(r"^-{3,}\s*(?P<label>[^-].*?)\s*-{3,}$")
DAILY_TASK_PATTERN = re.compile(r"^(?P<task>.+?): (?P<status>已完成|待完成)(?:\s+\+.*)?$")
DAILY_COMPLETED_PATTERN = re.compile(
    r"^完成任务: (?P<task>.+?) \(\+\d+分\)，当前分数: (?P<score>\d+/\d+)$"
)
DAILY_BLOCKED_PATTERN = re.compile(r"^任务无法完成: (?P<task>.+)$")
DAILY_SCORE_PATTERN = re.compile(r"当前(?:累计)?分数[：:]\s*(?P<score>\d+\s*/\s*\d+)")
POWER_PATTERN = re.compile(r"^开拓力: (?P<power>\d+)/300$")
PLAN_PATTERN = re.compile(
    r"^执行体力计划 \[(?P<index>\d+)/(?P<total>\d+)\]: "
    r"(?P<name>.+), 计划次数: (?P<count>\d+)$"
)
FARM_PATTERN = re.compile(
    r"开始刷(?P<name>.+?)，总计(?P<rounds>\d+)轮，每轮包含(?P<rewards>\d+)次"
)
INSTANCE_COMPLETED_PATTERN = re.compile(r"^第(?P<count>\d+)次副本完成$")
PLAN_REMAINING_PATTERN = re.compile(
    r"^体力计划剩余: (?P<name>.+), 剩余次数: (?P<count>\d+)$"
)
PLAN_COMPLETED_PATTERN = re.compile(r"^体力计划已完成: (?P<name>.+)$")
PLAN_SKIPPED_PATTERN = re.compile(r"^无法执行: (?P<name>.+?)，(?P<reason>.+)$")
TRAINING_TARGET_PATTERN = re.compile(r"^培养目标(?P<character>.+?)的待刷副本:$")
DIRECT_REWARD_PATTERN = re.compile(r"^领取(?P<reward>.+?奖励)完成$")
MAIL_REWARD_PATTERN = re.compile(r"^邮件奖励已领取$")
ACTIVITY_REMAINING_PATTERN = re.compile(
    r"^(?P<activity>位面分裂|异器盈界|花藏繁生)剩余次数[：:]"
    r"(?P<count>\d+)$"
)
REDEMPTION_RESULT_PATTERN = re.compile(
    r"^兑换码使用(?P<status>成功|失败): .+ \((?P<index>\d+)/(?P<total>\d+)\)$"
)
REDEMPTION_SUMMARY_PATTERN = re.compile(r"^成功使用了(?P<count>\d+)个兑换码:$")
ECHO_OF_WAR_PATTERN = re.compile(
    r"^历战余响本周可领取奖励次数[：:](?P<remaining>\d+)/(?P<total>\d+)$"
)
DIVERGENT_SCORE_PATTERN = re.compile(
    r"^差分宇宙积分[：:]\s*(?P<score>\d+)\s*/\s*(?P<total>\d+)$"
)
DIVERGENT_DURATION_PATTERN = re.compile(
    r"^本次差分宇宙用时[：:](?P<minutes>\d+) 分钟 (?P<seconds>\d+) 秒$"
)
DIVERGENT_COUNTS_PATTERN = re.compile(
    r"^已记录差分宇宙次数[：:]今日 (?P<daily>\d+) 次，本周 (?P<weekly>\d+) 次$"
)
REDEMPTION_CODE_ONLY_PATTERN = re.compile(r"^[A-Za-z0-9]{8,24}$")

DAILY_COMPLETED_MARKER = "每日实训已完成"
DAILY_INCOMPLETE_MARKER = "每日实训未完成"
RUN_STOP_MARKER = "停止运行"
STALE_SECONDS = 600
REPEATED_FAILURE_THRESHOLD = 3

KNOWN_STALL_MESSAGES = (
    "未识别出任何界面",
    "当前界面：未知",
    "截图失败：没有找到游戏窗口",
)


def _append_unique(items: list[str], value: str) -> None:
    value = value.strip()
    if value and value not in items:
        items.append(value)


def _append_daily_event(report: RunReport, event: RunEvent) -> None:
    if event not in report.daily_events:
        report.daily_events.append(event)


def _replace_prefixed(items: list[str], prefix: str, value: str) -> None:
    """Keep only the latest snapshot and place it at its actual event position."""
    for index, item in enumerate(items):
        if item.startswith(prefix):
            items.pop(index)
            break
    items.append(value)


def _in_power_plan_section(active_section: str) -> bool:
    return active_section == "执行体力计划" or active_section.startswith("体力计划：")


def _redact_sensitive_message(message: str) -> str:
    return re.sub(
        r"^(兑换码使用(?:成功|失败):)\s+.+?(\s+\(\d+/\d+\))$",
        r"\1 [已隐藏]\2",
        message,
    )


def _normalized_task_name(value: str) -> str:
    return re.sub(r"[\s·\-—_]", "", value).casefold()


def _plan_remaining_for(
    task_name: str,
    power_plan_remaining: dict[str, int],
) -> int | None:
    target = _normalized_task_name(task_name)
    return next(
        (
            count
            for name, count in power_plan_remaining.items()
            if _normalized_task_name(name) == target
        ),
        None,
    )


def _character_context(task_name: str, preferences: dict[str, Any]) -> str:
    normalized_task = _normalized_task_name(task_name)
    for goal in preferences.get("character_goals", []):
        if not isinstance(goal, dict):
            continue
        keywords = [str(item) for item in goal.get("keywords", [])]
        if not any(
            keyword and _normalized_task_name(keyword) in normalized_task
            for keyword in keywords
        ):
            continue
        character = str(goal.get("character", "")).strip()
        goal_text = str(goal.get("goal", "")).strip()
        if character and goal_text:
            return f"用于培养{character}（{goal_text}）"
        if character:
            return f"用于培养{character}"
    return ""


def _meaningful_message(message: str) -> bool:
    ignored = (
        "当前界面：",
        "切换到：",
        "准备发送 winotify",
        "winotify 通知发送完成",
        "GitHub 确认",
    )
    return not message.startswith(ignored)


def _detect_repeated_stall(
    recent_events: list[tuple[datetime, str, str]],
) -> tuple[str, int] | None:
    tail = recent_events[-30:]
    matched: list[str] = []
    for _timestamp, level, message in tail:
        if level not in {"WARNING", "ERROR", "CRITICAL"}:
            continue
        for marker in KNOWN_STALL_MESSAGES:
            if marker in message:
                matched.append(marker)
                break
        else:
            if "切换到" in message and "超时" in message:
                matched.append(re.sub(r"\s+", " ", message))

    if not matched:
        return None
    marker, count = Counter(matched).most_common(1)[0]
    if count < REPEATED_FAILURE_THRESHOLD:
        return None
    if not any(marker in event[2] for event in tail[-5:]):
        return None
    return marker, count


def parse_m7a_run(
    content: str,
    *,
    now: datetime,
    preferences: dict[str, Any] | None = None,
    run_stage: str = "",
    retries: int = 0,
    force_failed: bool = False,
    power_plan_remaining: dict[str, int] | None = None,
) -> RunReport:
    """Parse only the content after the run's recorded byte checkpoint."""
    preferences = preferences or {}
    power_plan_remaining = power_plan_remaining or {}
    report = RunReport(
        run_stage=run_stage,
        retries=retries,
        custom_context=preferences,
        evidence=build_evidence_bundle(game="starrail", log_text=content),
    )
    events: list[tuple[datetime, str, str]] = []
    active_section = ""
    active_plan: StaminaRun | None = None
    active_activity: StaminaRun | None = None
    active_default_stamina: StaminaRun | None = None
    pending_activity_name = ""
    pending_activity_remaining: int | None = None
    pending_activity_batch_count = 0
    pending_plan_batch_count = 0
    capturing_training_dungeons = False
    last_plan_constraint = ""
    redemption_total = 0
    redemption_failed = 0
    capturing_redemption_codes = False
    divergent_duration = ""
    divergent_daily_count: int | None = None
    divergent_weekly_count: int | None = None

    for raw_line in content.splitlines():
        line = raw_line.strip()
        section_match = SECTION_PATTERN.match(line)
        if section_match:
            label = section_match.group("label").strip()
            if label.startswith("开始") and label not in {"开始运行", "开始检测更新"}:
                active_section = label.removeprefix("开始").strip()
                if active_section != "执行体力计划":
                    active_plan = None
                    pending_plan_batch_count = 0
                if active_section != "检测活动":
                    active_activity = None
                    pending_activity_batch_count = 0
                if active_section != "清体力":
                    active_default_stamina = None
                if active_section == "清体力":
                    pending_activity_name = ""
                    pending_activity_remaining = None
            elif label == RUN_STOP_MARKER:
                report.stopped_normally = True
                active_section = ""
            continue

        separator_match = SEPARATOR_LABEL_PATTERN.match(line)
        if separator_match:
            label = separator_match.group("label").strip()
            farm_match = FARM_PATTERN.search(label)
            if farm_match:
                farm_name = farm_match.group("name").strip()
                farm_rounds = int(farm_match.group("rounds"))
                farm_rewards = int(farm_match.group("rewards"))
                if _in_power_plan_section(active_section) and active_plan is not None:
                    active_plan.name = farm_name
                    if active_plan.rounds is None:
                        active_plan.rounds = farm_rounds
                        active_plan.rewards_per_round = farm_rewards
                    else:
                        active_plan.rounds += farm_rounds
                        if active_plan.rewards_per_round != farm_rewards:
                            active_plan.rewards_per_round = None
                    pending_plan_batch_count = farm_rounds * farm_rewards
                elif (
                    pending_activity_name
                    and pending_activity_remaining is not None
                ):
                    if active_activity is None or active_activity.name != farm_name:
                        active_activity = StaminaRun(
                            name=farm_name,
                            source="activity",
                            activity_name=pending_activity_name,
                            activity_start_remaining=pending_activity_remaining,
                            activity_remaining_count=pending_activity_remaining,
                            remaining_plan_count=_plan_remaining_for(
                                farm_name, power_plan_remaining
                            ),
                        )
                        active_activity.character_context = _character_context(
                            farm_name, preferences
                        )
                        report.stamina_runs.append(active_activity)
                    pending_activity_batch_count = farm_rounds * farm_rewards
                elif active_section == "清体力":
                    active_default_stamina = StaminaRun(
                        name=farm_name,
                        source="default",
                        rounds=farm_rounds,
                        rewards_per_round=farm_rewards,
                    )
                    active_default_stamina.character_context = _character_context(
                        farm_name, preferences
                    )
                    report.stamina_runs.append(active_default_stamina)
            continue

        log_match = LOG_LINE_PATTERN.match(line)
        if not log_match:
            continue

        timestamp = datetime.strptime(
            log_match.group("timestamp"),
            "%Y-%m-%d %H:%M:%S,%f",
        )
        level = log_match.group("level")
        message = _redact_sensitive_message(log_match.group("message").strip())
        if capturing_redemption_codes:
            if REDEMPTION_CODE_ONLY_PATTERN.match(message):
                continue
            capturing_redemption_codes = False
        events.append((timestamp, level, message))
        report.last_log_at = timestamp

        direct_reward_match = DIRECT_REWARD_PATTERN.match(message)
        if direct_reward_match:
            reward = f"{direct_reward_match.group('reward')}完成"
            if report.daily_status == "completed":
                _append_unique(report.rewards_completed, reward)
            else:
                _append_daily_event(report, RunEvent(kind="reward", label=reward))

        if MAIL_REWARD_PATTERN.match(message):
            _append_unique(report.rewards_completed, "邮件奖励完成")

        redemption_result = REDEMPTION_RESULT_PATTERN.match(message)
        if redemption_result:
            redemption_total = max(
                redemption_total,
                int(redemption_result.group("total")),
            )
            if redemption_result.group("status") == "失败":
                redemption_failed += 1

        redemption_summary = REDEMPTION_SUMMARY_PATTERN.match(message)
        if redemption_summary:
            success_count = int(redemption_summary.group("count"))
            failed_count = max(redemption_failed, redemption_total - success_count)
            text = f"兑换码：成功{success_count}个"
            if failed_count:
                text += f"，失败{failed_count}个"
            _append_unique(report.other_tasks, text)
            capturing_redemption_codes = True

        echo_match = ECHO_OF_WAR_PATTERN.match(message)
        if echo_match:
            _append_unique(
                report.other_tasks,
                "历战余响：本周可领取奖励"
                f"{echo_match.group('remaining')}/{echo_match.group('total')}（已检查）",
            )

        duration_match = DIVERGENT_DURATION_PATTERN.match(message)
        if duration_match:
            divergent_duration = (
                f"{duration_match.group('minutes')}分{duration_match.group('seconds')}秒"
            )

        counts_match = DIVERGENT_COUNTS_PATTERN.match(message)
        if counts_match:
            divergent_daily_count = int(counts_match.group("daily"))
            divergent_weekly_count = int(counts_match.group("weekly"))

        if message == "差分宇宙已完成":
            details = ["完成1次"]
            if divergent_duration:
                details.append(f"用时{divergent_duration}")
            if divergent_daily_count is not None:
                details.append(f"今日{divergent_daily_count}次")
            if divergent_weekly_count is not None:
                details.append(f"本周{divergent_weekly_count}次")
            _append_unique(report.other_tasks, f"差分宇宙：{'，'.join(details)}")

        if message == "模拟宇宙奖励已领取":
            _append_unique(report.other_tasks, "领取模拟宇宙奖励")

        divergent_score_match = DIVERGENT_SCORE_PATTERN.match(message)
        if divergent_score_match:
            _replace_prefixed(
                report.other_tasks,
                "差分宇宙积分 ",
                "差分宇宙积分 "
                f"{divergent_score_match.group('score')}/{divergent_score_match.group('total')}",
            )

        activity_match = ACTIVITY_REMAINING_PATTERN.match(message)
        if activity_match:
            pending_activity_name = activity_match.group("activity")
            pending_activity_remaining = int(activity_match.group("count"))

        target_match = TRAINING_TARGET_PATTERN.match(message)
        if target_match:
            report.detected_training_target = target_match.group("character").strip()
            capturing_training_dungeons = True
        elif capturing_training_dungeons:
            if message.startswith("准备发送 winotify"):
                capturing_training_dungeons = False
            elif " - " in message and not message.startswith(("当前界面", "切换到")):
                _append_unique(report.detected_training_dungeons, message)

        task_match = DAILY_TASK_PATTERN.match(message)
        if task_match:
            task = task_match.group("task").strip()
            if task_match.group("status") == "已完成":
                _append_unique(report.daily_initial_completed, task)
            else:
                _append_unique(report.daily_unfinished, task)

        completed_match = DAILY_COMPLETED_PATTERN.match(message)
        if completed_match:
            task = completed_match.group("task").strip()
            _append_unique(report.daily_completed_this_run, task)
            if "委托" in task:
                report.daily_events = [
                    event
                    for event in report.daily_events
                    if not (event.kind == "reward" and "委托奖励" in event.label)
                ]
            _append_daily_event(report, RunEvent(kind="daily_task", label=task))
            if task in report.daily_unfinished:
                report.daily_unfinished.remove(task)
            report.daily_score = completed_match.group("score")

        blocked_match = DAILY_BLOCKED_PATTERN.match(message)
        if blocked_match:
            _append_unique(report.daily_unfinished, blocked_match.group("task"))

        score_match = DAILY_SCORE_PATTERN.search(message)
        if score_match:
            report.daily_score = score_match.group("score").replace(" ", "")

        if message == DAILY_COMPLETED_MARKER:
            report.daily_status = "completed"
            report.overall_status = "completed"
            _append_unique(report.rewards_completed, "每日实训奖励完成")
        elif message == DAILY_INCOMPLETE_MARKER:
            report.daily_status = "failed"
            report.overall_status = "failed"
        elif message == "开始「差分宇宙」":
            active_section = "差分宇宙"

        power_match = POWER_PATTERN.match(message)
        if power_match:
            power = int(power_match.group("power"))
            if report.stamina_start is None:
                report.stamina_start = power
            report.stamina_end = power

        plan_match = PLAN_PATTERN.match(message)
        if plan_match:
            last_plan_constraint = ""
            active_activity = None
            pending_activity_batch_count = 0
            pending_plan_batch_count = 0
            active_plan = StaminaRun(
                name=plan_match.group("name").strip(),
                plan_index=int(plan_match.group("index")),
                plan_total=int(plan_match.group("total")),
                planned_count=int(plan_match.group("count")),
            )
            active_plan.character_context = _character_context(active_plan.name, preferences)
            report.stamina_runs.append(active_plan)
            active_section = f"体力计划：{active_plan.name}"

        completed_instance_match = INSTANCE_COMPLETED_PATTERN.match(message)
        if (
            completed_instance_match
            and active_plan is not None
            and pending_plan_batch_count == 0
        ):
            active_plan.completed_instances = max(
                active_plan.completed_instances,
                int(completed_instance_match.group("count")),
            )

        if (
            message == "副本任务完成"
            and _in_power_plan_section(active_section)
            and active_plan is not None
        ):
            if pending_plan_batch_count > 0:
                active_plan.completed_instances += pending_plan_batch_count
                pending_plan_batch_count = 0
            active_plan.status = "completed"
            _append_daily_event(
                report,
                RunEvent(
                    kind="stamina",
                    stamina_index=report.stamina_runs.index(active_plan),
                ),
            )
        elif (
            message == "副本任务完成"
            and active_activity is not None
            and pending_activity_batch_count > 0
        ):
            active_activity.completed_instances += pending_activity_batch_count
            active_activity.activity_remaining_count = max(
                0,
                (active_activity.activity_start_remaining or 0)
                - active_activity.completed_instances,
            )
            active_activity.status = "completed"
            _append_daily_event(
                report,
                RunEvent(
                    kind="stamina",
                    stamina_index=report.stamina_runs.index(active_activity),
                ),
            )
            pending_activity_batch_count = 0
        elif (
            message == "副本任务完成"
            and active_section == "清体力"
            and active_default_stamina is not None
        ):
            active_default_stamina.status = "completed"
            _append_daily_event(
                report,
                RunEvent(
                    kind="stamina",
                    stamina_index=report.stamina_runs.index(active_default_stamina),
                ),
            )
            active_default_stamina = None

        if active_plan is not None and (
            re.match(r"^开拓力 < \d+$", message)
            or re.match(r"^开拓力: \d+ = 0 次挑战$", message)
            or message.startswith("沉浸器数量识别失败")
        ):
            last_plan_constraint = message

        remaining_match = PLAN_REMAINING_PATTERN.match(message)
        if remaining_match:
            matching = next(
                (
                    item
                    for item in reversed(report.stamina_runs)
                    if item.name == remaining_match.group("name").strip()
                ),
                None,
            )
            if matching:
                matching.remaining_plan_count = int(remaining_match.group("count"))
                matching.status = "completed"

        plan_completed_match = PLAN_COMPLETED_PATTERN.match(message)
        if plan_completed_match:
            name = plan_completed_match.group("name").strip()
            matching = next(
                (item for item in reversed(report.stamina_runs) if item.name == name),
                None,
            )
            if matching:
                matching.remaining_plan_count = 0
                matching.status = "completed"

        skipped_match = PLAN_SKIPPED_PATTERN.match(message)
        if skipped_match:
            name = skipped_match.group("name").strip()
            matching = next(
                (item for item in reversed(report.stamina_runs) if item.name == name),
                None,
            )
            if matching:
                matching.status = "skipped"
                stated_reason = skipped_match.group("reason").strip()
                matching.reason = (
                    f"{last_plan_constraint}，{stated_reason}"
                    if last_plan_constraint
                    else stated_reason
                )
            else:
                _append_unique(report.other_tasks, f"{name}未执行：{skipped_match.group('reason')}")

        if level in {"WARNING", "ERROR"}:
            _append_unique(report.recovered_warnings, message)

    for stamina_run in report.stamina_runs:
        if not stamina_run.character_context:
            stamina_run.character_context = _character_context(stamina_run.name, preferences)
        if (
            not stamina_run.character_context
            and report.detected_training_target
            and any(
                stamina_run.name.casefold() == dungeon.casefold()
                for dungeon in report.detected_training_dungeons
            )
        ):
            stamina_run.character_context = f"来自培养目标{report.detected_training_target}"

    if force_failed:
        report.overall_status = "failed"
        if report.daily_status == "unknown":
            report.daily_status = "failed"

    if report.stopped_normally:
        if report.overall_status == "unknown":
            report.overall_status = "failed"
        report.current_task = ""
        report.current_reason = ""
        return report

    meaningful = [event for event in events if _meaningful_message(event[2])]
    last_message = meaningful[-1][2] if meaningful else ""
    if active_section == "差分宇宙" and last_message:
        report.current_task = f"差分宇宙：{last_message}"
    else:
        report.current_task = active_section or last_message or run_stage or "未知阶段"

    comparable_now = now.replace(tzinfo=None) if now.tzinfo else now
    stale_seconds = (
        max(0, int((comparable_now - report.last_log_at).total_seconds()))
        if report.last_log_at
        else 0
    )
    repeated_stall = _detect_repeated_stall(events)

    if force_failed:
        report.current_reason = run_stage or last_message or "主链路返回失败"
    elif stale_seconds >= STALE_SECONDS:
        report.overall_status = "stalled"
        report.current_reason = (
            f"日志已{stale_seconds // 60}分钟无进展；"
            f"最后记录：{last_message or '无有效进度'}"
        )
    elif repeated_stall:
        marker, count = repeated_stall
        report.overall_status = "stalled"
        report.current_reason = f"近期重复{count}次“{marker}”"
    else:
        if report.overall_status == "unknown":
            report.overall_status = "in_progress"
        report.current_reason = f"日志仍在更新；最后记录：{last_message}" if last_message else "等待新日志"

    return report
