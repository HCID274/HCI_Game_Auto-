"""Wuthering Waves Feishu card rendering and optional webhook delivery."""

from __future__ import annotations

import logging

from game_automation_core.reporting.feishu import (
    build_sectioned_card,
    send_signed_payload,
)

from wuwa_auto.reporting.models import NarrativeReport
from wuwa_auto.settings import get_secret

log = logging.getLogger(__name__)


def build_report_card(
    *, title: str, template: str, narrative: NarrativeReport
) -> dict[str, object]:
    return build_sectioned_card(
        title=title,
        template=template,
        lead=narrative.summary,
        sections=[
            ("日常", narrative.daily),
            ("周常", narrative.weekly),
            ("后续事件", narrative.followup),
            ("异常记录", narrative.issues),
        ],
    )


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
