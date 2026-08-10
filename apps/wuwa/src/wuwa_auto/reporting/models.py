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
    daily_activity: dict[str, Any] = field(default_factory=dict)
    daily: list[ReportItem] = field(default_factory=list)
    weekly: list[ReportItem] = field(default_factory=list)
    followup: list[ReportItem] = field(default_factory=list)
    issues: list[ReportItem] = field(default_factory=list)
    cleanup: dict[str, Any] = field(default_factory=dict)
    user_context: dict[str, str] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RunFacts:
        """Restore archived facts without depending on an AI-written report."""

        def items(name: str) -> list[ReportItem]:
            raw_items = value.get(name, [])
            if not isinstance(raw_items, list):
                return []
            restored: list[ReportItem] = []
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("item_id", "")).strip()
                text = str(item.get("text", "")).strip()
                if item_id and text:
                    restored.append(ReportItem(item_id, text))
            return restored

        return cls(
            overall_status=str(value.get("overall_status", "unknown")),
            reason=str(value.get("reason", "")),
            duration_seconds=int(value.get("duration_seconds") or 0),
            workflow_task=str(value.get("workflow_task", "daily")),
            daily_activity=dict(value.get("daily_activity") or {}),
            daily=items("daily"),
            weekly=items("weekly"),
            followup=items("followup"),
            issues=items("issues"),
            cleanup=dict(value.get("cleanup") or {}),
            user_context=dict(value.get("user_context") or {}),
            evidence=dict(value.get("evidence") or {}),
        )


@dataclass(frozen=True)
class NarrativeReport:
    summary: str
    daily: list[str]
    weekly: list[str]
    followup: list[str]
    issues: list[str]
    analysis: dict[str, Any] = field(default_factory=dict)
    token_usage: dict[str, Any] = field(default_factory=dict)
