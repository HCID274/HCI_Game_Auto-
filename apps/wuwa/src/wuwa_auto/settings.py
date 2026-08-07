"""Project paths, installed application locations and local secrets."""

import os
from pathlib import Path

from dotenv import load_dotenv

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
USER_CONTEXT_DIR = PROJECT_ROOT / "UserContext"
TEMPLATES_DIR = PROJECT_ROOT / "templates"
RUNTIME_DIR = PROJECT_ROOT / "runtime"
LOGS_DIR = RUNTIME_DIR / "logs"
EVIDENCE_DIR = RUNTIME_DIR / "evidence"
REPORTS_DIR = RUNTIME_DIR / "reports"
RUNS_DIR = RUNTIME_DIR / "runs"
FARM_ECHO_TARGET_REQUEST = RUNTIME_DIR / "next-farm-echo-target"
VENDOR_DIR = RUNTIME_DIR / "vendor"
VIIPER_DIR = VENDOR_DIR / "viiper" / "0.7.0"
VIIPER_EXE = VIIPER_DIR / "viiper.exe"
USBIP_DIR = VENDOR_DIR / "usbip-win2" / "0.9.7.8"
USBIP_INSTALLER = USBIP_DIR / "USBip-0.9.7.8-x64.exe"

UU_EXE = Path(r"D:\2_Software\4_Games\Netease\UU\uu_launcher.exe")
WUWA_INSTALL_DIR = Path(r"D:\2_Software\4_Games\Wuthering Waves")
WUWA_LAUNCHER_EXE = WUWA_INSTALL_DIR / "launcher.exe"
WUWA_CLIENT_EXE = (
    WUWA_INSTALL_DIR
    / "Wuthering Waves Game"
    / "Client"
    / "Binaries"
    / "Win64"
    / "Client-Win64-Shipping.exe"
)
WUWA_LAUNCHER_READY_TEMPLATE = TEMPLATES_DIR / "client_launcher_ready.png"
WUWA_LAUNCHER_PRIMARY_ANCHOR_TEMPLATE = (
    TEMPLATES_DIR / "client_launcher_primary_anchor.png"
)
WUWA_CLIENT_LOGIN_TEMPLATE = TEMPLATES_DIR / "client_login_connect.png"
WUWA_CLIENT_MONTHLY_REWARD_TEMPLATE = TEMPLATES_DIR / "client_monthly_reward.png"
WUWA_CLIENT_REWARD_RESULT_TEMPLATE = TEMPLATES_DIR / "client_reward_result_close.png"
WUWA_CLIENT_NETWORK_RETRY_TEMPLATE = TEMPLATES_DIR / "client_network_retry.png"
WUWA_CLIENT_UPDATE_RESTART_NOTICE_TEMPLATE = (
    TEMPLATES_DIR / "client_update_restart_notice.png"
)
WUWA_CLIENT_UPDATE_RESTART_CONFIRM_TEMPLATE = (
    TEMPLATES_DIR / "client_update_restart_confirm.png"
)
OK_WW_EXE = Path(
    r"D:\2_Software\4_Games\Wuthering Waves\01Auto\ok-ww\ok-ww.exe"
)
OK_PYTHONW_EXE = OK_WW_EXE.parent / "data" / "apps" / "ok-ww" / "python" / "pythonw.exe"
OK_PYTHON_EXE = OK_PYTHONW_EXE.with_name("python.exe")
OK_WORKING_DIR = Path(
    r"D:\2_Software\4_Games\Wuthering Waves\01Auto\ok-ww"
    r"\data\apps\ok-ww\working"
)
OK_ENTRYPOINT = OK_WORKING_DIR / "main.py"
OK_LOG_FILE = OK_WORKING_DIR / "logs" / "ok-script.log"
OK_CONFIG_DIR = OK_WORKING_DIR / "configs"
OK_DAILY_CONFIG = OK_CONFIG_DIR / "DailyTask.json"
OK_FARM_ECHO_CONFIG = OK_CONFIG_DIR / "FarmEchoTask.json"
OK_GARDEN_CONFIG = OK_CONFIG_DIR / "GardenTask.json"
OK_NIGHTMARE_CONFIG = OK_CONFIG_DIR / "NightmareNestTask.json"


def load_local_environment() -> None:
    """Load Wuwa secrets, then reuse only unset values from Star Rail locally."""
    load_dotenv(ENV_PATH, override=False)
    sibling_env = PROJECT_ROOT.parent / "starrail" / ".env"
    if sibling_env.is_file():
        load_dotenv(sibling_env, override=False)


def get_secret(name: str) -> str:
    load_local_environment()
    return os.environ.get(name, "").strip()
