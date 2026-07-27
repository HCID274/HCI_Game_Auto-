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
