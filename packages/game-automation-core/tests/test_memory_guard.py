from types import SimpleNamespace

from game_automation_core.windows import memory_guard

GB = memory_guard.GB


class _FakeProcess:
    def __init__(self, pid: int, name: str, cmdline: list[str], private_gb: float) -> None:
        self.pid = pid
        self.info = {
            "name": name,
            "cmdline": cmdline,
            "memory_info": SimpleNamespace(private=int(private_gb * GB)),
        }
        self.killed = False

    def kill(self) -> None:
        self.killed = True


def _install(monkeypatch, processes, available_gb: list[float]) -> None:
    readings = iter(available_gb)
    monkeypatch.setattr(
        memory_guard.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(available=int(next(readings) * GB)),
    )
    monkeypatch.setattr(memory_guard.psutil, "process_iter", lambda _attrs: iter(processes))
    monkeypatch.setattr(
        memory_guard.psutil,
        "wait_procs",
        lambda procs, timeout: ([p for p in procs if p.killed], [p for p in procs if not p.killed]),
    )


def test_enough_memory_closes_nothing(monkeypatch) -> None:
    minecraft = _FakeProcess(1, "javaw.exe", ["javaw", r"D:\.minecraft\versions"], 9.5)
    _install(monkeypatch, [minecraft], [8.0])

    assert memory_guard.free_memory_for_games() == []
    assert not minecraft.killed


def test_low_memory_closes_only_minecraft_java(monkeypatch) -> None:
    minecraft = _FakeProcess(1, "javaw.exe", ["javaw", r"D:\.minecraft\versions"], 9.5)
    other_java = _FakeProcess(2, "java.exe", ["java", "-jar", "tool.jar"], 1.0)
    chrome = _FakeProcess(3, "chrome.exe", ["chrome"], 3.0)
    _install(monkeypatch, [minecraft, other_java, chrome], [0.5, 10.0])

    notes = memory_guard.free_memory_for_games()

    assert minecraft.killed
    assert not other_java.killed and not chrome.killed
    assert notes == [
        "开跑前可用内存只剩 0.5 GB，已关掉 Minecraft（1 个进程，约 9.5 GB），现在可用 10.0 GB"
    ]


def test_low_memory_without_minecraft_only_logs(monkeypatch) -> None:
    chrome = _FakeProcess(3, "chrome.exe", ["chrome"], 3.0)
    _install(monkeypatch, [chrome], [2.0])

    assert memory_guard.free_memory_for_games() == []
    assert not chrome.killed
