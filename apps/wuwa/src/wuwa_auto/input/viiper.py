"""Fully local virtual USB HID mouse backed by VIIPER and usbip-win2."""

from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import os
import secrets
import socket
import socketserver
import struct
import subprocess
import threading
import time
import urllib.request
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from typing import Self

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
RIGHT_BUTTON = 0x02
MIDDLE_BUTTON = 0x04
BUTTON_MASKS = {
    "left": LEFT_BUTTON,
    "right": RIGHT_BUTTON,
    "middle": MIDDLE_BUTTON,
}
CONTROL_PORT_ENV = "WUWA_VIRTUAL_HID_CONTROL_PORT"
CONTROL_TOKEN_ENV = "WUWA_VIRTUAL_HID_CONTROL_TOKEN"


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
            except TimeoutError as exc:
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
            # usbip.exe normally attaches in about two seconds, but a busy
            # driver stack can exceed VIIPER's five-second device cleanup
            # default. Keep the not-yet-attached device alive long enough for
            # the already-bounded local attach command to finish.
            "--api.device-handler-connect-timeout=20s",
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
        self._buttons = 0
        self._state_lock = threading.RLock()

    def __enter__(self) -> Self:
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
                # A worker can disappear while holding a combat button.  Emit a
                # final all-up packet before tearing down the virtual device so
                # the next task never inherits a stuck attack or dodge key.
                self.release_buttons()
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
        buttons: int | None = None,
        dx: int = 0,
        dy: int = 0,
        wheel: int = 0,
        pan: int = 0,
    ) -> None:
        with self._state_lock:
            if self.stream is None:
                raise VirtualHidError("virtual mouse is not connected")
            if buttons is not None:
                self._buttons = buttons & 0x1F
            self.stream.sendall(
                encode_mouse_packet(
                    buttons=self._buttons,
                    dx=dx,
                    dy=dy,
                    wheel=wheel,
                    pan=pan,
                )
            )

    def set_button(self, button: str, pressed: bool) -> None:
        """Change one button while preserving any other held buttons."""
        try:
            mask = BUTTON_MASKS[button]
        except KeyError as exc:
            raise ValueError(f"unsupported mouse button: {button}") from exc
        with self._state_lock:
            if pressed:
                self._buttons |= mask
            else:
                self._buttons &= ~mask
            self.send()

    def release_buttons(self) -> None:
        """Release every button and keep the state safe for later movement."""
        with self._state_lock:
            self._buttons = 0
            self.send()

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
        tolerance: int = 4,
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

            def command(error: int, *, multiplier: int = gain) -> int:
                if not error:
                    return 0
                magnitude = max(1, min(80, abs(error) // 4 or 1)) * multiplier
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

    def click_at(
        self,
        x: int,
        y: int,
        *,
        button: str = "left",
        hold: float = 0.08,
        log_action: bool = True,
    ) -> None:
        try:
            button_mask = BUTTON_MASKS[button]
        except KeyError as exc:
            raise ValueError(f"unsupported mouse button: {button}") from exc
        reached = self.move_to(x, y)
        with self._state_lock:
            previous_buttons = self._buttons
            self.send(buttons=previous_buttons | button_mask)
            time.sleep(hold)
            self._buttons = previous_buttons
            self.send()
        if log_action:
            log.info(
                "virtual HID %s-clicked target=%s reached=%s",
                button,
                (x, y),
                reached,
            )


class _VirtualHidControlServer:
    """Expose the workflow-owned HID to trusted local worker processes."""

    def __init__(self, mouse: VirtualHidMouse) -> None:
        self.mouse = mouse
        self.token = secrets.token_urlsafe(24)
        self._lock = threading.Lock()
        self._admission = threading.Condition()
        self._accepting = True
        self._active_handlers = 0
        owner = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                try:
                    payload = json.loads(self.rfile.readline(4096).decode("utf-8"))
                    if payload.get("token") != owner.token:
                        raise VirtualHidError("invalid virtual HID control token")
                    action = payload.get("action")
                    if action not in {"click", "button"}:
                        raise VirtualHidError("unsupported virtual HID control action")
                    with owner._admission:
                        if not owner._accepting:
                            raise VirtualHidError(
                                "virtual HID control is quiesced while the worker stops"
                            )
                        owner._active_handlers += 1
                    try:
                        with owner._lock:
                            button = str(payload.get("button", "left"))
                            if action == "click":
                                owner.mouse.click_at(
                                    int(payload["x"]),
                                    int(payload["y"]),
                                    button=button,
                                    hold=float(payload.get("hold", 0.08)),
                                    log_action=bool(payload.get("log_action", True)),
                                )
                            else:
                                x, y = payload.get("x"), payload.get("y")
                                if x is not None and y is not None:
                                    owner.mouse.move_to(int(x), int(y))
                                owner.mouse.set_button(
                                    button,
                                    bool(payload.get("pressed", False)),
                                )
                    finally:
                        with owner._admission:
                            owner._active_handlers -= 1
                            if owner._active_handlers == 0:
                                owner._admission.notify_all()
                    response = {"ok": True}
                except Exception as exc:  # noqa: BLE001 - return worker-safe error
                    response = {"ok": False, "error": str(exc)}
                self.wfile.write(
                    json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n"
                )

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.server = Server(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name="wuwa-virtual-hid-control",
            daemon=True,
        )

    def quiesce_and_release(self) -> None:
        """Stop admitting worker requests, then emit one serialized all-up."""
        with self._admission:
            self._accepting = False
            while self._active_handlers:
                self._admission.wait()
        with self._lock:
            self.mouse.release_buttons()

    def resume(self) -> None:
        """Allow a newly launched owned worker to use the shared HID again."""
        with self._admission:
            self._accepting = True
            self._admission.notify_all()

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    def start(self) -> None:
        self.thread.start()
        os.environ[CONTROL_PORT_ENV] = str(self.port)
        os.environ[CONTROL_TOKEN_ENV] = self.token
        log.info("virtual HID worker control ready on localhost port %s", self.port)

    def close(self) -> None:
        try:
            self.quiesce_and_release()
        except (OSError, VirtualHidError) as exc:
            log.warning("could not quiesce virtual HID control server: %s", exc)
        os.environ.pop(CONTROL_PORT_ENV, None)
        os.environ.pop(CONTROL_TOKEN_ENV, None)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


_active_mouse: VirtualHidMouse | None = None
_active_control: _VirtualHidControlServer | None = None


def active_virtual_mouse() -> VirtualHidMouse | None:
    """Return the workflow-owned HID within the current process, if active."""
    return _active_mouse


def release_active_mouse_buttons() -> bool:
    """Release held buttons before an owned worker is restarted or stopped."""
    mouse = active_virtual_mouse()
    if mouse is None:
        return False
    try:
        control = _active_control
        if control is not None:
            # Quiescing rejects late requests from the old worker, while the
            # admission/handler counters ensure an in-flight request finishes
            # before the all-up packet is emitted.
            control.quiesce_and_release()
        else:
            mouse.release_buttons()
    except (OSError, VirtualHidError) as exc:
        log.warning("could not release active virtual HID buttons: %s", exc)
        return False
    log.debug("released active virtual HID buttons before worker stop")
    return True


def resume_active_mouse_control() -> bool:
    """Re-open the control gate before launching the next owned worker."""
    control = _active_control
    if control is None:
        return False
    control.resume()
    return True


@contextmanager
def managed_virtual_mouse() -> Iterator[VirtualHidMouse]:
    """Reuse the workflow mouse, or create a scoped one for a recovery command."""
    global _active_control, _active_mouse
    if _active_mouse is not None:
        yield _active_mouse
        return
    with VirtualHidMouse() as mouse:
        control = _VirtualHidControlServer(mouse)
        control.start()
        _active_mouse = mouse
        _active_control = control
        try:
            yield mouse
        finally:
            _active_mouse = None
            try:
                control.close()
            finally:
                _active_control = None


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
