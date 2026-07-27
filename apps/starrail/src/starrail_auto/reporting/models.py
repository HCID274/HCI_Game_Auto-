"""Data structures shared by the report parser, AI writer and Feishu renderer."""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class StaminaRun:
    name: str
    source: str = "plan"
    activity_name: str = ""
    activity_start_remaining: int | None = None
    activity_remaining_count: int | None = None
    plan_index: int | None = None
    plan_total: int | None = None
    planned_count: int | None = None
    rounds: int | None = None
    rewards_per_round: int | None = None
    completed_instances: int = 0
    remaining_plan_count: int | None = None
    status: str = "started"
    reason: str = ""
    character_context: str = ""


@dataclass
class RunEvent:
    kind: str
    label: str = ""
    stamina_index: int | None = None


@dataclass
class RunReport:
    overall_status: str = "unknown"
    daily_status: str = "unknown"
    daily_score: str = "未读取"
    daily_initial_completed: list[str] = field(default_factory=list)
    daily_completed_this_run: list[str] = field(default_factory=list)
    daily_unfinished: list[str] = field(default_factory=list)
    stamina_runs: list[StaminaRun] = field(default_factory=list)
    stamina_start: int | None = None
    stamina_end: int | None = None
    daily_events: list[RunEvent] = field(default_factory=list)
    rewards_completed: list[str] = field(default_factory=list)
    other_tasks: list[str] = field(default_factory=list)
    recovered_warnings: list[str] = field(default_factory=list)
    current_task: str = ""
    current_reason: str = ""
    detected_training_target: str = ""
    detected_training_dungeons: list[str] = field(default_factory=list)
    stopped_normally: bool = False
    last_log_at: datetime | None = None
    run_stage: str = ""
    retries: int = 0
    custom_context: dict[str, Any] = field(default_factory=dict)

    def to_prompt_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["last_log_at"] = self.last_log_at.isoformat() if self.last_log_at else None
        return data


@dataclass(frozen=True)
class NarrativeReport:
    daily: str
    routine_tasks: list[str] = field(default_factory=list)
    other_tasks: list[str] = field(default_factory=list)
    current_task: str = ""
    issues: list[str] = field(default_factory=list)
    training_todos: list[str] = field(default_factory=list)
