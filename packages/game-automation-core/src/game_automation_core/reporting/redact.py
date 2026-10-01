"""Credential redaction applied to every outgoing card and archive."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_SENSITIVE = (
    (re.compile(r"sk-[A-Za-z0-9_-]{16,}"), "[已隐藏API密钥]"),
    (
        re.compile(r"https?://[^\s]+(?:hook|webhook)[^\s]*", re.IGNORECASE),
        "[已隐藏Webhook]",
    ),
    (
        re.compile(
            r"((?:[\"'])(?:[A-Za-z_][A-Za-z0-9_]*_)?"
            r"(?:api[_-]?key|token|secret|password|passwd|"
            r"webhook[_-]?(?:url|secret))(?:[\"'])\s*[:=]\s*"
            r"[\"']?)[^\"',;\s}]+",
            re.IGNORECASE,
        ),
        r"\1[已隐藏]",
    ),
    (
        re.compile(
            r"((?:\b[A-Za-z_][A-Za-z0-9_]*_)?"
            r"(?:api[_-]?key|token|secret|password|passwd|"
            r"webhook[_-]?(?:url|secret))\b\s*[:=]\s*)"
            r"[^\s,;]+",
            re.IGNORECASE,
        ),
        r"\1[已隐藏]",
    ),
    (
        re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
        r"\1[已隐藏]",
    ),
)


def redact_text(text: str) -> str:
    for pattern, replacement in _SENSITIVE:
        text = pattern.sub(replacement, text)
    return text


def redact_sensitive_data(value: Any) -> Any:
    """Recursively redact credential-like strings in nested report data."""

    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {key: redact_sensitive_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_sensitive_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive_data(item) for item in value)
    return value
