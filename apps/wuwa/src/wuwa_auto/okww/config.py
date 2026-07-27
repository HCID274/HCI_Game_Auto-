"""Validate the installed OK-WW configuration used by the daily workflow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wuwa_auto.settings import (
    OK_DAILY_CONFIG,
    OK_FARM_ECHO_CONFIG,
    OK_ENTRYPOINT,
    OK_LOG_FILE,
    OK_NIGHTMARE_CONFIG,
    OK_PYTHONW_EXE,
    OK_WORKING_DIR,
    OK_WW_EXE,
)

TELEPORT_AND_FARM_4C = "Teleport and Farm 4C Echo"
AUTO_FARM_NIGHTMARE = "Auto Farm all Nightmare Nest"
EXPECTED_REPEAT_FARM_COUNT = 5


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


def validate_daily_configuration() -> dict[str, Any]:
    for path in (
        OK_WW_EXE,
        OK_PYTHONW_EXE,
        OK_ENTRYPOINT,
        OK_WORKING_DIR,
        OK_LOG_FILE.parent,
    ):
        if not path.exists():
            raise RuntimeError(f"required OK-WW path is missing: {path}")

    daily = _load_json(OK_DAILY_CONFIG)
    farm_echo = _load_json(OK_FARM_ECHO_CONFIG)
    nightmare = _load_json(OK_NIGHTMARE_CONFIG)

    additional = daily.get("Additional Tasks to Run After Daily Task") or []
    if TELEPORT_AND_FARM_4C not in additional:
        raise RuntimeError(f"DailyTask must enable {TELEPORT_AND_FARM_4C!r}")
    if farm_echo.get("Teleport to Boss", "No") == "No":
        raise RuntimeError("FarmEchoTask must enable 'Teleport to Boss'")

    repeat_count = farm_echo.get("Repeat Farm Count")
    if repeat_count != EXPECTED_REPEAT_FARM_COUNT:
        raise RuntimeError(
            "FarmEchoTask 'Repeat Farm Count' must stay fixed at "
            f"{EXPECTED_REPEAT_FARM_COUNT}; actual={repeat_count!r}"
        )

    if AUTO_FARM_NIGHTMARE in additional and not nightmare.get("Which to Farm"):
        raise RuntimeError(
            "NightmareNestTask needs at least one 'Which to Farm' selection"
        )

    return {
        "daily_farm": daily.get("Which to Farm"),
        "daily_farm_index": daily.get("Which Tacet Suppression to Farm"),
        "additional_tasks": list(additional),
        "teleport_to_boss": farm_echo.get("Teleport to Boss"),
        "boss_challenge_index": farm_echo.get(
            "Which Boss Challenge to Teleport"
        ),
        "boss_level": farm_echo.get("Boss Level"),
        "boss": farm_echo.get("Boss"),
        "repeat_farm_count": repeat_count,
        "nightmare_targets": list(nightmare.get("Which to Farm") or []),
    }
