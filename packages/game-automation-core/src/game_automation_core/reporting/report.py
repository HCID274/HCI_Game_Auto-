"""两款游戏共用的日报：一张卡片说清今天做了什么、哪里没成。

各游戏只负责把自己的运行事实翻译成带状态符号的任务行和中文异常说明；
标题、颜色、栏目顺序和凭据脱敏统一在这里处理，保证两张卡片长得一样。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from game_automation_core.reporting.feishu import build_sectioned_card
from game_automation_core.reporting.redact import redact_sensitive_data

# 每条任务行以其中一个状态符号开头。
DONE = "✅"
FAILED = "❌"
WARN = "⚠️"
SKIPPED = "⏸️"
INFO = "ℹ️"

# 整体状态 -> (标题图标, 结论, 卡片颜色)
_HEADERS = {
    "completed": ("✅", "全部完成", "green"),
    "partial": ("⚠️", "部分完成", "orange"),
    "failed": ("❌", "失败", "red"),
}


def settle_status(
    status: str, tasks: Sequence[str], problems: Sequence[str] = ()
) -> str:
    """“全部完成”只留给每行都成功且没有异常说明的运行，否则降为部分完成。"""

    unfinished = any(line.startswith((FAILED, WARN)) for line in tasks)
    if status == "completed" and (unfinished or problems):
        return "partial"
    return status


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, minutes = divmod(seconds // 60, 60)
    if hours:
        return f"{hours}小时{minutes}分钟" if minutes else f"{hours}小时"
    if minutes:
        return f"{minutes}分钟"
    return f"{seconds}秒"


@dataclass(frozen=True)
class GameReport:
    """一次运行的最终日报。

    ``tasks`` 每行已带状态符号；``problems`` 是给人看的中文异常说明，
    有异常时排在最前面，用户打开卡片先看到哪里没成。
    """

    game: str
    status: str
    finished_at: datetime
    tasks: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    notes: list[tuple[str, list[str]]] = field(default_factory=list)
    duration_seconds: int | None = None

    def __post_init__(self) -> None:
        if self.status not in _HEADERS:
            raise ValueError(f"unknown report status: {self.status!r}")

    @property
    def title(self) -> str:
        icon, verdict, _ = _HEADERS[self.status]
        return f"{icon} {self.game} {verdict} · {self.finished_at:%m-%d %H:%M}"

    def to_card(self) -> dict[str, Any]:
        lead = (
            f"用时 {format_duration(self.duration_seconds)}"
            if self.duration_seconds
            else ""
        )
        card = build_sectioned_card(
            title=self.title,
            template=_HEADERS[self.status][2],
            lead=lead,
            sections=[
                ("异常记录", self.problems),
                ("今日任务", "\n".join(self.tasks)),
                *self.notes,
            ],
        )
        return redact_sensitive_data(card)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["finished_at"] = self.finished_at.isoformat()
        data["title"] = self.title
        return redact_sensitive_data(data)
