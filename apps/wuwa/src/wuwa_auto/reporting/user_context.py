"""Load lower-priority, user-editable Markdown reporting context."""

import logging
from pathlib import Path

from game_automation_core.reporting.context import read_markdown

from wuwa_auto.settings import USER_CONTEXT_DIR

REPORTING_PREFERENCES_PATH = USER_CONTEXT_DIR / "汇报偏好.md"
TRAINING_CONTEXT_PATH = USER_CONTEXT_DIR / "培养背景.md"

log = logging.getLogger(__name__)


def _read_markdown(path: Path) -> str:
    return read_markdown(path, logger=log)


def load_reporting_context() -> dict[str, str]:
    return {
        "reporting_preferences_markdown": _read_markdown(
            REPORTING_PREFERENCES_PATH
        ),
        "training_context_markdown": _read_markdown(TRAINING_CONTEXT_PATH),
    }
