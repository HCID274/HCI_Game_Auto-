from pathlib import Path

from wuwa_auto.okww.logs import (
    SUCCESS_MARKER,
    LogCursor,
    count_farm_echo_absorptions,
    count_farm_echo_completions,
    count_farm_echo_kill_confirmations,
    find_failure,
    is_recoverable_farm_echo_death,
    is_recoverable_farm_echo_entry_failure,
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
