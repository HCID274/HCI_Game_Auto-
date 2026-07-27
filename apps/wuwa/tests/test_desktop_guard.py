import pytest

from wuwa_auto.windows.desktop_guard import (
    DesktopBlockedError,
    WindowSnapshot,
    classify_blocker,
    require_desktop_ready,
)


def _window(
    *,
    process_name: str,
    title: str,
    command_line: str = "",
    foreground: bool = False,
) -> WindowSnapshot:
    return WindowSnapshot(
        hwnd=123,
        pid=456,
        process_name=process_name,
        executable="",
        title=title,
        command_line=command_line,
        foreground=foreground,
    )


def test_codex_firewall_picker_is_a_desktop_blocker() -> None:
    picker = _window(
        process_name="PickerHost.exe",
        title="Windows 安全中心",
        command_line="PickerHost.exe FirewallNotificationDialogServer -Embedding",
        foreground=True,
    )

    assert classify_blocker(picker) == "Windows firewall notification"
    with pytest.raises(DesktopBlockedError, match="Windows firewall notification"):
        require_desktop_ready(windows=[picker], check_input_desktop=False)


def test_regular_foreground_window_passes_desktop_guard() -> None:
    powershell = _window(
        process_name="powershell.exe",
        title="Administrator: Windows PowerShell",
        foreground=True,
    )

    assert (
        require_desktop_ready(
            windows=[powershell],
            check_input_desktop=False,
        )
        == powershell
    )
