"""UU process discovery, launch, and bounded termination."""

import logging
import subprocess

import psutil

from wuwa_auto.settings import UU_EXE
from wuwa_auto.uu.config import UU_PROCESS_NAMES
from wuwa_auto.uu.errors import UuStartupError

log = logging.getLogger(__name__)


def _uu_processes() -> list[psutil.Process]:
    processes: list[psutil.Process] = []
    for proc in psutil.process_iter(["name", "pid"]):
        name = (proc.info["name"] or "").casefold()
        if name in UU_PROCESS_NAMES:
            processes.append(proc)
    return processes


def is_uu_running() -> bool:
    return any(
        (proc.info["name"] or "").casefold() == "uu.exe"
        for proc in psutil.process_iter(["name"])
    )


def is_any_uu_process_running() -> bool:
    return bool(_uu_processes())


def start_uu() -> None:
    if not UU_EXE.is_file():
        raise UuStartupError(
            "start_uu_process",
            f"UU executable not found: {UU_EXE}",
            retryable=False,
        )
    try:
        subprocess.Popen([str(UU_EXE)], cwd=UU_EXE.parent)
    except OSError as exc:
        raise UuStartupError(
            "start_uu_process",
            f"failed to launch UU: {exc}",
            retryable=False,
        ) from exc


def terminate_uu() -> bool:
    targets = _uu_processes()
    for proc in targets:
        try:
            proc.terminate()
            log.info("terminated %s (pid=%d)", proc.name(), proc.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            log.warning("cannot terminate pid=%d: %s", proc.pid, exc)
    _, alive = psutil.wait_procs(targets, timeout=5)
    for proc in alive:
        try:
            proc.kill()
            log.warning("force-killed %s (pid=%d)", proc.name(), proc.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    _, still_alive = psutil.wait_procs(alive, timeout=3)
    return not still_alive and not _uu_processes()
