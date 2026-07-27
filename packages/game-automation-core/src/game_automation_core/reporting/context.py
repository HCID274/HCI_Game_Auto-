"""Safe readers for lower-priority, user-editable Markdown context."""

from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)


def strip_markdown_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()


def read_markdown(
    path: Path,
    *,
    strip_comments: bool = True,
    logger: logging.Logger = log,
) -> str:
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("user context cannot be loaded from %s: %s", path, exc)
        return ""
    return strip_markdown_comments(text) if strip_comments else text.strip()
