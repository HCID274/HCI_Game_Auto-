"""Run a bounded FarmEcho retry and count causal post-combat evidence."""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path


CONFIRMED_MARKER = "HOST_FARM_ECHO_KILL_CONFIRMED"
COMPLETED_MARKER = "HOST_FARM_ECHO_CONFIRMED_RETRY_COMPLETED"


def _write_result(path: Path, **values: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(values, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 3:
        raise SystemExit(
            "usage: confirmed_retry_worker.py OK_WORKING_DIR RESULT_PATH TARGET"
        )
    working_dir = Path(arguments[0]).resolve()
    result_path = Path(arguments[1]).resolve()
    target = int(arguments[2])
    if target < 1:
        raise SystemExit("TARGET must be positive")

    started = datetime.now().astimezone()
    confirmed = 0
    try:
        os.chdir(working_dir)
        sys.path.insert(0, str(working_dir))

        from config import config
        from ok import run_task
        from src.task.FarmEchoTask import FarmEchoTask as UpstreamFarmEchoTask
        from src.task.WWOneTimeTask import WWOneTimeTask

        class TargetReached(Exception):
            pass

        # Keep this class name identical to upstream so existing death markers
        # remain stable in the shared OK log.
        class FarmEchoTask(UpstreamFarmEchoTask):
            def __init__(self, *args: object, **kwargs: object) -> None:
                super().__init__(*args, **kwargs)
                self.host_confirmed = 0
                self.host_echo_confirmed_since_restart = False
                self.host_ignore_next_restart_confirmation = False

            def teleport_to_configured_boss_and_prepare(self) -> None:
                """Reuse a completed realm left behind by an earlier run."""
                self.ensure_main(time_out=180)
                # The first Esc immediately after login can be swallowed while
                # the restored scene is still becoming interactive.
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
                    # The next restart UI belongs to the preceding run. Use it
                    # to start a fresh fight, but never count that stale kill.
                    self.host_ignore_next_restart_confirmation = True
                    self.init_parameters()
                    self.log_info("HOST_FARM_ECHO_REUSE_COMPLETED_REALM")
                    self.wait_click_feature(
                        "claim_cancel_button_hcenter_vcenter",
                        relative_x=2,
                        raise_if_not_found=True,
                        post_action=lambda: self.send_key(
                            "esc",
                            after_sleep=1,
                        ),
                        settle_time=1,
                    )
                    self.wait_in_team_and_world(time_out=120)
                    self._has_treasure = False
                    self._just_entered_boss_realm = True
                    self.log_info("HOST_FARM_ECHO_REUSED_REALM_RESTARTED")
                    return
                # A normal world opens only the Esc menu. Close that harmless
                # probe before falling back to the configured F2 teleport.
                self.send_key("esc", after_sleep=0.5)
                self.ensure_main(time_out=30)
                super().teleport_to_configured_boss_and_prepare()

            def host_record_confirmation(self, source: str) -> None:
                nonlocal confirmed
                self.host_confirmed += 1
                confirmed = self.host_confirmed
                self.log_info(
                    f"{CONFIRMED_MARKER} {self.host_confirmed}/{target} "
                    f"source={source}"
                )
                if self.host_confirmed >= target:
                    raise TargetReached

            def incr_drop(self, dropped: object) -> object:
                result = super().incr_drop(dropped)
                if dropped and self._in_realm:
                    self.host_echo_confirmed_since_restart = True
                    self.host_record_confirmation("echo_absorbed")
                return result

            def wait_click_feature(
                self,
                feature: object,
                *args: object,
                **kwargs: object,
            ) -> object:
                nonlocal confirmed
                relative_x = kwargs.get("relative_x", 0.5)
                is_restart = (
                    feature == "claim_cancel_button_hcenter_vcenter"
                    and relative_x == 2
                )
                if not is_restart:
                    return super().wait_click_feature(feature, *args, **kwargs)

                if self.host_ignore_next_restart_confirmation:
                    self.host_ignore_next_restart_confirmation = False
                    return super().wait_click_feature(feature, *args, **kwargs)

                if self.host_echo_confirmed_since_restart:
                    # The preceding kill was already proven by an absorbed
                    # echo. Restart normally without counting it twice.
                    self.host_echo_confirmed_since_restart = False
                    return super().wait_click_feature(feature, *args, **kwargs)

                next_confirmed = self.host_confirmed + 1
                if next_confirmed >= target:
                    # Click the visible cancel/leave button itself instead of
                    # the adjacent restart button. This proves the kill while
                    # avoiding an unnecessary extra battle.
                    final_kwargs = dict(kwargs)
                    final_kwargs["relative_x"] = 0.5
                    clicked = super().wait_click_feature(
                        feature,
                        *args,
                        **final_kwargs,
                    )
                    if clicked:
                        self.wait_in_team_and_world(time_out=120)
                        self.host_record_confirmation("restart_screen")
                    return clicked

                clicked = super().wait_click_feature(feature, *args, **kwargs)
                if clicked:
                    self.host_record_confirmation("restart_screen")
                return clicked

            def run(self) -> None:
                WWOneTimeTask.run(self)
                self.use_liberation = self.config.get("Use Liberation")
                try:
                    self.do_run()
                except TargetReached:
                    self.log_info(COMPLETED_MARKER)
                    return
                if self.host_confirmed < target:
                    raise RuntimeError(
                        "confirmed retry exhausted its bounded combat attempts: "
                        f"confirmed={self.host_confirmed}/{target}"
                    )

        run_task(config, task=FarmEchoTask, debug=False, exit_after=False)
        if confirmed < target:
            raise RuntimeError(
                f"confirmed retry returned early: confirmed={confirmed}/{target}"
            )
        _write_result(
            result_path,
            success=True,
            reason=COMPLETED_MARKER,
            confirmed_count=confirmed,
            target_count=target,
            started_at=started.isoformat(),
            finished_at=datetime.now().astimezone().isoformat(),
        )
        return 0
    except BaseException as exc:
        _write_result(
            result_path,
            success=False,
            reason=str(exc),
            confirmed_count=confirmed,
            target_count=target,
            traceback=traceback.format_exc(),
            started_at=started.isoformat(),
            finished_at=datetime.now().astimezone().isoformat(),
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
