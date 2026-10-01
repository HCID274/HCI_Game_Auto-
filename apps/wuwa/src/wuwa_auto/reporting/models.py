"""鸣潮一次运行（或同日合并后）的汇报事实。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ReportItem:
    """卡片上的一行任务；``item_id`` 用于同日多阶段按任务合并。"""

    item_id: str
    mark: str
    text: str

    @property
    def line(self) -> str:
        return f"{self.mark} {self.text}"


@dataclass
class RunFacts:
    """``daily_ok``/``boss_ok`` 为 None 表示本轮没有跑这个阶段。"""

    overall_status: str
    workflow_task: str
    reason: str
    duration_seconds: int
    daily_ok: bool | None = None
    boss_ok: bool | None = None
    daily: list[ReportItem] = field(default_factory=list)
    weekly: list[ReportItem] = field(default_factory=list)
    boss: list[ReportItem] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    cleanup: dict[str, Any] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
