"""Reusable Feishu custom-bot transport and interactive-card primitives."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from urllib import request
from urllib.error import HTTPError, URLError

log = logging.getLogger(__name__)


def make_signature(timestamp: int, secret: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(string_to_sign, b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_signed_payload(
    payload: dict[str, object],
    *,
    webhook_url: str,
    secret: str,
    timeout: float = 8,
) -> bool:
    timestamp = int(time.time())
    body_payload = dict(payload)
    body_payload["timestamp"] = str(timestamp)
    body_payload["sign"] = make_signature(timestamp, secret)
    # 发送失败只返回 False，绝不抛出：汇报出错不能改变任务本身的退出码。
    try:
        req = request.Request(
            webhook_url,
            data=json.dumps(body_payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with request.urlopen(req, timeout=timeout) as response:
            response_body = response.read().decode("utf-8", errors="replace")
    except (ValueError, HTTPError, URLError, TimeoutError, OSError) as exc:
        log.warning("Feishu notification failed: %s", exc)
        return False
    # 签名错误、卡片格式被拒时机器人仍回 HTTP 200，只有响应体里的 code 为 0 才算送达。
    try:
        code = json.loads(response_body).get("code")
    except (ValueError, AttributeError):
        code = None
    if code != 0:
        log.warning("Feishu rejected the notification: body=%s", response_body)
        return False
    log.info("Feishu notification sent: body=%s", response_body)
    return True


def numbered_lines(items: list[str]) -> str:
    return "\n".join(f"{index}. {item}" for index, item in enumerate(items, 1))


def build_sectioned_card(
    *,
    title: str,
    template: str,
    lead: str,
    sections: list[tuple[str, str | list[str]]],
) -> dict[str, object]:
    elements: list[dict[str, object]] = []
    if lead:
        elements.append(
            {"tag": "div", "text": {"tag": "lark_md", "content": lead}}
        )
    for heading, content in sections:
        rendered = numbered_lines(content) if isinstance(content, list) else content
        if not rendered:
            continue
        if elements:
            elements.append({"tag": "hr"})
        elements.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**{heading}**\n{rendered}",
                },
            }
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


def card_text(payload: dict[str, object]) -> str:
    """Render a card as plain text for CLI previews and logs."""

    card = payload["card"]
    lines = [card["header"]["title"]["content"]]
    for element in card["elements"]:
        if element.get("tag") == "hr":
            lines.append("")
        else:
            lines.append(element["text"]["content"])
    return "\n".join(lines)
