"""Pinned, auditable installation of the USB/IP virtual host driver."""

from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import os
import subprocess
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

from wuwa_auto.settings import USBIP_INSTALLER

log = logging.getLogger(__name__)

USBIP_VERSION = "0.9.7.8"
USBIP_INSTALLER_URL = (
    "https://github.com/vadimgrn/usbip-win2/releases/download/"
    "v.0.9.7.8/USBip-0.9.7.8-x64.exe"
)
USBIP_INSTALLER_SHA256 = (
    "44451fe06f4186125c2a5ecd25b099c5560a61a60b1e56f5a0758e77a60afa44"
)
EXPECTED_INSTALLER_SIGNER = "Cloudyne Systems"
USBIP_EXE = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "USBip" / "usbip.exe"


class VirtualHidDriverError(RuntimeError):
    """The virtual HID prerequisite is missing or unsafe."""


@dataclass(frozen=True)
class DriverStatus:
    version: str
    usbip_exe: str
    usbip_exe_exists: bool
    driver_path: str
    driver_exists: bool
    driver_state: str | None
    driver_signature_status: str | None
    driver_signer: str | None
    healthy_mouse_devices: tuple[str, ...]

    @property
    def installed(self) -> bool:
        return (
            self.usbip_exe_exists
            and self.driver_exists
            and self.driver_state == "Running"
        )

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["installed"] = self.installed
        return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_pinned(url: str, destination: Path, expected_sha256: str) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and _sha256(destination) == expected_sha256:
        return destination

    partial = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "wuwa-auto/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, partial.open(
            "wb"
        ) as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        actual = _sha256(partial)
        if actual != expected_sha256:
            raise VirtualHidDriverError(
                f"download hash mismatch for {destination.name}: {actual}"
            )
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)
    return destination


