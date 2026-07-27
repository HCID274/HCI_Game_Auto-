"""Single-budget restart supervision shared by UU game adapters."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from game_automation_core.uu.errors import UuStartupError, UuStartupFinalError

log = logging.getLogger(__name__)


def run_with_restart_budget(
    *,
    attempt: Callable[[int], None],
    restart: Callable[[], object],
    max_restarts: int,
    restart_delay: float,
    sleep: Callable[[float], None] = time.sleep,
    logger: logging.Logger = log,
) -> int:
    """Run one adapter attempt plus at most ``max_restarts`` whole-process retries."""
    if max_restarts < 0:
        raise ValueError("max_restarts cannot be negative")
    last_error: UuStartupError | None = None
    restarts = 0
    for attempt_number in range(1, max_restarts + 2):
        try:
            attempt(attempt_number)
            return restarts
        except UuStartupError as exc:
            last_error = exc
            logger.warning(
                "UU attempt %d failed: retryable=%s step=%s reason=%s evidence=%s",
                attempt_number,
                exc.retryable,
                exc.step_name,
                exc.reason,
                exc.screenshot_path,
            )
            if not exc.retryable:
                exc.restarts_used = restarts
                raise
            if restarts >= max_restarts:
                break
            restart()
            restarts += 1
            sleep(restart_delay)
    if last_error is None:  # pragma: no cover - defensive invariant
        raise RuntimeError("UU supervisor ended without an attempt result")
    raise UuStartupFinalError(last_error, restarts)
