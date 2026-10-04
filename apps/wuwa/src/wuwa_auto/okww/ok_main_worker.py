"""挂上宿主的游戏启动参数补丁后运行 OK-WW 自带的 main.py。

周常乐园和独立讨伐直接调用上游入口，不经过其他 worker。鸣潮 3.7 起直接拉起
游戏必须带资源包参数，所以这里先安装 ``game_launch`` 补丁，再把其余命令行
参数原样交给上游 main.py。OK-WW 自带的 Python 直接运行本文件，只能依赖标准库。
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

try:
    from .game_launch import install_game_launch_arguments
except ImportError:  # executed directly by OK-WW's bundled Python
    from game_launch import install_game_launch_arguments


def main(argv: list[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        raise SystemExit("usage: ok_main_worker.py OK_WORKING_DIR [main.py arguments...]")
    working_dir = Path(arguments[0]).resolve()
    entry = working_dir / "main.py"
    os.chdir(working_dir)
    # 上游 config.py 必须先于本目录的同名模块被导入。
    sys.path.insert(0, str(working_dir))
    # 上游用 parse_known_args() 读取 sys.argv，导入 ok 前就要换成 main.py 的参数。
    sys.argv = [str(entry), *arguments[1:]]
    install_game_launch_arguments()
    runpy.run_path(str(entry), run_name="__main__")


if __name__ == "__main__":
    main()
