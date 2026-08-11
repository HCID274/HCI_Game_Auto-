"""Evidence-grounded reporting-agent primitives shared by both games.

The game adapters remain responsible for domain facts.  This module only
normalises evidence, extracts provider token usage, and validates the small
diagnostic object returned by the language model.  It deliberately has no
OpenAI/DeepSeek dependency so the reporting pipeline can be replayed offline.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import asdict, dataclass
from typing import Any

_MAX_EVIDENCE_CHARS = 14_000
_MAX_EVIDENCE_LINES = 180
_INTERESTING_LINE = re.compile(
    r"(?i)(?:error|warning|critical|failed|exception|timeout|未完成|未确认|异常|"
    r"完成|领取|奖励|任务|daily|tacet|garden|echo|boss|activity|claim|"
    r"每日|周常|体力|副本|停止运行|DailyTask|FarmEchoTask|NightmareNestTask)"
)
_SENSITIVE = (
    (re.compile(r"sk-[A-Za-z0-9_-]{16,}"), "[已隐藏API密钥]"),
    (
        re.compile(r"https?://[^\s]+(?:hook|webhook)[^\s]*", re.IGNORECASE),
        "[已隐藏Webhook]",
    ),
    (
        re.compile(r"(兑换码使用(?:成功|失败):)\s+\S+", re.IGNORECASE),
        r"\1 [已隐藏]",
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


@dataclass(frozen=True)
class TokenUsage:
    """Provider-reported token counts; never estimate missing usage."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    output_input_ratio: float | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    available: bool = False
    model: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AgentResponseError(RuntimeError):
    """A provider response was received but failed local validation."""

    def __init__(self, message: str, *, token_usage: dict[str, Any] | None = None):
        super().__init__(message)
        self.token_usage = token_usage or TokenUsage().to_dict()


def _read_value(value: Any, *names: str) -> Any:
    if value is None:
        return None
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
        return None
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return None


def _non_negative_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return max(0, number)


def token_usage_from_response(response: Any, *, model: str = "") -> TokenUsage:
    """Extract OpenAI-compatible usage across SDK and test-double shapes."""

    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, Mapping):
        usage = response.get("usage")
    prompt = _non_negative_int(
        _read_value(usage, "prompt_tokens", "input_tokens")
    )
    completion = _non_negative_int(
        _read_value(usage, "completion_tokens", "output_tokens")
    )
    total = _non_negative_int(_read_value(usage, "total_tokens"))
    prompt_details = _read_value(usage, "prompt_tokens_details", "input_tokens_details")
    cached = _non_negative_int(
        _read_value(usage, "prompt_cache_hit_tokens", "cached_tokens")
    )
    if cached is None:
        cached = _non_negative_int(
            _read_value(prompt_details, "cached_tokens", "cache_read_input_tokens")
        )
    details = _read_value(usage, "completion_tokens_details", "output_tokens_details")
    reasoning = _non_negative_int(
        _read_value(details, "reasoning_tokens", "reasoning_output_tokens")
    )
    if prompt is None or completion is None:
        return TokenUsage(model=model)
    total = total if total is not None else prompt + completion
    ratio = round(completion / prompt, 4) if prompt else None
    return TokenUsage(
        input_tokens=prompt,
        output_tokens=completion,
        total_tokens=total,
        output_input_ratio=ratio,
        cached_input_tokens=cached,
        reasoning_tokens=reasoning,
        available=True,
        model=model,
    )


def _redact(text: str) -> str:
    for pattern, replacement in _SENSITIVE:
        text = pattern.sub(replacement, text)
    return text


def redact_sensitive_data(value: Any) -> Any:
    """Recursively redact credential-like strings before AI/archive use."""

    if isinstance(value, str):
        return _redact(value)
    if isinstance(value, Mapping):
        return {key: redact_sensitive_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_sensitive_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive_data(item) for item in value)
    return value


def _select_evidence_lines(
    lines: list[str],
    *,
    max_lines: int = _MAX_EVIDENCE_LINES,
    ignored_line_numbers: Collection[int] = (),
) -> list[tuple[int, str]]:
    ignored = set(ignored_line_numbers)
    all_items = [
        (number, line)
        for number, line in enumerate(lines, 1)
        if number not in ignored
    ]
    if len(all_items) <= max_lines:
        return all_items
    interesting = [item for item in all_items if _INTERESTING_LINE.search(item[1])]
    # Keep the run boundary and latest state first; then fill with relevant
    # middle lines.  Sorting only after the bounded selection prevents a noisy
    # prefix from crowding out the final error marker.
    selected: list[tuple[int, str]] = []
    for item in all_items[:12] + all_items[-24:] + interesting:
        if item not in selected:
            selected.append(item)
        if len(selected) >= max_lines:
            break
    return sorted(selected, key=lambda item: item[0])


