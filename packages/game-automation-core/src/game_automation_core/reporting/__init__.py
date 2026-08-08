"""Reusable report transport, context, and evidence helpers."""

from game_automation_core.reporting.agent import (
    AgentResponseError,
    TokenUsage,
    build_evidence_bundle,
    diagnostic_lines,
    diagnostics_match_status,
    redact_sensitive_data,
    token_usage_from_response,
    validate_diagnostics,
)
from game_automation_core.reporting.archive import write_json_archive
from game_automation_core.reporting.context import (
    read_markdown,
    strip_markdown_comments,
)
from game_automation_core.reporting.feishu import (
    build_sectioned_card,
    make_signature,
    numbered_lines,
    send_signed_payload,
)

__all__ = [
    "AgentResponseError",
    "TokenUsage",
    "build_evidence_bundle",
    "build_sectioned_card",
    "diagnostic_lines",
    "diagnostics_match_status",
    "make_signature",
    "numbered_lines",
    "read_markdown",
    "redact_sensitive_data",
    "send_signed_payload",
    "strip_markdown_comments",
    "token_usage_from_response",
    "validate_diagnostics",
    "write_json_archive",
]
