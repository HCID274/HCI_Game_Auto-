"""Single command-line interface for scheduled and manual automation."""

import argparse
from pathlib import Path
from typing import Sequence

from starrail_auto.reporting.training_plan import load_training_plan, set_goal_status
from starrail_auto.uu.service import execute_action
from starrail_auto.windows.elevation import relaunch_cli_elevated
from starrail_auto.workflows.cleanup import execute_cleanup
from starrail_auto.workflows.daily import run_daily, run_universe


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="starrail-auto")
    parser.add_argument("--already-elevated", action="store_true", help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command", required=True)

    daily = commands.add_parser("daily", help="run the complete daily workflow")
    daily.add_argument("--timeout", type=int)

    universe = commands.add_parser("universe", help="run the optional universe task")
    universe.add_argument("--timeout", type=int)

    cleanup = commands.add_parser("cleanup", help="close game/M7A and stop UU acceleration")
    cleanup.add_argument("--delay", type=int, default=0)
    cleanup.add_argument("--log-file", type=Path)

    uu = commands.add_parser("uu", help="control UU accelerator")
    uu.add_argument(
        "action",
        nargs="?",
        default="start",
        choices=["start", "disconnect", "minimize", "stop"],
    )
    uu.add_argument("--log-file", type=Path)

    plan = commands.add_parser("plan", help="manage character training goals")
    plan_commands = plan.add_subparsers(dest="plan_command", required=True)
    plan_commands.add_parser("list")
    complete = plan_commands.add_parser("complete")
    complete.add_argument("goal_id")
    complete.add_argument("--evidence", default="人工确认完成")
    reopen = plan_commands.add_parser("reopen")
    reopen.add_argument("goal_id")

    elevate = commands.add_parser("elevate", help="rerun a command with UAC elevation")
    elevate.add_argument("arguments", nargs=argparse.REMAINDER)
    return parser


def _print_plan() -> None:
    for goal in load_training_plan().goals:
        marker = "x" if goal.completed else " "
        print(f"[{marker}] {goal.goal_id}: {goal.character} / {goal.category}")


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "daily":
        return run_daily(args.timeout)
    if args.command == "universe":
        return run_universe(args.timeout)
    if args.command == "cleanup":
        return execute_cleanup(args.delay, args.log_file)
    if args.command == "uu":
        return execute_action(args.action, args.log_file)
    if args.command == "elevate":
        if not args.arguments:
            raise SystemExit("elevate requires a command")
        return relaunch_cli_elevated(args.arguments)
    if args.plan_command == "complete":
        set_goal_status(
            args.goal_id,
            completed=True,
            evidence=args.evidence,
        )
    elif args.plan_command == "reopen":
        set_goal_status(args.goal_id, completed=False, evidence="")
    _print_plan()
    return 0
