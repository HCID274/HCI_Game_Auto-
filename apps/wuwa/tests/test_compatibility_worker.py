import pytest
from wuwa_auto.okww.compatibility_worker import _require


class _HealingDomain:
    revive_action = None
    ensure_main = None
    in_team_and_world = None
    wait_in_team_and_world = None
    revive_at_tower_and_heal = None
    wait_click_feature = None


def test_compatibility_contract_includes_waypoint_healing_api() -> None:
    _require(
        _HealingDomain,
        "revive_action",
        "ensure_main",
        "in_team_and_world",
        "wait_in_team_and_world",
        "revive_at_tower_and_heal",
        "wait_click_feature",
    )


def test_compatibility_contract_rejects_missing_waypoint_healing_api() -> None:
    class MissingHealingDomain:
        revive_action = None
        ensure_main = None
        in_team_and_world = None
        wait_in_team_and_world = None
        wait_click_feature = None

    with pytest.raises(RuntimeError, match="revive_at_tower_and_heal"):
        _require(MissingHealingDomain, "revive_at_tower_and_heal")
