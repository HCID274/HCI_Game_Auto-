import json
from datetime import datetime

import pytest

from game_automation_core.reporting.archive import write_json_archive
from game_automation_core.reporting.context import read_markdown
from game_automation_core.reporting.feishu import (
    build_sectioned_card,
    card_text,
    make_signature,
)
from game_automation_core.reporting.redact import redact_sensitive_data
from game_automation_core.reporting.report import (
    DONE,
    FAILED,
    SKIPPED,
    WARN,
    GameReport,
    format_duration,
    settle_status,
)


def test_markdown_comments_are_not_active_context(tmp_path) -> None:
    path = tmp_path / "context.md"
    path.write_text("保留<!--隐藏规则-->内容", encoding="utf-8")
    assert read_markdown(path) == "保留内容"


def test_archive_is_valid_utf8_json(tmp_path) -> None:
    path = write_json_archive(tmp_path / "report.json", {"完成": True})
    assert json.loads(path.read_text(encoding="utf-8")) == {"完成": True}
    assert not (tmp_path / ".report.json.tmp").exists()


def test_card_omits_empty_sections_and_signature_is_stable() -> None:
    card = build_sectioned_card(
        title="完成", template="green", lead="摘要", sections=[("日常", ["任务"]), ("周常", [])]
    )
    assert card["card"]["elements"][2]["text"]["content"] == "**日常**\n1. 任务"
    assert "周常" not in str(card)
    assert make_signature(123, "secret") == make_signature(123, "secret")


def test_redaction_covers_nested_credentials() -> None:
    redacted = redact_sensitive_data(
        {
            "items": [
                "FEISHU_WEBHOOK_SECRET=abc123",
                ("api_key: xyz", "Bearer abc.def"),
                'config {"token": "tok123"}',
                "https://open.feishu.cn/open-apis/bot/v2/hook/zzz",
                "sk-abcdefghijklmnopqrstuvwxyz",
            ],
        }
    )
    text = str(redacted)
    for secret in ("abc123", "xyz", "abc.def", "tok123", "hook/zzz", "sk-abcdefghij"):
        assert secret not in text


def test_success_card_lists_tasks_without_problem_section() -> None:
    report = GameReport(
        game="星铁",
        status="completed",
        finished_at=datetime(2026, 10, 1, 6, 12),
        tasks=[f"{DONE} 每日实训 500/500", f"{SKIPPED} 侵蚀隧洞：开拓力不足"],
        notes=[("养成待办", ["远坂凛：遗器"])],
        duration_seconds=2460,
    )

    card = report.to_card()
    text = card_text(card)

    assert card["card"]["header"]["template"] == "green"
    assert report.title == "✅ 星铁 全部完成 · 10-01 06:12"
    assert "用时 41分钟" in text
    assert "异常记录" not in text
    assert f"**今日任务**\n{DONE} 每日实训 500/500\n{SKIPPED} 侵蚀隧洞：开拓力不足" in text
    assert "**养成待办**\n1. 远坂凛：遗器" in text


def test_problems_lead_the_card_and_secrets_are_redacted() -> None:
    report = GameReport(
        game="鸣潮",
        status="failed",
        finished_at=datetime(2026, 10, 1, 5, 52),
        tasks=[f"{FAILED} 讨伐强敌第3项：吸收声骸 0/5"],
        problems=["讨伐：OK-WW 启动后 10 分钟没开始讨伐", "token=SECRET123"],
    )

    elements = report.to_card()["card"]["elements"]

    assert elements[0]["text"]["content"].startswith("**异常记录**\n1. 讨伐")
    assert elements[2]["text"]["content"].startswith("**今日任务**")
    assert "SECRET123" not in str(report.to_card())
    assert "SECRET123" not in str(report.to_dict())
    assert report.to_dict()["title"].startswith("❌ 鸣潮 失败")


def test_unknown_status_is_rejected() -> None:
    with pytest.raises(ValueError):
        GameReport(game="星铁", status="in_progress", finished_at=datetime(2026, 1, 1))


@pytest.mark.parametrize(
    ("status", "tasks", "problems", "expected"),
    [
        ("completed", [f"{DONE} 日常", f"{SKIPPED} 体力不足，计划保留"], [], "completed"),
        ("completed", [f"{DONE} 日常", f"{WARN} 活跃度没确认"], [], "partial"),
        ("completed", [f"{DONE} 日常"], ["收尾没关掉游戏"], "partial"),
        ("failed", [f"{DONE} 日常"], [], "failed"),
    ],
)
def test_completed_is_reserved_for_clean_runs(status, tasks, problems, expected) -> None:
    assert settle_status(status, tasks, problems) == expected


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(45, "45秒"), (600, "10分钟"), (3610, "1小时"), (4920, "1小时22分钟")],
)
def test_duration_is_human_readable(seconds: int, text: str) -> None:
    assert format_duration(seconds) == text
