"""Run OK-WW's proven domain recovery sequence without changing its source."""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path


RECOVERY_STARTED_MARKER = "HOST_FARM_ECHO_RECOVERY_STARTED"
RECOVERY_COMPLETED_MARKER = "HOST_FARM_ECHO_RECOVERY_COMPLETED"
WORLD_RECOVERY_STARTED_MARKER = "HOST_WORLD_STATE_RECOVERY_STARTED"
WORLD_RECOVERY_COMPLETED_MARKER = "HOST_WORLD_STATE_RECOVERY_COMPLETED"


def _write_result(path: Path, **values: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(values, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) not in {2, 3}:
        raise SystemExit(
            "usage: recovery_worker.py OK_WORKING_DIR RESULT_PATH [death|world]"
        )
    working_dir = Path(arguments[0]).resolve()
    result_path = Path(arguments[1]).resolve()
    mode = arguments[2] if len(arguments) == 3 else "death"
    if mode not in {"death", "world"}:
        raise SystemExit(f"unsupported recovery mode: {mode}")
    started = datetime.now().astimezone()
    try:
        os.chdir(working_dir)
        sys.path.insert(0, str(working_dir))

        from config import config
        from ok import run_task
        from src.task.DomainTask import DomainTask
        from src.task.WWOneTimeTask import WWOneTimeTask

        class FarmEchoDeathRecoveryTask(DomainTask):
            name = "Farm Echo Death Recovery"

            def run(self) -> None:
                WWOneTimeTask.run(self)
                self.log_info(RECOVERY_STARTED_MARKER)
                if not self.wait_feature(
                    "revive_confirm_hcenter_vcenter",
                    threshold=0.8,
                    time_out=5,
                ):
                    raise RuntimeError("revive dialog is no longer visible")
                if not self.revive_action():
                    raise RuntimeError("domain recovery did not return to world")
                if not self.in_team_and_world():
                    raise RuntimeError("recovery finished outside world team state")
                self.log_info(RECOVERY_COMPLETED_MARKER)

        class WorldStateRecoveryTask(DomainTask):
            name = "World State Recovery"

            def run(self) -> None:
                WWOneTimeTask.run(self)
                self.log_info(WORLD_RECOVERY_STARTED_MARKER)
                self.ensure_main(time_out=600)
                # DailyTask regards any visible party HUD as "main", including
                # a Tacet challenge restored after a restart. Probe the Esc
                # menu explicitly and use OK-WW's own domain-exit feature.
                self.sleep(3)
                self.send_key("esc", after_sleep=1)
                exited = self.wait_click_feature(
                    "gray_confirm_exit_button",
                    relative_x=-1,
                    raise_if_not_found=False,
                    time_out=5,
                    click_after_delay=0.5,
                    threshold=0.7,
                    after_sleep=1,
                )
                if not exited:
                    # Normal open world: the probe merely opened the Esc menu.
                    self.send_key("esc", after_sleep=1)
                if not self.wait_until(
                    self.in_world,
                    time_out=120,
                    raise_if_not_found=False,
                ):
                    raise RuntimeError(
                        "world-state recovery did not reach the open world"
                    )
                self.log_info(WORLD_RECOVERY_COMPLETED_MARKER)

        run_task(
            config,
            task=(
                FarmEchoDeathRecoveryTask
                if mode == "death"
                else WorldStateRecoveryTask
            ),
            debug=False,
            exit_after=False,
        )
        completed_marker = (
            RECOVERY_COMPLETED_MARKER
            if mode == "death"
            else WORLD_RECOVERY_COMPLETED_MARKER
        )
        _write_result(
            result_path,
            success=True,
            reason=completed_marker,
            started_at=started.isoformat(),
            finished_at=datetime.now().astimezone().isoformat(),
        )
        return 0
    except BaseException as exc:
        _write_result(
            result_path,
            success=False,
            reason=str(exc),
            traceback=traceback.format_exc(),
            started_at=started.isoformat(),
            finished_at=datetime.now().astimezone().isoformat(),
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
