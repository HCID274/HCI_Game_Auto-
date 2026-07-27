"""Feishu webhook notifications for Star Rail automation."""

import logging
from dataclasses import dataclass
from datetime import datetime

from game_automation_core.reporting.feishu import (
    build_sectioned_card,
    make_signature,
    send_signed_payload,
)

from starrail_auto.settings import get_secret

REQUEST_TIMEOUT = 8

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeishuConfig:
    webhook_url: str
    secret: str
    enabled: bool = True


def _load_config() -> FeishuConfig | None:
    """Load webhook secrets from the project .env file."""
    webhook_url = get_secret("FEISHU_WEBHOOK_URL")
    secret = get_secret("FEISHU_WEBHOOK_SECRET")
    if not webhook_url or not secret:
        log.warning("Feishu notification environment is incomplete")
        return None

    return FeishuConfig(webhook_url=webhook_url, secret=secret)


def _make_signature(timestamp: int, secret: str) -> str:
    """Build Feishu custom bot signature."""
    return make_signature(timestamp, secret)


def _send_payload(payload: dict[str, object]) -> bool:
    """Sign and send one custom-bot payload."""
    config = _load_config()
    if config is None:
        return False

    return send_signed_payload(
        payload,
        webhook_url=config.webhook_url,
        secret=config.secret,
        timeout=REQUEST_TIMEOUT,
    )


def _send_text(text: str) -> bool:
    """Send a concise text message. Failures are logged but not raised."""
    return _send_payload(
        {
            "msg_type": "text",
            "content": {"text": text},
        }
    )


def send_starrail_report_card(
    *,
    title: str,
    template: str,
    daily: str,
    routine_tasks: list[str],
    other_tasks: list[str],
    current_task: str,
    issues: list[str],
    training_todos: list[str] | None = None,
    reminders: list[str] | None = None,
) -> bool:
    """Render the stable report layout as a Feishu interactive card."""
    return _send_payload(
        build_sectioned_card(
            title=title,
            template=template,
            lead=f"**每日实训**\n{daily}",
            sections=[
                ("日常", routine_tasks),
                ("后续完成", other_tasks),
                ("当前任务", current_task),
                ("异常记录", issues),
                ("养成计划待办", training_todos or []),
                ("提醒", reminders or []),
            ],
        )
    )


def notify_starrail_success(retries: int, finished_at: datetime | None = None) -> None:
    """Notify successful daily completion."""
    ts = (finished_at or datetime.now()).strftime("%m-%d %H:%M")
    _send_text(f"✅️ 星铁完成 {ts} 重试{retries}")


def notify_starrail_failure(
    stage: str,
    retries: int,
    finished_at: datetime | None = None,
) -> None:
    """Notify final failure with stage and retry count."""
    ts = (finished_at or datetime.now()).strftime("%m-%d %H:%M")
    _send_text(f"❌️ 星铁失败 {stage} {ts} 重试{retries}")
