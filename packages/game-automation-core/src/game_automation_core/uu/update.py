"""Bounded recovery after a verified mandatory UU client update."""

from __future__ import annotations

import time
from collections.abc import Callable


def recover_mandatory_update(
    *,
    accept_update: Callable[[], bool],
    update_visible: Callable[[float], bool],
    primary_pids: Callable[[], frozenset[int]],
    start_process: Callable[[], None],
    focus_window: Callable[[float], str],
    timeout: float,
    relaunch_grace: float,
    poll_interval: float,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Accept one update and return only after a new usable UU generation exists."""
    old_pids = primary_pids()
    if not accept_update():
        return False

    deadline = monotonic() + timeout
    absent_since: float | None = None
    relaunched = False
    last_focus_error: Exception | None = None
    while monotonic() < deadline:
        current_pids = primary_pids()
        now = monotonic()
        if not current_pids:
            absent_since = now if absent_since is None else absent_since
            if not relaunched and now - absent_since >= relaunch_grace:
                start_process()
                relaunched = True
        else:
            absent_since = None
            generation_changed = not old_pids or current_pids.isdisjoint(old_pids)
            if generation_changed:
                try:
                    focus_window(min(5.0, max(deadline - now, 0.1)))
                    last_focus_error = None
                    if not update_visible(0.8):
                        return True
                except RuntimeError as exc:
                    last_focus_error = exc
        sleep(poll_interval)

    detail = f"; last focus error: {last_focus_error}" if last_focus_error else ""
    raise TimeoutError(
        f"mandatory UU update did not produce a usable new process generation within {timeout:.0f}s{detail}"
    )


__all__ = ["recover_mandatory_update"]
