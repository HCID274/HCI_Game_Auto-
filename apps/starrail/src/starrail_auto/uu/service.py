"""UU startup, reuse, retry, and shutdown orchestration."""

import logging
import time
from pathlib import Path

from starrail_auto.uu.config import (
    CONFIRM_TIMEOUT,
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
    click,
    dismiss_known_popups,
    focus_uu_window,
    keep_uu_in_background_on_exit,
    minimize_uu_window,
    move_mouse_to,
    require_admin,
    require_supported_display,
    startup_error,
    try_locate_image,
    wait_for_image,
)
from starrail_auto.uu.errors import UuStartupError, UuStartupFinalError
from starrail_auto.uu.processes import is_uu_running, kill_uu, start_uu

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


def _run_startup_attempt(attempt_no: int) -> None:
    log.info("UU startup attempt %d started", attempt_no)
    require_supported_display()
    was_running = is_uu_running()
    _ensure_uu_started()

    try:
        title = focus_uu_window()
    except RuntimeError as exc:
        raise startup_error("focus_uu_window", str(exc)) from exc
    log.info("UU window focused: %s", title)
    dismiss_known_popups("startup focus")

    if was_running:
        dismiss_known_popups("reuse acceleration check")
        target = try_locate_image(TPL_STEP_3, timeout=REUSE_CONFIRM_TIMEOUT)
        if target is not None:
            log.info("existing acceleration confirmed at %s", target)
            return
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
    max_attempts = UU_STARTUP_MAX_RESTARTS + 1
    last_error: UuStartupError | None = None
    restarts_used = 0

    with keep_uu_in_background_on_exit("startup chain exit"):
        for attempt in range(1, max_attempts + 1):
            log.info("UU startup supervisor attempt %d/%d", attempt, max_attempts)
            try:
                _run_startup_attempt(attempt)
                return restarts_used
            except UuStartupError as exc:
                last_error = exc
                log.warning(
                    "UU attempt %d failed: retryable=%s step=%s reason=%s screenshot=%s",
                    attempt,
                    exc.retryable,
                    exc.step_name,
                    exc.reason,
                    exc.screenshot_path,
                )
                if not exc.retryable:
                    exc.restarts_used = restarts_used
                    raise
                if attempt >= max_attempts:
                    break
                kill_uu()
                restarts_used += 1
                time.sleep(UU_RESTART_DELAY)

    if last_error is None:
        raise RuntimeError("UU startup failed without a captured error")
    raise UuStartupFinalError(last_error, restarts_used)


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
