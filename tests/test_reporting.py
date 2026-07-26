from pathlib import Path
from types import SimpleNamespace

from wuwa_auto.reporting.parser import parse_run
from wuwa_auto.reporting.summarizer import build_fallback_narrative


def _result(tmp_path: Path, text: str, **overrides: object) -> SimpleNamespace:
    log = tmp_path / "current.log"
    log.write_text(text, encoding="utf-8")
    values: dict[str, object] = {
        "status": "success",
        "reason": "Daily Task Completed",
        "duration_seconds": 506,
        "log_slice_path": str(log),
        "config": {"boss_challenge_index": 2, "daily_farm_index": 6},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_only_executed_daily_and_followup_items_are_reported(tmp_path: Path) -> None:
    text = """
DailyTask:info_set total daily points 120
DailyTask:claim daily reward via  coordinate
DailyTask:info_set current task claim mail
DailyTask:battle pass
DailyTask:info_set current task check weekly garden
DailyTask:weekly garden already completed
FarmEchoTask:info_set Teleport to Boss Boss Challenge 1
FarmEchoTask:start wait in combat
FarmEchoTask:start wait in combat
FarmEchoTask:farm echo walk_find_echo True
DailyTask:Daily Task Completed
"""
    facts = parse_run(_result(tmp_path, text))
    narrative = build_fallback_narrative(facts)

    assert narrative.daily == [
        "领取每日活跃度奖励（检测到120点）",
        "先约电台：已执行奖励领取操作",
    ]
    assert narrative.weekly == []
    assert narrative.followup == ["讨伐强敌第2项 2次", "吸收声骸1次"]
    assert "邮件" not in str(narrative)


def test_battle_pass_is_omitted_when_claim_branch_is_not_entered(
    tmp_path: Path,
) -> None:
    text = """
DailyTask:battle pass
DailyTask:can not battle pass, maybe ended
DailyTask:info_set current task check weekly garden
DailyTask:Daily Task Completed
"""
    facts = parse_run(_result(tmp_path, text))
    assert all(item.item_id != "battle-pass" for item in facts.daily)


def test_weekly_is_reported_only_when_run_reaches_completion(tmp_path: Path) -> None:
    text = """
DailyTask:weekly garden not completed, run GardenTask
GardenTask:乐园任务完成, 已达到上限
DailyTask:Daily Task Completed
"""
    facts = parse_run(_result(tmp_path, text))
    assert [item.text for item in facts.weekly] == ["完成幻梦游园本周目标"]


def test_historical_zero_based_boss_log_keeps_gui_item_number(
    tmp_path: Path,
) -> None:
    text = """
FarmEchoTask:info_set Teleport to Boss Boss Challenge 1
FarmEchoTask:start wait in combat
DailyTask:Daily Task Completed
"""
    facts = parse_run(_result(tmp_path, text, config={}))
    assert facts.followup[0].text == "讨伐强敌第2项 1次"
