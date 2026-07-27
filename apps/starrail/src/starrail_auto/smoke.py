"""Read-only substitute for the M7A core task during integration acceptance."""

from __future__ import annotations

from datetime import datetime

from game_automation_core.reporting.archive import write_json_archive
from game_automation_core.reporting.feishu import build_sectioned_card
from starrail_auto.m7a.config import M7A_LAUNCHER, M7A_LOG_DIR
from starrail_auto.settings import RUNTIME_DIR, get_secret
from starrail_auto.windows.desktop_guard import describe_window, require_desktop_ready


def run_smoke() -> int:
    foreground = require_desktop_ready()
    missing = [str(path) for path in (M7A_LAUNCHER, M7A_LOG_DIR) if not path.exists()]
    if missing:
        raise RuntimeError(f"Star Rail smoke preflight paths missing: {missing}")
    secrets = {
        name: bool(get_secret(name))
        for name in ("DEEPSEEK_API_KEY", "FEISHU_WEBHOOK_URL", "FEISHU_WEBHOOK_SECRET")
    }
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    card = build_sectioned_card(
        title="星铁集成预演",
        template="blue",
        lead="基础设施已通过；M7A 核心任务未调用。",
        sections=[("边界", ["桌面可交互", "M7A 路径可用", "飞书仅本地预演"])],
    )
    path = RUNTIME_DIR / "smoke" / f"{run_id}.preview.json"
    write_json_archive(
        path,
        {
            "run_id": run_id,
            "app": "starrail",
            "core_command_invoked": False,
            "foreground": describe_window(foreground),
            "m7a_launcher": str(M7A_LAUNCHER),
            "secrets_present": secrets,
            "feishu_card": card,
        },
    )
    print(f"Star Rail integration substitute passed: {path}")
    return 0
