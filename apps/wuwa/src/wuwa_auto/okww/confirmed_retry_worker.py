"""Run upstream FarmEcho and count only actual echo absorption.

The successful 2026-08-04/08 implementation deliberately leaves navigation
and combat to OK-WW.  The only host hook in the normal task is the absorption
counter; confirmed party-death screens are surfaced so the parent can run its
separate, post-combat recovery worker.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import MethodType

try:
    from .farm_echo_state import (
        REALM_DEFEAT_MARKER,
        REVIVE_DIALOG_MARKER,
        realm_defeat_visible,
        revive_dialog_visible,
    )
except ImportError:  # executed directly by OK-WW's bundled Python
    from farm_echo_state import (
        REALM_DEFEAT_MARKER,
        REVIVE_DIALOG_MARKER,
        realm_defeat_visible,
        revive_dialog_visible,
    )


CONFIRMED_MARKER = "HOST_FARM_ECHO_ABSORPTION_CONFIRMED"
COMPLETED_MARKER = "HOST_FARM_ECHO_ABSORPTION_TARGET_COMPLETED"
ENTRY_HID_MARKER = "HOST_FARM_ECHO_ENTRY_VIRTUAL_HID_CLICK"
ENTRY_PAGE_CONFIRMED_MARKER = "HOST_FARM_ECHO_BOSS_PAGE_CONFIRMED"
ENTRY_PAGE_RESELECTED_MARKER = "HOST_FARM_ECHO_BOSS_PAGE_RESELECTED"
UPSTREAM_INTERACTION_MARKER = "HOST_FARM_ECHO_UPSTREAM_INTERACTION"
_UPSTREAM_INTERACTION = "PyDirect"

_ENTRY_CLICK_NAMES = frozenset(
    {
        "gray_book_boss",
        "gray_book_boss_highlight",
        "qiangdi",
        "boss_proceed",
        "team_start_challenge",
    }
)


@contextmanager
def _scoped_entry_navigation_hid(task: object) -> Iterator[None]:
    """Use HID only while OK-WW navigates F2 into the configured realm.

    The successful 2026-08-08 run proved that the game can ignore OK-WW's
    PostMessage clicks on this page.  Installing adapters on the instance (and
    removing them in ``finally``) keeps every combat/input method on the normal
    FarmEcho class exactly upstream once entry finishes.
    """
    try:
        from .virtual_hid import virtual_hid_click
    except ImportError:  # executed directly by OK-WW's bundled Python
        from virtual_hid import virtual_hid_click

    original_click = task.click  # type: ignore[attr-defined]
    original_open_boss_book = task.open_boss_book  # type: ignore[attr-defined]
    had_click = "click" in vars(task)
    previous_click = vars(task).get("click")
    had_open = "open_boss_book" in vars(task)
    previous_open = vars(task).get("open_boss_book")

    def entry_click(
        self: object,
        x: object = -1,
        y: object = -1,
        move_back: bool = False,
        name: object = None,
        interval: float = -1,
        move: bool = True,
        down_time: float = 0.02,
        after_sleep: float = 0,
        key: str = "left",
    ) -> object:
        if (
            key == "left"
            and name in _ENTRY_CLICK_NAMES
            and isinstance(x, (int, float))
            and isinstance(y, (int, float))
            and not (0 < x < 1 or 0 < y < 1)
        ):
            if not self.check_interval(interval):  # type: ignore[attr-defined]
                self.executor.reset_scene()  # type: ignore[attr-defined]
                return False
            target_x = self.width // 2 if x == -1 else int(x)  # type: ignore[attr-defined]
            target_y = self.height // 2 if y == -1 else int(y)  # type: ignore[attr-defined]
            absolute_x, absolute_y = (
                self.executor.interaction.capture.get_abs_cords(  # type: ignore[attr-defined]
                    target_x,
                    target_y,
                )
            )
            virtual_hid_click(
                absolute_x,
                absolute_y,
                hold=max(0.08, float(down_time)),
                log_action=True,
            )
            self.log_info(  # type: ignore[attr-defined]
                f"{ENTRY_HID_MARKER} {absolute_x},{absolute_y} {name}"
            )
            if after_sleep > 0:
                self.sleep(after_sleep)  # type: ignore[attr-defined]
            self.executor.reset_scene()  # type: ignore[attr-defined]
            return True
        return original_click(
            x,
            y,
            move_back=move_back,
            name=name,
            interval=interval,
            move=move,
            down_time=down_time,
            after_sleep=after_sleep,
            key=key,
        )

    def entry_open_boss_book(
        self: object,
        name: str,
        after_sleep: float = 2,
    ) -> None:
        original_open_boss_book(name, after_sleep=after_sleep)
        if name != "qiangdi":
            return

        def challenge_page_visible() -> bool:
            return bool(
                self.wait_ocr(  # type: ignore[attr-defined]
                    match="讨伐强敌",
                    time_out=3,
                    settle_time=0.5,
                    raise_if_not_found=False,
                )
            )

        if challenge_page_visible():
            self.log_info(ENTRY_PAGE_CONFIRMED_MARKER)  # type: ignore[attr-defined]
            return

        for _ in range(2):
            category = self.find_one(  # type: ignore[attr-defined]
                ["gray_book_boss", "gray_book_boss_highlight"],
                box="box_gray_book",
                threshold=0.3,
            )
            if category:
                self.click_box(category, after_sleep=1.5)  # type: ignore[attr-defined]
            self.log_info(ENTRY_PAGE_RESELECTED_MARKER)  # type: ignore[attr-defined]
            original_open_boss_book(name, after_sleep=after_sleep)
            if challenge_page_visible():
                self.log_info(ENTRY_PAGE_CONFIRMED_MARKER)  # type: ignore[attr-defined]
                return
        raise RuntimeError("Host failed to verify Boss Challenge guidebook page")

    task.click = MethodType(entry_click, task)  # type: ignore[attr-defined]
    task.open_boss_book = MethodType(  # type: ignore[attr-defined]
        entry_open_boss_book,
        task,
    )
    try:
        yield
    finally:
        if had_click:
            task.click = previous_click  # type: ignore[attr-defined]
        else:
            delattr(task, "click")
        if had_open:
            task.open_boss_book = previous_open  # type: ignore[attr-defined]
        else:
            delattr(task, "open_boss_book")


def _write_result(path: Path, **values: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(values, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    # A currently running recovery parent may still append its old one-shot
    # ``resume`` argument.  Accept but intentionally ignore it: the historical
    # worker's existing realm probe handles that state without combat hooks.
    if len(arguments) not in {3, 4}:
        raise SystemExit(
            "usage: confirmed_retry_worker.py OK_WORKING_DIR RESULT_PATH TARGET"
        )
    working_dir = Path(arguments[0]).resolve()
    result_path = Path(arguments[1]).resolve()
    target = int(arguments[2])
    if target < 1:
        raise SystemExit("TARGET must be positive")

    started = datetime.now().astimezone()
    absorbed = 0
    try:
        os.chdir(working_dir)
        sys.path.insert(0, str(working_dir))

        from ok import run_task
        from src.task.FarmEchoTask import FarmEchoTask as UpstreamFarmEchoTask
        from src.task.WWOneTimeTask import WWOneTimeTask

        from config import config

        # Keep every combat decision and action in OK-WW, but use one of its
        # own foreground input backend.  Both plain and foreground PostMessage
        # failed to establish a target on this desktop, while Pynput repeatedly
        # lost the target and let the boss reset.  The missing dependency for
        # OK-WW's bundled PyDirect transport is verified during deployment.
        # This worker selects only that transport; it does not replace combat.
        config["windows"]["interaction"] = _UPSTREAM_INTERACTION

        class TargetReached(Exception):
            pass

        # Keep this class name identical to upstream so OK-WW loads the user's
        # persisted FarmEchoTask configuration.
        class FarmEchoTask(UpstreamFarmEchoTask):
            def __init__(self, *args: object, **kwargs: object) -> None:
                super().__init__(*args, **kwargs)
                self.host_absorbed = 0

            def manage_boss_interactions(self) -> None:
                # This hook is dormant throughout combat.  It only promotes a
                # confirmed death screen to the parent recovery workflow.
                if self.in_combat():
                    return
                if revive_dialog_visible(self):
                    self.log_info(REVIVE_DIALOG_MARKER)
                    raise RuntimeError("FarmEcho character revival is required")
                if self._in_realm and realm_defeat_visible(self):
                    self.log_info(REALM_DEFEAT_MARKER)
                    raise RuntimeError("FarmEcho realm challenge failed")
                super().manage_boss_interactions()

            def teleport_to_configured_boss_and_prepare(self) -> None:
                """Reuse a completed realm left behind by an earlier run."""
                self.ensure_main(time_out=180)
                self.sleep(4)
                self.send_key("esc", after_sleep=0.5)
                reusable_realm = self.wait_feature(
                    "claim_cancel_button_hcenter_vcenter",
                    time_out=5,
                    raise_if_not_found=False,
                )
                if reusable_realm:
                    self._teleport_walk_result = "realm"
                    self._in_realm = True
                    self.treat_as_not_in_realm = False
                    self._has_treasure = True
                    self._just_entered_boss_realm = False
                    self.init_parameters()
                    self.log_info("HOST_FARM_ECHO_REUSE_COMPLETED_REALM")
                    self.wait_click_feature(
                        "claim_cancel_button_hcenter_vcenter",
                        relative_x=2,
                        raise_if_not_found=True,
                        post_action=lambda: self.send_key("esc", after_sleep=1),
                        settle_time=1,
                    )
                    self.wait_in_team_and_world(time_out=120)
                    self._has_treasure = False
                    self._just_entered_boss_realm = True
                    self.log_info("HOST_FARM_ECHO_REUSED_REALM_RESTARTED")
                    return
                self.send_key("esc", after_sleep=0.5)
                self.ensure_main(time_out=30)
                with _scoped_entry_navigation_hid(self):
                    super().teleport_to_configured_boss_and_prepare()

            def host_record_absorption(self) -> None:
                nonlocal absorbed
                self.host_absorbed += 1
                absorbed = self.host_absorbed
                self.log_info(f"{CONFIRMED_MARKER} {self.host_absorbed}/{target}")
                if self.host_absorbed >= target:
                    raise TargetReached

            def incr_drop(self, dropped: object) -> object:
                result = super().incr_drop(dropped)
                if dropped and self._in_realm:
                    self.host_record_absorption()
                return result

            def run(self) -> None:
                WWOneTimeTask.run(self)
                self.log_info(
                    f"{UPSTREAM_INTERACTION_MARKER} {_UPSTREAM_INTERACTION}"
                )
                self.use_liberation = self.config.get("Use Liberation")
                try:
                    self.do_run()
                except TargetReached:
                    self.log_info(COMPLETED_MARKER)
                    return
                if self.host_absorbed < target:
                    raise RuntimeError(
                        "confirmed retry exhausted its bounded combat attempts: "
                        f"absorbed={self.host_absorbed}/{target}"
                    )

        run_task(config, task=FarmEchoTask, debug=False, exit_after=False)
        if absorbed < target:
            raise RuntimeError(
                f"confirmed retry returned early: absorbed={absorbed}/{target}"
            )
        _write_result(
            result_path,
            success=True,
            reason=COMPLETED_MARKER,
            absorbed_count=absorbed,
            target_count=target,
            started_at=started.isoformat(),
            finished_at=datetime.now().astimezone().isoformat(),
        )
        return 0
    except BaseException as exc:  # noqa: BLE001 - serialize every worker failure
        _write_result(
            result_path,
            success=False,
            reason=str(exc),
            absorbed_count=absorbed,
            target_count=target,
            traceback=traceback.format_exc(),
            started_at=started.isoformat(),
            finished_at=datetime.now().astimezone().isoformat(),
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
