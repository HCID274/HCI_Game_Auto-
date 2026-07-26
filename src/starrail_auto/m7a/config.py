"""M7A paths, watchdog policy, markers, and exit codes."""

import re
from pathlib import Path

from starrail_auto.settings import EVIDENCE_DIR

M7A_LAUNCHER = Path(
    r"D:\2_Software\4_Games\StarRail\Auto\March7thAssistant_full\March7th Launcher.exe"
)
M7A_LOG_DIR = Path(
    r"D:\2_Software\4_Games\StarRail\Auto\March7thAssistant_full\logs"
)
DEBUG_DIR = EVIDENCE_DIR

GRACE_PERIOD = 60
CPU_IDLE_THRESHOLD = 2.0
CPU_IDLE_WINDOW = 900
LOG_HEARTBEAT_TIMEOUT = 600
WATCHDOG_INTERVAL = 30
DAILY_RESULT_POLL_INTERVAL = 5
GAME_READY_TIMEOUT = 120
GAME_READY_INTERVAL = 2
GAME_NETWORK_HOST = "hsr.hoyoverse.com"
GAME_NETWORK_PORT = 443
GAME_NETWORK_TIMEOUT = 10

DEFAULT_TIMEOUTS = {"universe": 7200, "main": 1800}
GAME_PROCESS_NAMES = {"starrail.exe"}
GAME_WINDOW_KEYWORDS = ("崩坏：星穹铁道",)
M7A_ASSISTANT_PROCESS_NAME = "march7th assistant.exe"
M7A_RUNTIME_DISCOVERY_TIMEOUT = 15
M7A_RUNTIME_DISCOVERY_INTERVAL = 0.5
M7A_DAILY_COMPLETION_MARKER = "每日实训已完成"
M7A_DAILY_INCOMPLETE_MARKER = "每日实训未完成"
M7A_COMPLETION_VALIDATION_TIMEOUT = 15
M7A_COMPLETION_VALIDATION_INTERVAL = 0.5
M7A_DAILY_SCORE_PATTERN = re.compile(r"当前(?:累计)?分数[：:]\s*(\d+)\s*/\s*(\d+)")
M7A_DAILY_BLOCKER_PATTERN = re.compile(r"任务无法完成:\s*(.+)")
M7A_DAILY_BLOCKER_LABELS = (
    ("累计消耗120点开拓力", "体力"),
    ("使用支援角色", "支援"),
    ("差分宇宙", "差分"),
    ("货币战争", "差分"),
    ("万能合成机", "合成"),
    ("派遣委托", "委托"),
)

EXIT_OK = 0
EXIT_UU_FAILED = 10
EXIT_M7A_LAUNCH_FAILED = 20
EXIT_GAME_READY_TIMEOUT = 21
EXIT_M7A_EXIT_NONZERO = 22
EXIT_DAILY_VALIDATION_FAILED = 23
EXIT_GAME_NETWORK_FAILED = 24
EXIT_WATCHDOG_HARD_TIMEOUT = 30
EXIT_WATCHDOG_CPU_IDLE = 31
EXIT_WATCHDOG_LOG_STALLED = 32
