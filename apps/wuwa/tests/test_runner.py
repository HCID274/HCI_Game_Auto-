from pathlib import Path

from wuwa_auto.okww import runner


def test_daily_resume_command_keeps_daily_worker_and_adds_one_shot_flag(
    monkeypatch,
) -> None:
    monkeypatch.setattr(runner, "OK_PYTHONW_EXE", Path("pythonw.exe"))
    monkeypatch.setattr(runner, "DAILY_WORKER_ENTRYPOINT", Path("daily_worker.py"))
    monkeypatch.setattr(runner, "OK_WORKING_DIR", Path("working"))

    command = runner._build_task_command(
        1,
        "daily",
        daily_resume_after_nightmare=True,
    )

    assert command == [
        "pythonw.exe",
        "daily_worker.py",
        "--resume-after-nightmare",
        "working",
    ]


def test_regular_daily_command_does_not_skip_nightmare(monkeypatch) -> None:
    monkeypatch.setattr(runner, "OK_PYTHONW_EXE", Path("pythonw.exe"))
    monkeypatch.setattr(runner, "DAILY_WORKER_ENTRYPOINT", Path("daily_worker.py"))
    monkeypatch.setattr(runner, "OK_WORKING_DIR", Path("working"))

    assert runner._build_task_command(1, "daily") == [
        "pythonw.exe",
        "daily_worker.py",
        "working",
    ]
