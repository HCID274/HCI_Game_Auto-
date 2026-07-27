"""Current-run M7A log boundaries, outcome parsing, and evidence."""

import logging
import time
from datetime import datetime
from pathlib import Path

from starrail_auto.m7a.config import (
    DEBUG_DIR,
    EXIT_DAILY_VALIDATION_FAILED,
    EXIT_GAME_NETWORK_FAILED,
    EXIT_M7A_EXIT_NONZERO,
    EXIT_OK,
    EXIT_WATCHDOG_CPU_IDLE,
    EXIT_WATCHDOG_HARD_TIMEOUT,
    EXIT_WATCHDOG_LOG_STALLED,
    M7A_COMPLETION_VALIDATION_INTERVAL,
    M7A_COMPLETION_VALIDATION_TIMEOUT,
    M7A_DAILY_BLOCKER_LABELS,
    M7A_DAILY_BLOCKER_PATTERN,
    M7A_DAILY_COMPLETION_MARKER,
    M7A_DAILY_INCOMPLETE_MARKER,
    M7A_DAILY_SCORE_PATTERN,
    M7A_LOG_DIR,
    M7A_RUN_STOP_MARKER,
)
from starrail_auto.m7a.models import M7ALogCheckpoint

log = logging.getLogger(__name__)


def get_latest_m7a_log() -> Path | None:
    if not M7A_LOG_DIR.exists():
        return None
    logs = sorted(M7A_LOG_DIR.glob("*.log"), key=lambda path: path.stat().st_mtime, reverse=True)
    return logs[0] if logs else None


def capture_log_checkpoint() -> M7ALogCheckpoint:
    path = M7A_LOG_DIR / f"{datetime.now():%Y-%m-%d}.log"
    offset = path.stat().st_size if path.exists() else 0
    log.info("captured M7A log checkpoint: path=%s offset=%d", path, offset)
    return M7ALogCheckpoint(path=path, offset=offset)


def read_log_since(checkpoint: M7ALogCheckpoint) -> str:
    if not checkpoint.path.exists():
        return ""
    with checkpoint.path.open("rb") as handle:
        size = checkpoint.path.stat().st_size
        handle.seek(checkpoint.offset if size >= checkpoint.offset else 0)
        return handle.read().decode("utf-8", errors="replace")


def wait_for_daily_completion(checkpoint: M7ALogCheckpoint) -> bool:
    deadline = time.monotonic() + M7A_COMPLETION_VALIDATION_TIMEOUT
    while time.monotonic() < deadline:
        if M7A_DAILY_COMPLETION_MARKER in read_log_since(checkpoint):
            log.info("daily completion validated from current M7A log")
            return True
        time.sleep(M7A_COMPLETION_VALIDATION_INTERVAL)
    log.error(
        "daily completion validation failed: marker=%s path=%s offset=%d",
        M7A_DAILY_COMPLETION_MARKER,
        checkpoint.path,
        checkpoint.offset,
    )
    return False


def daily_run_outcome(checkpoint: M7ALogCheckpoint) -> str | None:
    content = read_log_since(checkpoint)
    if M7A_DAILY_COMPLETION_MARKER in content:
        return "completed"
    if M7A_DAILY_INCOMPLETE_MARKER in content:
        return "incomplete"
    return None


def main_run_outcome(checkpoint: M7ALogCheckpoint) -> str | None:
    """Resolve main only after failure or M7A's own final stop boundary."""
    content = read_log_since(checkpoint)
    if M7A_DAILY_COMPLETION_MARKER in content:
        return "completed" if M7A_RUN_STOP_MARKER in content else None
    if M7A_DAILY_INCOMPLETE_MARKER in content:
        return "incomplete"
    if M7A_RUN_STOP_MARKER in content:
        return "incomplete"
    return None


def summarize_daily_failure(checkpoint: M7ALogCheckpoint) -> str:
    content = read_log_since(checkpoint)
    scores = M7A_DAILY_SCORE_PATTERN.findall(content)
    score = f"{scores[-1][0]}/{scores[-1][1]}" if scores else "未达标"
    blockers: list[str] = []
    for raw_blocker in M7A_DAILY_BLOCKER_PATTERN.findall(content):
        for marker, label in M7A_DAILY_BLOCKER_LABELS:
            if marker in raw_blocker and label not in blockers:
                blockers.append(label)
                break
    summary = f"实训{score}"
    if blockers:
        summary = f"{summary} 卡{'/'.join(blockers)}"
    return summary


def stage_for_exit_code(exit_code: int, checkpoint: M7ALogCheckpoint) -> str:
    if exit_code == EXIT_DAILY_VALIDATION_FAILED:
        return summarize_daily_failure(checkpoint)
    return {
        EXIT_OK: "",
        EXIT_M7A_EXIT_NONZERO: "M7A",
        EXIT_GAME_NETWORK_FAILED: "网络代理",
        EXIT_WATCHDOG_HARD_TIMEOUT: "超时",
        EXIT_WATCHDOG_CPU_IDLE: "CPU",
        EXIT_WATCHDOG_LOG_STALLED: "日志",
    }.get(exit_code, "看门狗")


def capture_failure_evidence(
    reason: str,
    checkpoint: M7ALogCheckpoint | None,
) -> None:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshot_path: Path | None = DEBUG_DIR / f"m7a_failure_{stamp}.png"
    log_path: Path | None = DEBUG_DIR / f"m7a_failure_{stamp}.log"
    try:
        from PIL import ImageGrab

        ImageGrab.grab(all_screens=True).save(screenshot_path)
    except Exception as exc:  # pragma: no cover - interactive desktop required
        log.warning("failed to capture watchdog screenshot: %s", exc)
        screenshot_path = None
    try:
        content = read_log_since(checkpoint) if checkpoint else ""
        log_path.write_text(f"reason={reason}\n\n{content}", encoding="utf-8")
    except OSError as exc:
        log.warning("failed to save watchdog log evidence: %s", exc)
        log_path = None
    log.warning(
        "failure evidence preserved: reason=%s screenshot=%s log=%s",
        reason,
        screenshot_path,
        log_path,
    )
