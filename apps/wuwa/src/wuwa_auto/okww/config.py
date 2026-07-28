"""Validate only the installed OK-WW configuration required by each workflow."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from wuwa_auto.settings import (
    OK_DAILY_CONFIG,
    OK_ENTRYPOINT,
    OK_FARM_ECHO_CONFIG,
    OK_GARDEN_CONFIG,
    OK_LOG_FILE,
    OK_NIGHTMARE_CONFIG,
    OK_PYTHONW_EXE,
    OK_WORKING_DIR,
    OK_WW_EXE,
)

TELEPORT_AND_FARM_4C = "Teleport and Farm 4C Echo"
AUTO_FARM_NIGHTMARE = "Auto Farm all Nightmare Nest"
EXPECTED_REPEAT_FARM_COUNT = 5
COMBAT_PASSES_PER_CONFIRMED_KILL = 12
MAX_RECOVERY_COMBAT_ATTEMPTS = (
    EXPECTED_REPEAT_FARM_COUNT * COMBAT_PASSES_PER_CONFIRMED_KILL
)


def confirmed_retry_attempt_limit(target_count: int) -> int:
    """Allow detector re-entry without relaxing the exact kill target."""
    if not 1 <= target_count <= EXPECTED_REPEAT_FARM_COUNT:
        raise ValueError(
            "confirmed retry target must be between 1 and "
            f"{EXPECTED_REPEAT_FARM_COUNT}; actual={target_count}"
        )
    return target_count * COMBAT_PASSES_PER_CONFIRMED_KILL


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"required OK-WW configuration is missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid OK-WW configuration {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"OK-WW configuration must be an object: {path}")
    return value


def _validate_common_paths() -> None:
    for path in (
        OK_WW_EXE,
        OK_PYTHONW_EXE,
        OK_ENTRYPOINT,
        OK_WORKING_DIR,
        OK_LOG_FILE.parent,
    ):
        if not path.exists():
            raise RuntimeError(f"required OK-WW path is missing: {path}")


def _load_farm_echo_facts(
    expected_repeat_count: int = EXPECTED_REPEAT_FARM_COUNT,
) -> dict[str, Any]:
    farm_echo = _load_json(OK_FARM_ECHO_CONFIG)
    if farm_echo.get("Teleport to Boss", "No") == "No":
        raise RuntimeError("FarmEchoTask must enable 'Teleport to Boss'")

    repeat_count = farm_echo.get("Repeat Farm Count")
    if repeat_count != expected_repeat_count:
        raise RuntimeError(
            "FarmEchoTask 'Repeat Farm Count' must stay fixed at "
            f"{expected_repeat_count}; actual={repeat_count!r}"
        )

    return {
        "teleport_to_boss": farm_echo.get("Teleport to Boss"),
        "boss_challenge_index": farm_echo.get(
            "Which Boss Challenge to Teleport"
        ),
        "boss_level": farm_echo.get("Boss Level"),
        "boss": farm_echo.get("Boss"),
        "repeat_farm_count": repeat_count,
    }


def validate_farm_echo_configuration(
    expected_repeat_count: int = EXPECTED_REPEAT_FARM_COUNT,
) -> dict[str, Any]:
    _validate_common_paths()
    return _load_farm_echo_facts(expected_repeat_count)


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.recovery.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


@contextmanager
def temporary_farm_echo_repeat_count(repeat_count: int) -> Iterator[None]:
    """Override only the user config for one bounded retry, then restore bytes."""
    if not 1 <= repeat_count <= MAX_RECOVERY_COMBAT_ATTEMPTS:
        raise ValueError(
            "recovery repeat count must be between 1 and "
            f"{MAX_RECOVERY_COMBAT_ATTEMPTS}; actual={repeat_count}"
        )
    original = OK_FARM_ECHO_CONFIG.read_bytes()
    config = _load_json(OK_FARM_ECHO_CONFIG)
    config["Repeat Farm Count"] = repeat_count
    modified = (json.dumps(config, ensure_ascii=False, indent=4) + "\n").encode(
        "utf-8"
    )
    _atomic_write(OK_FARM_ECHO_CONFIG, modified)
    try:
        yield
    finally:
        _atomic_write(OK_FARM_ECHO_CONFIG, original)


def validate_weekly_garden_configuration() -> dict[str, Any]:
    _validate_common_paths()
    garden = _load_json(OK_GARDEN_CONFIG)
    return {"garden_config": garden}


def validate_daily_configuration() -> dict[str, Any]:
    _validate_common_paths()
    daily = _load_json(OK_DAILY_CONFIG)
    additional = daily.get("Additional Tasks to Run After Daily Task") or []
    if TELEPORT_AND_FARM_4C not in additional:
        raise RuntimeError(f"DailyTask must enable {TELEPORT_AND_FARM_4C!r}")

    nightmare_targets: list[Any] = []
    if AUTO_FARM_NIGHTMARE in additional:
        nightmare = _load_json(OK_NIGHTMARE_CONFIG)
        nightmare_targets = list(nightmare.get("Which to Farm") or [])
        if not nightmare_targets:
            raise RuntimeError(
                "NightmareNestTask needs at least one 'Which to Farm' selection"
            )

    return {
        "daily_farm": daily.get("Which to Farm"),
        "daily_farm_index": daily.get("Which Tacet Suppression to Farm"),
        "additional_tasks": list(additional),
        **_load_farm_echo_facts(),
        "nightmare_targets": nightmare_targets,
    }
