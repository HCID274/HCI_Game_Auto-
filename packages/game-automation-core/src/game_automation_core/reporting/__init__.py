"""Reusable reporting transport and user-context helpers."""
"""Reusable report transport, context, and evidence helpers."""

from game_automation_core.reporting.archive import write_json_archive
from game_automation_core.reporting.context import read_markdown, strip_markdown_comments
from game_automation_core.reporting.feishu import (
    build_sectioned_card,
    make_signature,
    numbered_lines,
    send_signed_payload,
)

__all__ = [
    "build_sectioned_card",
    "make_signature",
    "numbered_lines",
    "read_markdown",
    "send_signed_payload",
    "strip_markdown_comments",
    "write_json_archive",
]
