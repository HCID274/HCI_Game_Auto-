"""同一天讨伐和日常分开跑完时，合并成一份当天日报。"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from wuwa_auto.reporting.models import ReportItem, RunFacts
from wuwa_auto.reporting.parser import DAILY_ITEM_ORDER, parse_run
from wuwa_auto.settings import REPORTS_DIR, RUNS_DIR


@dataclass(frozen=True)
class _Candidate:
    run_id: str
    finished_at: str
    facts: RunFacts

    @property
    def sort_key(self) -> tuple[str, str]:
        return self.finished_at, self.run_id


def _archived_candidates(
    day: str, boss_names: Mapping[int, str] | None
) -> list[_Candidate]:
    from wuwa_auto.okww.runner import OkRunResult

    candidates: list[_Candidate] = []
    if not REPORTS_DIR.is_dir():
        return candidates
    for path in sorted(REPORTS_DIR.glob(f"{day}*.json")):
        if path.name.endswith(".preview.json"):
            continue
        try:
            run_id = str(json.loads(path.read_text(encoding="utf-8")).get("run_id") or "")
            result_path = RUNS_DIR / run_id / "result.json"
            result = OkRunResult(**json.loads(result_path.read_text(encoding="utf-8")))
        except (OSError, TypeError, ValueError, AttributeError):
            continue
        if not run_id:
            continue
        # 归档本身可能就是合并结果；只重新解析那次运行自己的结果和日志，
        # 避免旧结论一层层叠进以后的日报。
        facts = parse_run(result, boss_names=boss_names)
        if facts.workflow_task != "weekly_garden":
            candidates.append(_Candidate(run_id, result.finished_at, facts))
    return candidates


def _daily_order(item: ReportItem) -> int:
    # 新增的日常项还没排进顺序表时放在最后，不让整张日报出错。
    order = DAILY_ITEM_ORDER
    return order.index(item.item_id) if item.item_id in order else len(order)


def _latest(candidates: Iterable[_Candidate]) -> _Candidate | None:
    # 最近一次结算为准：早先的成功不能盖住后来的失败。
    return max(candidates, key=lambda item: item.sort_key, default=None)


def build_daily_rollup(
    result: Any,
    facts: RunFacts,
    *,
    boss_names: Mapping[int, str] | None = None,
) -> RunFacts:
    """返回当天的合并事实；周常、单独的阶段和当天唯一一次运行原样返回。"""

    run_id = str(getattr(result, "run_id", ""))
    day = run_id[:8]
    if facts.workflow_task == "weekly_garden" or not (len(day) == 8 and day.isdigit()):
        return facts

    current = _Candidate(run_id, str(getattr(result, "finished_at", "")), facts)
    # 只合并本次之前结算的运行：重放早先的运行时不能看到当天后来的结果。
    by_run_id = {
        item.run_id: item
        for item in _archived_candidates(day, boss_names)
        if item.sort_key <= current.sort_key
    }
    by_run_id[run_id] = current
    candidates = sorted(by_run_id.values(), key=lambda item: item.sort_key)
    daily = _latest(item for item in candidates if item.facts.daily_ok is not None)
    boss = _latest(
        item for item in candidates if item.facts.boss_ok is not None or item.facts.boss
    )
    if daily is None or boss is None or len(candidates) == 1:
        return facts

    selected = list({item.run_id: item for item in (daily, boss)}.values())
    # 补跑只更新本轮做到的项目，不能抹掉当天早先已确认的成果；
    # 每日活跃度只认最新一次日常，旧的领取状态可能已经过期。
    history = [item for item in candidates if item is daily or item.facts.daily_ok]
    daily_items: dict[str, ReportItem] = {}
    for candidate in history:
        for item in candidate.facts.daily:
            if candidate is daily or item.item_id != "daily-activity":
                daily_items[item.item_id] = item

    latest = candidates[-1]
    daily_ok, boss_ok = daily.facts.daily_ok, boss.facts.boss_ok
    if daily_ok and boss_ok:
        status = "completed"
    elif daily_ok or boss_ok or any(item.facts.overall_status != "failed" for item in selected):
        status = "partial"
    else:
        status = "failed"
    sources = {item.run_id for item in (*history, *selected)}
    return RunFacts(
        overall_status=status,
        workflow_task="daily",
        reason="; ".join(dict.fromkeys(item.facts.reason for item in selected)),
        duration_seconds=sum(item.facts.duration_seconds for item in selected),
        daily_ok=daily_ok,
        boss_ok=boss_ok,
        daily=sorted(daily_items.values(), key=_daily_order),
        weekly=list(daily.facts.weekly),
        boss=list(boss.facts.boss),
        # 每个阶段的异常只取该阶段最近一次结算；收尾问题只看当天最后一次运行。
        boss_issues=list(boss.facts.boss_issues),
        daily_issues=list(daily.facts.daily_issues),
        cleanup=latest.facts.cleanup,
        sources=[item.run_id for item in candidates if item.run_id in sources],
    )
