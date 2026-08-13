"""Host wrapper for an absorption-confirmed, bounded FarmEcho retry."""

from __future__ import annotations

import json
import logging
import subprocess
import time
from datetime import datetime
from pathlib import Path

from wuwa_auto.client.launcher import (
    click_startup_network_retry,
    is_game_window_alive,
    startup_network_retry_visible,
)
from wuwa_auto.okww.logs import (
    LogCursor,
    count_farm_echo_absorptions,
    has_farm_echo_current_char_bind_failure,
)
from wuwa_auto.okww.recovery import focus_game_window_for_ok_startup
from wuwa_auto.okww.runner import (
    LOG_STALL_TIMEOUT,
    POLL_INTERVAL,
    STARTUP_LOG_TIMEOUT,
    OkRunResult,
    preflight_farm_echo_task,
    release_active_mouse_buttons,
    resume_active_mouse_control,
    write_result,
)
from wuwa_auto.settings import (
    OK_LOG_FILE,
    OK_PYTHON_EXE,
    OK_WORKING_DIR,
    RUNS_DIR,
)
from wuwa_auto.uu.desktop import require_admin, save_step_screenshot

log = logging.getLogger(__name__)

CONFIRMED_RETRY_WORKER = Path(__file__).with_name("confirmed_retry_worker.py")
MAX_FARM_ECHO_RUNTIME_SECONDS = 3600.0
ACTIVE_REALM_BIND_FAILURE_REASON = (
    "fresh upstream worker could not bind the current active character"
)
OK_STARTUP_WINDOW_STABLE_MARKER = "StartController:started window size stable"
UPSTREAM_INTERACTION_MARKER = "HOST_FARM_ECHO_UPSTREAM_INTERACTION"
GAMEPLAY_HANDOFF_MARKER = "HOST_FARM_ECHO_GAMEPLAY_HANDOFF"
MAX_STARTUP_NETWORK_RETRIES = 3
STARTUP_NETWORK_RETRY_COOLDOWN_SECONDS = 15.0


def _live_combat_degradation_reason(
    text: str,
    *,
    resume_active_realm: bool,
) -> str | None:
    if resume_active_realm and has_farm_echo_current_char_bind_failure(text):
        return ACTIVE_REALM_BIND_FAILURE_REASON
    return None


def _focus_ok_startup_window_if_needed(
    text: str,
    *,
    already_focused: bool,
) -> bool:
    """Focus once after OK owns and binds a cold-started game window."""
    if already_focused:
        return True
    if OK_STARTUP_WINDOW_STABLE_MARKER not in text:
        return False
    if UPSTREAM_INTERACTION_MARKER in text:
        return False
    focus_game_window_for_ok_startup()
    log.info("OK-WW cold-start window focused before task execution")
    return True


def _handle_startup_network_retry(
    text: str,
    *,
    retry_clicks: int,
    last_retry_at: float,
    now: float,
) -> tuple[int, float, str | None]:
    """Handle only the exact pre-gameplay network dialog with a three-click cap."""
    if OK_STARTUP_WINDOW_STABLE_MARKER not in text:
        return retry_clicks, last_retry_at, None
    if GAMEPLAY_HANDOFF_MARKER in text:
        return retry_clicks, last_retry_at, None
    if not startup_network_retry_visible():
        return retry_clicks, last_retry_at, None
    if (
        retry_clicks > 0
        and now - last_retry_at < STARTUP_NETWORK_RETRY_COOLDOWN_SECONDS
    ):
        return retry_clicks, last_retry_at, None
    if retry_clicks >= MAX_STARTUP_NETWORK_RETRIES:
        return (
            retry_clicks,
            last_retry_at,
            (
                "FarmEcho startup network retry exhausted after "
                f"{MAX_STARTUP_NETWORK_RETRIES} attempts"
            ),
        )
    if not click_startup_network_retry():
        return retry_clicks, last_retry_at, None
    retry_clicks += 1
    log.warning(
        "clicked exact OK-owned startup network retry %s/%s",
        retry_clicks,
        MAX_STARTUP_NETWORK_RETRIES,
    )
    return retry_clicks, now, None


def _stop_process(process: subprocess.Popen[object]) -> None:
    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    finally:
        # The retry worker talks to the parent HID server.  Release after the
        # child is gone; releasing before terminate can be reasserted by a
        # final queued mouse-up/down packet from the child.
        release_active_mouse_buttons()


