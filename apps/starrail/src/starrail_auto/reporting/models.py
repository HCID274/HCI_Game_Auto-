"""Facts extracted from one M7A run; the report builder turns them into lines."""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class StaminaRun:
    name: str
    source: str = "plan"  # plan | activity | default（计划外清体力）
    activity_name: str = ""
    activity_start_remaining: int | None = None
    activity_remaining_count: int | None = None
    planned_count: int | None = None
    rounds: int | None = None
    rewards_per_round: int | None = None
    completed_instances: int = 0
    remaining_plan_count: int | None = None
    status: str = "started"  # started | completed | skipped | failed
    reason: str = ""
    trainee: str = ""  # 例如“远坂凛 行迹材料”，只来自养成计划或 M7A 培养目标


@dataclass
class RunReport:
    overall_status: str = "unknown"
    daily_status: str = "unknown"
    daily_score: str = "未读取"
    daily_already_settled: bool = False
    daily_completed_this_run: list[str] = field(default_factory=list)
    daily_unfinished: list[str] = field(default_factory=list)
    stamina_runs: list[StaminaRun] = field(default_factory=list)
    rewards: list[str] = field(default_factory=list)
    other_tasks: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    current_task: str = ""
    current_reason: str = ""
    stopped_normally: bool = False
    last_log_at: datetime | None = None
    run_stage: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["last_log_at"] = self.last_log_at.isoformat() if self.last_log_at else None
        return data
