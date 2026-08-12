"""Run OK-WW DailyTask with host-owned, in-memory compatibility fixes."""

from __future__ import annotations

import inspect
import os
import sys
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any

# The bundled OK-WW interpreter starts this file directly, so make the host
# package importable without installing it into the upstream environment.
HOST_SRC = Path(__file__).resolve().parents[2]
if str(HOST_SRC) not in sys.path:
    sys.path.insert(0, str(HOST_SRC))

from wuwa_auto.okww.daily_activity import install_daily_activity_override
from wuwa_auto.okww.daily_trace import install_daily_trace


CLICK_ATTEMPTS = 2
TRANSITION_TIMEOUT_SECONDS = 8
WORLD_TIMEOUT_SECONDS = 120
RECOVERY_TIMEOUT_SECONDS = 60
CONFIRM_FEATURES = (
    "confirm_btn_hcenter_vcenter",
    "confirm_btn_highlight_hcenter_vcenter",
)
TRAVEL_ATTEMPT_MARKER = "HOST_NIGHTMARE_TRAVEL_ATTEMPT"
TRAVEL_CONFIRMED_MARKER = "HOST_NIGHTMARE_TRAVEL_CONFIRMED"
TRAVEL_RETRY_MARKER = "HOST_NIGHTMARE_TRAVEL_RETRY"
TRAVEL_NOT_CONFIRMED_MARKER = "HOST_NIGHTMARE_TRAVEL_NOT_CONFIRMED"
TRAVEL_RECOVERED_MARKER = "HOST_NIGHTMARE_TRAVEL_RECOVERED_TO_WORLD"
COMPATIBILITY_MARKER = "HOST_NIGHTMARE_OVERRIDE_COMPATIBLE"
DAILY_RESUME_MARKER = "HOST_DAILY_RESUME_AFTER_NIGHTMARE"
AUTO_FARM_NIGHTMARE_NEST = "Auto Farm all Nightmare Nest"
ADDITIONAL_TASKS = "Additional Tasks to Run After Daily Task"
FARM_NIGHTMARE_FOR_DAILY_ECHO = "Farm Nightmare Nest for Daily Echo"


def _target_key(nest: object) -> str:
    return str(getattr(nest, "cache_key", "unknown"))


def _probe_transition(task: Any, travel_name: str) -> str | None:
    """Return a stable transition state while safely handling confirmation UI."""
    if task.in_team_and_world():
        return "world"
    confirm = task._find_first_feature(CONFIRM_FEATURES, threshold=0.6)
    if confirm:
        task.click(confirm, after_sleep=1)
        return None
    if not task.find_one(travel_name, threshold=0.7):
        return "button_gone"
    return None


def _cache_skipped_target(task: Any, nest: object) -> None:
    cache_key = getattr(nest, "cache_key", None)
    unreachable = getattr(task, "_unreachable_nests", None)
    if cache_key and hasattr(unreachable, "add"):
        unreachable.add(cache_key)


def _recover_before_skip(
    task: Any,
    nest: object,
    *,
    upstream_ensure_main: Callable[..., object],
    reason: str,
) -> bool:
    """Preserve evidence and return to open world before skipping one target."""
    target = _target_key(nest)
    _cache_skipped_target(task, nest)
    task.log_info(
        f"{TRAVEL_NOT_CONFIRMED_MARKER} target={target} reason={reason}"
    )
    try:
        task.screenshot(TRAVEL_NOT_CONFIRMED_MARKER)
    except Exception as exc:
        task.log_info(f"host nightmare evidence capture failed: {exc}")

    # DailyTask temporarily shadows the instance's ensure_main with a lambda.
    # Calling the method captured from the upstream class bypasses that shadow
    # without writing to any file in the OK-WW installation.
    try:
        upstream_ensure_main(task, time_out=RECOVERY_TIMEOUT_SECONDS)
    except Exception as exc:
        raise RuntimeError(
            "host nightmare travel recovery could not return to open world: "
            f"target={target}; reason={reason}; recovery={exc}"
        ) from exc
    if not task.in_team_and_world():
        raise RuntimeError(
            "host nightmare travel recovery returned outside open world: "
            f"target={target}; reason={reason}"
        )
    task.log_info(f"{TRAVEL_RECOVERED_MARKER} target={target}")
    return False


