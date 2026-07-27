"""Atomic JSON evidence archives shared by game reporting pipelines."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_json_archive(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)
    return path
