"""给 OK-WW 拉起游戏的命令行补上资源包参数。

鸣潮 3.7 起，直接运行 ``Wuthering Waves.exe`` 必须带 ``-krqlv=<资源包>``，
否则游戏启动约 9 秒后断言崩溃（"kuro: Use launcher to start game!"）。
OK-WW v3.6.7 不带这个参数，上游已在 master 修复但尚未发布正式版。
这里在运行时包装 OK-WW 的 ``execute``，不改它的安装目录；上游发布修复后
参数已存在时不会重复追加，可以直接删掉本模块。

本文件由 OK-WW 自带的 Python 按同目录模块导入，只能依赖标准库和 ``ok``。
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

# 已安装的游戏资源包档位（官方启动器里的流畅 sd / 高清 hd / 极致 uhd）；
# 在启动器里换了档位，这里要同步改。
GAME_PACKAGE = "hd"
GAME_EXE_NAME = "wuthering waves.exe"
PACKAGE_OPTION = "-krqlv"


def with_resource_package(
    game_cmd: str, arguments: str | None, package: str = GAME_PACKAGE
) -> str | None:
    """只处理游戏本体；已带资源包参数时原样返回。"""

    if not str(game_cmd).casefold().endswith(GAME_EXE_NAME):
        return arguments
    if PACKAGE_OPTION in (arguments or ""):
        return arguments
    return f"{arguments or ''} {PACKAGE_OPTION}={package}".strip()


def install_game_launch_arguments() -> None:
    """在 OK-WW 的工作目录进入 ``sys.path`` 之后、``run_task`` 之前调用。"""

    from ok.core import start_controller

    original = start_controller.execute
    if getattr(original, "_wuwa_resource_package", False):
        return

    def execute(game_cmd: str, arguments: Any = None, *args: Any, **kwargs: Any) -> Any:
        return original(game_cmd, with_resource_package(game_cmd, arguments), *args, **kwargs)

    execute._wuwa_resource_package = True  # type: ignore[attr-defined]
    start_controller.execute = execute
    log.info("OK-WW game launch now carries %s=%s", PACKAGE_OPTION, GAME_PACKAGE)
