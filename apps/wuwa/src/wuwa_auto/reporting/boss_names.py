"""读取用户登记的讨伐 Boss 名称，让日报写出具体打的是哪只。"""

from __future__ import annotations

import re
from pathlib import Path

from game_automation_core.reporting.context import read_markdown

from wuwa_auto.settings import USER_CONTEXT_DIR

BOSS_NAMES_PATH = USER_CONTEXT_DIR / "讨伐Boss.md"
_ENTRY = re.compile(
    r"^\s*-\s*讨伐强敌第(?P<index>\d+)项[：:]\s*(?P<name>.+?)\s*$",
    re.MULTILINE,
)


def load_boss_names(path: Path = BOSS_NAMES_PATH) -> dict[int, str]:
    """返回 {讨伐强敌编号: Boss 名}；文件缺失或没登记时为空。"""

    return {
        int(match["index"]): match["name"]
        for match in _ENTRY.finditer(read_markdown(path))
        if match["name"] != "待填写"
    }
