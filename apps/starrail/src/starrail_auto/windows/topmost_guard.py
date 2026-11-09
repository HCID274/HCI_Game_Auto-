"""星铁自动化运行期间，别家置顶窗口不许盖住 UU、游戏、米哈游启动器和三月七助手。"""

from contextlib import AbstractContextManager

from game_automation_core.windows.topmost_guard import (
    TopmostCoverGuard,
    keep_covering_windows_down,
)

from starrail_auto.m7a.config import (
    GAME_PROCESS_NAMES,
    M7A_ASSISTANT_PROCESS_NAME,
    M7A_LAUNCHER_PROCESS_NAME,
)
from starrail_auto.uu.config import UU_PROCESS_NAMES

# hyp.exe 是米哈游启动器：游戏要更新时，脚本要点它的“重试”。
GUARDED_PROCESS_NAMES = frozenset(
    {
        *UU_PROCESS_NAMES,
        *GAME_PROCESS_NAMES,
        M7A_ASSISTANT_PROCESS_NAME,
        M7A_LAUNCHER_PROCESS_NAME,
        "hyp.exe",
    }
)


def keep_game_windows_uncovered() -> AbstractContextManager[TopmostCoverGuard]:
    return keep_covering_windows_down(GUARDED_PROCESS_NAMES)
