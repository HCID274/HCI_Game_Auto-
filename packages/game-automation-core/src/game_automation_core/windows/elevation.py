"""Relaunch a Python module with Windows UAC elevation and return its exit code."""

from __future__ import annotations

import ctypes
import subprocess
import sys
from collections.abc import Sequence
from ctypes import wintypes
from pathlib import Path

SEE_MASK_NOCLOSEPROCESS = 0x00000040
SW_HIDE = 0
SW_SHOWNORMAL = 1
INFINITE = 0xFFFFFFFF


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", wintypes.ULONG),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIconOrMonitor", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


def is_running_as_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # pragma: no cover - host-dependent fallback
        return False


def _run_module(module_name: str, project_root: Path, cli_args: Sequence[str]) -> int:
    completed = subprocess.run(
        [sys.executable, "-m", module_name, "--already-elevated", *cli_args],
        cwd=project_root,
        check=False,
    )
    return completed.returncode


def relaunch_module_elevated(
    *,
    module_name: str,
    project_root: Path,
    cli_args: Sequence[str],
    hide_console: bool = True,
) -> int:
    """Run one module in an elevated child and wait only for that child."""
    if is_running_as_admin():
        return _run_module(module_name, project_root, cli_args)

    shell32 = ctypes.windll.shell32
    kernel32 = ctypes.windll.kernel32
    execute_info = SHELLEXECUTEINFOW()
    execute_info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    execute_info.fMask = SEE_MASK_NOCLOSEPROCESS
    execute_info.lpVerb = "runas"
    execute_info.lpFile = sys.executable
    execute_info.lpParameters = subprocess.list2cmdline(
        ["-m", module_name, "--already-elevated", *cli_args]
    )
    execute_info.lpDirectory = str(project_root)
    execute_info.nShow = SW_HIDE if hide_console else SW_SHOWNORMAL

    if not shell32.ShellExecuteExW(ctypes.byref(execute_info)):
        raise ctypes.WinError()
    if not execute_info.hProcess:
        raise RuntimeError("elevated process returned no handle")

    try:
        kernel32.WaitForSingleObject(execute_info.hProcess, INFINITE)
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(
            execute_info.hProcess, ctypes.byref(exit_code)
        ):
            raise ctypes.WinError()
        return int(exit_code.value)
    finally:
        kernel32.CloseHandle(execute_info.hProcess)

