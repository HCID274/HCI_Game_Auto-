"""开跑前更新三月七助手：只用假进程和假日志，不启动真的更新程序。"""

from pathlib import Path

import pytest

from starrail_auto.m7a import app_update
from starrail_auto.m7a.models import M7ALogCheckpoint


class FakeUpdaterPath:
    def is_file(self) -> bool:
        return True

    def __str__(self) -> str:
        return "updater.exe"


class FakeUpdater:
    pid = 4321

    def __init__(self, exit_after_polls: int | None) -> None:
        self._remaining = exit_after_polls

    def poll(self) -> int | None:
        if self._remaining is None:
            return None
        if self._remaining <= 0:
            return 0
        self._remaining -= 1
        return None


@pytest.fixture
def env(monkeypatch):
    state = {"versions": ["v2026.11.19"], "log": "", "running": False}
    calls = {"popen": [], "kill": [], "stop": []}
    clock = {"now": 0.0}

    def fake_version() -> str:
        return state["versions"].pop(0) if len(state["versions"]) > 1 else state["versions"][0]

    monkeypatch.setattr(app_update, "_version", fake_version)
    monkeypatch.setattr(app_update, "_m7a_running", lambda: state["running"])
    monkeypatch.setattr(app_update, "UPDATER", FakeUpdaterPath())
    monkeypatch.setattr(
        app_update,
        "capture_log_checkpoint",
        lambda: M7ALogCheckpoint(path=Path("unused.log"), offset=0),
    )
    monkeypatch.setattr(app_update, "read_log_since", lambda _checkpoint: state["log"])
    monkeypatch.setattr(app_update, "_kill_tree", lambda pid: calls["kill"].append(pid))
    monkeypatch.setattr(
        app_update, "stop_new_m7a_processes", lambda started: calls["stop"].append(started)
    )

    def fake_sleep(seconds: float) -> None:
        clock["now"] += seconds

    monkeypatch.setattr(app_update.time, "sleep", fake_sleep)
    monkeypatch.setattr(app_update.time, "monotonic", lambda: clock["now"])

    def popen(command, cwd):
        calls["popen"].append(command)
        return state["updater"]

    monkeypatch.setattr(app_update.subprocess, "Popen", popen)
    return state, calls


def test_new_version_installed_closes_the_gui_it_opened(env) -> None:
    state, calls = env
    state["updater"] = FakeUpdater(exit_after_polls=2)
    state["versions"] = ["v2026.11.19", "v2026.11.20"]

    notes = app_update.update_m7a()

    assert notes == ["三月七助手已更新：v2026.11.19 → v2026.11.20"]
    assert calls["popen"] == [["updater.exe", "--start-minimized-to-tray"]]
    assert len(calls["stop"]) == 1
    assert calls["kill"] == []


def test_already_latest_adds_no_report_line(env) -> None:
    state, calls = env
    state["updater"] = FakeUpdater(exit_after_polls=1)
    state["log"] = "INFO | 当前已是最新版本\n"

    assert app_update.update_m7a() == []
    assert calls["stop"] == [] and calls["kill"] == []


def test_failure_dialog_is_closed_and_old_version_runs(env) -> None:
    state, calls = env
    # 出错后更新程序弹窗等人点，自己不会退出。
    state["updater"] = FakeUpdater(exit_after_polls=None)
    state["log"] = "ERROR | 更新过程出错：下载失败：连接超时\n"

    notes = app_update.update_m7a()

    assert notes == ["三月七助手没能更新（下载失败：连接超时），今天用的还是 v2026.11.19"]
    assert calls["kill"] == [FakeUpdater.pid]


def test_stuck_download_times_out_but_cover_gets_more_time(env) -> None:
    state, calls = env
    state["updater"] = FakeUpdater(exit_after_polls=None)
    assert app_update.update_m7a() == [
        "三月七助手没能更新（下载或解压超时），今天用的还是 v2026.11.19"
    ]

    state["log"] = "INFO | 开始覆盖安装\n"
    calls["kill"].clear()
    assert app_update.update_m7a() == [
        "三月七助手没能更新（覆盖新文件超时），今天用的还是 v2026.11.19"
    ]
    assert calls["kill"] == [FakeUpdater.pid]


def test_running_m7a_skips_update(env) -> None:
    state, calls = env
    state["running"] = True

    assert app_update.update_m7a() == [
        "开跑前三月七助手已经开着，今天没更新，用的还是 v2026.11.19"
    ]
    assert calls["popen"] == []
