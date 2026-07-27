"""Top-level scheduled workflows for M7A-backed tasks."""

import logging
from datetime import datetime

from starrail_auto.integrations.feishu import (
    notify_starrail_failure,
    notify_starrail_success,
)
from starrail_auto.m7a.config import (
    DEFAULT_TIMEOUTS,
    EXIT_GAME_NETWORK_FAILED,
    EXIT_OK,
    EXIT_UU_FAILED,
)
from starrail_auto.m7a.environment import check_game_network
from starrail_auto.m7a.models import RunResult
from starrail_auto.m7a.runner import run_m7a
from starrail_auto.reporting.service import report_main_run
from starrail_auto.settings import LOGS_DIR
from starrail_auto.uu.errors import UuStartupError, UuStartupFinalError
from starrail_auto.uu.service import ensure_uu_connected

log = logging.getLogger(__name__)


def _setup_logging() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(getattr(handler, "_starrail_file", False) for handler in root.handlers):
        file_handler = logging.FileHandler(
            LOGS_DIR / f"{datetime.now():%Y-%m-%d}.log",
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler._starrail_file = True  # type: ignore[attr-defined]
        root.addHandler(file_handler)
    if not any(getattr(handler, "_starrail_console", False) for handler in root.handlers):
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler._starrail_console = True  # type: ignore[attr-defined]
        root.addHandler(console_handler)


def _run(task: str, timeout: int) -> RunResult:
    if not check_game_network():
        return RunResult(EXIT_GAME_NETWORK_FAILED, stage="网络代理")
    retries = 0
    try:
        retries = ensure_uu_connected()
    except (UuStartupFinalError, UuStartupError) as exc:
        retries = exc.restarts_used
        log.error("UU acceleration failed: %s", exc)
        return RunResult(EXIT_UU_FAILED, stage="UU", retries=retries)
    except RuntimeError as exc:
        log.error("UU acceleration failed: %s", exc)
        return RunResult(EXIT_UU_FAILED, stage="UU", retries=retries)
    if not check_game_network():
        return RunResult(EXIT_GAME_NETWORK_FAILED, stage="网络代理", retries=retries)
    return run_m7a(task, timeout, uu_retries=retries)


def execute_task(task: str, timeout: int | None = None) -> int:
    _setup_logging()
    result = _run(task, timeout or DEFAULT_TIMEOUTS.get(task, 1800))
    if task == "main":
        try:
            report_main_run(
                log_path=result.report_log_path,
                offset=result.report_log_offset,
                exit_code=result.exit_code,
                stage=result.stage,
                retries=result.retries,
            )
        except Exception:
            log.exception("final report service failed")
            _send_short_notification(result)
    else:
        _send_short_notification(result)
    log.info("workflow finished with code %d", result.exit_code)
    return result.exit_code


def _send_short_notification(result: RunResult) -> None:
    if result.exit_code == EXIT_OK:
        notify_starrail_success(result.retries)
    else:
        notify_starrail_failure(result.stage or "未知", result.retries)


def run_daily(timeout: int | None = None) -> int:
    return execute_task("main", timeout)


def run_universe(timeout: int | None = None) -> int:
    return execute_task("universe", timeout)
