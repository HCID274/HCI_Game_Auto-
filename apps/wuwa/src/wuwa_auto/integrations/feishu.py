"""鸣潮专用飞书机器人；真实发送默认关闭，需在 .env 里显式打开。"""

from __future__ import annotations

import logging

from game_automation_core.reporting.feishu import send_signed_payload

from wuwa_auto.settings import get_secret

log = logging.getLogger(__name__)


def _enabled() -> bool:
    return get_secret("WUWA_FEISHU_SEND_ENABLED").casefold() in {
        "1", "true", "yes", "on"
    }


def send_report_card(payload: dict[str, object]) -> bool:
    if not _enabled():
        log.info("Wuwa Feishu real sending is disabled; preview only")
        return False
    url = get_secret("WUWA_FEISHU_WEBHOOK_URL")
    secret = get_secret("WUWA_FEISHU_WEBHOOK_SECRET")
    if not url or not secret:
        log.warning("Wuwa Feishu environment is incomplete")
        return False
    return send_signed_payload(payload, webhook_url=url, secret=secret, timeout=8)
