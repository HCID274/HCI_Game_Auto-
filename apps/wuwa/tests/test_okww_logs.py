from pathlib import Path

from wuwa_auto.okww.logs import SUCCESS_MARKER, LogCursor, find_failure


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
