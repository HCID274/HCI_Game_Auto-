from pathlib import Path
from types import SimpleNamespace

from wuwa_auto.reporting.parser import parse_run
from wuwa_auto.reporting.summarizer import _validate_wording, build_fallback_narrative


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
        "领取每日活跃度奖励（活跃度120点）",
        "先约电台：已执行奖励领取操作",
    ]
    assert narrative.weekly == []
    assert narrative.followup == ["讨伐强敌第2项 2次", "吸收声骸1次"]
    assert "邮件" not in str(narrative)


def test_unconfirmed_daily_points_are_only_reported_as_claim_action(
    tmp_path: Path,
) -> None:
    text = """
DailyTask:info_set total daily points 0
DailyTask:claim daily reward via  coordinate
DailyTask:Daily Task Completed
"""
    facts = parse_run(_result(tmp_path, text))

    assert [item.item_id for item in facts.daily] == [
        "daily-activity-claim-action"
    ]
    assert facts.daily[0].text == (
        "每日活跃度：已执行奖励领取操作（最终活跃度未从日志确认）"
    )


def test_real_tacet_markers_report_claimed_runs_and_fixed_waveplates(
    tmp_path: Path,
) -> None:
    text = """
TacetTask:start walk_to_treasure
TacetTask:info_set current_stamina 144
TacetTask:start walk_to_treasure
TacetTask:info_set current_stamina 24
BaseWWTask:current stamina: -36 must_use completed, no need to use back_up
DailyTask:Daily Task Completed
"""
    facts = parse_run(_result(tmp_path, text))

    assert facts.daily[0].text == "无音区第6项清剿2场，消耗120结晶波片"


def test_unclaimed_tacet_restart_is_not_counted(tmp_path: Path) -> None:
    text = """
TacetTask:start walk_to_treasure
TacetTask:is not claim treasure, restart challenge
TacetTask:start walk_to_treasure
DailyTask:Daily Task Completed
"""
    facts = parse_run(_result(tmp_path, text))

    assert facts.daily[0].text == "无音区第6项清剿1场，消耗60结晶波片"


def test_historical_nightmare_echo_and_unreachable_target_are_reported(
    tmp_path: Path,
) -> None:
    text = """
NightmareNestTask:farm echo walk find true
NightmareNestTask:nightmare nest unreachable, skip this run: go_nest:41:18
DailyTask:Daily Task Completed
"""
    facts = parse_run(_result(tmp_path, text))

    assert [item.text for item in facts.daily] == ["梦魇巢穴吸收声骸1次"]
    assert [item.text for item in facts.issues] == [
        "梦魇巢穴存在未解锁或不可达目标，已跳过1处"
    ]
    assert facts.overall_status == "partial_success"


def test_nightmare_echo_does_not_inflate_farm_echo_pickup(tmp_path: Path) -> None:
    text = """
NightmareNestTask:farm echo yolo find True
FarmEchoTask:farm echo on the face
DailyTask:Daily Task Completed
"""
    facts = parse_run(_result(tmp_path, text))

    assert [item.text for item in facts.daily] == ["梦魇巢穴吸收声骸1次"]
    assert [item.text for item in facts.followup] == ["吸收声骸1次"]


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


def test_standalone_weekly_garden_is_reported(tmp_path: Path) -> None:
    text = """
GardenTask:garden end [本周游历值, 已达到上限]
GardenTask:乐园任务完成, 已达到上限
TaskExecutor:Successfully Executed Task, Exiting Game and App!
"""
    facts = parse_run(
        _result(
            tmp_path,
            text,
            config={"workflow_task": "weekly_garden"},
        )
    )
    narrative = build_fallback_narrative(facts)
    assert narrative.summary.startswith("鸣潮周常完成")
    assert narrative.weekly == ["完成幻梦游园本周目标"]


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


def test_ai_cannot_promote_battle_pass_action_to_claimed_reward(
    tmp_path: Path,
) -> None:
    text = """
DailyTask:battle pass
DailyTask:info_set current task check weekly garden
DailyTask:Daily Task Completed
"""
    facts = parse_run(_result(tmp_path, text))
    summary, wording = _validate_wording(
        {
            "summary": "完成",
            "wording": {"battle-pass": "先约电台：领取奖励"},
        },
        facts,
    )
    assert summary == "完成"
    assert wording["battle-pass"] == "先约电台：已执行奖励领取操作"


def test_ai_cannot_promote_unconfirmed_daily_action_to_claimed_reward(
    tmp_path: Path,
) -> None:
    text = """
DailyTask:info_set total daily points 0
DailyTask:claim daily reward via  coordinate
DailyTask:Daily Task Completed
"""
    facts = parse_run(_result(tmp_path, text))
    summary, wording = _validate_wording(
        {
            "summary": "完成",
            "wording": {
                "daily-activity-claim-action": "每日活跃度奖励已领取",
            },
        },
        facts,
    )

    assert summary == "完成"
    assert wording["daily-activity-claim-action"] == (
        "每日活跃度：已执行奖励领取操作（最终活跃度未从日志确认）"
    )
