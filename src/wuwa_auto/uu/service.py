"""Screenshot-driven UU connection state machine for Wuthering Waves."""

import logging
import time
from pathlib import Path

from wuwa_auto.uu.config import (
    CARD_TIMEOUT,
    CONFIRM_TIMEOUT,
    MAX_RESTARTS,
    POST_CLICK_DELAY,
    POST_HOVER_DELAY,
    RESTART_DELAY,
    REUSE_TIMEOUT,
    START_BUTTON_TIMEOUT,
    STARTUP_WAIT,
    TPL_GAME_ACTIVE,
    TPL_GAME_CARD,
    TPL_START_ACCELERATION,
    TPL_STOP_ACCELERATION,
)
from wuwa_auto.uu.desktop import (
    click_after_evidence,
    dismiss_known_popups,
    focus_uu_window,
    hover_after_evidence,
    minimize_on_exit,
    minimize_uu_window,
    park_cursor_for_detection,
    require_admin,
    require_supported_display,
    save_step_screenshot,
    startup_error,
    try_locate_image,
    wait_for_image,
)
from wuwa_auto.uu.errors import UuStartupError, UuStartupFinalError
from wuwa_auto.uu.processes import is_uu_running, start_uu, terminate_uu

log = logging.getLogger(__name__)


def _ensure_process() -> bool:
    already_running = is_uu_running()
    if already_running:
        log.info("UU process already exists")
        return True
    log.info("launching UU")
    start_uu()
    time.sleep(STARTUP_WAIT)
    if not is_uu_running():
        raise startup_error("start_uu", "UU process did not appear")
    return False


def inspect_uu() -> Path:
    """Launch and capture UU without clicking any state-changing control."""
    require_admin()
    require_supported_display()
    _ensure_process()
    try:
        title = focus_uu_window()
    except RuntimeError as exc:
        raise startup_error("focus_uu", str(exc)) from exc
    log.info("UU focused for inspection: %s", title)
    dismiss_known_popups("inspection")
    return save_step_screenshot("uu_inspect")


def _confirm_wuthering_active(timeout: float) -> bool:
    if not TPL_GAME_ACTIVE.is_file() or not TPL_STOP_ACCELERATION.is_file():
        return False
    active = try_locate_image(TPL_GAME_ACTIVE, timeout=timeout)
    stop = try_locate_image(TPL_STOP_ACCELERATION, timeout=timeout)
    return active is not None and stop is not None


def _run_attempt(attempt: int) -> None:
    log.info("UU attempt %d", attempt)
    require_supported_display()
    _ensure_process()
    try:
        title = focus_uu_window()
    except RuntimeError as exc:
        raise startup_error("focus_uu", str(exc)) from exc
    log.info("UU focused: %s", title)
    dismiss_known_popups("focus")
    save_step_screenshot("uu_focused")

    if _confirm_wuthering_active(REUSE_TIMEOUT):
        log.info("existing Wuthering Waves acceleration confirmed")
        return

    # A restart preserves the system cursor position.  If it is already over
    # the game card, UU starts in the hovered state and the normal card template
    # is intentionally absent.  Accept that state directly; otherwise park the
    # cursor so the unhovered card can be matched deterministically.
    start = try_locate_image(TPL_START_ACCELERATION, timeout=REUSE_TIMEOUT)
    if start is None:
        park_cursor_for_detection()
        time.sleep(POST_HOVER_DELAY)
        card = wait_for_image(
            TPL_GAME_CARD,
            step_name="locate_wuthering_card",
            timeout=CARD_TIMEOUT,
        )
        hover_after_evidence(card, "wuthering_card")
        time.sleep(POST_HOVER_DELAY)
        start = wait_for_image(
            TPL_START_ACCELERATION,
            step_name="locate_start_acceleration",
            timeout=START_BUTTON_TIMEOUT,
        )
    else:
        log.info("UU Wuthering card is already hovered")
    click_after_evidence(start, "start_acceleration")
    time.sleep(POST_CLICK_DELAY)
    dismiss_known_popups("post acceleration")

    if not _confirm_wuthering_active(CONFIRM_TIMEOUT):
        raise startup_error(
            "confirm_wuthering_acceleration",
            "Wuthering identity and stop button were not both detected",
        )
    save_step_screenshot("uu_wuthering_acceleration_confirmed")
    log.info("Wuthering Waves acceleration confirmed")


def ensure_connected() -> int:
    require_admin()
    last_error: UuStartupError | None = None
    restarts = 0
    with minimize_on_exit("ensure connected"):
        for attempt in range(1, MAX_RESTARTS + 2):
            try:
                _run_attempt(attempt)
                return restarts
            except UuStartupError as exc:
                last_error = exc
                log.warning("UU attempt failed: %s", exc)
                if not exc.retryable:
                    exc.restarts_used = restarts
                    raise
                if attempt > MAX_RESTARTS:
                    break
                terminate_uu()
                restarts += 1
                time.sleep(RESTART_DELAY)
    if last_error is None:
        raise RuntimeError("UU failed without a captured error")
    raise UuStartupFinalError(last_error, restarts)


def disconnect() -> None:
    require_admin()
    if not is_uu_running():
        log.info("UU is not running")
        return
    with minimize_on_exit("disconnect"):
        focus_uu_window()
        if not _confirm_wuthering_active(REUSE_TIMEOUT):
            raise startup_error(
                "disconnect_preflight",
                "current acceleration is not verified as Wuthering Waves",
                retryable=False,
            )
        target = wait_for_image(
            TPL_STOP_ACCELERATION,
            step_name="locate_stop_acceleration",
            timeout=5,
        )
        click_after_evidence(target, "stop_acceleration")
        deadline = time.monotonic() + CONFIRM_TIMEOUT
        while time.monotonic() < deadline:
            if not _confirm_wuthering_active(1):
                save_step_screenshot("uu_wuthering_disconnected")
                log.info("Wuthering Waves acceleration disconnected")
                return
            time.sleep(1)
        raise startup_error(
            "confirm_wuthering_disconnected",
            "Wuthering acceleration still appeared active after stop click",
            retryable=False,
        )


def execute_action(action: str) -> int:
    try:
        if action == "inspect":
            path = inspect_uu()
            log.info("inspection screenshot: %s", path)
        elif action == "start":
            ensure_connected()
        elif action == "disconnect":
            disconnect()
        elif action == "minimize":
            minimize_uu_window()
        elif action == "stop":
            require_admin()
            terminate_uu()
        else:
            raise ValueError(f"unknown UU action: {action}")
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1
    return 0
