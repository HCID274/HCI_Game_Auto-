"""Wuthering Waves adapter for the shared Windows elevation helper."""

from collections.abc import Sequence

from game_automation_core.windows.elevation import relaunch_module_elevated
from wuwa_auto.settings import PROJECT_ROOT


def relaunch_cli_elevated(cli_args: Sequence[str]) -> int:
    return relaunch_module_elevated(
        module_name="wuwa_auto",
        project_root=PROJECT_ROOT,
        cli_args=cli_args,
        hide_console=True,
    )
