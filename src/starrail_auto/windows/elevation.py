"""Windows elevation support for the unified CLI."""

import ctypes
import subprocess
import sys
from typing import Sequence

from starrail_auto.settings import PROJECT_ROOT

SEE_MASK_NOCLOSEPROCESS = 0x00000040
SW_SHOWNORMAL = 1
INFINITE = 0xFFFFFFFF


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("fMask", ctypes.c_ulong),
        ("hwnd", ctypes.c_void_p),
        ("lpVerb", ctypes.c_wchar_p),
        ("lpFile", ctypes.c_wchar_p),
        ("lpParameters", ctypes.c_wchar_p),
        ("lpDirectory", ctypes.c_wchar_p),
        ("nShow", ctypes.c_int),
        ("hInstApp", ctypes.c_void_p),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", ctypes.c_wchar_p),
        ("hkeyClass", ctypes.c_void_p),
        ("dwHotKey", ctypes.c_ulong),
        ("hIconOrMonitor", ctypes.c_void_p),
        ("hProcess", ctypes.c_void_p),
    ]


def _is_running_as_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # pragma: no cover - 依赖宿主环境
        return False


def _run_cli(cli_args: Sequence[str]) -> int:
    completed = subprocess.run(
        [sys.executable, "-m", "starrail_auto", "--already-elevated", *cli_args],
        cwd=PROJECT_ROOT,
    )
    return completed.returncode


def relaunch_cli_elevated(cli_args: Sequence[str]) -> int:
    """Run unified CLI arguments in an elevated child process and wait for it."""
    if _is_running_as_admin():
        return _run_cli(cli_args)

    shell32 = ctypes.windll.shell32
    kernel32 = ctypes.windll.kernel32

    execute_info = SHELLEXECUTEINFOW()
    execute_info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    execute_info.fMask = SEE_MASK_NOCLOSEPROCESS
    execute_info.hwnd = None
    execute_info.lpVerb = "runas"
    execute_info.lpFile = sys.executable
    execute_info.lpParameters = subprocess.list2cmdline(
        ["-m", "starrail_auto", "--already-elevated", *cli_args]
    )
    execute_info.lpDirectory = str(PROJECT_ROOT)
    execute_info.nShow = SW_SHOWNORMAL

    success = shell32.ShellExecuteExW(ctypes.byref(execute_info))
    if not success:
        raise RuntimeError("failed to relaunch elevated process")

    kernel32.WaitForSingleObject(execute_info.hProcess, INFINITE)
    exit_code = ctypes.c_ulong()
    kernel32.GetExitCodeProcess(execute_info.hProcess, ctypes.byref(exit_code))
    kernel32.CloseHandle(execute_info.hProcess)
    return int(exit_code.value)
