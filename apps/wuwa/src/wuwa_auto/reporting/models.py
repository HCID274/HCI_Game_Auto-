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
    """``daily_ok``/``boss_ok`` 为 None 表示本轮没有跑这个阶段。

    异常按阶段分开存：同日合并时每个阶段只沿用它最近一次结算的说明，
    不会把已经补跑成功的阶段的旧失败带进日报。
    """

    overall_status: str
    workflow_task: str
    reason: str
    duration_seconds: int
    daily_ok: bool | None = None
    # 日常跑到了 Daily Task Completed；没全部完成时由具体任务行说明，不再写“中途停止”。
    daily_finished: bool = False
    boss_ok: bool | None = None
    daily: list[ReportItem] = field(default_factory=list)
    weekly: list[ReportItem] = field(default_factory=list)
    boss: list[ReportItem] = field(default_factory=list)
    boss_issues: list[str] = field(default_factory=list)
    daily_issues: list[str] = field(default_factory=list)
    other_issues: list[str] = field(default_factory=list)
    cleanup: dict[str, Any] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)

    @property
    def issues(self) -> list[str]:
        """卡片上的异常记录：讨伐、日常、其他，最后是收尾问题。"""

        lines = [
            *self.boss_issues,
            *self.daily_issues,
            *self.other_issues,
            *(str(issue) for issue in self.cleanup.get("issues", [])),
        ]
        return list(dict.fromkeys(lines))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
