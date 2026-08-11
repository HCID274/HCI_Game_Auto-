from pathlib import Path

from wuwa_auto.okww.logs import (
    SUCCESS_MARKER,
    LogCursor,
    count_farm_echo_absorptions,
    count_farm_echo_completions,
    count_farm_echo_kill_confirmations,
    find_failure,
    has_farm_echo_combat_degradation,
    has_farm_echo_current_char_bind_failure,
    has_farm_echo_lucilla_liberation_stall,
    is_recoverable_farm_echo_death,
    is_recoverable_farm_echo_entry_failure,
    is_recoverable_farm_echo_party_member_unavailable,
    is_recoverable_farm_echo_realm_defeat,
)


def test_log_cursor_only_reads_current_append(tmp_path: Path) -> None:
    path = tmp_path / "ok.log"
    path.write_text(f"old {SUCCESS_MARKER}\n", encoding="utf-8")
    cursor = LogCursor(path)
    with path.open("a", encoding="utf-8") as stream:
        stream.write("new run started\n")
    assert cursor.read_new().splitlines() == ["new run started"]


def test_log_cursor_detects_truncation(tmp_path: Path) -> None:
    path = tmp_path / "ok.log"
    path.write_text("old content that must be ignored\n", encoding="utf-8")
    cursor = LogCursor(path)
    path.write_text(f"{SUCCESS_MARKER}\n", encoding="utf-8")
    assert SUCCESS_MARKER in cursor.read_new()


def test_direct_daily_failure_marker() -> None:
    assert find_failure("Daily Task exception stopped") == (
        "Daily Task exception stopped"
    )


def test_generic_task_failure_marker() -> None:
    assert find_failure(
        "2026 ERROR TaskExecutor TaskExecutor:Farm Echo Task exception stopped traceback"
    ) == "Farm Echo Task exception stopped traceback"


def test_farm_echo_counts_only_post_combat_results() -> None:
    text = """
FarmEchoTask:start wait in combat
FarmEchoTask:farm echo walk_find_echo None
FarmEchoTask:start wait in combat
FarmEchoTask:farm echo on the face
FarmEchoTask:start wait in combat
FarmEchoTask:raise_not_in_combat char dead
FarmEchoTask:info_set Revive Failed
"""
    assert count_farm_echo_completions(text) == 2
    assert is_recoverable_farm_echo_death(text)


def test_farm_echo_death_requires_both_markers() -> None:
    assert not is_recoverable_farm_echo_death(
        "FarmEchoTask:raise_not_in_combat char dead"
    )


def test_confirmed_realm_defeat_is_recoverable_without_revive_dialog() -> None:
    text = "FarmEchoTask:HOST_FARM_ECHO_REALM_DEFEAT_CONFIRMED\n"

    assert is_recoverable_farm_echo_realm_defeat(text)
    assert is_recoverable_farm_echo_death(text)


def test_host_confirmed_revive_dialog_is_recoverable() -> None:
    assert is_recoverable_farm_echo_death(
        "FarmEchoTask:HOST_FARM_ECHO_REVIVE_DIALOG_CONFIRMED\n"
    )


def test_host_confirmed_unavailable_party_member_is_recoverable() -> None:
    text = "FarmEchoTask:HOST_FARM_ECHO_PARTY_MEMBER_UNAVAILABLE_CONFIRMED\n"

    assert is_recoverable_farm_echo_party_member_unavailable(text)
    assert is_recoverable_farm_echo_death(text)


def test_farm_echo_entry_failure_is_recoverable() -> None:
    assert is_recoverable_farm_echo_entry_failure(
        "FarmEchoTask:info_set app Teleport to boss failed\n"
    )
    assert not is_recoverable_farm_echo_entry_failure(
        "FarmEchoTask:info_set Teleport to Boss Boss Challenge 1\n"
    )


