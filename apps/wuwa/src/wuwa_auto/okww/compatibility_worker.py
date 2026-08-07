"""Probe only the upstream symbols used by host-owned OK-WW adapters."""

from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path


MARKER = "HOST_OKWW_COMPATIBLE"


def _require(owner: type[object], *names: str) -> None:
    missing = [name for name in names if not hasattr(owner, name)]
    if missing:
        raise RuntimeError(f"{owner.__name__} missing required API: {missing}")


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        raise SystemExit("usage: compatibility_worker.py OK_WORKING_DIR")
    working_dir = Path(arguments[0]).resolve()
    os.chdir(working_dir)
    sys.path.insert(0, str(working_dir))

    from ok import run_task
    from src.task.DomainTask import DomainTask
    from src.task.FarmEchoTask import FarmEchoTask
    from src.task.NightmareNestTask import NightmareNestTask
    from src.task.WWOneTimeTask import WWOneTimeTask

    if not callable(run_task):
        raise RuntimeError("ok.run_task is no longer callable")
    _require(WWOneTimeTask, "run")
    _require(
        FarmEchoTask,
        "teleport_to_configured_boss_and_prepare",
        "incr_drop",
        "do_run",
        "ensure_main",
        "wait_in_team_and_world",
    )
    _require(
        DomainTask,
        "revive_action",
        "ensure_main",
        "in_team_and_world",
        "wait_click_feature",
    )

    travel = getattr(NightmareNestTask, "_travel_to_nest_or_skip", None)
    if travel is None or list(inspect.signature(travel).parameters) != [
        "self",
        "nest",
    ]:
        raise RuntimeError(
            "NightmareNestTask._travel_to_nest_or_skip contract changed"
        )
    _require(NightmareNestTask, "ensure_main")
    print(MARKER)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
