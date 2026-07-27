import json

from game_automation_core.reporting.archive import write_json_archive
from game_automation_core.reporting.context import read_markdown
from game_automation_core.reporting.feishu import build_sectioned_card, make_signature


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
