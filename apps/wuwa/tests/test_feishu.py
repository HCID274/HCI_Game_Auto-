from wuwa_auto.integrations.feishu import build_report_card
from wuwa_auto.reporting.models import NarrativeReport


def test_card_omits_empty_weekly_and_never_creates_mail_section() -> None:
    card = build_report_card(
        title="✅ 鸣潮完成",
        template="green",
        narrative=NarrativeReport(
            summary="鸣潮日常完成",
            daily=["先约电台：已执行奖励领取操作"],
            weekly=[],
            followup=["讨伐强敌第2项 5次"],
            issues=[],
        ),
    )
    rendered = str(card)
    assert "'content': '**日常**" in rendered
    assert "'content': '**后续事件**" in rendered
    assert "**周常**" not in rendered
    assert "邮件" not in rendered