def test_farm_echo_kill_count_requires_restart_screen_confirmation() -> None:
    text = """
FarmEchoTask:farm echo walk_find_echo None
FarmEchoTask:left_click claim_cancel_button_hcenter_vcenter (769, 900)
FarmEchoTask:farm echo walk_find_echo None
FarmEchoTask:HOST_FARM_ECHO_KILL_CONFIRMED 2/3
"""
    assert count_farm_echo_completions(text) == 2
    assert count_farm_echo_kill_confirmations(text) == 2


def test_echo_pickup_and_its_restart_are_one_kill() -> None:
    text = """
FarmEchoTask:farm echo walk_find_echo True
FarmEchoTask:left_click claim_cancel_button_hcenter_vcenter (769, 900)
FarmEchoTask:farm echo yolo find True
"""
    assert count_farm_echo_kill_confirmations(text) == 2
    assert count_farm_echo_absorptions(text) == 2


def test_cumulative_host_confirmation_overrides_upstream_evidence() -> None:
    text = """
FarmEchoTask:left_click claim_cancel_button_hcenter_vcenter (769, 900)
FarmEchoTask:HOST_FARM_ECHO_KILL_CONFIRMED 1/2 source=restart_screen
FarmEchoTask:farm echo walk_find_echo True
FarmEchoTask:HOST_FARM_ECHO_KILL_CONFIRMED 2/2 source=echo_absorbed
"""
    assert count_farm_echo_kill_confirmations(text) == 2


def test_absorption_count_ignores_kills_without_echo_drop() -> None:
    text = """
FarmEchoTask:farm echo walk_find_echo None
FarmEchoTask:left_click claim_cancel_button_hcenter_vcenter (769, 900)
FarmEchoTask:farm echo walk_find_echo True
FarmEchoTask:HOST_FARM_ECHO_ABSORPTION_CONFIRMED 1/5
FarmEchoTask:left_click claim_cancel_button_hcenter_vcenter (769, 900)
FarmEchoTask:farm echo walk_find_echo True
FarmEchoTask:HOST_FARM_ECHO_ABSORPTION_CONFIRMED 2/5
"""
    assert count_farm_echo_kill_confirmations(text) == 3
    assert count_farm_echo_absorptions(text) == 2

def test_farm_echo_combat_degradation_markers() -> None:
    liberation = "clicked liberation but no effect"
    target = (
        "Target enemy failed, please disable Nvidia/AMD Filter or Sharpening!"
    )
    assert has_farm_echo_combat_degradation(f"{liberation}\n{target}")
    assert not has_farm_echo_combat_degradation(liberation)
    assert not has_farm_echo_combat_degradation(target)
    assert not has_farm_echo_combat_degradation(
        "FarmEchoTask:farm echo walk_find_echo True"
    )


def test_lucilla_blind_fallback_is_deterministic_combat_degradation() -> None:
    stalled = """
Lucilla:Lucilla perform lib
Lucilla:Lucilla perform lib end
"""
    normal = """
Lucilla:Lucilla perform lib
Lucilla:Lucilla transform ended, stop pulse heavy early
Lucilla:Lucilla perform lib end
"""

    assert has_farm_echo_lucilla_liberation_stall(stalled)
    # Historical successful runs can contain a blind fallback before a later
    # normal rotation succeeds, so this is diagnostic evidence only.
    assert not has_farm_echo_combat_degradation(stalled)
    assert not has_farm_echo_lucilla_liberation_stall(normal)
    assert not has_farm_echo_combat_degradation(normal)


def test_lucilla_stall_detection_handles_multiple_liberations() -> None:
    text = """
Lucilla:Lucilla perform lib
Lucilla:Lucilla transform ended, stop pulse heavy early
Lucilla:Lucilla perform lib end
Lucilla:Lucilla perform lib
Lucilla:Lucilla perform lib end
"""

    assert has_farm_echo_lucilla_liberation_stall(text)


def test_current_character_bind_failure_is_combat_degradation() -> None:
    text = "FarmEchoTask:could not find char 0 please check current char\n"

    assert has_farm_echo_current_char_bind_failure(text)
    assert has_farm_echo_combat_degradation(text)
