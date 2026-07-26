"""Load lower-priority, user-editable Markdown reporting context."""

import logging
import re
from pathlib import Path

from wuwa_auto.settings import USER_CONTEXT_DIR

REPORTING_PREFERENCES_PATH = USER_CONTEXT_DIR / "汇报偏好.md"
TRAINING_CONTEXT_PATH = USER_CONTEXT_DIR / "培养背景.md"

log = logging.getLogger(__name__)


def _read_markdown(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("user context cannot be loaded from %s: %s", path, exc)
        return ""
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()


def load_reporting_context() -> dict[str, str]:
    return {
        "reporting_preferences_markdown": _read_markdown(
            REPORTING_PREFERENCES_PATH
        ),
        "training_context_markdown": _read_markdown(TRAINING_CONTEXT_PATH),
    }
