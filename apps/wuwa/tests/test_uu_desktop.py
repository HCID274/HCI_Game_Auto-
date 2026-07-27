from types import SimpleNamespace

from wuwa_auto.uu import desktop


def test_leave_pyautogui_failsafe_corner_moves_to_screen_center(monkeypatch):
    moves: list[tuple[int, int]] = []
    monkeypatch.setattr(desktop.pyautogui, "size", lambda: SimpleNamespace(width=2560, height=1440))
    monkeypatch.setattr(desktop.pyautogui, "position", lambda: SimpleNamespace(x=0, y=0))
    monkeypatch.setattr(
        desktop.ctypes.windll.user32,
        "SetCursorPos",
        lambda x, y: moves.append((x, y)) or 1,
    )

    desktop._leave_pyautogui_failsafe_corner()

    assert moves == [(1280, 720)]


def test_leave_pyautogui_failsafe_corner_preserves_safe_position(monkeypatch):
    moves: list[tuple[int, int]] = []
    monkeypatch.setattr(desktop.pyautogui, "size", lambda: SimpleNamespace(width=2560, height=1440))
    monkeypatch.setattr(desktop.pyautogui, "position", lambda: SimpleNamespace(x=840, y=575))
    monkeypatch.setattr(
        desktop.ctypes.windll.user32,
        "SetCursorPos",
        lambda x, y: moves.append((x, y)) or 1,
    )

    desktop._leave_pyautogui_failsafe_corner()

    assert moves == []


def test_park_cursor_for_detection_uses_right_side_outside_uu(monkeypatch):
    moves: list[tuple[int, int]] = []
    monkeypatch.setattr(desktop.pyautogui, "size", lambda: SimpleNamespace(width=2560, height=1440))
    monkeypatch.setattr(
        desktop.ctypes.windll.user32,
        "SetCursorPos",
        lambda x, y: moves.append((x, y)) or 1,
    )

    desktop.park_cursor_for_detection()

    assert moves == [(2558, 720)]


def test_focus_uu_window_uses_verified_activation(monkeypatch):
    window = SimpleNamespace(title="网易UU加速器", isMinimized=False, _hWnd=123)
    activations: list[tuple[int, float]] = []
    monkeypatch.setattr(desktop, "get_uu_windows", lambda: [window])
    monkeypatch.setattr(
        desktop,
        "activate_window",
        lambda hwnd, timeout: activations.append((hwnd, timeout)),
    )
    monkeypatch.setattr(desktop.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(desktop.ctypes.windll.user32, "GetForegroundWindow", lambda: 123)

    assert desktop.focus_uu_window(timeout=0.1) == "网易UU加速器"
    assert activations == [(123, 1.5)]
