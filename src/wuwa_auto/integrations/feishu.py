"""Wuthering Waves Feishu card rendering and optional webhook delivery."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from urllib import request
from urllib.error import HTTPError, URLError

from wuwa_auto.reporting.models import NarrativeReport
from wuwa_auto.settings import get_secret

log = logging.getLogger(__name__)


def _numbered(items: list[str]) -> str:
    return "\n".join(f"{index}. {item}" for index, item in enumerate(items, 1))


def build_report_card(
    *, title: str, template: str, narrative: NarrativeReport
) -> dict[str, object]:
    elements: list[dict[str, object]] = [
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": narrative.summary},
        }
    ]
    for heading, items in (
        ("日常", narrative.daily),
        ("周常", narrative.weekly),
        ("后续事件", narrative.followup),
        ("异常记录", narrative.issues),
    ):
        if not items:
            continue
        elements.extend(
            [
                {"tag": "hr"},
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**{heading}**\n{_numbered(items)}",
                    },
                },
            ]
        )
    return {
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
    timestamp = int(time.time())
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(string_to_sign, b"", hashlib.sha256).digest()
    body_payload = dict(payload)
    body_payload["timestamp"] = str(timestamp)
    body_payload["sign"] = base64.b64encode(digest).decode("utf-8")
    req = request.Request(
        url,
        data=json.dumps(body_payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=8) as response:
            log.info("Wuwa Feishu notification sent: status=%s", response.status)
            return True
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        log.warning("Wuwa Feishu notification failed: %s", exc)
        return False
