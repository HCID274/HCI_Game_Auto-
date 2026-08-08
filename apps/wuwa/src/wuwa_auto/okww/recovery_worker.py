"""Run OK-WW's proven domain recovery sequence without changing its source."""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

try:
    from .confirmed_retry_worker import _virtual_hid_click
    from .farm_echo_state import (
        IN_PLACE_REVIVAL_COMPLETED_MARKER,
        REALM_DEFEAT_RETRY_COMPLETED_MARKER,
        click_realm_defeat_retry,
        click_revive_confirm,
        realm_defeat_visible,
    )
except ImportError:  # executed directly by OK-WW's bundled Python
    from confirmed_retry_worker import _virtual_hid_click
    from farm_echo_state import (
        IN_PLACE_REVIVAL_COMPLETED_MARKER,
        REALM_DEFEAT_RETRY_COMPLETED_MARKER,
        click_realm_defeat_retry,
        click_revive_confirm,
        realm_defeat_visible,
    )


RECOVERY_STARTED_MARKER = "HOST_FARM_ECHO_RECOVERY_STARTED"
RECOVERY_COMPLETED_MARKER = "HOST_FARM_ECHO_RECOVERY_COMPLETED"
WORLD_RECOVERY_STARTED_MARKER = "HOST_WORLD_STATE_RECOVERY_STARTED"
WORLD_RECOVERY_COMPLETED_MARKER = "HOST_WORLD_STATE_RECOVERY_COMPLETED"
RECOVERY_HID_CLICK_MARKER = "HOST_FARM_ECHO_RECOVERY_VIRTUAL_HID_CLICK"


