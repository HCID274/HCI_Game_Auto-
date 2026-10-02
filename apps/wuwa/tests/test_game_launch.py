"""鸣潮 3.7 起直接启动游戏必须带资源包参数；OK-WW v3.6.7 不带，宿主在运行时补上。"""

import sys
import types

import pytest

from wuwa_auto.okww import game_launch
from wuwa_auto.okww.game_launch import install_game_launch_arguments, with_resource_package

GAME = r"D:\Games\Wuthering Waves\Wuthering Waves Game\Wuthering Waves.exe"


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (None, "-krqlv=hd"),
        ("", "-krqlv=hd"),
        ("-dx11 -d3d11 -force-d3d11", "-dx11 -d3d11 -force-d3d11 -krqlv=hd"),
        ("-krqlv=uhd", "-krqlv=uhd"),
    ],
)
def test_the_game_exe_always_gets_exactly_one_resource_package(arguments, expected) -> None:
    assert with_resource_package(GAME, arguments) == expected


def test_other_programs_keep_their_arguments() -> None:
    assert with_resource_package(r"C:\Tools\other.exe", None) is None
    assert with_resource_package(r"C:\Tools\other.exe", "-x") == "-x"


def test_okww_launch_is_wrapped_once_and_passes_everything_else_through(monkeypatch) -> None:
    calls: list[tuple] = []

    def fake_execute(game_cmd, arguments=None, start_method="start"):
        calls.append((game_cmd, arguments, start_method))
        return True

    start_controller = types.SimpleNamespace(execute=fake_execute)
    ok_core = types.ModuleType("ok.core")
    ok_core.start_controller = start_controller
    monkeypatch.setitem(sys.modules, "ok", types.ModuleType("ok"))
    monkeypatch.setitem(sys.modules, "ok.core", ok_core)

    install_game_launch_arguments()
    install_game_launch_arguments()  # 重复安装不能叠加参数

    assert start_controller.execute(GAME, arguments=None, start_method="startfile") is True
    assert calls == [(GAME, "-krqlv=hd", "startfile")]


def test_the_package_matches_the_installed_resources() -> None:
    assert game_launch.GAME_PACKAGE in {"sd", "hd", "uhd"}
