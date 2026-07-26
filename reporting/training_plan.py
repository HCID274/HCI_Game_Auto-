"""Persistent Markdown-backed character training plan."""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from reporting.models import StaminaRun


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PLAN_PATH = PROJECT_ROOT / "prompts" / "training_plan.md"
GOAL_PATTERN = re.compile(
    r"^- \[(?P<checked>[ xX])\] `(?P<id>[^`]+)` "
    r"(?P<character>[^｜|]+)[｜|](?P<category>.+)$"
)
FIELD_PATTERN = re.compile(
    r"^\s+-\s+(?P<label>关联副本|完成条件|完成日期|完成依据)[：:]\s*(?P<value>.*)$"
)


@dataclass(frozen=True)
class TrainingGoal:
    goal_id: str
    character: str
    category: str
    dungeon: str = ""
    completion_condition: str = ""
    completed: bool = False
    completed_at: str = ""
    evidence: str = ""

    def to_context(self) -> dict[str, str | bool]:
        return {
            "id": self.goal_id,
            "character": self.character,
            "category": self.category,
            "dungeon": self.dungeon,
            "completed": self.completed,
        }


@dataclass(frozen=True)
class TrainingPlan:
    goals: tuple[TrainingGoal, ...] = ()
    completed_this_run: tuple[TrainingGoal, ...] = ()

    @property
    def active_goals(self) -> tuple[TrainingGoal, ...]:
        return tuple(goal for goal in self.goals if not goal.completed)

    def to_context(self) -> dict[str, list[dict[str, str | bool]]]:
        return {
            "active_goals": [goal.to_context() for goal in self.active_goals],
            "completed_this_run": [
                goal.to_context() for goal in self.completed_this_run
            ],
        }


def load_training_plan(path: Path = DEFAULT_PLAN_PATH) -> TrainingPlan:
    if not path.exists():
        return TrainingPlan()

    goals: list[TrainingGoal] = []
    current: dict[str, str | bool] | None = None

    def finish_current() -> None:
        nonlocal current
        if current is None:
            return
        goals.append(
            TrainingGoal(
                goal_id=str(current["goal_id"]),
                character=str(current["character"]).strip(),
                category=str(current["category"]).strip(),
                dungeon=str(current.get("关联副本", "")).strip(),
                completion_condition=str(current.get("完成条件", "")).strip(),
                completed=bool(current["completed"]),
                completed_at=str(current.get("完成日期", "")).strip(),
                evidence=str(current.get("完成依据", "")).strip(),
            )
        )
        current = None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        goal_match = GOAL_PATTERN.match(raw_line.strip())
        if goal_match:
            finish_current()
            current = {
                "goal_id": goal_match.group("id").strip(),
                "character": goal_match.group("character").strip(),
                "category": goal_match.group("category").strip(),
                "completed": goal_match.group("checked").lower() == "x",
            }
            continue
        field_match = FIELD_PATTERN.match(raw_line)
        if field_match and current is not None:
            current[field_match.group("label")] = field_match.group("value").strip()
    finish_current()
    return TrainingPlan(tuple(goals))


def _goal_lines(goal: TrainingGoal) -> list[str]:
    checked = "x" if goal.completed else " "
    lines = [f"- [{checked}] `{goal.goal_id}` {goal.character}｜{goal.category}"]
    lines.append(f"  - 关联副本：{goal.dungeon or '待填写'}")
    lines.append(
        f"  - 完成条件：{goal.completion_condition or '人工确认完成'}"
    )
    if goal.completed_at:
        lines.append(f"  - 完成日期：{goal.completed_at}")
    if goal.evidence:
        lines.append(f"  - 完成依据：{goal.evidence}")
    return lines


