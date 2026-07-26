"""Network and visible-game preflight checks."""

import ctypes
import ipaddress
import logging
import socket
import time

import psutil

from starrail_auto.m7a.config import (
    GAME_NETWORK_HOST,
    GAME_NETWORK_PORT,
    GAME_NETWORK_TIMEOUT,
    GAME_PROCESS_NAMES,
    GAME_READY_INTERVAL,
    GAME_READY_TIMEOUT,
    GAME_WINDOW_KEYWORDS,
)

log = logging.getLogger(__name__)


def visible_window_titles() -> list[str]:
    titles: list[str] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def enum_windows_proc(hwnd: int, _lparam: int) -> bool:
        if not ctypes.windll.user32.IsWindowVisible(hwnd):
            return True
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buffer, len(buffer))
        if buffer.value.strip():
            titles.append(buffer.value.strip())
        return True

    ctypes.windll.user32.EnumWindows(enum_windows_proc, 0)
    return titles


def is_game_process_running() -> bool:
    return any(
        (proc.info["name"] or "").casefold() in GAME_PROCESS_NAMES
        for proc in psutil.process_iter(["name"])
    )


def is_game_window_present() -> bool:
    return any(
        any(keyword.casefold() in title.casefold() for keyword in GAME_WINDOW_KEYWORDS)
        for title in visible_window_titles()
    )


def is_game_network_ready(*, resolver: object = socket.getaddrinfo) -> bool:
    """Reject TUN fake-DNS ranges before M7A launches the game."""
    try:
        addresses = resolver(GAME_NETWORK_HOST, GAME_NETWORK_PORT, type=socket.SOCK_STREAM)
    except OSError as exc:
        log.error("game DNS preflight failed for %s: %s", GAME_NETWORK_HOST, exc)
        return False
    resolved: list[str] = []
    for address in addresses:
        ip = address[4][0]
        if ip in resolved:
            continue
        resolved.append(ip)
        try:
            if ipaddress.ip_address(ip).is_global:
                log.info("game DNS preflight passed: host=%s ip=%s", GAME_NETWORK_HOST, ip)
                return True
        except ValueError:
            continue
    log.error("game DNS preflight rejected non-public addresses: %s", resolved)
    return False


def check_game_network() -> bool:
    if not is_game_network_ready():
        return False
    try:
        with socket.create_connection(
            (GAME_NETWORK_HOST, GAME_NETWORK_PORT),
            timeout=GAME_NETWORK_TIMEOUT,
        ):
            log.info("game TCP preflight passed: %s:%d", GAME_NETWORK_HOST, GAME_NETWORK_PORT)
            return True
    except OSError as exc:
        log.error("game TCP preflight failed: %s", exc)
        return False


def wait_for_game_ready(
    timeout: int = GAME_READY_TIMEOUT,
    *,
    process_check: object = is_game_process_running,
    window_check: object = is_game_window_present,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process_check() and window_check():
            log.info("game process and visible window are ready")
            return True
        time.sleep(GAME_READY_INTERVAL)
    return False
