"""Feishu webhook notifications for Star Rail automation."""

import base64
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from urllib import request
from urllib.error import HTTPError, URLError

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
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(string_to_sign, b"", digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _send_payload(payload: dict[str, object]) -> bool:
    """Sign and send one custom-bot payload."""
    config = _load_config()
    if config is None:
        return False

    timestamp = int(time.time())
    payload = dict(payload)
    payload["timestamp"] = str(timestamp)
    payload["sign"] = _make_signature(timestamp, config.secret)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        config.webhook_url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            response_body = resp.read().decode("utf-8", errors="replace")
            log.info("Feishu notification sent: status=%s body=%s", resp.status, response_body)
            return True
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        log.warning("Feishu notification failed: %s", exc)
        return False


def _send_text(text: str) -> bool:
    """Send a concise text message. Failures are logged but not raised."""
    return _send_payload(
        {
            "msg_type": "text",
            "content": {"text": text},
        }
    )


def _numbered_lines(items: list[str]) -> str:
    return "\n".join(f"{index}. {item}" for index, item in enumerate(items, start=1))


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
    elements: list[dict[str, object]] = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**每日实训**\n{daily}",
            },
        }
    ]

    if routine_tasks:
        elements.extend(
            [
                {"tag": "hr"},
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**日常**\n{_numbered_lines(routine_tasks)}",
                    },
                },
            ]
        )
    if other_tasks:
        elements.extend(
            [
                {"tag": "hr"},
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**后续完成**\n{_numbered_lines(other_tasks)}",
                    },
                },
            ]
        )
    if current_task:
        elements.extend(
            [
                {"tag": "hr"},
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**当前任务**\n{current_task}",
                    },
                },
            ]
        )
    if issues:
        elements.extend(
            [
                {"tag": "hr"},
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**异常记录**\n{_numbered_lines(issues)}",
                    },
                },
            ]
        )

    if training_todos:
        elements.extend(
            [
                {"tag": "hr"},
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": (
                            "**养成计划待办**\n"
                            f"{_numbered_lines(training_todos)}"
                        ),
                    },
                },
            ]
        )

    if reminders:
        elements.extend(
            [
                {"tag": "hr"},
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**提醒**\n{_numbered_lines(reminders)}",
                    },
                },
            ]
        )

    return _send_payload(
        {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": template,
                    "title": {"tag": "plain_text", "content": title},
                },
                "elements": elements,
            },
        }
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
