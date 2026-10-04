import json
import os
import sys
import types

from wuwa_auto.okww import ok_main_worker


def test_worker_patches_the_launch_then_runs_upstream_main_with_its_arguments(
    tmp_path, monkeypatch
) -> None:
    working = tmp_path / "working"
    working.mkdir()
    seen = tmp_path / "seen.json"
    (working / "main.py").write_text(
        "import json, os, sys\n"
        "from ok.core import start_controller\n"
        f"json.dump({{'argv': sys.argv[1:], 'cwd': os.getcwd(), "
        f"'patched': getattr(start_controller.execute, '_wuwa_resource_package', False)}}, "
        f"open({str(seen)!r}, 'w'))\n",
        encoding="utf-8",
    )
    start_controller = types.ModuleType("ok.core.start_controller")
    start_controller.execute = lambda *args, **kwargs: None
    ok_core = types.ModuleType("ok.core")
    ok_core.start_controller = start_controller
    monkeypatch.setitem(sys.modules, "ok", types.ModuleType("ok"))
    monkeypatch.setitem(sys.modules, "ok.core", ok_core)
    monkeypatch.setitem(sys.modules, "ok.core.start_controller", start_controller)
    monkeypatch.setattr(sys, "argv", list(sys.argv))
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.chdir(tmp_path)

    ok_main_worker.main([str(working), "--headless", "-t", "src.task.GardenTask.GardenTask", "-e"])

    result = json.loads(seen.read_text())
    assert result["argv"] == ["--headless", "-t", "src.task.GardenTask.GardenTask", "-e"]
    assert os.path.samefile(result["cwd"], working)
    assert result["patched"] is True
