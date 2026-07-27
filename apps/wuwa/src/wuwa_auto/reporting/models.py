"""Stable facts and report wording models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ReportItem:
    item_id: str
    text: str


@dataclass
class RunFacts:
    overall_status: str
    reason: str
    duration_seconds: int
    workflow_task: str = "daily"
    daily: list[ReportItem] = field(default_factory=list)
    weekly: list[ReportItem] = field(default_factory=list)
    followup: list[ReportItem] = field(default_factory=list)
    issues: list[ReportItem] = field(default_factory=list)
    cleanup: dict[str, Any] = field(default_factory=dict)
    user_context: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NarrativeReport:
    summary: str
    daily: list[str]
    weekly: list[str]
    followup: list[str]
    issues: list[str]
