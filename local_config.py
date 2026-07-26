"""Load project-local secrets from .env."""

import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent
ENV_PATH = PROJECT_ROOT / ".env"


def load_local_environment() -> None:
    """Load local values without overriding explicitly configured system values."""
    load_dotenv(ENV_PATH, override=False)


def get_secret(name: str) -> str:
    load_local_environment()
    return os.environ.get(name, "").strip()
