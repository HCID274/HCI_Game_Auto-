"""Evidence-preserving final cleanup for unattended daily runs."""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, field

import psutil

from wuwa_auto.client.launcher import (
    is_client_launcher_running,
    stop_client_launchers,
)
from wuwa_auto.okww.runner import (
    _running_ok_processes,
    stop_daily_workers,
    stop_pyappify_launchers,
    stop_wuthering_game,
)
from wuwa_auto.uu.processes import (
    is_any_uu_process_running,
    is_uu_running,
    terminate_uu,
)
from wuwa_auto.uu.service import disconnect

log = logging.getLogger(__name__)
_WORKFLOW_COMMANDS = {"daily", "farm-echo", "weekly-garden"}


@dataclass
class CleanupResult:
    completed: bool = False
    ok_closed: bool = False
    game_closed: bool = False
    acceleration_disconnected: bool = False
    uu_exited: bool = False
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _game_running() -> bool:
    return any(
        (proc.info["name"] or "").casefold() == "client-win64-shipping.exe"
        for proc in psutil.process_iter(["name"])
    )


def _is_wuwa_workflow_controller(command_line: list[str] | None) -> bool:
    if not command_line:
        return False
    tokens = [str(token).casefold() for token in command_line]
    has_entrypoint = any("wuwa-auto" in token for token in tokens)
    return has_entrypoint and any(token in _WORKFLOW_COMMANDS for token in tokens)


def stop_stale_workflow_controllers() -> list[int]:
    """Terminate orphaned Wuwa workflow controllers outside this process tree."""
    protected = {os.getpid()}
    current = psutil.Process()
    protected.update(parent.pid for parent in current.parents())
    stale: list[psutil.Process] = []
    for process in psutil.process_iter(["pid", "cmdline"]):
        if process.pid in protected:
            continue
        try:
            if _is_wuwa_workflow_controller(process.info.get("cmdline")):
                stale.append(process)
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    for process in stale:
        try:
            process.terminate()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            log.exception("could not terminate stale Wuwa controller pid=%s", process.pid)
    _, alive = psutil.wait_procs(stale, timeout=5)
    for process in alive:
        try:
            process.kill()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            log.exception("could not kill stale Wuwa controller pid=%s", process.pid)
    stopped = [process.pid for process in stale]
    if stopped:
        log.warning("stopped stale Wuwa workflow controllers: %s", stopped)
    return stopped


def cleanup_after_run(*, acceleration_was_connected: bool) -> CleanupResult:
    """Capture is done by the caller; this function then closes every owned leaf."""
    result = CleanupResult()
    try:
        stop_stale_workflow_controllers()
        stop_daily_workers()
        stop_pyappify_launchers()
    except Exception as exc:
        result.issues.append(f"OK-WW关闭异常：{exc}")
    result.ok_closed = not _running_ok_processes()
    if not result.ok_closed:
        result.issues.append("OK-WW进程未完全退出")

    try:
        stop_wuthering_game()
        stop_client_launchers()
    except Exception as exc:
        result.issues.append(f"鸣潮关闭异常：{exc}")
    result.game_closed = not _game_running() and not is_client_launcher_running()
    if not result.game_closed:
        result.issues.append("鸣潮客户端或启动器进程未完全退出")

    if is_uu_running():
        try:
            disconnect()
            result.acceleration_disconnected = True
        except Exception as exc:
            if acceleration_was_connected:
                result.issues.append(f"鸣潮加速未确认断开：{exc}")
            else:
                log.info("UU disconnect was unnecessary or unverifiable: %s", exc)
    else:
        result.acceleration_disconnected = True

    try:
        terminate_uu()
    except Exception as exc:
        result.issues.append(f"UU退出异常：{exc}")
    result.uu_exited = not is_any_uu_process_running()
    if not result.uu_exited:
        result.issues.append("UU进程未完全退出")

    result.completed = result.ok_closed and result.game_closed and result.uu_exited
    log.info("daily cleanup result: %s", result.to_dict())
    return result