def render_training_plan(plan: TrainingPlan) -> str:
    lines = [
        "# 星铁养成计划",
        "",
        "<!--",
        "复选框由程序维护；角色、类型、关联副本和完成条件可以直接编辑。",
        "只有明确完成证据才会从进行中迁移到已完成。",
        "-->",
        "",
        "## 进行中",
        "",
    ]
    active = list(plan.active_goals)
    if active:
        for index, goal in enumerate(active):
            if index:
                lines.append("")
            lines.extend(_goal_lines(goal))
    else:
        lines.append("- 暂无")

    lines.extend(["", "## 已完成", ""])
    completed = [goal for goal in plan.goals if goal.completed]
    if completed:
        for index, goal in enumerate(completed):
            if index:
                lines.append("")
            lines.extend(_goal_lines(goal))
    else:
        lines.append("- 暂无")
    return "\n".join(lines) + "\n"


def save_training_plan(plan: TrainingPlan, path: Path = DEFAULT_PLAN_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(render_training_plan(plan), encoding="utf-8")
    os.replace(temporary, path)


def _normalized_dungeon(value: str) -> str:
    return re.sub(r"[\s·\-—_]", "", value).casefold()


def _completion_evidence(run: StaminaRun) -> str:
    if run.remaining_plan_count == 0:
        return f"{run.name}剩余计划0次"
    if (
        run.status == "completed"
        and run.planned_count is not None
        and run.rounds is not None
        and run.rewards_per_round is not None
        and run.rounds * run.rewards_per_round >= run.planned_count
    ):
        completed_count = run.rounds * run.rewards_per_round
        return f"{run.name}完成{completed_count}次，覆盖计划{run.planned_count}次"
    return ""


def reconcile_training_plan(
    stamina_runs: list[StaminaRun],
    *,
    completed_at: datetime,
    path: Path = DEFAULT_PLAN_PATH,
) -> TrainingPlan:
    """Complete active goals only when the run contains strong plan evidence."""
    plan = load_training_plan(path)
    updated: list[TrainingGoal] = []
    completed_now: list[TrainingGoal] = []

    for goal in plan.goals:
        replacement = goal
        if not goal.completed and goal.dungeon and goal.dungeon != "待填写":
            target = _normalized_dungeon(goal.dungeon)
            for run in stamina_runs:
                if _normalized_dungeon(run.name) != target:
                    continue
                evidence = _completion_evidence(run)
                if evidence:
                    replacement = replace(
                        goal,
                        completed=True,
                        completed_at=completed_at.strftime("%Y-%m-%d"),
                        evidence=evidence,
                    )
                    completed_now.append(replacement)
                break
        updated.append(replacement)

    result = TrainingPlan(tuple(updated), tuple(completed_now))
    if completed_now:
        save_training_plan(result, path)
    return result


def set_goal_status(
    goal_id: str,
    *,
    completed: bool,
    evidence: str,
    path: Path = DEFAULT_PLAN_PATH,
) -> TrainingPlan:
    plan = load_training_plan(path)
    found = False
    goals: list[TrainingGoal] = []
    for goal in plan.goals:
        if goal.goal_id != goal_id:
            goals.append(goal)
            continue
        found = True
        goals.append(
            replace(
                goal,
                completed=completed,
                completed_at=datetime.now().strftime("%Y-%m-%d") if completed else "",
                evidence=evidence if completed else "",
            )
        )
    if not found:
        raise ValueError(f"unknown training goal: {goal_id}")
    result = TrainingPlan(tuple(goals))
    save_training_plan(result, path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage the Markdown training plan")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list")
    complete = subparsers.add_parser("complete")
    complete.add_argument("goal_id")
    complete.add_argument("--evidence", default="人工确认完成")
    reopen = subparsers.add_parser("reopen")
    reopen.add_argument("goal_id")
    args = parser.parse_args()

    if args.command == "complete":
        plan = set_goal_status(
            args.goal_id,
            completed=True,
            evidence=args.evidence,
        )
    elif args.command == "reopen":
        plan = set_goal_status(args.goal_id, completed=False, evidence="")
    else:
        plan = load_training_plan()

    for goal in plan.goals:
        marker = "x" if goal.completed else " "
        print(f"[{marker}] {goal.goal_id}: {goal.character} / {goal.category}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
