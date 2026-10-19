"""开跑前腾内存：可用内存不够时关掉 Minecraft，返回日报要写的一行。

1017 晨 Minecraft 占 8–11 GB、16 GB 内存耗尽，星铁连崩三次，之后整屏截图一直全黑。
只关 Minecraft 的 Java 进程；Chrome、Claude、WSL 等其他大进程只记日志，不关。
"""

from __future__ import annotations

import logging
from collections import defaultdict

import psutil

log = logging.getLogger(__name__)

GB = 1024**3
# 星铁本体约 4.6 GB、三月七助手约 1 GB，鸣潮加 OK-WW 相近；低于此值才动手。
MIN_AVAILABLE_GB = 6.0
JAVA_NAMES = frozenset({"java.exe", "javaw.exe"})


def _private_bytes(process: psutil.Process) -> int:
    memory = process.info.get("memory_info")
    return int(getattr(memory, "private", 0) or getattr(memory, "rss", 0) or 0) if memory else 0


def _is_minecraft(process: psutil.Process) -> bool:
    if (process.info.get("name") or "").casefold() not in JAVA_NAMES:
        return False
    return "minecraft" in " ".join(process.info.get("cmdline") or []).casefold()


def _largest(processes: list[psutil.Process], count: int = 3) -> str:
    totals: dict[str, int] = defaultdict(int)
    for process in processes:
        totals[process.info.get("name") or "?"] += _private_bytes(process)
    ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)[:count]
    return "、".join(f"{name} {size / GB:.1f} GB" for name, size in ranked)


def free_memory_for_games(*, min_available_gb: float = MIN_AVAILABLE_GB) -> list[str]:
    """可用内存低于门槛就关 Minecraft；关了东西才返回日报行，任何异常都不影响开跑。"""
    try:
        available = psutil.virtual_memory().available / GB
        if available >= min_available_gb:
            log.info("memory preflight passed: available=%.1f GB", available)
            return []
        processes = list(psutil.process_iter(["name", "cmdline", "memory_info"]))
        targets = [process for process in processes if _is_minecraft(process)]
        if not targets:
            log.warning(
                "memory preflight: available=%.1f GB, no Minecraft to close; largest: %s",
                available,
                _largest(processes),
            )
            return []
        freed = sum(_private_bytes(process) for process in targets) / GB
        for process in targets:
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied:
                log.warning("memory preflight: cannot close pid=%s", process.pid)
        _, alive = psutil.wait_procs(targets, timeout=10)
        after = psutil.virtual_memory().available / GB
        note = (
            f"开跑前可用内存只剩 {available:.1f} GB，已关掉 Minecraft"
            f"（{len(targets) - len(alive)} 个进程，约 {freed:.1f} GB），现在可用 {after:.1f} GB"
        )
        if alive:
            note += f"；还有 {len(alive)} 个没关掉"
        log.warning("memory preflight: %s", note)
        if after < min_available_gb:
            rest = [process for process in processes if process not in targets]
            log.warning("memory preflight: still low; largest: %s", _largest(rest))
        return [note]
    except Exception:
        log.exception("memory preflight failed; continuing without it")
        return []


__all__ = ["MIN_AVAILABLE_GB", "free_memory_for_games"]
