"""UU process discovery, launch, and termination."""

import logging
import subprocess

import psutil

from starrail_auto.uu.config import UU_EXE
from starrail_auto.uu.errors import UuStartupError

log = logging.getLogger(__name__)


def is_uu_running() -> bool:
    for proc in psutil.process_iter(["name"]):
        if proc.info["name"] and "uu" in proc.info["name"].lower():
            return True
    return False


def start_uu() -> None:
    if not UU_EXE.exists():
        raise UuStartupError(
            "start_uu_process",
            f"UU executable not found: {UU_EXE}",
            retryable=False,
        )
    try:
        subprocess.Popen([str(UU_EXE)])
    except OSError as exc:
        raise UuStartupError(
            "start_uu_process",
            f"failed to start UU accelerator: {exc}",
            retryable=False,
        ) from exc


def kill_uu() -> None:
    targets: list[psutil.Process] = []
    for proc in psutil.process_iter(["name", "pid"]):
        name = proc.info["name"] or ""
        if "uu" not in name.lower():
            continue
        try:
            proc.terminate()
            targets.append(proc)
            log.info("terminated %s (pid=%d)", name, proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            log.warning("cannot terminate %s (pid=%d): %s", name, proc.info["pid"], exc)

    if not targets:
        log.info("no UU processes found")
        return

    _, alive = psutil.wait_procs(targets, timeout=5)
    for proc in alive:
        try:
            proc.kill()
            log.warning("force-killed %s (pid=%d)", proc.name(), proc.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    log.info("UU accelerator stopped")