def build_evidence_bundle(
    *,
    game: str,
    log_text: str,
    source: str = "",
    max_chars: int = _MAX_EVIDENCE_CHARS,
    max_lines: int = _MAX_EVIDENCE_LINES,
    ignored_line_numbers: Collection[int] = (),
) -> dict[str, Any]:
    """Create a bounded, line-addressable evidence bundle for the Agent.

    The full log remains the deterministic parser's input.  The model only
    receives a bounded, redacted slice with stable ``L<number>`` references,
    so it can explain anomalies without being allowed to invent business
    facts or consume credentials accidentally.
    """

    lines = log_text.splitlines()
    selected = _select_evidence_lines(
        lines,
        max_lines=max_lines,
        ignored_line_numbers=ignored_line_numbers,
    )
    line_count = len(lines)
    # Use the last retained lines rather than the physical log tail.  A
    # filtered startup block at the end must not push the final real error out
    # of the bounded Agent context.
    tail_refs = {item[0] for item in selected[-24:]}
    tail = [item for item in selected if item[0] in tail_refs]
    head = [item for item in selected if item[0] <= 12]
    middle = [item for item in selected if item not in tail and item not in head]
    priority: list[tuple[int, str]] = []
    seen_lines: set[int] = set()
    for item in list(reversed(tail)) + head + middle:
        if item[0] in seen_lines:
            continue
        seen_lines.add(item[0])
        priority.append(item)
    entries: list[dict[str, str | int]] = []
    used = 0
    for number, raw in priority:
        text = _redact(raw)
        available = max(0, max_chars - used - 14)
        if available <= 0:
            continue
        text = text[:available]
        item_size = len(text) + 14
        if used + item_size > max_chars:
            continue
        entries.append({"ref": f"L{number}", "line": number, "text": text})
        used += item_size
    entries.sort(key=lambda item: int(item["line"]))
    return {
        "schema_version": "report-agent-evidence.v1",
        "game": game,
        "source": source,
        "line_count": line_count,
        "line_refs": [entry["ref"] for entry in entries],
        "lines": entries,
    }


def validate_diagnostics(
    value: Any,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep only bounded diagnostic claims that cite observed evidence lines."""

    if not isinstance(value, Mapping):
        return {}
    allowed_refs = {str(item) for item in evidence.get("line_refs", [])}

    def valid_refs(value: Any) -> list[str]:
        if not isinstance(value, (list, tuple)):
            return []
        return [str(ref) for ref in value if str(ref) in allowed_refs]

    anomalies: list[dict[str, Any]] = []
    raw_anomalies = value.get("anomalies", [])
    if isinstance(raw_anomalies, list):
        for item in raw_anomalies[:8]:
            if not isinstance(item, Mapping):
                continue
            message = str(item.get("message", "")).strip()
            refs = valid_refs(item.get("evidence_refs"))
            confidence = str(item.get("confidence", "medium")).casefold()
            if not message or not refs or confidence not in {"low", "medium", "high"}:
                continue
            anomalies.append(
                {
                    "code": str(item.get("code", "agent-observation"))[:80],
                    "message": message[:280],
                    "evidence_refs": refs,
                    "confidence": confidence,
                }
            )
    root_cause = str(value.get("root_cause", "")).strip()[:360]
    root_refs = valid_refs(value.get("root_cause_refs"))
    uncertainty = [
        str(item).strip()[:220]
        for item in value.get("uncertainties", [])[:8]
        if str(item).strip()
    ] if isinstance(value.get("uncertainties", []), list) else []
    result: dict[str, Any] = {"anomalies": anomalies, "uncertainties": uncertainty}
    if root_cause and root_refs:
        result["root_cause"] = root_cause
        result["root_cause_refs"] = root_refs
    return result if anomalies or uncertainty or "root_cause" in result else {}


def diagnostics_match_status(
    analysis: Mapping[str, Any] | None,
    status: str,
) -> bool:
    """Reject diagnostic prose that reverses the deterministic run status."""

    if not isinstance(analysis, Mapping):
        return True
    text_parts = [str(analysis.get("root_cause", ""))]
    for item in analysis.get("anomalies", []):
        if isinstance(item, Mapping):
            text_parts.append(str(item.get("message", "")))
    text_parts.extend(str(item) for item in analysis.get("uncertainties", []))
    text = "".join(text_parts)
    if status == "completed":
        return not re.search(r"主流程(?:已)?失败|奖励(?:未|没有)领取|每日活跃(?:度)?未确认", text)
    if status == "failed":
        return not re.search(
            r"主流程(?:已)?完成|奖励(?:已|已经)领取|本轮(?:已|已经)?完成|成功",
            text,
        )
    if status == "partial_success":
        return not re.search(r"全部完成|完全完成|奖励(?:已|已经)全部领取", text)
    if status in {"unknown", "in_progress", "stalled"}:
        return not re.search(
            r"主流程(?:已)?完成|奖励(?:已|已经)领取|本轮(?:已|已经)?完成|成功",
            text,
        )
    return True


def diagnostic_lines(analysis: Mapping[str, Any] | None) -> list[str]:
    """Render validated Agent diagnostics without hiding deterministic issues."""

    if not isinstance(analysis, Mapping):
        return []
    lines: list[str] = []
    root = str(analysis.get("root_cause", "")).strip()
    if root:
        refs = "、".join(str(ref) for ref in analysis.get("root_cause_refs", []))
        suffix = f"（证据{refs}）" if refs else ""
        lines.append(f"AI分析：{_redact(root)}{suffix}")
    for item in analysis.get("anomalies", []):
        if isinstance(item, Mapping) and item.get("message"):
            refs = "、".join(str(ref) for ref in item.get("evidence_refs", []))
            suffix = f"（证据{refs}）" if refs else ""
            lines.append(f"AI观察：{_redact(str(item['message']))}{suffix}")
    for item in analysis.get("uncertainties", []):
        lines.append(f"AI待确认：{_redact(str(item))}")
    return lines
