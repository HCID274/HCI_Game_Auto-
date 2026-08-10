"""Local virtual-HID client for non-combat recovery and daily UI actions."""

from __future__ import annotations

import json
import os
import re
import socket


def virtual_hid_request(request: dict[str, object]) -> dict[str, object]:
    port = os.environ.get("WUWA_VIRTUAL_HID_CONTROL_PORT")
    token = os.environ.get("WUWA_VIRTUAL_HID_CONTROL_TOKEN")
    if not port or not token:
        raise RuntimeError("host virtual HID control is unavailable")
    request = {"token": token, **request}
    with socket.create_connection(("127.0.0.1", int(port)), timeout=8) as client:
        client.settimeout(8)
        client.sendall(json.dumps(request).encode("utf-8") + b"\n")
        response = bytearray()
        while not response.endswith(b"\n"):
            chunk = client.recv(4096)
            if not chunk:
                break
            response.extend(chunk)
    try:
        return json.loads(response.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid virtual HID response: {response!r}") from exc


def virtual_hid_click(
    x: int,
    y: int,
    *,
    button: str = "left",
    hold: float = 0.2,
    log_action: bool = True,
) -> None:
    """Emit one bounded virtual USB-HID click through the parent workflow."""

    def send_request(target_x: int, target_y: int) -> dict[str, object]:
        return virtual_hid_request(
            {
                "action": "click",
                "x": target_x,
                "y": target_y,
                "button": button,
                "hold": float(hold),
                "log_action": log_action,
            }
        )

    payload = send_request(int(x), int(y))
    if not payload.get("ok"):
        error = str(payload.get("error") or "")
        cursor = re.search(r"cursor=\((-?\d+), (-?\d+)\)", error)
        if cursor:
            cursor_x, cursor_y = map(int, cursor.groups())
            if abs(cursor_x - x) <= 5 and abs(cursor_y - y) <= 5:
                payload = send_request(cursor_x, cursor_y)
    if not payload.get("ok"):
        raise RuntimeError(f"virtual HID click failed: {payload.get('error')}")


# Preserve the established private import name inside daily/recovery adapters.
_virtual_hid_click = virtual_hid_click
