from wuwa_auto.okww.confirmed_retry import (
    ACTIVE_REALM_BIND_FAILURE_REASON,
    GAMEPLAY_HANDOFF_MARKER,
    MAX_STARTUP_NETWORK_RETRIES,
    OK_STARTUP_WINDOW_STABLE_MARKER,
    UPSTREAM_INTERACTION_MARKER,
    _focus_ok_startup_window_if_needed,
    _handle_startup_network_retry,
    _live_combat_degradation_reason,
)


def test_lucilla_blind_fallback_does_not_stop_a_live_worker() -> None:
    text = "Lucilla:Lucilla perform lib\nLucilla:Lucilla perform lib end\n"

    assert _live_combat_degradation_reason(
        text,
        resume_active_realm=False,
    ) is None


def test_current_character_bind_failure_only_stops_resume_worker() -> None:
    text = "FarmEchoTask:could not find char 0 please check current char\n"

    assert _live_combat_degradation_reason(
        text,
        resume_active_realm=True,
    ) == ACTIVE_REALM_BIND_FAILURE_REASON
    assert _live_combat_degradation_reason(
        text,
        resume_active_realm=False,
    ) is None


def test_cold_start_focus_runs_once_before_upstream_task(monkeypatch) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(
        "wuwa_auto.okww.confirmed_retry.focus_game_window_for_ok_startup",
        lambda: calls.append(True),
    )

    focused = _focus_ok_startup_window_if_needed(
        OK_STARTUP_WINDOW_STABLE_MARKER,
        already_focused=False,
    )
    focused = _focus_ok_startup_window_if_needed(
        OK_STARTUP_WINDOW_STABLE_MARKER,
        already_focused=focused,
    )

    assert focused is True
    assert calls == [True]


def test_cold_start_focus_does_not_interrupt_running_upstream(monkeypatch) -> None:
    def unexpected_focus() -> None:
        raise AssertionError("startup focus must stay outside the running task")

    monkeypatch.setattr(
        "wuwa_auto.okww.confirmed_retry.focus_game_window_for_ok_startup",
        unexpected_focus,
    )

    assert _focus_ok_startup_window_if_needed(
        f"{OK_STARTUP_WINDOW_STABLE_MARKER}\n{UPSTREAM_INTERACTION_MARKER}",
        already_focused=False,
    ) is False


def test_startup_network_retry_clicks_three_times_then_fails(monkeypatch) -> None:
    clicks: list[bool] = []
    monkeypatch.setattr(
        "wuwa_auto.okww.confirmed_retry.startup_network_retry_visible",
        lambda: True,
    )
    monkeypatch.setattr(
        "wuwa_auto.okww.confirmed_retry.click_startup_network_retry",
        lambda: clicks.append(True) or True,
    )
    retry_clicks = 0
    last_retry_at = 0.0
    reason = None

    for now in (0.0, 16.0, 32.0, 48.0):
        retry_clicks, last_retry_at, reason = _handle_startup_network_retry(
            OK_STARTUP_WINDOW_STABLE_MARKER,
            retry_clicks=retry_clicks,
            last_retry_at=last_retry_at,
            now=now,
        )

    assert retry_clicks == MAX_STARTUP_NETWORK_RETRIES
    assert len(clicks) == MAX_STARTUP_NETWORK_RETRIES
    assert reason == "FarmEcho startup network retry exhausted after 3 attempts"


def test_startup_network_retry_never_clicks_after_gameplay_handoff(
    monkeypatch,
) -> None:
    def unexpected_detection() -> bool:
        raise AssertionError("startup detector must be dormant after gameplay handoff")

    monkeypatch.setattr(
        "wuwa_auto.okww.confirmed_retry.startup_network_retry_visible",
        unexpected_detection,
    )

    retry_clicks, last_retry_at, reason = _handle_startup_network_retry(
        f"{OK_STARTUP_WINDOW_STABLE_MARKER}\n{GAMEPLAY_HANDOFF_MARKER}",
        retry_clicks=0,
        last_retry_at=0.0,
        now=0.0,
    )

    assert (retry_clicks, last_retry_at, reason) == (0, 0.0, None)
