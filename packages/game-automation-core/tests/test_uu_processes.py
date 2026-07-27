from pathlib import Path
from types import SimpleNamespace

from game_automation_core.uu.processes import UuProcessController, UuProcessSpec


def test_discovery_uses_exact_names_and_launcher_path(monkeypatch) -> None:
    processes = [
        SimpleNamespace(info={"name": "not-uu.exe", "pid": 1, "exe": r"C:\x\not-uu.exe"}),
        SimpleNamespace(info={"name": "uu.exe", "pid": 2, "exe": r"C:\wrong\uu.exe"}),
        SimpleNamespace(info={"name": "uu_ball.exe", "pid": 3, "exe": r"C:\x\uu_ball.exe"}),
    ]
    monkeypatch.setattr(
        "game_automation_core.uu.processes.psutil.process_iter",
        lambda _attrs: processes,
    )
    controller = UuProcessController(
        UuProcessSpec(
            executable=Path(r"C:\expected\uu.exe"),
            managed_names=frozenset({"uu.exe", "uu_ball.exe"}),
        )
    )
    assert [process.info["pid"] for process in controller.managed_processes()] == [3]
