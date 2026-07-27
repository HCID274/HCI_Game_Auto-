"""Evidence-preserving final cleanup for unattended daily runs."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field

import psutil

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


def cleanup_after_run(*, acceleration_was_connected: bool) -> CleanupResult:
    """Capture is done by the caller; this function then closes every owned leaf."""
    result = CleanupResult()
    try:
        stop_daily_workers()
        stop_pyappify_launchers()
    except Exception as exc:
        result.issues.append(f"OK-WW关闭异常：{exc}")
    result.ok_closed = not _running_ok_processes()
    if not result.ok_closed:
        result.issues.append("OK-WW进程未完全退出")

    try:
        stop_wuthering_game()
    except Exception as exc:
        result.issues.append(f"鸣潮关闭异常：{exc}")
    result.game_closed = not _game_running()
    if not result.game_closed:
        result.issues.append("鸣潮进程未完全退出")

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
