"""Run a bounded FarmEcho retry and count only actual echo absorption."""

from __future__ import annotations

import json
import os
import re
import socket
import sys
import traceback
from datetime import datetime
from pathlib import Path


CONFIRMED_MARKER = "HOST_FARM_ECHO_ABSORPTION_CONFIRMED"
COMPLETED_MARKER = "HOST_FARM_ECHO_ABSORPTION_TARGET_COMPLETED"
BOSS_PAGE_RESELECTED_MARKER = "HOST_FARM_ECHO_BOSS_PAGE_RESELECTED"
BOSS_PAGE_CONFIRMED_MARKER = "HOST_FARM_ECHO_BOSS_PAGE_CONFIRMED"
HID_CLICK_MARKER = "HOST_FARM_ECHO_VIRTUAL_HID_CLICK"


def _virtual_hid_click(
    x: int,
    y: int,
    *,
    hold: float = 0.2,
    log_action: bool = True,
) -> None:
    """Ask the parent workflow to emit a real local USB-HID click."""
    port = os.environ.get("WUWA_VIRTUAL_HID_CONTROL_PORT")
    token = os.environ.get("WUWA_VIRTUAL_HID_CONTROL_TOKEN")
    if not port or not token:
        raise RuntimeError("host virtual HID control is unavailable")

    def send_request(target_x: int, target_y: int) -> dict[str, object]:
        request = {
            "token": token,
            "action": "click",
            "x": target_x,
            "y": target_y,
            "hold": float(hold),
            "log_action": log_action,
        }
        with socket.create_connection(
            ("127.0.0.1", int(port)), timeout=8
        ) as client:
            client.settimeout(8)
            client.sendall(json.dumps(request).encode("utf-8") + b"\n")
            response = bytearray()
            while not response.endswith(b"\n"):
                chunk = client.recv(4096)
                if not chunk:
                    break
                response.extend(chunk)
        try:
            return json.loads(response.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"invalid virtual HID response: {response!r}") from exc

    payload = send_request(int(x), int(y))
    if not payload.get("ok"):
        # VIIPER 0.7 uses accelerated relative deltas. On a high-DPI desktop
        # it can stabilize three or four pixels from the requested center.
        # That is still safely inside an OK-recognized UI box; ask the parent
        # to click the measured cursor position instead of discarding it.
        error = str(payload.get("error") or "")
        cursor = re.search(r"cursor=\((-?\d+), (-?\d+)\)", error)
        if cursor:
            cursor_x, cursor_y = map(int, cursor.groups())
            if abs(cursor_x - x) <= 5 and abs(cursor_y - y) <= 5:
                payload = send_request(cursor_x, cursor_y)
    if not payload.get("ok"):
        raise RuntimeError(f"virtual HID click failed: {payload.get('error')}")


def _open_verified_boss_book(
    task: object,
    upstream_open: object,
    name: str,
    *,
    after_sleep: float,
) -> None:
    """Open an upstream book section and verify the current Chinese boss page."""
    upstream_open(name, after_sleep=after_sleep)  # type: ignore[operator]
    if name != "qiangdi":
        return

    def challenge_page_visible() -> bool:
        return bool(
            task.wait_ocr(  # type: ignore[attr-defined]
                match="讨伐强敌",
                time_out=3,
                settle_time=0.5,
                raise_if_not_found=False,
            )
        )

    if challenge_page_visible():
        task.log_info(BOSS_PAGE_CONFIRMED_MARKER)  # type: ignore[attr-defined]
        return

    # The F2 category click is occasionally swallowed after a client update or
    # scene restore.  In that case the generic ``boss_proceed`` template can
    # match an unrelated Daily Activity "Go" button.  Re-select the detected
    # boss category before retrying the existing upstream sub-page click.
    for _ in range(2):
        category = task.find_one(  # type: ignore[attr-defined]
            ["gray_book_boss", "gray_book_boss_highlight"],
            box="box_gray_book",
            threshold=0.3,
        )
        if category:
            task.click_box(category, after_sleep=1.5)  # type: ignore[attr-defined]
        task.log_info(BOSS_PAGE_RESELECTED_MARKER)  # type: ignore[attr-defined]
        upstream_open(name, after_sleep=after_sleep)  # type: ignore[operator]
        if challenge_page_visible():
            task.log_info(BOSS_PAGE_CONFIRMED_MARKER)  # type: ignore[attr-defined]
            return
    raise RuntimeError("Host failed to verify Boss Challenge guidebook page")


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
    absorbed = 0
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
                self.host_absorbed = 0

            def open_boss_book(self, name: str, after_sleep: float = 2) -> None:
                _open_verified_boss_book(
                    self,
                    super().open_boss_book,
                    name,
                    after_sleep=after_sleep,
                )

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
                # Game 3.5 can ignore OK's configured PostMessage mouse clicks
                # while its keyboard messages still work. Preserve upstream
                # recognition and flow, but emit ordinary left clicks through
                # the already-enumerated local virtual USB mouse.
                if (
                    key == "left"
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
                        hold=max(0.08, float(down_time)),
                        log_action=bool(name),
                    )
                    if name:
                        self.log_info(
                            f"{HID_CLICK_MARKER} {absolute_x},{absolute_y} {name}"
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
                    # This stale result is only a control-flow handoff. The
                    # absorption target changes solely in ``incr_drop``.
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

            def host_record_absorption(self) -> None:
                nonlocal absorbed
                self.host_absorbed += 1
                absorbed = self.host_absorbed
                self.log_info(
                    f"{CONFIRMED_MARKER} {self.host_absorbed}/{target}"
                )
                if self.host_absorbed >= target:
                    raise TargetReached

            def incr_drop(self, dropped: object) -> object:
                result = super().incr_drop(dropped)
                if dropped and self._in_realm:
                    self.host_record_absorption()
                return result

            def run(self) -> None:
                WWOneTimeTask.run(self)
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
    except BaseException as exc:
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
