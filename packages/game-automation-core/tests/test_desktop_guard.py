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
