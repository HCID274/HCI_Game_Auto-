"""Data passed between M7A execution layers."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    stage: str = ""
    retries: int = 0
    report_log_path: Path | None = None
    report_log_offset: int = 0


@dataclass(frozen=True)
class M7ALogCheckpoint:
    path: Path
    offset: int