def confirmed_nightmare_travel(
    task: Any,
    nest: object,
    *,
    upstream_ensure_main: Callable[..., object],
    click_attempts: int = CLICK_ATTEMPTS,
    transition_timeout: float = TRANSITION_TIMEOUT_SECONDS,
) -> bool:
    """Confirm a real UI transition instead of treating one stale frame as unreachable."""
    if click_attempts < 1:
        raise ValueError("click_attempts must be positive")

    travel = task.wait_until(
        task._find_travel_button,
        raise_if_not_found=False,
        time_out=2,
    )
    if not travel:
        return _recover_before_skip(
            task,
            nest,
            upstream_ensure_main=upstream_ensure_main,
            reason="travel_button_missing",
        )

    target = _target_key(nest)
    for attempt in range(1, click_attempts + 1):
        task.log_info(
            f"{TRAVEL_ATTEMPT_MARKER} {attempt}/{click_attempts} "
            f"target={target} button={travel.name}"
        )
        task.click(travel, after_sleep=1)
        transition = task.wait_until(
            lambda: _probe_transition(task, travel.name),
            time_out=transition_timeout,
            raise_if_not_found=False,
        )
        if transition == "world":
            task.log_info(
                f"{TRAVEL_CONFIRMED_MARKER} target={target} source=world"
            )
            return True
        if transition == "button_gone":
            if task.wait_in_team_and_world(
                time_out=WORLD_TIMEOUT_SECONDS,
                raise_if_not_found=False,
            ):
                task.log_info(
                    f"{TRAVEL_CONFIRMED_MARKER} target={target} "
                    "source=button_gone"
                )
                return True
            return _recover_before_skip(
                task,
                nest,
                upstream_ensure_main=upstream_ensure_main,
                reason="world_not_reached_after_transition",
            )

        if attempt < click_attempts:
            task.log_info(
                f"{TRAVEL_RETRY_MARKER} target={target} "
                "reason=button_still_visible"
            )
            refreshed = task.wait_until(
                task._find_travel_button,
                raise_if_not_found=False,
                time_out=2,
            )
            if refreshed:
                travel = refreshed
                continue
            if task.wait_in_team_and_world(
                time_out=WORLD_TIMEOUT_SECONDS,
                raise_if_not_found=False,
            ):
                task.log_info(
                    f"{TRAVEL_CONFIRMED_MARKER} target={target} "
                    "source=late_transition"
                )
                return True
            return _recover_before_skip(
                task,
                nest,
                upstream_ensure_main=upstream_ensure_main,
                reason="travel_button_disappeared_without_world",
            )

    return _recover_before_skip(
        task,
        nest,
        upstream_ensure_main=upstream_ensure_main,
        reason="button_still_visible_after_retry",
    )


def install_nightmare_override(task_class: type[Any]) -> None:
    """Install the narrow override after validating the upstream API contract."""
    method = getattr(task_class, "_travel_to_nest_or_skip", None)
    ensure_main = getattr(task_class, "ensure_main", None)
    if method is None or ensure_main is None:
        raise RuntimeError(
            "OK-WW NightmareNestTask is incompatible: required methods missing"
        )
    parameters = list(inspect.signature(method).parameters)
    if parameters != ["self", "nest"]:
        raise RuntimeError(
            "OK-WW NightmareNestTask is incompatible: "
            f"_travel_to_nest_or_skip signature={parameters!r}"
        )

    def host_travel(self: Any, nest: object) -> bool:
        return confirmed_nightmare_travel(
            self,
            nest,
            upstream_ensure_main=ensure_main,
        )

    host_travel.__name__ = method.__name__
    host_travel.__qualname__ = method.__qualname__
    setattr(task_class, "_travel_to_nest_or_skip", host_travel)


def install_daily_resume_after_nightmare(task_class: type[Any]) -> None:
    """Skip only already-settled Nightmare work in this worker process."""

    original = getattr(task_class, "run", None)
    if not callable(original) or getattr(original, "__wuwa_host_daily_resume__", False):
        return

    @wraps(original)
    def resumed(self: Any, *args: Any, **kwargs: Any) -> Any:
        config = getattr(self, "config", None)
        if not isinstance(config, dict):
            raise RuntimeError("DailyTask config is unavailable for bounded resume")
        had_farm_nightmare = FARM_NIGHTMARE_FOR_DAILY_ECHO in config
        original_farm_nightmare = config.get(FARM_NIGHTMARE_FOR_DAILY_ECHO)
        had_additional = ADDITIONAL_TASKS in config
        original_additional = config.get(ADDITIONAL_TASKS)
        config[FARM_NIGHTMARE_FOR_DAILY_ECHO] = False
        config[ADDITIONAL_TASKS] = [
            item
            for item in list(original_additional or [])
            if item != AUTO_FARM_NIGHTMARE_NEST
        ]
        self.log_info(DAILY_RESUME_MARKER)
        try:
            return original(self, *args, **kwargs)
        finally:
            if had_farm_nightmare:
                config[FARM_NIGHTMARE_FOR_DAILY_ECHO] = original_farm_nightmare
            else:
                config.pop(FARM_NIGHTMARE_FOR_DAILY_ECHO, None)
            if had_additional:
                config[ADDITIONAL_TASKS] = original_additional
            else:
                config.pop(ADDITIONAL_TASKS, None)

    resumed.__wuwa_host_daily_resume__ = True
    task_class.run = resumed


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    compatibility_only = False
    resume_after_nightmare = False
    while arguments and arguments[0].startswith("--"):
        option = arguments.pop(0)
        if option == "--check-compatibility":
            compatibility_only = True
            continue
        if option == "--resume-after-nightmare":
            resume_after_nightmare = True
            continue
        raise SystemExit(f"unsupported host worker option: {option}")
    if len(arguments) != 1:
        raise SystemExit(
            "usage: daily_worker.py [--check-compatibility] "
            "[--resume-after-nightmare] OK_WORKING_DIR"
        )

    working_dir = Path(arguments[0]).resolve()
    os.chdir(working_dir)
    sys.path.insert(0, str(working_dir))

    from src.task.NightmareNestTask import NightmareNestTask
    from src.task.DailyTask import DailyTask
    from src.task.TacetTask import TacetTask
    from src.task.ForgeryTask import ForgeryTask
    from src.task.SimulationTask import SimulationTask

    install_nightmare_override(NightmareNestTask)
    install_daily_activity_override(DailyTask)
    install_daily_trace(
        DailyTask,
        nightmare_task_class=NightmareNestTask,
        tacet_task_class=TacetTask,
        forgery_task_class=ForgeryTask,
        simulation_task_class=SimulationTask,
    )
    if resume_after_nightmare:
        install_daily_resume_after_nightmare(DailyTask)
    if compatibility_only:
        print(COMPATIBILITY_MARKER)
        return 0

    from config import config
    from ok import run_task

    run_task(config, task=1, debug=False, exit_after=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
