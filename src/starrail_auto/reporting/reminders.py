"""Deterministic date reminders loaded from editable Markdown."""

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from starrail_auto.settings import USER_CONTEXT_DIR

DEFAULT_REMINDERS_PATH = USER_CONTEXT_DIR / "日期提醒.md"
REMINDER_PATTERN = re.compile(
    r"^- \[(?P<checked>[ xX])\] `(?P<id>[^`]+)` (?P<label>.+)$"
)
EXPIRY_PATTERN = re.compile(r"^\s+-\s+到期日期[：:]\s*(?P<value>\d{4}-\d{2}-\d{2})$")


@dataclass(frozen=True)
class DateReminder:
    reminder_id: str
    label: str
    expires_on: date
    completed: bool = False


def load_reminders(path: Path = DEFAULT_REMINDERS_PATH) -> tuple[DateReminder, ...]:
    if not path.exists():
        return ()
    reminders: list[DateReminder] = []
    current: dict[str, object] | None = None

    def finish_current() -> None:
        nonlocal current
        if current is not None and "expires_on" in current:
            reminders.append(DateReminder(**current))
        current = None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        reminder_match = REMINDER_PATTERN.match(raw_line.strip())
        if reminder_match:
            finish_current()
            current = {
                "reminder_id": reminder_match.group("id").strip(),
                "label": reminder_match.group("label").strip(),
                "completed": reminder_match.group("checked").lower() == "x",
            }
            continue
        expiry_match = EXPIRY_PATTERN.match(raw_line)
        if expiry_match and current is not None:
            current["expires_on"] = datetime.strptime(
                expiry_match.group("value"), "%Y-%m-%d"
            ).date()
    finish_current()
    return tuple(reminders)


def format_active_reminders(
    on_date: date,
    path: Path = DEFAULT_REMINDERS_PATH,
) -> list[str]:
    messages: list[str] = []
    for reminder in load_reminders(path):
        if reminder.completed:
            continue
        remaining = (reminder.expires_on - on_date).days
        if remaining > 0:
            messages.append(f"距离{reminder.label}过期还有{remaining}天")
        elif remaining == 0:
            messages.append(f"{reminder.label}今天过期")
        else:
            messages.append(f"{reminder.label}已过期{-remaining}天")
    return messages
