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

FARM_ECHO_COMPLETION_MARKERS = (
    "FarmEchoTask:farm echo on the face",
    "FarmEchoTask:farm echo yolo find ",
    "FarmEchoTask:farm echo walk_circle_find_echo ",
    "FarmEchoTask:farm echo walk_find_echo ",
)
FARM_ECHO_DEATH_MARKERS = (
    "FarmEchoTask:raise_not_in_combat char dead",
    "FarmEchoTask:info_set Revive Failed",
)
FARM_ECHO_REALM_DEFEAT_MARKER = "HOST_FARM_ECHO_REALM_DEFEAT_CONFIRMED"
FARM_ECHO_REVIVE_DIALOG_MARKER = "HOST_FARM_ECHO_REVIVE_DIALOG_CONFIRMED"
FARM_ECHO_ENTRY_FAILURE_MARKERS = (
    "FarmEchoTask:info_set app Teleport to boss failed",
    "RuntimeError: Teleport to boss failed",
    "Host failed to verify Boss Challenge guidebook page",
)
FARM_ECHO_RESTART_CONFIRMATION = (
    "FarmEchoTask:left_click claim_cancel_button_hcenter_vcenter"
)
HOST_FARM_ECHO_CONFIRMATION = "HOST_FARM_ECHO_KILL_CONFIRMED"
HOST_FARM_ECHO_ABSORPTION_CONFIRMATION = (
    "HOST_FARM_ECHO_ABSORPTION_CONFIRMED"
)
FARM_ECHO_COMBAT_DEGRADATION_MARKERS = (
    "clicked liberation but no effect",
    "Target enemy failed, please disable Nvidia/AMD Filter or Sharpening!",
)
FARM_ECHO_PICKUP_CONFIRMATION_MARKERS = (
    "FarmEchoTask:farm echo on the face",
    "FarmEchoTask:farm echo yolo find True",
    "FarmEchoTask:farm echo walk_circle_find_echo True",
    "FarmEchoTask:farm echo walk_find_echo True",
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
    explicit = next((marker for marker in FAILURE_MARKERS if marker in text), None)
    if explicit:
        return explicit
    for line in text.splitlines():
        if "Task exception stopped" in line:
            return line.rsplit("TaskExecutor:", 1)[-1].strip()
    return None


def count_farm_echo_completions(text: str) -> int:
    """Count only post-combat FarmEcho results, including a missing echo drop."""
    return sum(
        1
        for line in text.splitlines()
        if any(marker in line for marker in FARM_ECHO_COMPLETION_MARKERS)
    )


def count_farm_echo_kill_confirmations(text: str) -> int:
    """Count kills from causal post-combat evidence, without double counting."""
    lines = text.splitlines()
    host_counts: list[int] = []
    for line in lines:
        if HOST_FARM_ECHO_CONFIRMATION not in line:
            continue
        suffix = line.split(HOST_FARM_ECHO_CONFIRMATION, 1)[1].strip()
        numerator = suffix.split("/", 1)[0].strip()
        if numerator.isdigit():
            host_counts.append(int(numerator))
    if host_counts:
        # The host marker is cumulative and already deduplicates the upstream
        # evidence that immediately preceded it.
        return max(host_counts)

    confirmed = 0
    pending_echo_pickup = False
    for line in lines:
        if any(marker in line for marker in FARM_ECHO_PICKUP_CONFIRMATION_MARKERS):
            pending_echo_pickup = True
        elif FARM_ECHO_RESTART_CONFIRMATION in line:
            confirmed += 1
            pending_echo_pickup = False
    if pending_echo_pickup:
        confirmed += 1
    return confirmed


def count_farm_echo_absorptions(text: str) -> int:
    """Count actual echo pickups, preferring the host's cumulative fact."""
    host_counts: list[int] = []
    for line in text.splitlines():
        if HOST_FARM_ECHO_ABSORPTION_CONFIRMATION not in line:
            continue
        suffix = line.split(HOST_FARM_ECHO_ABSORPTION_CONFIRMATION, 1)[1].strip()
        numerator = suffix.split("/", 1)[0].strip()
        if numerator.isdigit():
            host_counts.append(int(numerator))
    if host_counts:
        return max(host_counts)
    return sum(
        1
        for line in text.splitlines()
        if any(marker in line for marker in FARM_ECHO_PICKUP_CONFIRMATION_MARKERS)
    )


def has_farm_echo_combat_degradation(text: str) -> bool:
    """Recognize OK-WW combat input/detection degradation in a failed run."""
    return any(marker in text for marker in FARM_ECHO_COMBAT_DEGRADATION_MARKERS)


def is_recoverable_farm_echo_death(text: str) -> bool:
    """Require both current-run death facts before touching the game UI."""
    normal_death = all(marker in text for marker in FARM_ECHO_DEATH_MARKERS)
    return (
        normal_death
        or FARM_ECHO_REALM_DEFEAT_MARKER in text
        or FARM_ECHO_REVIVE_DIALOG_MARKER in text
    )


def is_recoverable_farm_echo_realm_defeat(text: str) -> bool:
    """Recognize the separately confirmed full-realm defeat state."""
    return FARM_ECHO_REALM_DEFEAT_MARKER in text


def is_recoverable_farm_echo_entry_failure(text: str) -> bool:
    """Recognize a pre-combat guidebook/teleport failure from this run."""
    return any(marker in text for marker in FARM_ECHO_ENTRY_FAILURE_MARKERS)
