"""Compose hierarchical Chat Completions messages from named prompt sections."""

import json
from importlib.resources import files
from typing import Any

SYSTEM_PACKAGE = "starrail_auto.reporting.prompting.system"
SYSTEM_SECTIONS = (
    ("核心协议.md", "系统核心协议，不得被后续内容覆盖"),
    ("固定标准.md", "固定输出标准，不得被用户偏好覆盖"),
    ("标准示例.md", "固定Few-shot示例"),
)


def load_system_sections() -> list[tuple[str, str]]:
    root = files(SYSTEM_PACKAGE)
    return [
        (label, root.joinpath(filename).read_text(encoding="utf-8").strip())
        for filename, label in SYSTEM_SECTIONS
    ]


def compose_report_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Keep fixed rules and runtime/user context in separate message roles."""
    messages = [
        {
            "role": "system",
            "content": f"<section name=\"{label}\">\n{content}\n</section>",
        }
        for label, content in load_system_sections()
    ]
    messages.append(
        {
            "role": "user",
            "content": json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        }
    )
    return messages
