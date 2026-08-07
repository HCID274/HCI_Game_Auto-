"""Unified command line interface."""

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Sequence

from wuwa_auto.daily import (
    run_daily_workflow,
    run_farm_echo_workflow,
    run_weekly_garden_workflow,
)
from wuwa_auto.okww.runner import OkRunResult, preflight_daily_task, run_daily_task
from wuwa_auto.settings import LOGS_DIR, RUNS_DIR
from wuwa_auto.uu.service import execute_action
from wuwa_auto.windows.elevation import relaunch_cli_elevated


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wuwa-auto")
    parser.add_argument("--already-elevated", action="store_true", help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("smoke", help="validate integration without starting OK-WW")

    uu = commands.add_parser("uu", help="inspect or control UU")
    uu.add_argument(
        "action",
        choices=["inspect", "start", "disconnect", "minimize", "stop"],
    )

    okww = commands.add_parser("ok", help="validate or run OK-WW DailyTask")
    okww.add_argument(
        "action",
        choices=[
            "preflight",
            "run",
            "stop-launcher",
            "stop-worker",
            "stop-game",
            "probe-connect",
            "reset-input",
            "probe-windowed",
            "recover-garden",
        ],
    )

    commands.add_parser("daily", help="ensure UU, then run the OK-WW daily chain")
    commands.add_parser("farm-echo", help="run only OK-WW FarmEchoTask")
    commands.add_parser("weekly-garden", help="run only OK-WW GardenTask")
    commands.add_parser(
        "cleanup", help="close Wuwa/OK, disconnect Wuwa acceleration, then exit UU"
    )

    client = commands.add_parser("client", help="inspect or prepare official launcher")
    client.add_argument("action", choices=["prepare", "stop-launcher"])

    report = commands.add_parser("report", help="rebuild a local report preview")
    report.add_argument(
        "run_id",
        nargs="?",
        default="latest",
        help="runtime run id, or latest",
    )

    input_device = commands.add_parser(
        "input", help="manage the fully local virtual USB HID mouse"
    )
    input_device.add_argument(
        "action",
        choices=["status", "prepare", "install-driver", "probe"],
    )

    elevate = commands.add_parser("elevate", help="rerun a command with UAC")
    elevate.add_argument("arguments", nargs=argparse.REMAINDER)
    return parser


def _configure_logging() -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    path = LOGS_DIR / f"wuwa-auto_{datetime.now():%Y-%m-%d}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(path, encoding="utf-8"),
        ],
    )
    return path


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "elevate":
        if not args.arguments:
            raise SystemExit("elevate requires a command")
        return relaunch_cli_elevated(args.arguments)

    _configure_logging()
    if args.command == "smoke":
        from wuwa_auto.smoke import run_smoke

        return run_smoke()
    if args.command == "uu":
        return execute_action(args.action)
    if args.command == "input":
        if args.action == "status":
            from wuwa_auto.input.driver import driver_status

            logging.getLogger(__name__).info(
                "virtual HID status: %s",
                json.dumps(driver_status().to_dict(), ensure_ascii=False),
            )
            return 0
        if args.action == "prepare":
            from wuwa_auto.input.driver import prepare_usbip_installer
            from wuwa_auto.input.viiper import ensure_viiper_binary

            logging.getLogger(__name__).info(
                "prepared virtual HID dependencies: driver=%s viiper=%s",
                prepare_usbip_installer(),
                ensure_viiper_binary(),
            )
            return 0
        if args.action == "install-driver":
            from wuwa_auto.input.driver import install_usbip_driver

            logging.getLogger(__name__).info(
                "virtual HID driver installed: %s",
                json.dumps(install_usbip_driver().to_dict(), ensure_ascii=False),
            )
            return 0
        from wuwa_auto.input.viiper import probe_virtual_mouse

        logging.getLogger(__name__).info(
            "virtual HID probe passed: %s",
            json.dumps(probe_virtual_mouse(), ensure_ascii=False),
        )
        return 0
    if args.command == "ok":
        if args.action == "probe-windowed":
            from wuwa_auto.okww.recovery import probe_windowed_claim

            probe_windowed_claim()
            return 0
        if args.action == "reset-input":
            from wuwa_auto.okww.recovery import reset_game_mouse_capture

            reset_game_mouse_capture()
            return 0
        if args.action == "probe-connect":
            from wuwa_auto.okww.recovery import probe_login_connect

            probe_login_connect()
            return 0
        if args.action == "recover-garden":
            from wuwa_auto.okww.recovery import recover_garden_entry

            recover_garden_entry()
            return 0
        if args.action == "stop-launcher":
            from wuwa_auto.okww.runner import stop_pyappify_launchers

            return stop_pyappify_launchers()
        if args.action == "stop-worker":
            from wuwa_auto.okww.runner import stop_daily_workers

            return stop_daily_workers()
        if args.action == "stop-game":
            from wuwa_auto.okww.runner import stop_wuthering_game

            return stop_wuthering_game()
        if args.action == "preflight":
            preflight_daily_task()
            logging.getLogger(__name__).info("OK-WW DailyTask preflight passed")
            return 0
        return run_daily_task().exit_code
    if args.command == "daily":
        return run_daily_workflow()
    if args.command == "farm-echo":
        return run_farm_echo_workflow()
    if args.command == "weekly-garden":
        return run_weekly_garden_workflow()
    if args.command == "cleanup":
        from wuwa_auto.cleanup import cleanup_after_run

        return 0 if cleanup_after_run(acceleration_was_connected=True).completed else 2
    if args.command == "client":
        if args.action == "stop-launcher":
            from wuwa_auto.client.launcher import stop_client_launchers

            return 0 if stop_client_launchers() >= 0 else 1
        from wuwa_auto.client.launcher import ensure_client_ready
        from wuwa_auto.input.viiper import managed_virtual_mouse

        with managed_virtual_mouse() as mouse:
            result = ensure_client_ready(mouse)
        logging.getLogger(__name__).info("client preparation result: %s", result)
        return 0
    if args.command == "report":
        from wuwa_auto.reporting.service import report_run

        if args.run_id == "latest":
            candidates = sorted(
                path for path in RUNS_DIR.iterdir()
                if path.is_dir() and (path / "result.json").is_file()
            )
            if not candidates:
                raise SystemExit("no archived Wuwa run was found")
            run_dir = candidates[-1]
        else:
            run_dir = RUNS_DIR / args.run_id
        result_path = run_dir / "result.json"
        if not result_path.is_file():
            raise SystemExit(f"run result not found: {result_path}")
        result = OkRunResult(**json.loads(result_path.read_text(encoding="utf-8")))
        path = report_run(result, allow_send=False)
        logging.getLogger(__name__).info("report preview written: %s", path)
        return 0
    raise SystemExit(f"unsupported command: {args.command}")
