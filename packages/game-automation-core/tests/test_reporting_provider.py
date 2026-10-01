from game_automation_core.reporting.provider import report_headers


def test_opencode_has_session_and_application_identity():
    headers = report_headers("https://opencode.ai/zen/go/v1")
    assert headers["User-Agent"] == "hci-game-automation-report/1.0"
    assert headers["x-opencode-session"]
    assert headers["x-opencode-session"] != report_headers("https://opencode.ai/zen/go/v1")["x-opencode-session"]


def test_other_providers_do_not_receive_opencode_session():
    for url in ("https://api.deepseek.com", "https://opencode.ai.example.com/v1"):
        assert "x-opencode-session" not in report_headers(url)
