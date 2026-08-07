"""Fail fast when an OK-WW update changes APIs used by host adapters."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from wuwa_auto.settings import OK_PYTHON_EXE, OK_WORKING_DIR

log = logging.getLogger(__name__)

WORKER = Path(__file__).with_name("compatibility_worker.py")
MARKER = "HOST_OKWW_COMPATIBLE"


def validate_okww_compatibility() -> None:
    completed = subprocess.run(
        [str(OK_PYTHON_EXE), str(WORKER), str(OK_WORKING_DIR)],
        cwd=OK_WORKING_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    output = "\n".join(
        part.strip()
        for part in (completed.stdout, completed.stderr)
        if part.strip()
    )
    if completed.returncode != 0 or MARKER not in completed.stdout:
        raise RuntimeError(
            "OK-WW compatibility probe failed; the installed upstream version "
            f"may have changed: exit={completed.returncode}; output={output[-4000:]}"
        )
    log.info("OK-WW host adapter compatibility passed")


__all__ = ["validate_okww_compatibility"]