def _powershell_json(script: str) -> object:
    completed = subprocess.run(
        [
            "pwsh.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=45,
    )
    if completed.returncode:
        raise VirtualHidDriverError(completed.stderr.strip() or completed.stdout.strip())
    output = completed.stdout.strip()
    return json.loads(output) if output else None


def _authenticode(path: Path) -> tuple[str | None, str | None]:
    quoted = str(path).replace("'", "''")
    data = _powershell_json(
        "$s=Get-AuthenticodeSignature -LiteralPath '"
        + quoted
        + "'; [pscustomobject]@{Status=[string]$s.Status;"
        "Signer=$s.SignerCertificate.Subject} | ConvertTo-Json -Compress"
    )
    if not isinstance(data, dict):
        return None, None
    return str(data.get("Status") or ""), str(data.get("Signer") or "")


def _usbip_root_thumbprints() -> set[str]:
    data = _powershell_json(
        "@((Get-ChildItem Cert:\\LocalMachine\\Root | "
        "Where-Object {$_.Subject -eq 'CN=USBip'} | "
        "Select-Object -ExpandProperty Thumbprint)) | ConvertTo-Json -Compress"
    )
    if data is None:
        return set()
    if isinstance(data, str):
        return {data}
    if isinstance(data, list):
        return {str(value) for value in data}
    return set()


def _remove_new_usbip_roots(thumbprints: set[str]) -> None:
    for thumbprint in sorted(thumbprints):
        if not thumbprint or any(char not in "0123456789ABCDEFabcdef" for char in thumbprint):
            continue
        subprocess.run(
            [
                "pwsh.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Remove-Item -LiteralPath "
                f"'Cert:\\LocalMachine\\Root\\{thumbprint}' -Force",
            ],
            check=False,
            timeout=30,
        )


def healthy_mouse_devices() -> tuple[str, ...]:
    data = _powershell_json(
        "@(Get-PnpDevice -PresentOnly -Class Mouse -ErrorAction SilentlyContinue | "
        "Where-Object {$_.Status -eq 'OK'} | "
        "ForEach-Object {($_.FriendlyName + '|' + $_.InstanceId)}) | "
        "ConvertTo-Json -Compress"
    )
    if data is None:
        return ()
    if isinstance(data, str):
        return (data,)
    if isinstance(data, list):
        return tuple(str(value) for value in data)
    return ()


def _installed_driver_details() -> tuple[Path | None, str | None]:
    data = _powershell_json(
        "$d=Get-CimInstance Win32_SystemDriver -Filter \"Name='usbip2_ude'\" "
        "-ErrorAction SilentlyContinue; if ($d) { "
        "[pscustomobject]@{Path=$d.PathName.Trim('\\\"');State=$d.State} | "
        "ConvertTo-Json -Compress }"
    )
    if not isinstance(data, dict):
        return None, None
    raw_path = str(data.get("Path") or "")
    return (Path(raw_path) if raw_path else None), str(data.get("State") or "")


def driver_status() -> DriverStatus:
    driver_path, driver_state = _installed_driver_details()
    signature_status: str | None = None
    signer: str | None = None
    if driver_path is not None and driver_path.exists():
        try:
            signature_status, signer = _authenticode(driver_path)
        except VirtualHidDriverError:
            log.exception("could not inspect USB/IP driver signature")
    return DriverStatus(
        version=USBIP_VERSION,
        usbip_exe=str(USBIP_EXE),
        usbip_exe_exists=USBIP_EXE.exists(),
        driver_path=str(driver_path or ""),
        driver_exists=bool(driver_path and driver_path.exists()),
        driver_state=driver_state,
        driver_signature_status=signature_status,
        driver_signer=signer,
        healthy_mouse_devices=healthy_mouse_devices(),
    )


def prepare_usbip_installer() -> Path:
    installer = _download_pinned(
        USBIP_INSTALLER_URL,
        USBIP_INSTALLER,
        USBIP_INSTALLER_SHA256,
    )
    status, signer = _authenticode(installer)
    if status != "Valid" or EXPECTED_INSTALLER_SIGNER not in (signer or ""):
        raise VirtualHidDriverError(
            f"USB/IP installer signature rejected: status={status!r}, signer={signer!r}"
        )
    log.info("verified USB/IP installer version=%s sha256=%s", USBIP_VERSION, _sha256(installer))
    return installer


def _try_restore_point() -> None:
    # This cmdlet is Windows PowerShell-only.  The host has PowerShell 7 ahead
    # of the inbox modules in PSModulePath, so give 5.1 its own compatible
    # module path instead of allowing it to load PowerShell 7 type data.
    environment = os.environ.copy()
    user_profile = Path(environment.get("USERPROFILE", r"C:\Users\Default"))
    environment["PSModulePath"] = os.pathsep.join(
        [
            str(user_profile / "Documents" / "WindowsPowerShell" / "Modules"),
            r"C:\Program Files\WindowsPowerShell\Modules",
            r"C:\Windows\System32\WindowsPowerShell\v1.0\Modules",
        ]
    )
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Checkpoint-Computer -Description 'WuwaAuto before USBip' "
            "-RestorePointType MODIFY_SETTINGS",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        check=False,
        timeout=180,
    )
    if completed.returncode:
        log.warning("system restore point was unavailable: %s", completed.stderr.strip())
    else:
        log.info("created system restore point before USB/IP installation")


def install_usbip_driver() -> DriverStatus:
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise VirtualHidDriverError(
            "driver installation requires elevation: wuwa-auto elevate input install-driver"
        )
    installer = prepare_usbip_installer()
    before_roots = _usbip_root_thumbprints()
    _try_restore_point()
    completed = subprocess.run(
        [
            str(installer),
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/SP-",
            "/CLOSEAPPLICATIONS",
        ],
        check=False,
        timeout=300,
    )
    if completed.returncode not in (0, 3010):
        raise VirtualHidDriverError(
            f"USB/IP installer exited with code {completed.returncode}"
        )

    new_roots = _usbip_root_thumbprints() - before_roots
    if new_roots:
        _remove_new_usbip_roots(new_roots)
        raise VirtualHidDriverError(
            "USB/IP installation attempted to add a test root certificate; "
            "the certificate was removed and the driver was rejected"
        )

    status = driver_status()
    if not status.installed:
        raise VirtualHidDriverError("USB/IP files were not installed")
    if status.driver_signature_status != "Valid":
        raise VirtualHidDriverError(
            "installed USB/IP kernel driver does not have a valid Windows signature"
        )
    log.info(
        "USB/IP driver installed signer=%s; a reboot is recommended before unattended use",
        status.driver_signer,
    )
    return status
