"""把一次 M7A 运行的事实翻译成星铁日报：每行一个任务，失败时先说卡在哪。"""

from datetime import datetime

from game_automation_core.reporting.report import (
    DONE,
    FAILED,
    INFO,
    SKIPPED,
    WARN,
    GameReport,
    settle_status,
)

from starrail_auto.reporting.models import RunReport, StaminaRun
from starrail_auto.reporting.training_plan import TrainingPlan

# RunResult.stage（前置检查或看门狗给出的阶段名）的中文说明；
# “实训…”开头的阶段由每日实训那一行表达，不再重复。
STAGE_TEXT = {
    "UU": "UU 加速器没能连上星铁",
    "网络代理": "网络检查没通过（DNS 或 443 端口不通）",
    "M7A启动": "三月七助手没能启动",
    "游戏检测": "游戏窗口没有按时出现",
    "M7A配置保护": "三月七助手的配置被异常改动，已拦截并停止",
    "M7A": "三月七助手异常退出",
    "超时": "运行超过了时间上限",
    "CPU": "三月七助手长时间没反应，被看门狗停止",
    "日志": "三月七助手日志长时间不更新，被看门狗停止",
    "看门狗": "看门狗停止了运行",
}
DAILY_TASK_ALIASES = {
    "派遣委托或收取1次委托奖励": "派遣委托",
    "使用1次「万能合成机」": "万能合成机",
}
MAX_ERRORS = 3


def describe_stage(stage: str) -> str:
    return STAGE_TEXT.get(stage, stage or "未知原因")


def _daily_line(run: RunReport, *, failed: bool) -> str:
    score = "" if run.daily_score == "未读取" else f" {run.daily_score}"
    if run.daily_status == "completed":
        if run.daily_already_settled and not run.daily_completed_this_run:
            return f"{DONE} 每日实训：本刷新周期已经完成"
        text = f"{DONE} 每日实训{score or '：已完成'}"
        if run.daily_completed_this_run:
            tasks = "、".join(
                DAILY_TASK_ALIASES.get(task, task) for task in run.daily_completed_this_run
            )
            text += f"（本次补做：{tasks}）"
        return text
    if run.daily_status == "failed" or failed:
        text = f"{FAILED} 每日实训{score or '：没完成'}"
        if run.daily_unfinished:
            tasks = "、".join(DAILY_TASK_ALIASES.get(task, task) for task in run.daily_unfinished)
            return f"{text}（还差：{tasks}）"
        return text if score else f"{text}（分数没读到）"
    return f"{WARN} 每日实训：结果未确认"


def _stamina_count(item: StaminaRun) -> str:
    if item.completed_instances:
        return f"{item.completed_instances} 次"
    if item.rounds is not None and item.rewards_per_round is not None:
        if item.rounds == 1:
            return f"{item.rewards_per_round} 次"
        return f"{item.rounds} 轮 × {item.rewards_per_round} 次"
    if item.rounds is not None:
        return f"{item.rounds} 轮"
    return ""


def _plan_fully_completed(item: StaminaRun) -> bool:
    if item.remaining_plan_count == 0:
        return True
    if item.planned_count is None:
        return False
    if item.completed_instances >= item.planned_count:
        return True
    return (
        item.rounds is not None
        and item.rewards_per_round is not None
        and item.rounds * item.rewards_per_round >= item.planned_count
    )


def _stamina_line(item: StaminaRun, *, failed: bool) -> str:
    name = item.name.replace(" - ", "·")
    if item.trainee:
        name = f"{name}（{item.trainee}）"
    if item.source == "activity" and item.activity_name:
        name = f"{item.activity_name}：{name}"
    elif item.source == "default":
        name = f"清体力：{name}"

    if item.status == "skipped":
        return f"{SKIPPED} {name}：{item.reason or '条件不足，计划保留'}"
    if item.status == "failed":
        return f"{FAILED} {name}：中途出错（{item.reason}）"
    if item.status != "completed":
        return f"{FAILED} {name}：没打完" if failed else f"{WARN} {name}：没确认打完"

    count = _stamina_count(item)
    details = [count] if count else []
    if item.remaining_plan_count:
        details.append(f"计划还剩 {item.remaining_plan_count} 次")
    elif item.source != "default" and _plan_fully_completed(item):
        details.append("计划已完成")
    if item.source == "activity" and item.activity_remaining_count is not None:
        details.append(f"双倍还剩 {item.activity_remaining_count} 次")
    return f"{DONE} {name}：{'，'.join(details) or '已完成'}"


def _other_line(text: str) -> str:
    if "未执行：" in text:
        return f"{SKIPPED} {text}"
    if text.startswith(("历战余响", "差分宇宙积分")):
        return f"{INFO} {text}"
    return f"{DONE} {text}"


def _status(run: RunReport) -> str:
    if run.overall_status in {"failed", "stalled"} or run.daily_status != "completed":
        return "failed"
    if run.overall_status == "in_progress":
        return "partial"
    return "completed"


def _problems(run: RunReport) -> list[str]:
    problems: list[str] = []
    if run.run_stage and not run.run_stage.startswith("实训"):
        problems.append(describe_stage(run.run_stage))
    if not run.stopped_normally and run.current_task:
        if run.overall_status == "in_progress":
            problems.append(f"汇报时三月七助手还在运行：{run.current_task}")
        elif run.overall_status == "stalled":
            problems.append(f"疑似卡在「{run.current_task}」：{run.current_reason}")
        else:
            problems.append(f"停在「{run.current_task}」")
    if run.errors:
        shown = "；".join(run.errors[:MAX_ERRORS])
        more = f" 等 {len(run.errors)} 条" if len(run.errors) > MAX_ERRORS else ""
        problems.append(f"三月七助手报错：{shown}{more}")
    return problems


def build_report(
    run: RunReport,
    *,
    plan: TrainingPlan,
    reminders: list[str],
    finished_at: datetime,
    duration_seconds: int | None = None,
) -> GameReport:
    status = _status(run)
    failed = status == "failed"
    tasks = [_daily_line(run, failed=failed)]
    # 体力花在哪单独成一栏，不和每日实训挤在一起；“未执行”也是体力计划。
    stamina = [_stamina_line(item, failed=failed) for item in run.stamina_runs]
    if run.rewards:
        tasks.append(f"{DONE} 领取奖励：{'、'.join(run.rewards)}")
    for text in run.other_tasks:
        (stamina if "未执行：" in text else tasks).append(_other_line(text))
    tasks.extend(
        f"{DONE} 养成计划完成：{goal.character} {goal.category}"
        for goal in plan.completed_this_run
    )
    status = settle_status(status, [*tasks, *stamina])

    notes = [
        ("体力去向", "\n".join(stamina)),
        ("养成待办", [f"{goal.character}：{goal.category}" for goal in plan.active_goals]),
        ("提醒", reminders),
    ]
    return GameReport(
        game="星铁",
        status=status,
        finished_at=finished_at,
        tasks=tasks,
        problems=[] if status == "completed" else _problems(run),
        notes=notes,
        duration_seconds=duration_seconds,
    )
