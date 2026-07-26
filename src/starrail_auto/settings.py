"""Project paths and local secret loading."""

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


def load_local_environment() -> None:
    """Load local values without overriding explicitly configured system values."""
    load_dotenv(ENV_PATH, override=False)


def get_secret(name: str) -> str:
    load_local_environment()
    return os.environ.get(name, "").strip()
