"""汇报请求的提供商路由元信息。"""

from urllib.parse import urlparse
from uuid import uuid4


def report_headers(base_url: str) -> dict[str, str]:
    headers = {"User-Agent": "hci-game-automation-report/1.0"}
    if urlparse(base_url).hostname == "opencode.ai":
        # 一次汇报及其重试复用同一个客户端、会话标识。
        headers["x-opencode-session"] = str(uuid4())
    return headers