def run_confirmed_farm_echo_retry(
    *,
    target_count: int,
    attempt_limit: int,
    runtime_limit_seconds: float = MAX_FARM_ECHO_RUNTIME_SECONDS,
    resume_active_realm: bool = False,
) -> OkRunResult:
    """Retry until N echoes are absorbed, bounding only time without progress."""
    if (
        runtime_limit_seconds <= 0
        or runtime_limit_seconds > MAX_FARM_ECHO_RUNTIME_SECONDS
    ):
        raise ValueError(
            "FarmEcho runtime limit must be within "
            f"(0, {MAX_FARM_ECHO_RUNTIME_SECONDS:.0f}] seconds"
        )
    require_admin()
    facts = preflight_farm_echo_task(attempt_limit)
    facts["configured_attempt_limit"] = facts["repeat_farm_count"]
    facts["repeat_farm_count"] = target_count
    facts.update(
        {
            "workflow_task": "farm_echo_confirmed_retry",
            "target_count": target_count,
            "attempt_limit": attempt_limit,
            "farm_echo_runtime_limit_seconds": runtime_limit_seconds,
            "resume_active_realm": resume_active_realm,
        }
    )
    started = datetime.now().astimezone()
    run_id = started.strftime("%Y%m%d_%H%M%S") + "_farm_echo_confirmed_retry"
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    slice_path = run_dir / "ok-current-run.log"
    worker_result_path = run_dir / "worker-result.json"
    console_path = run_dir / "worker-console.log"
    cursor = LogCursor(OK_LOG_FILE)
    command = [
        str(OK_PYTHON_EXE),
        str(CONFIRMED_RETRY_WORKER),
        str(OK_WORKING_DIR),
        str(worker_result_path),
        str(target_count),
    ]
    if resume_active_realm:
        command.append("resume")
    log.info(
        "starting confirmed FarmEcho retry target=%s attempt_limit=%s",
        target_count,
        attempt_limit,
    )

    collected: list[str] = []
    started_monotonic = time.monotonic()
    last_log_activity = started_monotonic
    no_progress_deadline = started_monotonic + runtime_limit_seconds
    last_confirmed_absorptions = 0
    saw_log_activity = False
    saw_game_window = False
    timeout_reason = ""
    live_combat_degradation = False
    startup_window_focused = False
    startup_network_retry_clicks = 0
    last_startup_network_retry_at = 0.0
    startup_network_retry_exhausted = False
    with console_path.open("w", encoding="utf-8") as console:
        resume_active_mouse_control()
        process: subprocess.Popen[object] = subprocess.Popen(
            command,
            cwd=OK_WORKING_DIR,
            stdin=subprocess.DEVNULL,
            stdout=console,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        while process.poll() is None:
            now = time.monotonic()
            chunk = cursor.read_new()
            if chunk:
                collected.append(chunk)
                slice_path.write_text("".join(collected), encoding="utf-8")
                last_log_activity = now
                saw_log_activity = True
                for line in chunk.splitlines():
                    if "HOST_FARM_ECHO_" in line or "Revive Failed" in line:
                        log.info("confirmed retry progress: %s", line)
                try:
                    startup_window_focused = _focus_ok_startup_window_if_needed(
                        "".join(collected),
                        already_focused=startup_window_focused,
                    )
                except Exception as exc:
                    timeout_reason = (
                        "OK-WW cold-start window could not be focused: "
                        f"{exc}"
                    )
                    log.exception(timeout_reason)
                    break
                degradation_reason = _live_combat_degradation_reason(
                    "".join(collected),
                    resume_active_realm=resume_active_realm,
                )
                if degradation_reason:
                    live_combat_degradation = True
                    timeout_reason = degradation_reason
                    log.warning(
                        "confirmed FarmEcho retry detected live combat "
                        "degradation; stopping only the upstream worker"
                    )
                    break
            text = "".join(collected)
            confirmed_absorptions = count_farm_echo_absorptions(text)
            if confirmed_absorptions > last_confirmed_absorptions:
                last_confirmed_absorptions = confirmed_absorptions
                no_progress_deadline = now + runtime_limit_seconds
                log.info(
                    "FarmEcho absorption progress advanced to %s; reset the "
                    "no-progress deadline",
                    confirmed_absorptions,
                )
            try:
                (
                    startup_network_retry_clicks,
                    last_startup_network_retry_at,
                    network_retry_reason,
                ) = _handle_startup_network_retry(
                    text,
                    retry_clicks=startup_network_retry_clicks,
                    last_retry_at=last_startup_network_retry_at,
                    now=now,
                )
            except Exception as exc:
                timeout_reason = f"FarmEcho startup network retry handler failed: {exc}"
                log.exception(timeout_reason)
                break
            if network_retry_reason:
                timeout_reason = network_retry_reason
                startup_network_retry_exhausted = True
                break
            # Liveness guard: track whether the client window has EVER appeared
            # during this run.  Only after it has appeared once do we treat its
            # disappearance as a crash / wrong-click (退出 instead of 重试) that
            # must stop immediately rather than wait out the 45-minute log-stall
            # timeout.  During cold start the window legitimately does not exist
            # yet, so we must not fire there.
            game_alive = is_game_window_alive()
            if game_alive:
                saw_game_window = True
            elif saw_game_window:
                timeout_reason = (
                    "Wuthering Waves client window disappeared during confirmed "
                    "FarmEcho retry; the startup network retry likely clicked "
                    "退出 instead of 重试"
                )
                log.error(timeout_reason)
                try:
                    save_step_screenshot("ok_confirmed_retry_client_gone")
                except Exception:
                    log.exception("could not save client-gone evidence")
                break
            if now >= no_progress_deadline:
                timeout_reason = (
                    "FarmEcho made no new absorption progress for "
                    f"{runtime_limit_seconds:.0f} seconds"
                )
                break
            if not saw_log_activity and now - started_monotonic > STARTUP_LOG_TIMEOUT:
                timeout_reason = (
                    "confirmed FarmEcho retry produced no log before startup deadline"
                )
                break
            if saw_log_activity and now - last_log_activity > LOG_STALL_TIMEOUT:
                timeout_reason = "confirmed FarmEcho retry log stalled for 45 minutes"
                break
            time.sleep(POLL_INTERVAL)
        if timeout_reason:
            _stop_process(process)
        exit_code = process.poll()

    chunk = cursor.read_new()
    if chunk:
        collected.append(chunk)
    slice_path.write_text("".join(collected), encoding="utf-8")

    payload: dict[str, object] = {}
    if worker_result_path.is_file():
        try:
            loaded = json.loads(worker_result_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        except (OSError, json.JSONDecodeError):
            log.exception("invalid confirmed retry result: %s", worker_result_path)
    absorbed_count = int(payload.get("absorbed_count") or 0)
    if not absorbed_count:
        absorbed_count = count_farm_echo_absorptions("".join(collected))
    facts["confirmed_farm_echo_absorption_count"] = absorbed_count
    facts["ok_cold_start_window_focused"] = startup_window_focused
    facts["farm_echo_startup_network_retry_clicks"] = startup_network_retry_clicks
    facts["farm_echo_startup_network_retry_exhausted"] = (
        startup_network_retry_exhausted
    )
    facts["farm_echo_no_progress_timeout_seconds"] = runtime_limit_seconds
    facts["farm_echo_live_combat_degradation"] = live_combat_degradation
    facts["farm_echo_live_combat_degradation_reason"] = (
        timeout_reason if live_combat_degradation else ""
    )
    success = (
        not timeout_reason
        and exit_code == 0
        and payload.get("success") is True
        and absorbed_count >= target_count
    )
    if success:
        reason = (
            f"FarmEcho absorption confirmed {absorbed_count}/{target_count} echoes"
        )
        evidence = save_step_screenshot("ok_farm_echo_confirmed_retry_completed")
    else:
        reason = str(
            timeout_reason
            or payload.get("reason")
            or f"confirmed FarmEcho retry worker exited with code {exit_code}"
        )
        evidence = save_step_screenshot("ok_farm_echo_confirmed_retry_failed")

    finished = datetime.now().astimezone()
    result = OkRunResult(
        run_id=run_id,
        status="success" if success else "failed",
        reason=reason,
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
        duration_seconds=round((finished - started).total_seconds()),
        log_slice_path=str(slice_path),
        evidence_path=str(evidence),
        config=facts,
        exit_code=0 if success else 1,
    )
    write_result(result, run_dir)
    return result
