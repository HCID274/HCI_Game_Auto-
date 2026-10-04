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


def test_upstream_entry_tasks_run_through_the_launch_argument_worker(monkeypatch) -> None:
    # 2026-10-04：周常乐园直接调上游 main.py，漏了 -krqlv，游戏启动即崩。
    monkeypatch.setattr(runner, "OK_PYTHONW_EXE", Path("pythonw.exe"))
    monkeypatch.setattr(runner, "OK_MAIN_WORKER_ENTRYPOINT", Path("ok_main_worker.py"))
    monkeypatch.setattr(runner, "OK_WORKING_DIR", Path("working"))

    assert runner._build_task_command(11, "weekly_garden") == [
        "pythonw.exe",
        "ok_main_worker.py",
        "working",
        "--headless",
        "-t",
        "src.task.GardenTask.GardenTask",
        "-e",
    ]
    assert runner._build_task_command(3, "farm_echo")[1:4] == [
        "ok_main_worker.py",
        "working",
        "--headless",
    ]
