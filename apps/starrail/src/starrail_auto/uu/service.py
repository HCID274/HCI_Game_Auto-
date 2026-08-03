"""UU startup, reuse, retry, and shutdown orchestration."""

import logging
import time
from pathlib import Path

from game_automation_core.uu.supervisor import run_with_restart_budget
from game_automation_core.uu.update import recover_mandatory_update

from starrail_auto.uu.config import (
    CONFIRM_TIMEOUT,
    MANDATORY_UPDATE_POLL_INTERVAL,
    MANDATORY_UPDATE_RELAUNCH_GRACE,
    MANDATORY_UPDATE_TIMEOUT,
    POST_CLICK_WAIT,
    POST_MOVE_DELAY,
    REUSE_CONFIRM_TIMEOUT,
    STEP_1_INITIAL_DELAY,
    STEP_1_TIMEOUT,
    STEP_2_TIMEOUT,
    STOP_ACCELERATION_TIMEOUT,
    TPL_STEP_1,
    TPL_STEP_2,
    TPL_STEP_3,
    UU_RESTART_DELAY,
    UU_STARTUP_MAX_RESTARTS,
)
from starrail_auto.uu.desktop import (
    accept_mandatory_update,
    click,
    dismiss_known_popups,
    focus_uu_window,
    keep_uu_in_background_on_exit,
    mandatory_update_visible,
    minimize_uu_window,
    move_mouse_to,
    require_admin,
    require_supported_display,
    startup_error,
    try_locate_image,
    wait_for_image,
)
from starrail_auto.uu.errors import UuStartupError, UuStartupFinalError
from starrail_auto.uu.processes import (
    is_uu_running,
    kill_uu,
    start_uu,
    uu_primary_pids,
)
from starrail_auto.windows.desktop_guard import (
    DesktopBlockedError,
    describe_window,
    require_desktop_ready,
)

log = logging.getLogger(__name__)


def _ensure_uu_started() -> None:
    if is_uu_running():
        log.info("UU accelerator already running")
        return
    log.info("starting UU accelerator")
    start_uu()
    time.sleep(5)
    if not is_uu_running():
        raise startup_error("ensure_uu_started", "UU process was not detected after launch")


def _confirm_starrail_active(timeout: float) -> bool:
    """Require both Star Rail identity and the generic active-state button."""
    identity = try_locate_image(TPL_STEP_1, timeout=timeout)
    stop = try_locate_image(TPL_STEP_3, timeout=timeout)
    return identity is not None and stop is not None


def _recover_mandatory_update(context: str) -> bool:
    try:
        return recover_mandatory_update(
            accept_update=lambda: accept_mandatory_update(context),
            update_visible=lambda timeout: mandatory_update_visible(timeout),
            primary_pids=uu_primary_pids,
            start_process=start_uu,
            focus_window=focus_uu_window,
            timeout=MANDATORY_UPDATE_TIMEOUT,
            relaunch_grace=MANDATORY_UPDATE_RELAUNCH_GRACE,
            poll_interval=MANDATORY_UPDATE_POLL_INTERVAL,
        )
    except TimeoutError as exc:
        raise startup_error(
            "mandatory_update_recovery",
            str(exc),
            retryable=False,
        ) from exc


def _run_startup_attempt(attempt_no: int, *, update_used: bool = False) -> None:
    log.info("UU startup attempt %d started", attempt_no)
    try:
        foreground = require_desktop_ready()
    except DesktopBlockedError as exc:
        raise startup_error(
            "desktop_guard",
            str(exc),
            retryable=False,
        ) from exc
    log.info("desktop preflight passed: %s", describe_window(foreground))
    require_supported_display()
    was_running = is_uu_running()
    _ensure_uu_started()

    try:
        title = focus_uu_window()
    except RuntimeError as exc:
        raise startup_error("focus_uu_window", str(exc)) from exc
    log.info("UU window focused: %s", title)
    if _recover_mandatory_update("startup focus"):
        if update_used:
            raise startup_error(
                "mandatory_update_loop",
                "mandatory update reappeared after one completed update",
                retryable=False,
            )
        return _run_startup_attempt(attempt_no, update_used=True)
    dismiss_known_popups("startup focus")

    if was_running:
        dismiss_known_popups("reuse acceleration check")
        if _confirm_starrail_active(REUSE_CONFIRM_TIMEOUT):
            log.info("existing Star Rail acceleration confirmed")
            return
        target = try_locate_image(TPL_STEP_3, timeout=REUSE_CONFIRM_TIMEOUT)
        if target is not None:
            log.info(
                "existing acceleration has no Star Rail identity; disconnecting before retry"
            )
            click(target)
            time.sleep(POST_MOVE_DELAY)
            raise startup_error(
                "reuse_acceleration_identity",
                "active acceleration was not verified as Star Rail",
            )
        log.info("existing UU session is not accelerated; running full chain")

    first = wait_for_image(
        TPL_STEP_1,
        step_name="locate_startup_move_target",
        initial_delay=STEP_1_INITIAL_DELAY,
        timeout=STEP_1_TIMEOUT,
    )
    move_mouse_to(first)
    time.sleep(POST_MOVE_DELAY)
    dismiss_known_popups("before second startup step")

    second = wait_for_image(
        TPL_STEP_2,
        step_name="locate_startup_click_target",
        timeout=STEP_2_TIMEOUT,
    )
    click(second)
    time.sleep(POST_CLICK_WAIT)
    if _recover_mandatory_update("before acceleration confirmation"):
        if update_used:
            raise startup_error(
                "mandatory_update_loop",
                "mandatory update reappeared after one completed update",
                retryable=False,
            )
        return _run_startup_attempt(attempt_no, update_used=True)
    dismiss_known_popups("before acceleration confirmation")

    confirmed = wait_for_image(
        TPL_STEP_3,
        step_name="confirm_acceleration",
        timeout=CONFIRM_TIMEOUT,
    )
    log.info("acceleration confirmed at %s", confirmed)


def ensure_uu_connected() -> int:
    """Ensure acceleration, allowing at most three process restarts."""
    require_admin()
    with keep_uu_in_background_on_exit("startup chain exit"):
        return run_with_restart_budget(
            attempt=_run_startup_attempt,
            restart=kill_uu,
            max_restarts=UU_STARTUP_MAX_RESTARTS,
            restart_delay=UU_RESTART_DELAY,
            sleep=time.sleep,
            logger=log,
        )


def stop_uu_acceleration() -> None:
    require_admin()
    if not is_uu_running():
        log.info("UU accelerator is not running; acceleration is already stopped")
        return
    with keep_uu_in_background_on_exit("stop acceleration exit"):
        log.info("UU window focused: %s", focus_uu_window())
        dismiss_known_popups("stop acceleration focus")
        target = wait_for_image(
            TPL_STEP_3,
            step_name="locate_stop_acceleration_button",
            timeout=STOP_ACCELERATION_TIMEOUT,
        )
        click(target)
        log.info("UU acceleration stop button clicked")


def execute_action(action: str = "start", log_file: Path | None = None) -> int:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        handlers=handlers,
    )
    try:
        if action == "disconnect":
            stop_uu_acceleration()
        elif action == "minimize":
            minimize_uu_window()
        elif action == "stop":
            kill_uu()
        else:
            ensure_uu_connected()
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1
    return 0


__all__ = [
    "UuStartupError",
    "UuStartupFinalError",
    "ensure_uu_connected",
    "execute_action",
    "kill_uu",
    "minimize_uu_window",
    "stop_uu_acceleration",
]
