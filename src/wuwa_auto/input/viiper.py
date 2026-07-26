"""Fully local virtual USB HID mouse backed by VIIPER and usbip-win2."""

from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import os
import socket
import struct
import subprocess
import time
import urllib.request
import zipfile
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from typing import Iterator

from wuwa_auto.input.driver import USBIP_EXE, driver_status, healthy_mouse_devices
from wuwa_auto.settings import LOGS_DIR, VIIPER_DIR, VIIPER_EXE

log = logging.getLogger(__name__)

VIIPER_VERSION = "0.7.0"
VIIPER_URL = (
    "https://github.com/Alia5/VIIPER/releases/download/"
    "v0.7.0/viiper-windows-amd64.zip"
)
VIIPER_ZIP_SHA256 = "a02b06751d64e43e7700aba8ee1f7e3e4f5f4e7f370a11722ff922ab075c1629"
VIIPER_EXE_SHA256 = "1868d682f4cc6d62349bbccbf0727b05d3eb6e22027ac34f0f1d9b1de56f2ddc"
MOUSE_PACKET = struct.Struct("<Bhhhh")
LEFT_BUTTON = 0x01


class VirtualHidError(RuntimeError):
    """The local virtual mouse could not be created or controlled."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_viiper_binary() -> Path:
    if VIIPER_EXE.exists() and _sha256(VIIPER_EXE) == VIIPER_EXE_SHA256:
        return VIIPER_EXE

    VIIPER_DIR.mkdir(parents=True, exist_ok=True)
    archive = VIIPER_DIR / "viiper-windows-amd64.zip"
    partial = archive.with_suffix(".zip.part")
    request = urllib.request.Request(VIIPER_URL, headers={"User-Agent": "wuwa-auto/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, partial.open(
            "wb"
        ) as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        if _sha256(partial) != VIIPER_ZIP_SHA256:
            raise VirtualHidError("VIIPER archive hash mismatch")
        os.replace(partial, archive)
    finally:
        partial.unlink(missing_ok=True)

    with zipfile.ZipFile(archive) as bundle:
        members = [name for name in bundle.namelist() if Path(name).name == "viiper.exe"]
        if len(members) != 1:
            raise VirtualHidError(f"unexpected VIIPER archive contents: {members}")
        with bundle.open(members[0]) as source, VIIPER_EXE.open("wb") as target:
            while chunk := source.read(1024 * 1024):
                target.write(chunk)
    if _sha256(VIIPER_EXE) != VIIPER_EXE_SHA256:
        VIIPER_EXE.unlink(missing_ok=True)
        raise VirtualHidError("VIIPER executable hash mismatch")
    log.info("prepared pinned VIIPER %s at %s", VIIPER_VERSION, VIIPER_EXE)
    return VIIPER_EXE


def encode_mouse_packet(
    *, buttons: int = 0, dx: int = 0, dy: int = 0, wheel: int = 0, pan: int = 0
) -> bytes:
    values = (dx, dy, wheel, pan)
    if any(value < -32768 or value > 32767 for value in values):
        raise ValueError(f"mouse delta out of int16 range: {values}")
    return MOUSE_PACKET.pack(buttons & 0x1F, dx, dy, wheel, pan)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _api_request(port: int, request: str, timeout: float = 8) -> dict[str, object]:
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as connection:
        connection.settimeout(timeout)
        connection.sendall(request.encode("utf-8") + b"\0")
        response = bytearray()
        while True:
            try:
                chunk = connection.recv(65536)
            except socket.timeout as exc:
                raise VirtualHidError(f"VIIPER API timed out for {request!r}") from exc
            if not chunk:
                break
            response.extend(chunk)
    try:
        data = json.loads(response.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VirtualHidError(f"invalid VIIPER response: {response!r}") from exc
    if not isinstance(data, dict):
        raise VirtualHidError(f"unexpected VIIPER response: {data!r}")
    if int(data.get("status", 200)) >= 400:
        raise VirtualHidError(f"VIIPER rejected {request!r}: {data}")
    return data


class ViiperServer:
    def __init__(self) -> None:
        self.api_port = _free_port()
        self.usb_port = _free_port()
        while self.usb_port == self.api_port:
            self.usb_port = _free_port()
        self.process: subprocess.Popen[bytes] | None = None
        self._log_file = None

    def start(self) -> None:
        status = driver_status()
        if not status.installed:
            raise VirtualHidError(
                "usbip-win2 is not installed; run "
                "wuwa-auto elevate input install-driver once"
            )
        executable = ensure_viiper_binary()
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        self._log_file = (LOGS_DIR / "viiper.log").open("ab")
        command = [
            str(executable),
            "server",
            f"--api.addr=127.0.0.1:{self.api_port}",
            f"--usb.addr=127.0.0.1:{self.usb_port}",
            "--api.auto-attach-local-client=true",
            # VIIPER 0.7.0 predates usbip-win2 0.9.7.8's native IOCTL API
            # change.  Its supported command-line fallback is compatible.
            "--api.auto-attach-windows-native=false",
            "--update-notify=none",
            "--log.level=info",
        ]
        environment = os.environ.copy()
        environment["PATH"] = os.pathsep.join(
            [str(USBIP_EXE.parent), environment.get("PATH", "")]
        )
        self.process = subprocess.Popen(
            command,
            cwd=VIIPER_DIR,
            stdin=subprocess.DEVNULL,
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env=environment,
        )
        deadline = time.monotonic() + 15
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise VirtualHidError(
                    f"VIIPER exited during startup with code {self.process.returncode}"
                )
            try:
                _api_request(self.api_port, "ping", timeout=1)
                log.info(
                    "VIIPER local server ready pid=%s api_port=%s usb_port=%s",
                    self.process.pid,
                    self.api_port,
                    self.usb_port,
                )
                return
            except (OSError, VirtualHidError) as exc:
                last_error = exc
                time.sleep(0.2)
        raise VirtualHidError(f"VIIPER did not become ready: {last_error}")

    def close(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.process = None
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None


class VirtualHidMouse:
    """A standard relative HID mouse that is enumerated by the local host."""

    def __init__(self) -> None:
        self.server = ViiperServer()
        self.stream: socket.socket | None = None
        self.bus_id: int | None = None
        self.device_id: str | None = None

    def __enter__(self) -> "VirtualHidMouse":
        self.start()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()

    def start(self) -> None:
        self.server.start()
        try:
            bus = _api_request(self.server.api_port, "bus/create")
            self.bus_id = int(bus["busId"])
            device = _api_request(
                self.server.api_port,
                f'bus/{self.bus_id}/add {{"type":"mouse"}}',
            )
            self.device_id = str(device["devId"])
            self.stream = socket.create_connection(
                ("127.0.0.1", self.server.api_port), timeout=8
            )
            self.stream.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.stream.sendall(
                f"bus/{self.bus_id}/{self.device_id}\0".encode("ascii")
            )
            self.send()
            deadline = time.monotonic() + 12
            devices: tuple[str, ...] = ()
            while time.monotonic() < deadline:
                devices = healthy_mouse_devices()
                if devices:
                    log.info("healthy host mouse enumerated: %s", devices)
                    return
                time.sleep(0.5)
            raise VirtualHidError(
                "virtual HID stream opened but Windows did not enumerate a healthy mouse"
            )
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self.stream is not None:
            try:
                self.send()
            except OSError:
                pass
            self.stream.close()
            self.stream = None
        if self.bus_id is not None and self.device_id is not None:
            try:
                _api_request(
                    self.server.api_port,
                    f"bus/{self.bus_id}/remove {self.device_id}",
                    timeout=3,
                )
            except (OSError, VirtualHidError):
                pass
        if self.bus_id is not None:
            try:
                _api_request(
                    self.server.api_port,
                    f"bus/remove {self.bus_id}",
                    timeout=3,
                )
            except (OSError, VirtualHidError):
                pass
        self.bus_id = None
        self.device_id = None
        self.server.close()

    def send(
        self,
        *,
        buttons: int = 0,
        dx: int = 0,
        dy: int = 0,
        wheel: int = 0,
        pan: int = 0,
    ) -> None:
        if self.stream is None:
            raise VirtualHidError("virtual mouse is not connected")
        self.stream.sendall(
            encode_mouse_packet(
                buttons=buttons,
                dx=dx,
                dy=dy,
                wheel=wheel,
                pan=pan,
            )
        )

    @staticmethod
    def cursor_position() -> tuple[int, int]:
        point = wintypes.POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            raise ctypes.WinError()
        return point.x, point.y

    def move_to(
        self,
        x: int,
        y: int,
        *,
        tolerance: int = 2,
        timeout: float = 4,
    ) -> tuple[int, int]:
        target = (int(x), int(y))
        deadline = time.monotonic() + timeout
        previous: tuple[int, int] | None = None
        stagnant = 0
        while time.monotonic() < deadline:
            current = self.cursor_position()
            error_x = target[0] - current[0]
            error_y = target[1] - current[1]
            if abs(error_x) <= tolerance and abs(error_y) <= tolerance:
                self.send()
                return current
            if previous == current:
                stagnant += 1
            else:
                stagnant = 0
            previous = current
            # Windows applies its configured mouse speed and acceleration to
            # HID deltas.  Feed roughly one quarter of the measured error and
            # close the loop against GetCursorPos instead of assuming 1:1
            # pixels.  A stagnant cursor gets progressively larger nudges.
            gain = min(4, 1 + stagnant)

            def command(error: int) -> int:
                if not error:
                    return 0
                magnitude = max(1, min(80, abs(error) // 4 or 1)) * gain
                return max(-120, min(120, magnitude if error > 0 else -magnitude))

            dx = command(error_x)
            dy = command(error_y)
            self.send(dx=dx, dy=dy)
            time.sleep(0.018)
            self.send()
            time.sleep(0.012)
        raise VirtualHidError(
            f"virtual mouse could not reach {target}; cursor={self.cursor_position()}"
        )

    def click_at(self, x: int, y: int, *, hold: float = 0.08) -> None:
        reached = self.move_to(x, y)
        self.send(buttons=LEFT_BUTTON)
        time.sleep(hold)
        self.send()
        log.info("virtual HID clicked target=%s reached=%s", (x, y), reached)


_active_mouse: VirtualHidMouse | None = None


@contextmanager
def managed_virtual_mouse() -> Iterator[VirtualHidMouse]:
    """Reuse the workflow mouse, or create a scoped one for a recovery command."""
    global _active_mouse
    if _active_mouse is not None:
        yield _active_mouse
        return
    with VirtualHidMouse() as mouse:
        _active_mouse = mouse
        try:
            yield mouse
        finally:
            _active_mouse = None


def probe_virtual_mouse() -> dict[str, object]:
    with managed_virtual_mouse() as mouse:
        start = mouse.cursor_position()
        width = ctypes.windll.user32.GetSystemMetrics(0)
        height = ctypes.windll.user32.GetSystemMetrics(1)
        target = (
            max(20, min(width - 20, start[0] + (40 if start[0] < width - 60 else -40))),
            max(20, min(height - 20, start[1] + (40 if start[1] < height - 60 else -40))),
        )
        mouse.move_to(*target)
        moved = mouse.cursor_position()
        mouse.move_to(*start)
        return {
            "start": start,
            "target": target,
            "moved": moved,
            "restored": mouse.cursor_position(),
            "healthy_mouse_devices": healthy_mouse_devices(),
        }
