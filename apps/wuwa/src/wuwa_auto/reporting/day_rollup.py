"""Merge independently settled Wuwa phases into one same-day report input."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from game_automation_core.reporting.agent import build_evidence_bundle

from wuwa_auto.reporting.models import ReportItem, RunFacts
from wuwa_auto.reporting.noise import known_upstream_noise_lines
from wuwa_auto.settings import REPORTS_DIR, RUNS_DIR


@dataclass(frozen=True)
class _Candidate:
    run_id: str
    result: Any
    facts: RunFacts

    @property
    def sort_key(self) -> str:
        return str(getattr(self.result, "finished_at", "")) or self.run_id

    @property
    def sequence(self) -> dict[str, Any]:
        value = getattr(self.result, "config", {}).get("daily_sequence") or {}
        return value if isinstance(value, dict) else {}

    @property
    def workflow(self) -> str:
        configured = getattr(self.result, "config", {}).get("workflow_task")
        return str(configured or self.facts.workflow_task)

    @property
    def daily_attempted(self) -> bool:
        return self.workflow == "daily" or bool(self.sequence.get("daily_status"))

    @property
    def daily_succeeded(self) -> bool:
        if self.sequence.get("daily_status"):
            return self.sequence.get("daily_status") == "success"
        return (
            self.workflow == "daily"
            and getattr(self.result, "status", "") == "success"
        )

    @property
    def followup_attempted(self) -> bool:
        return (
            self.workflow in {"farm_echo", "farm_echo_confirmed_retry"}
            or bool(self.sequence.get("boss_status"))
            or bool(self.facts.followup)
        )

    @property
    def followup_succeeded(self) -> bool:
        if self.sequence.get("boss_status"):
            return self.sequence.get("boss_status") == "success"
        return (
            self.workflow in {"farm_echo", "farm_echo_confirmed_retry"}
            and getattr(self.result, "status", "") == "success"
        )


def _read_result(path: Path) -> Any | None:
    try:
        from wuwa_auto.okww.runner import OkRunResult

        return OkRunResult(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _archived_candidates(day: str) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    if not REPORTS_DIR.is_dir():
        return candidates
    for path in sorted(REPORTS_DIR.glob(f"{day}*.json")):
        if path.name.endswith(".preview.json"):
            continue
        try:
            archive = json.loads(path.read_text(encoding="utf-8"))
            run_id = str(archive.get("run_id", ""))
            facts_value = archive.get("facts")
            if not run_id or not isinstance(facts_value, dict):
                continue
            result = _read_result(RUNS_DIR / run_id / "result.json")
            if result is None:
                continue
            facts = RunFacts.from_dict(facts_value)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if facts.workflow_task == "weekly_garden":
            continue
        candidates.append(_Candidate(run_id, result, facts))
    return candidates


def _select_stage(
    candidates: list[_Candidate],
    *,
    attempted: str,
    succeeded: str,
) -> _Candidate | None:
    stage = [item for item in candidates if getattr(item, attempted)]
    successful = [item for item in stage if getattr(item, succeeded)]
    pool = successful or stage
    return max(pool, key=lambda item: item.sort_key) if pool else None


def _dedupe_items(items: list[ReportItem]) -> list[ReportItem]:
    seen: set[tuple[str, str]] = set()
    result: list[ReportItem] = []
    for item in items:
        key = (item.item_id, item.text)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _combined_evidence(candidates: list[_Candidate]) -> dict[str, Any]:
    parts: list[str] = []
    source_ids: list[str] = []
    for item in sorted(candidates, key=lambda candidate: candidate.sort_key):
        path = Path(str(getattr(item.result, "log_slice_path", "")))
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace").rstrip()
        parts.append(f"=== HOST DAY PHASE {item.run_id} ===\n{text}")
        source_ids.append(item.run_id)
    merged = "\n\n".join(parts)
    return build_evidence_bundle(
        game="wuwa",
        log_text=merged,
        source=",".join(source_ids),
        ignored_line_numbers=known_upstream_noise_lines(merged),
    )


def build_daily_rollup(result: Any, cleanup: Any | None, facts: RunFacts) -> RunFacts:
    """Return final same-day phase state, keeping weekly reports independent."""

    if facts.workflow_task == "weekly_garden":
        return facts
    run_id = str(getattr(result, "run_id", ""))
    day = run_id[:8]
    if len(day) != 8 or not day.isdigit():
        return facts

    by_run_id = {item.run_id: item for item in _archived_candidates(day)}
    by_run_id[run_id] = _Candidate(run_id, result, facts)
    candidates = sorted(by_run_id.values(), key=lambda item: item.sort_key)
    daily = _select_stage(
        candidates,
        attempted="daily_attempted",
        succeeded="daily_succeeded",
    )
    followup = _select_stage(
        candidates,
        attempted="followup_attempted",
        succeeded="followup_succeeded",
    )

    # A genuinely standalone phase remains a phase report.  Once both daily
    # and boss/echo stages exist, they form one Wuwa daily notification input.
    if daily is None or followup is None:
        return facts

    selected = list({item.run_id: item for item in (daily, followup)}.values())
    issues: list[ReportItem] = []
    if not daily.daily_succeeded:
        issues.extend(daily.facts.issues)
    if not followup.followup_succeeded:
        issues.extend(followup.facts.issues)

    latest = max(candidates, key=lambda item: item.sort_key)
    cleanup_data = latest.facts.cleanup
    if cleanup_data and not cleanup_data.get("completed", False):
        issues.extend(
            item for item in latest.facts.issues if item.item_id.startswith("cleanup-")
        )

    daily_ok = daily.daily_succeeded
    followup_ok = followup.followup_succeeded
    if daily_ok and followup_ok and not issues:
        status = "completed"
    elif daily_ok or followup_ok:
        status = "partial_success"
    else:
        status = "failed"

    return RunFacts(
        overall_status=status,
        reason=(
            "same-day daily and FarmEcho phases completed"
            if status == "completed"
            else "; ".join(item.facts.reason for item in selected if item.facts.reason)
        ),
        duration_seconds=sum(item.facts.duration_seconds for item in selected),
        workflow_task="daily",
        daily_activity=daily.facts.daily_activity,
        daily=list(daily.facts.daily),
        weekly=[],
        followup=list(followup.facts.followup),
        issues=_dedupe_items(issues),
        cleanup=cleanup_data,
        user_context={},
        evidence=_combined_evidence(selected),
    )
