import threading

import pytest

from game_automation_core.windows.topmost_guard import (
    TopLevelWindow,
    TopmostCoverGuard,
    covering_windows,
    keep_covering_windows_down,
)

PROTECTED = frozenset({"uu.exe", "uu_ball.exe", "starrail.exe"})
UU = TopLevelWindow(1, 100, "uu.exe", (800, 400, 1800, 1100), topmost=False)
# 11-09 现场：Claude Code 提醒窗口（pwsh.exe）置顶，停在屏幕正中，压着 UU 的“停止加速”按钮。
NOTIFIER = TopLevelWindow(2, 200, "pwsh.exe", (830, 495, 1730, 945), topmost=True)


def _guard(windows, *, owners=None, push_ok=True):
    calls = {"pushed": [], "raised": []}

    def push_down(hwnd):
        calls["pushed"].append(hwnd)
        return push_ok

    def raise_topmost(hwnd):
        calls["raised"].append(hwnd)
        return True

    guard = TopmostCoverGuard(
        PROTECTED,
        enumerate_windows=lambda: list(windows),
        push_down=push_down,
        raise_topmost=raise_topmost,
        owner_pid=lambda hwnd: (owners or {}).get(hwnd, 0),
        describe=lambda hwnd: f"hwnd={hwnd}",
    )
    return guard, calls


def test_only_foreign_topmost_windows_over_protected_windows_are_selected():
    windows = [
        NOTIFIER,
        TopLevelWindow(3, 300, "explorer.exe", (0, 1380, 2560, 1440), topmost=True),
        TopLevelWindow(4, 400, "chrome.exe", (900, 500, 1500, 900), topmost=False),
        TopLevelWindow(5, 500, "snipaste.exe", (0, 0, 300, 200), topmost=True),
        TopLevelWindow(6, 100, "uu_ball.exe", (1000, 600, 1060, 660), topmost=True),
        TopLevelWindow(7, 700, "", (900, 500, 1500, 900), topmost=True),
        TopLevelWindow(8, 800, "pwsh.exe", (1800, 400, 2000, 600), topmost=True),
        UU,
    ]

    assert covering_windows(windows, PROTECTED) == [NOTIFIER]


def test_nothing_is_selected_without_a_protected_window_on_screen():
    assert covering_windows([NOTIFIER], PROTECTED) == []


def test_pushes_the_cover_again_after_it_retakes_topmost_and_restores_it_once():
    guard, calls = _guard([NOTIFIER, UU], owners={2: 200})

    guard.sweep()
    guard.sweep()  # 提醒窗口来了新提醒，又把自己置顶
    guard.restore()

    assert calls["pushed"] == [2, 2]
    assert calls["raised"] == [2]
    assert guard.pushed == {}


def test_restore_skips_windows_that_closed_or_changed_owner():
    guard, calls = _guard([NOTIFIER, UU], owners={2: 999})

    guard.sweep()
    guard.restore()

    assert calls["raised"] == []


def test_failed_push_is_not_restored_later():
    guard, calls = _guard([NOTIFIER, UU], owners={2: 200}, push_ok=False)

    guard.sweep()
    guard.restore()

    assert calls["pushed"] == [2]
    assert calls["raised"] == []


def test_background_guard_restores_even_when_the_run_fails():
    swept = threading.Event()
    guard, calls = _guard([NOTIFIER, UU], owners={2: 200})
    sweep = guard.sweep

    def sweep_and_signal():
        sweep()
        swept.set()

    guard.sweep = sweep_and_signal

    with pytest.raises(RuntimeError):
        with keep_covering_windows_down(PROTECTED, interval=0.01, guard=guard):
            assert swept.wait(2)
            raise RuntimeError("UU failed")

    assert 2 in calls["pushed"]
    assert calls["raised"] == [2]


def test_restore_errors_do_not_change_the_run_result():
    guard, _ = _guard([])

    def broken_restore():
        raise OSError("SetWindowPos failed")

    guard.restore = broken_restore

    with keep_covering_windows_down(PROTECTED, interval=0.01, guard=guard):
        result = "daily ok"

    assert result == "daily ok"


def test_background_guard_survives_a_failing_sweep():
    attempts = threading.Event()
    count = {"n": 0}
    guard, _ = _guard([])

    def flaky():
        count["n"] += 1
        if count["n"] >= 3:
            attempts.set()
        raise OSError("EnumWindows failed")

    guard.sweep = flaky

    with keep_covering_windows_down(PROTECTED, interval=0.01, guard=guard):
        assert attempts.wait(2)