def _write_result(path: Path, **values: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(values, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _active_challenge_visible(task: object) -> bool:
    """Require an actual combat signal; realm/setup UI alone is insufficient."""
    in_combat = task.in_combat  # type: ignore[attr-defined]
    return bool(in_combat(target=True) or in_combat())


def _wait_for_active_challenge(task: object, *, time_out: float) -> bool:
    return bool(
        task.wait_until(  # type: ignore[attr-defined]
            lambda: _active_challenge_visible(task),
            time_out=time_out,
            raise_if_not_found=False,
        )
    )


def _recover_detected_death_state(task: object) -> str:
    """Recover from the visible UI, independent of exception ordering."""
    if realm_defeat_visible(task, time_out=2):
        return _retry_realm_defeat(task)

    if not task.wait_feature(
        "revive_confirm_hcenter_vcenter", threshold=0.8, time_out=5
    ):
        raise RuntimeError("neither revive dialog nor realm defeat is visible")
    click_revive_confirm(task)
    if not _wait_for_active_challenge(task, time_out=15):
        raise RuntimeError("in-place revival did not resume the active realm")
    task.log_info(IN_PLACE_REVIVAL_COMPLETED_MARKER)
    return IN_PLACE_REVIVAL_COMPLETED_MARKER


def _retry_realm_defeat(task: object) -> str:
    """Restart a failed realm in place and verify the challenge is active."""
    click_realm_defeat_retry(task)
    if task.wait_click_feature(
        "team_start_challenge",
        raise_if_not_found=False,
        time_out=12,
        after_sleep=1,
    ):
        task.wait_click_skip_dialog_confirm()
    if not _wait_for_active_challenge(task, time_out=60):
        raise RuntimeError("realm retry did not resume the challenge")
    task.log_info(REALM_DEFEAT_RETRY_COMPLETED_MARKER)
    return REALM_DEFEAT_RETRY_COMPLETED_MARKER


def _require_recovery_completion(marker: str | None) -> str:
    """Reject OK-WW task failures that its executor catches internally."""
    if not marker:
        raise RuntimeError("recovery task returned without a host completion marker")
    return marker


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) not in {2, 3}:
        raise SystemExit(
            "usage: recovery_worker.py OK_WORKING_DIR RESULT_PATH "
            "[death|realm_defeat|world]"
        )
    working_dir = Path(arguments[0]).resolve()
    result_path = Path(arguments[1]).resolve()
    mode = arguments[2] if len(arguments) == 3 else "death"
    if mode not in {"death", "realm_defeat", "world"}:
        raise SystemExit(f"unsupported recovery mode: {mode}")
    started = datetime.now().astimezone()
    try:
        os.chdir(working_dir)
        sys.path.insert(0, str(working_dir))

        from ok import run_task
        from src.task.DomainTask import DomainTask
        from src.task.WWOneTimeTask import WWOneTimeTask

        from config import config

        recovery_completion: str | None = None

        class VirtualHidRecoveryMixin:
            def click(
                self,
                x: object = -1,
                y: object = -1,
                move_back: bool = False,
                name: object = None,
                interval: float = -1,
                move: bool = False,
                down_time: float = 0.01,
                after_sleep: float = 0,
                key: str = "left",
                **kwargs: object,
            ) -> object:
                if (
                    key in {"left", "middle", "right"}
                    and isinstance(x, (int, float))
                    and isinstance(y, (int, float))
                    and not (0 < x < 1 or 0 < y < 1)
                ):
                    if not self.check_interval(interval):
                        self.executor.reset_scene()
                        return False
                    target_x = self.width // 2 if x == -1 else int(x)
                    target_y = self.height // 2 if y == -1 else int(y)
                    absolute_x, absolute_y = (
                        self.executor.interaction.capture.get_abs_cords(
                            target_x, target_y
                        )
                    )
                    _virtual_hid_click(
                        absolute_x,
                        absolute_y,
                        button=key,
                        hold=max(0.08, float(down_time)),
                        log_action=bool(name),
                    )
                    if name:
                        self.log_info(
                            f"{RECOVERY_HID_CLICK_MARKER} "
                            f"{absolute_x},{absolute_y} {name}"
                        )
                    if after_sleep > 0:
                        self.sleep(after_sleep)
                    self.executor.reset_scene()
                    return True
                return super().click(
                    x,
                    y,
                    move_back=move_back,
                    name=name,
                    interval=interval,
                    move=move,
                    down_time=down_time,
                    after_sleep=after_sleep,
                    key=key,
                    **kwargs,
                )

        class FarmEchoDeathRecoveryTask(VirtualHidRecoveryMixin, DomainTask):
            name = "Farm Echo Death Recovery"

            def run(self) -> None:
                nonlocal recovery_completion
                WWOneTimeTask.run(self)
                self.log_info(RECOVERY_STARTED_MARKER)
                recovery_completion = _recover_detected_death_state(self)
                self.log_info(RECOVERY_COMPLETED_MARKER)

        class WorldStateRecoveryTask(VirtualHidRecoveryMixin, DomainTask):
            name = "World State Recovery"

            def run(self) -> None:
                nonlocal recovery_completion
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
                recovery_completion = WORLD_RECOVERY_COMPLETED_MARKER
                self.log_info(WORLD_RECOVERY_COMPLETED_MARKER)

        class RealmDefeatRecoveryTask(VirtualHidRecoveryMixin, DomainTask):
            name = "Farm Echo Realm Defeat Recovery"

            def run(self) -> None:
                nonlocal recovery_completion
                WWOneTimeTask.run(self)
                recovery_completion = _retry_realm_defeat(self)

        task_by_mode = {
            "death": FarmEchoDeathRecoveryTask,
            "realm_defeat": RealmDefeatRecoveryTask,
            "world": WorldStateRecoveryTask,
        }
        run_task(
            config,
            task=task_by_mode[mode],
            debug=False,
            exit_after=False,
        )
        completed_marker = _require_recovery_completion(recovery_completion)
        _write_result(
            result_path,
            success=True,
            reason=completed_marker,
            resume_active_realm=completed_marker
            in {
                IN_PLACE_REVIVAL_COMPLETED_MARKER,
                REALM_DEFEAT_RETRY_COMPLETED_MARKER,
            },
            started_at=started.isoformat(),
            finished_at=datetime.now().astimezone().isoformat(),
        )
        return 0
    except BaseException as exc:  # noqa: BLE001 - serialize every worker failure
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
