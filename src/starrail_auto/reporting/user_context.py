"""Load lower-priority user Markdown context."""

import logging
import re
from pathlib import Path
from typing import Any

from starrail_auto.settings import USER_CONTEXT_DIR

REPORTING_PREFERENCES_PATH = USER_CONTEXT_DIR / "汇报偏好.md"
TRAINING_CONTEXT_PATH = USER_CONTEXT_DIR / "培养背景.md"
TRAINING_PLAN_PATH = USER_CONTEXT_DIR / "养成计划.md"

log = logging.getLogger(__name__)


def _field(markdown: str, *labels: str) -> str:
    alternatives = "|".join(re.escape(label) for label in labels)
    match = re.search(
        rf"^[ \t]*-[ \t]*(?:{alternatives})[ \t]*[：:][ \t]*"
        rf"(?P<value>[^\r\n]*?)[ \t]*$",
        markdown,
        flags=re.MULTILINE,
    )
    return match.group("value").strip() if match else ""


def load_training_context(path: Path = TRAINING_CONTEXT_PATH) -> dict[str, Any]:
    """Load free-form Markdown plus optional fields used for strict matching."""
    if not path.exists():
        return {}
    try:
        markdown = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        log.warning("training context cannot be loaded: %s", exc)
        return {}

    parseable_markdown = re.sub(r"<!--.*?-->", "", markdown, flags=re.DOTALL)
    character = _field(parseable_markdown, "角色")
    goal = _field(parseable_markdown, "培养目标", "目标")
    keyword_text = _field(
        parseable_markdown,
        "关联副本或关键词",
        "关联副本",
        "关联关键词",
    )
    keywords = [
        item.strip()
        for item in re.split(r"[、,，;；]", keyword_text)
        if item.strip()
    ]

    context: dict[str, Any] = {
        "training_context_markdown": parseable_markdown.strip(),
    }
    if character:
        context["character_goals"] = [
            {
                "character": character,
                "goal": goal,
                "keywords": keywords,
            }
        ]
    return context


def load_reporting_preferences(
    path: Path = REPORTING_PREFERENCES_PATH,
) -> str:
    """Load optional user emphasis without promoting it to system instructions."""
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        log.warning("reporting preferences cannot be loaded: %s", exc)
        return ""


def load_reporting_context() -> dict[str, Any]:
    """Combine user notes and the structured Markdown training plan."""
    from starrail_auto.reporting.training_plan import load_training_plan

    context = load_training_context()
    plan = load_training_plan(TRAINING_PLAN_PATH)
    context["reporting_preferences_markdown"] = load_reporting_preferences()
    context["training_plan"] = plan.to_context()

    mapped_goals = [
        {
            "character": goal.character,
            "goal": goal.category,
            "keywords": [goal.dungeon],
        }
        for goal in plan.goals
        if goal.dungeon and goal.dungeon != "待填写"
    ]
    if mapped_goals:
        context.setdefault("character_goals", []).extend(mapped_goals)
    return context
