"""Current-run slicing and deterministic OK-WW result markers."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

SUCCESS_MARKER = "Daily Task Completed"
FAILURE_MARKERS = (
    "Daily Task exception stopped",
    'Teleport and Farm 4C Echo requires "Teleport to Boss"',
    'Auto Farm all Nightmare Nest requires at least one "Which to Farm"',
    "Start task failed: Daily Task",
)


@dataclass
class LogCursor:
    path: Path
    offset: int = field(init=False)
    _prefix_length: int = field(init=False, default=0)
    _prefix_digest: bytes = field(init=False, default=b"")

    def __post_init__(self) -> None:
        if not self.path.is_file():
            self.offset = 0
            return
        self.offset = self.path.stat().st_size
        self._prefix_length = min(self.offset, 4096)
        with self.path.open("rb") as stream:
            self._prefix_digest = hashlib.sha256(
                stream.read(self._prefix_length)
            ).digest()

    def _same_file_prefix(self) -> bool:
        if not self._prefix_length:
            return True
        with self.path.open("rb") as stream:
            current = hashlib.sha256(stream.read(self._prefix_length)).digest()
        return current == self._prefix_digest

    def read_new(self) -> str:
        if not self.path.is_file():
            return ""
        size = self.path.stat().st_size
        if size < self.offset or not self._same_file_prefix():
            self.offset = 0
            self._prefix_length = 0
            self._prefix_digest = b""
        if size <= self.offset:
            return ""
        with self.path.open("rb") as stream:
            stream.seek(self.offset)
            payload = stream.read()
        self.offset += len(payload)
        return payload.decode("utf-8", errors="replace")


def find_failure(text: str) -> str | None:
    return next((marker for marker in FAILURE_MARKERS if marker in text), None)

