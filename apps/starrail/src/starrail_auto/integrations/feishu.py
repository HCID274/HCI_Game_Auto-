"""Feishu webhook delivery for Star Rail cards."""

import logging

from game_automation_core.reporting.feishu import send_signed_payload

from starrail_auto.settings import get_secret

REQUEST_TIMEOUT = 8

log = logging.getLogger(__name__)


def send_card(payload: dict[str, object]) -> bool:
    """Sign and send one card; failures are logged and never raised."""
    webhook_url = get_secret("FEISHU_WEBHOOK_URL")
    secret = get_secret("FEISHU_WEBHOOK_SECRET")
    if not webhook_url or not secret:
        log.warning("Feishu notification environment is incomplete")
        return False
    return send_signed_payload(
        payload,
        webhook_url=webhook_url,
        secret=secret,
        timeout=REQUEST_TIMEOUT,
    )
