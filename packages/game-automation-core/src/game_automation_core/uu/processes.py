"""Exact-path UU process discovery, launch, and bounded termination."""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import psutil

from game_automation_core.uu.errors import UuStartupError

log = logging.getLogger(__name__)


def _normalized(path: str | os.PathLike[str]) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


@dataclass(frozen=True, slots=True)
class UuProcessSpec:
    executable: Path
    managed_names: frozenset[str]
    primary_names: frozenset[str] = frozenset({"uu.exe"})

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "managed_names",
            frozenset(name.casefold() for name in self.managed_names),
        )
        object.__setattr__(
            self,
            "primary_names",
            frozenset(name.casefold() for name in self.primary_names),
        )


class UuProcessController:
    def __init__(self, spec: UuProcessSpec) -> None:
        self.spec = spec

    def managed_processes(self) -> list[psutil.Process]:
        processes: list[psutil.Process] = []
        for process in psutil.process_iter(["name", "pid", "exe"]):
            name = (process.info.get("name") or "").casefold()
            if name not in self.spec.managed_names:
                continue
            # The configured launcher is authoritative when the process exposes
            # an executable path. Trusted sibling components are admitted only
            # by their exact executable names, never command-line substrings.
            executable = process.info.get("exe") or ""
            if (
                name == self.spec.executable.name.casefold()
                and executable
                and _normalized(executable) != _normalized(self.spec.executable)
            ):
                continue
            processes.append(process)
        return processes

    def is_running(self) -> bool:
        return any(
            (process.info.get("name") or "").casefold() in self.spec.primary_names
            for process in psutil.process_iter(["name"])
        )

    def is_any_running(self) -> bool:
        return bool(self.managed_processes())

    def primary_pids(self) -> frozenset[int]:
        return frozenset(
            process.pid
            for process in self.managed_processes()
            if process.name().casefold() in self.spec.primary_names
        )

    def start(self) -> None:
        executable = self.spec.executable
        if not executable.is_file():
            raise UuStartupError(
                "start_uu_process",
                f"UU executable not found: {executable}",
                retryable=False,
            )
        try:
            subprocess.Popen([str(executable)], cwd=executable.parent)
        except OSError as exc:
            raise UuStartupError(
                "start_uu_process",
                f"failed to launch UU: {exc}",
                retryable=False,
            ) from exc

    def terminate(self, *, graceful_timeout: float = 5, kill_timeout: float = 3) -> bool:
        targets = self.managed_processes()
        for process in targets:
            try:
                process.terminate()
                log.info("terminated %s (pid=%d)", process.name(), process.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
                log.warning("cannot terminate pid=%d: %s", process.pid, exc)
        _, alive = psutil.wait_procs(targets, timeout=graceful_timeout)
        for process in alive:
            try:
                process.kill()
                log.warning("force-killed %s (pid=%d)", process.name(), process.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        _, still_alive = psutil.wait_procs(alive, timeout=kill_timeout)
        return not still_alive and not self.managed_processes()
