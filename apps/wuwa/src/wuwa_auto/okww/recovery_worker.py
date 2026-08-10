"""Run OK-WW's proven domain recovery sequence without changing its source."""

from __future__ import annotations

import json
import os
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path

try:
    from .farm_echo_state import (
        REALM_DEFEAT_HEAL_RECOVERY_COMPLETED_MARKER,
        REVIVE_DIALOG_HEAL_RECOVERY_COMPLETED_MARKER,
        click_realm_defeat_exit,
        realm_defeat_visible,
    )
    from .virtual_hid import _virtual_hid_click
except ImportError:  # executed directly by OK-WW's bundled Python
    from farm_echo_state import (
        REALM_DEFEAT_HEAL_RECOVERY_COMPLETED_MARKER,
        REVIVE_DIALOG_HEAL_RECOVERY_COMPLETED_MARKER,
        click_realm_defeat_exit,
        realm_defeat_visible,
    )
    from virtual_hid import _virtual_hid_click


RECOVERY_STARTED_MARKER = "HOST_FARM_ECHO_RECOVERY_STARTED"
RECOVERY_COMPLETED_MARKER = "HOST_FARM_ECHO_RECOVERY_COMPLETED"
WORLD_RECOVERY_STARTED_MARKER = "HOST_WORLD_STATE_RECOVERY_STARTED"
WORLD_RECOVERY_COMPLETED_MARKER = "HOST_WORLD_STATE_RECOVERY_COMPLETED"
RECOVERY_HID_CLICK_MARKER = "HOST_FARM_ECHO_RECOVERY_VIRTUAL_HID_CLICK"
DETECT_ACTION_OCR_MARKER = "HOST_FARM_ECHO_RECOVERY_DETECT_ACTION_OCR"
_DETECT_ACTION_RE = re.compile(r"探测|探測|Detect", re.IGNORECASE)
_DETECT_ACTION_REGION = (0.70, 0.78, 0.99, 0.98)


def _ocr_box_center(box: object) -> tuple[int, int] | None:
    center = getattr(box, "center", None)
    point = center() if callable(center) else None
    if (
        isinstance(point, tuple)
        and len(point) == 2
        and all(isinstance(value, (int, float)) for value in point)
    ):
        return int(point[0]), int(point[1])
    x = getattr(box, "x", None)
    y = getattr(box, "y", None)
    width = getattr(box, "width", None)
    height = getattr(box, "height", None)
    if all(isinstance(value, (int, float)) for value in (x, y, width, height)):
        return int(x + width / 2), int(y + height / 2)
    return None


def _find_detect_action_center(task: object) -> tuple[int, int] | None:
    """Locate the current F2 detect button instead of trusting a stale point."""

    boxes = task.wait_ocr(  # type: ignore[attr-defined]
        *_DETECT_ACTION_REGION,
        match=_DETECT_ACTION_RE,
        time_out=3,
        settle_time=0.5,
        raise_if_not_found=False,
    )
    if not boxes:
        return None
    candidates = list(boxes) if isinstance(boxes, (list, tuple)) else [boxes]
    centers = [center for box in candidates if (center := _ocr_box_center(box))]
    return max(centers, key=lambda point: point[1]) if centers else None


