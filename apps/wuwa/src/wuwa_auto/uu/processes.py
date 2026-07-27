"""Wuthering Waves configuration adapter for shared UU process management."""

from game_automation_core.uu.processes import UuProcessController, UuProcessSpec
from wuwa_auto.settings import UU_EXE
from wuwa_auto.uu.config import UU_PROCESS_NAMES

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


def start_uu() -> None:
    _controller.start()


def terminate_uu() -> bool:
    return _controller.terminate()
