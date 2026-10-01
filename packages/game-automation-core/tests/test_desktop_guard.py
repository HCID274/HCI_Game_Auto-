import pytest

from game_automation_core.windows.desktop_guard import (
    DesktopBlockedError,
    WindowSnapshot,
    classify_blocker,
    require_desktop_ready,
)


def _window(process_name: str, title: str, *, foreground: bool = True) -> WindowSnapshot:
    return WindowSnapshot(
        hwnd=123,
        pid=456,
        process_name=process_name,
        executable="",
        title=title,
        command_line="",
        foreground=foreground,
    )


def test_known_system_dialog_blocks_automation() -> None:
    picker = _window("PickerHost.exe", "Windows 安全中心")
    assert classify_blocker(picker) == "Windows firewall notification"
    with pytest.raises(DesktopBlockedError):
        require_desktop_ready(windows=[picker], check_input_desktop=False)


def test_normal_foreground_is_returned() -> None:
    shell = _window("powershell.exe", "Administrator: PowerShell")
    assert require_desktop_ready(windows=[shell], check_input_desktop=False) == shell


def test_notification_scan_does_not_change_shared_win32_rect_signature(monkeypatch) -> None:
    import ctypes
    from game_automation_core.windows import desktop_guard

    if not hasattr(ctypes, "windll"):
        pytest.skip("Windows ABI regression")
    original = ctypes.windll.user32.GetWindowRect.argtypes
    monkeypatch.setattr(desktop_guard, "visible_windows", lambda: [])
    desktop_guard.dismiss_known_desktop_notifications()
    assert ctypes.windll.user32.GetWindowRect.argtypes == original


@pytest.mark.parametrize(
    ("process_name", "title", "matched", "expected_closes"),
    [
        ("Flow.Launcher.exe", "Flow Launcher", True, 1),
        ("Flow.Launcher.exe", "Flow Launcher", False, 0),
        ("Flow.Launcher.exe", "Settings", True, 0),
        ("other.exe", "Flow Launcher", True, 0),
    ],
)
def test_notification_close_requires_identity_and_image(
    monkeypatch, tmp_path, process_name, title, matched, expected_closes
):
    import ctypes
    from types import SimpleNamespace

    import pyautogui
    from game_automation_core.windows import desktop_guard

    closes, evidence = [], []

    def get_rect(hwnd, output):
        output._obj.left, output._obj.top = 100, 100
        output._obj.right, output._obj.bottom = 800, 500
        return True

    def post_message(*args):
        closes.append(args)
        return True

    user32 = SimpleNamespace(
        GetWindowRect=get_rect,
        IsWindowVisible=lambda hwnd: False,
        PostMessageW=post_message,
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: user32, raising=False)
    monkeypatch.setattr(desktop_guard, "visible_windows", lambda: [_window(process_name, title)])
    monkeypatch.setattr(pyautogui, "locateOnScreen", lambda *a, **kw: (1, 2) if matched else None)
    monkeypatch.setattr(pyautogui, "screenshot", lambda path: evidence.append(path))
    monkeypatch.chdir(tmp_path)

    desktop_guard.dismiss_known_desktop_notifications()

    assert len(closes) == len(evidence) == expected_closes
    if closes:
        assert closes == [(123, 0x0010, 0, 0)]
