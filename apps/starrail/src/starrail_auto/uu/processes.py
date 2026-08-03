"""Star Rail configuration adapter for shared UU process management."""

from game_automation_core.uu.processes import UuProcessController, UuProcessSpec
from starrail_auto.uu.config import UU_EXE, UU_PROCESS_NAMES

_controller = UuProcessController(
    UuProcessSpec(
        executable=UU_EXE,
        managed_names=frozenset(UU_PROCESS_NAMES),
    )
)


def is_uu_running() -> bool:
    return _controller.is_running()


def is_any_uu_process_running() -> bool:
    return _controller.is_any_running()


def uu_primary_pids() -> frozenset[int]:
    return _controller.primary_pids()


def start_uu() -> None:
    _controller.start()


def kill_uu() -> bool:
    return _controller.terminate()


def terminate_uu() -> bool:
    return _controller.terminate()
