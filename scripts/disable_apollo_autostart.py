"""Disable Apollo's Windows service and stop its current processes."""

import ctypes
import subprocess
import sys
from ctypes import wintypes


SERVICE_NAME = "ApolloService"
VIRTUAL_DISPLAY_INSTANCE_ID = r"ROOT\DISPLAY\0001"
PRIMARY_DISPLAY = r"\\.\DISPLAY1"
TARGET_WIDTH = 2560
TARGET_HEIGHT = 1440

ENUM_CURRENT_SETTINGS = -1
DM_PELSWIDTH = 0x00080000
DM_PELSHEIGHT = 0x00100000
DM_DISPLAYFREQUENCY = 0x00400000
CDS_UPDATEREGISTRY = 0x00000001
CDS_TEST = 0x00000002
DISP_CHANGE_SUCCESSFUL = 0


class DEVMODEW(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", wintypes.WCHAR * 32),
        ("dmSpecVersion", wintypes.WORD),
        ("dmDriverVersion", wintypes.WORD),
        ("dmSize", wintypes.WORD),
        ("dmDriverExtra", wintypes.WORD),
        ("dmFields", wintypes.DWORD),
        ("dmOrientation", ctypes.c_short),
        ("dmPaperSize", ctypes.c_short),
        ("dmPaperLength", ctypes.c_short),
        ("dmPaperWidth", ctypes.c_short),
        ("dmScale", ctypes.c_short),
        ("dmCopies", ctypes.c_short),
        ("dmDefaultSource", ctypes.c_short),
        ("dmPrintQuality", ctypes.c_short),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", wintypes.WCHAR * 32),
        ("dmLogPixels", wintypes.WORD),
        ("dmBitsPerPel", wintypes.DWORD),
        ("dmPelsWidth", wintypes.DWORD),
        ("dmPelsHeight", wintypes.DWORD),
        ("dmDisplayFlags", wintypes.DWORD),
        ("dmDisplayFrequency", wintypes.DWORD),
        ("dmICMMethod", wintypes.DWORD),
        ("dmICMIntent", wintypes.DWORD),
        ("dmMediaType", wintypes.DWORD),
        ("dmDitherType", wintypes.DWORD),
        ("dmReserved1", wintypes.DWORD),
        ("dmReserved2", wintypes.DWORD),
        ("dmPanningWidth", wintypes.DWORD),
        ("dmPanningHeight", wintypes.DWORD),
    ]


def _run_sc(*args: str, allowed_codes: set[int] | None = None) -> None:
    completed = subprocess.run(
        ["sc.exe", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.stdout.strip():
        print(completed.stdout.strip())
    if completed.stderr.strip():
        print(completed.stderr.strip(), file=sys.stderr)

    valid_codes = allowed_codes or {0}
    if completed.returncode not in valid_codes:
        raise RuntimeError(
            f"sc.exe {' '.join(args)} failed with code {completed.returncode}"
        )


def _disable_virtual_display() -> None:
    completed = subprocess.run(
        ["pnputil.exe", "/disable-device", VIRTUAL_DISPLAY_INSTANCE_ID],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.stdout.strip():
        print(completed.stdout.strip())
    if completed.stderr.strip():
        print(completed.stderr.strip(), file=sys.stderr)
    if completed.returncode != 0:
        raise RuntimeError(
            "failed to disable Apollo virtual display "
            f"{VIRTUAL_DISPLAY_INSTANCE_ID}: code {completed.returncode}"
        )


def _restore_primary_display_resolution() -> None:
    user32 = ctypes.windll.user32
    mode = DEVMODEW()
    mode.dmSize = ctypes.sizeof(DEVMODEW)
    if not user32.EnumDisplaySettingsW(
        PRIMARY_DISPLAY,
        ENUM_CURRENT_SETTINGS,
        ctypes.byref(mode),
    ):
        raise RuntimeError(f"cannot read display settings for {PRIMARY_DISPLAY}")

    mode.dmPelsWidth = TARGET_WIDTH
    mode.dmPelsHeight = TARGET_HEIGHT
    mode.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | DM_DISPLAYFREQUENCY

    test_result = user32.ChangeDisplaySettingsExW(
        PRIMARY_DISPLAY,
        ctypes.byref(mode),
        None,
        CDS_TEST,
        None,
    )
    if test_result != DISP_CHANGE_SUCCESSFUL:
        raise RuntimeError(
            f"display mode test failed for {TARGET_WIDTH}x{TARGET_HEIGHT}: {test_result}"
        )

    apply_result = user32.ChangeDisplaySettingsExW(
        PRIMARY_DISPLAY,
        ctypes.byref(mode),
        None,
        CDS_UPDATEREGISTRY,
        None,
    )
    if apply_result != DISP_CHANGE_SUCCESSFUL:
        raise RuntimeError(
            f"cannot apply {TARGET_WIDTH}x{TARGET_HEIGHT}: {apply_result}"
        )
    print(
        f"restored {PRIMARY_DISPLAY} to {TARGET_WIDTH}x{TARGET_HEIGHT} "
        f"at {mode.dmDisplayFrequency}Hz"
    )


def main() -> int:
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise RuntimeError("administrator privileges are required")

    _run_sc("config", SERVICE_NAME, "start=", "disabled")
    _run_sc("stop", SERVICE_NAME, allowed_codes={0, 1062})
    _disable_virtual_display()
    _restore_primary_display_resolution()
    _run_sc("query", SERVICE_NAME)
    _run_sc("qc", SERVICE_NAME)
    return 0


if __name__ == "__main__":
    sys.exit(main())
