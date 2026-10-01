"""Version-day deferral: weekday mornings update only, evenings rerun dailies.

Agreed 2026-08-20: on a weekday the version-day morning chain downloads the
new client and stops (login servers are closed anyway); the daily rerun is
scheduled for the same evening.  On weekends the chain downloads and then
continues straight into the daily workflow.
"""

from __future__ import annotations

import getpass
import logging
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from wuwa_auto.settings import PROJECT_ROOT

log = logging.getLogger(__name__)

EVENING_RERUN_TASK_NAME = "HCID274_GameAutomation_WuwaVersionDayEvening"
EVENING_RERUN_SCRIPT = PROJECT_ROOT / "scripts" / "version_day_evening_rerun.ps1"
EVENING_RERUN_HOUR = 20
# Version-day mornings defer; evenings and weekends continue directly.
DEFER_CUTOFF_HOUR = 17


def should_defer_daily_to_evening(now: datetime) -> bool:
    """Weekday mornings defer the daily rerun to the same evening."""
    return now.weekday() < 5 and now.hour < DEFER_CUTOFF_HOUR


def evening_rerun_at(now: datetime) -> datetime:
    rerun = now.replace(hour=EVENING_RERUN_HOUR, minute=0, second=0, microsecond=0)
    if rerun <= now:
        rerun += timedelta(days=1)
    return rerun


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def build_registration_script(rerun_at: datetime, user: str) -> str:
    return (
        "$a = New-ScheduledTaskAction -Execute 'powershell.exe' "
        f"-Argument {_ps_quote('-NoProfile -ExecutionPolicy Bypass -File ' + str(EVENING_RERUN_SCRIPT))}; "
        f"$t = New-ScheduledTaskTrigger -Once -At '{rerun_at:%Y-%m-%dT%H:%M:%S}'; "
        f"$p = New-ScheduledTaskPrincipal -UserId { _ps_quote(user)} "
        "-LogonType Interactive -RunLevel Highest; "
        f"Register-ScheduledTask -TaskName { _ps_quote(EVENING_RERUN_TASK_NAME)} "
        "-Action $a -Trigger $t -Principal $p -Force | Out-Null"
    )


def register_evening_rerun(now: datetime) -> datetime:
    """Register the one-shot evening rerun task and return its start time.

    The task script deletes its own registration before starting the
    orchestrator, so the trigger can never fire twice.
    """
    rerun_at = evening_rerun_at(now)
    script = build_registration_script(rerun_at, getpass.getuser())
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "evening rerun task registration failed: "
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )
    log.info(
        "version-day evening rerun registered: task=%s at %s",
        EVENING_RERUN_TASK_NAME,
        rerun_at.isoformat(),
    )
    return rerun_at
