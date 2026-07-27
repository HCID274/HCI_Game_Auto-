"""Read-only substitute for the OK-WW core task during integration acceptance."""

from __future__ import annotations

from datetime import datetime

from game_automation_core.reporting.archive import write_json_archive
from game_automation_core.reporting.feishu import build_sectioned_card
from wuwa_auto.input.driver import driver_status
from wuwa_auto.okww.config import validate_daily_configuration
from wuwa_auto.settings import RUNTIME_DIR, get_secret
from wuwa_auto.windows.desktop_guard import describe_window, require_desktop_ready


def run_smoke() -> int:
    foreground = require_desktop_ready()
    ok_config = validate_daily_configuration()
    hid = driver_status()
    if not hid.installed:
        raise RuntimeError("Wuwa integration smoke requires the installed virtual HID driver")
    secrets = {
        name: bool(get_secret(name))
        for name in (
            "DEEPSEEK_API_KEY",
            "WUWA_FEISHU_WEBHOOK_URL",
            "WUWA_FEISHU_WEBHOOK_SECRET",
        )
    }
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    card = build_sectioned_card(
        title="鸣潮集成预演",
        template="blue",
        lead="基础设施已通过；OK-WW 核心任务未调用。",
        sections=[
            ("边界", ["桌面可交互", "OK-WW 配置有效", "虚拟 HID 驱动正常", "飞书仅本地预演"]),
        ],
    )
    path = RUNTIME_DIR / "smoke" / f"{run_id}.preview.json"
    write_json_archive(
        path,
        {
            "run_id": run_id,
            "app": "wuwa",
            "core_command_invoked": False,
            "foreground": describe_window(foreground),
            "ok_config": ok_config,
            "virtual_hid": hid.to_dict(),
            "secrets_present": secrets,
            "feishu_card": card,
        },
    )
    print(f"Wuwa integration substitute passed: {path}")
    return 0