class VirtualHidRecoveryMixin:
    """Route every recovery click, including relative clicks, through HID.

    OK-WW uses normalized coordinates for the F2 ``探测`` button and several
    waypoint-healing controls.  Passing those calls to the normal interaction
    backend can silently lose them on this machine.  ``click_relative`` keeps
    OK-WW's own aspect-ratio conversion, then calls this method again with
    frame pixels, which are converted to desktop coordinates for HID input.
    """

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
        ):
            if key == "left" and abs(x - 0.89) < 0.001 and abs(y - 0.92) < 0.001:
                detected = _find_detect_action_center(self)
                if detected is not None:
                    self.log_info(  # type: ignore[attr-defined]
                        f"{DETECT_ACTION_OCR_MARKER} {detected[0]},{detected[1]}"
                    )
                    return self.click(
                        detected[0],
                        detected[1],
                        move_back=move_back,
                        name="detect_action_ocr",
                        interval=interval,
                        move=move,
                        down_time=down_time,
                        after_sleep=after_sleep,
                        key=key,
                    )
            if 0 < x < 1 or 0 < y < 1:
                return self.click_relative(  # type: ignore[attr-defined]
                    x,
                    y,
                    move_back=move_back,
                    hcenter=bool(kwargs.get("hcenter", False)),
                    vcenter=bool(kwargs.get("vcenter", False)),
                    move=move,
                    after_sleep=after_sleep,
                    name=name,
                    interval=interval,
                    down_time=down_time,
                    key=key,
                )
            if not self.check_interval(interval):  # type: ignore[attr-defined]
                self.executor.reset_scene()  # type: ignore[attr-defined]
                return False
            target_x = self.width // 2 if x == -1 else int(x)  # type: ignore[attr-defined]
            target_y = self.height // 2 if y == -1 else int(y)  # type: ignore[attr-defined]
            absolute_x, absolute_y = (
                self.executor.interaction.capture.get_abs_cords(  # type: ignore[attr-defined]
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
                self.log_info(  # type: ignore[attr-defined]
                    f"{RECOVERY_HID_CLICK_MARKER} "
                    f"{absolute_x},{absolute_y} {name}"
                )
            if after_sleep > 0:
                self.sleep(after_sleep)  # type: ignore[attr-defined]
            self.executor.reset_scene()  # type: ignore[attr-defined]
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
        return _heal_after_realm_defeat(task)

    if not task.wait_feature(
        "revive_confirm_hcenter_vcenter", threshold=0.8, time_out=5
    ):
        raise RuntimeError("neither revive dialog nor realm defeat is visible")
    # DomainTask.revive_action is the upstream, proven sequence:
    # close the dialog, leave the realm, wait for the team/world state, then
    # teleport to the healing waypoint.  Clicking the dialog and continuing
    # in-place leaves the party damaged and caused the next challenge to fail.
    revive_action = getattr(task, "revive_action", None)
    if not callable(revive_action) or not revive_action():
        raise RuntimeError("upstream revive_action did not complete waypoint healing")
    task.log_info(REVIVE_DIALOG_HEAL_RECOVERY_COMPLETED_MARKER)
    return REVIVE_DIALOG_HEAL_RECOVERY_COMPLETED_MARKER


def _heal_after_realm_defeat(task: object) -> str:
    """Exit a failed realm, heal at a waypoint, and re-enter from F2 later."""

    click_realm_defeat_exit(task)
    if not task.wait_in_team_and_world(
        time_out=120,
        raise_if_not_found=False,
    ):
        raise RuntimeError("realm defeat exit did not return to the team/world state")
    heal = getattr(task, "revive_at_tower_and_heal", None)
    if not callable(heal):
        raise TypeError("OK-WW DomainTask has no waypoint healing method")
    heal()
    if not task.wait_in_team_and_world(
        time_out=120,
        raise_if_not_found=False,
    ):
        raise RuntimeError("waypoint healing did not return to the team/world state")
    task.log_info(REALM_DEFEAT_HEAL_RECOVERY_COMPLETED_MARKER)
    return REALM_DEFEAT_HEAL_RECOVERY_COMPLETED_MARKER


def _require_recovery_completion(marker: str | None) -> str:
    """Reject OK-WW task failures that its executor catches internally."""
    if not marker:
        raise RuntimeError("recovery task returned without a host completion marker")
    return marker


def _recovery_result_payload(
    completed_marker: str,
    *,
    started_at: str,
    finished_at: str,
) -> dict[str, object]:
    """Serialize a successful recovery with an explicit re-entry contract.

    Every recovery path ends outside the failed realm.  The next worker then
    follows the same fresh OK-WW entry path used on successful historical days.
    """

    return {
        "success": True,
        "reason": completed_marker,
        "resume_active_realm": False,
        "started_at": started_at,
        "finished_at": finished_at,
    }


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
                recovery_completion = _heal_after_realm_defeat(self)

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
            **_recovery_result_payload(
                completed_marker,
                started_at=started.isoformat(),
                finished_at=datetime.now().astimezone().isoformat(),
            ),
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
