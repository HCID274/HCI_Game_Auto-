"""Read-only access to M7A's persistent stamina plan."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from starrail_auto.m7a.config import M7A_CONFIG_PATH

log = logging.getLogger(__name__)


def load_power_plan_remaining(
    path: Path = M7A_CONFIG_PATH,
) -> dict[str, int]:
    """Return exact dungeon names and their current remaining plan counts."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        log.warning("M7A power plan cannot be loaded from %s: %s", path, exc)
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("power_plan"), list):
        return {}

    remaining: dict[str, int] = {}
    for entry in data["power_plan"]:
        if not isinstance(entry, list) or len(entry) < 3:
            continue
        instance_type, instance_name, count = entry[:3]
        if not isinstance(instance_type, str) or not isinstance(instance_name, str):
            continue
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            continue
        remaining[f"{instance_type.strip()} - {instance_name.strip()}"] = count
    return remaining
