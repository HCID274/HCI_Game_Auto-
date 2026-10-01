from unittest.mock import patch

from wuwa_auto.integrations.feishu import send_card


def test_real_sending_stays_off_until_explicitly_enabled() -> None:
    secrets = {"WUWA_FEISHU_WEBHOOK_URL": "https://example.invalid/hook"}
    with patch(
        "wuwa_auto.integrations.feishu.get_secret",
        side_effect=lambda name: secrets.get(name, ""),
    ), patch("wuwa_auto.integrations.feishu.send_signed_payload") as send:
        assert send_card({"msg_type": "interactive"}) is False

    send.assert_not_called()
