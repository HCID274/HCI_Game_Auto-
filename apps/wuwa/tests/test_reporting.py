from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from wuwa_auto.reporting.models import NarrativeReport, ReportItem, RunFacts
from wuwa_auto.reporting.noise import known_upstream_noise_lines
from wuwa_auto.reporting.parser import parse_run
from wuwa_auto.reporting.prompting import compose_report_messages
from wuwa_auto.reporting.service import (
    _redact_narrative,
    _should_show_agent_diagnostics,
)
from wuwa_auto.reporting.summarizer import (
    _safe_summary,
    _validate_wording,
    build_fallback_narrative,
    summarize_report,
    summarize_with_ai,
)


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
DailyTask:HOST_DAILY_ACTIVITY_CLAIM_VERIFIED {"points": 120, "target": 100}
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
        "领取每日活跃度奖励（活跃度120点，已确认100%）",
        "先约电台：已执行奖励领取操作",
    ]
    assert narrative.weekly == []
    assert narrative.followup == ["讨伐强敌第2项 1次", "吸收声骸1次"]
    assert "邮件" not in str(narrative)


def test_parser_keeps_current_run_evidence_for_agent(tmp_path: Path) -> None:
    facts = parse_run(_result(tmp_path, "DailyTask:Daily Task Completed\n"))

    assert facts.evidence["game"] == "wuwa"
    assert facts.evidence["line_refs"] == ["L1"]


def test_wuwa_prompt_keeps_protocol_style_and_run_data_separate() -> None:
    messages = compose_report_messages({"evidence": {"line_refs": ["L1"]}})

    assert [item["role"] for item in messages] == ["system", "system", "user"]
    assert "每日活跃度" in messages[0]["content"]
    assert "日常、周常" in messages[1]["content"]
    assert '"line_refs"' in messages[2]["content"]


def test_prompt_uses_log_order_and_declares_filtered_startup_noise() -> None:
    messages = compose_report_messages({"evidence": {"line_refs": ["L2", "L4"]}})

    system_text = "\n".join(item["content"] for item in messages[:2])
    assert "时间戳和证据行号" in system_text
    assert "不要套用或硬编码" in system_text
    assert "移除历史上确认无业务影响" in system_text


def test_known_upstream_noise_is_filtered_only_from_agent_evidence() -> None:
    text = """TaskExecutor:install ocr translations error for zh_CN
StartController:waiting for game to start error Selected capture method is not supported
windows_graphics:update:use WGC capture
StartController:NVIDIA RTX Dynamic Vibrance is enabled and may cause malfunctions!
ERROR: real task failure"""

    assert known_upstream_noise_lines(text) == frozenset({1, 2, 4})


def test_parser_keeps_real_failure_while_filtering_startup_noise(
    tmp_path: Path,
) -> None:
    text = """TaskExecutor:install ocr translations error for zh_CN
StartController:NVIDIA RTX Dynamic Vibrance is enabled and may cause malfunctions!
DailyTask:Daily Task exception stopped"""
    facts = parse_run(
        _result(tmp_path, text, status="failed", reason="Daily Task exception stopped")
    )

    assert facts.evidence["line_refs"] == ["L3"]
    assert facts.issues[0].text == "主流程失败：Daily Task exception stopped"


def test_capture_fallback_is_kept_when_no_recovery_is_observed() -> None:
    text = "StartController:waiting for game to start error Selected capture method is not supported\n"

    assert known_upstream_noise_lines(text) == frozenset()


def test_capture_fallback_is_not_hidden_by_same_line_or_distant_wgc_text() -> None:
    same_line = (
        "StartController:error Selected capture method is not supported; "
        "use WGC capture\n"
    )
    distant = "\n".join(
        [
            "StartController:error Selected capture method is not supported",
            *(["DailyTask:progress"] * 21),
            "windows_graphics:start WGC capture",
        ]
    )

    assert known_upstream_noise_lines(same_line) == frozenset()
    assert known_upstream_noise_lines(distant) == frozenset()


def test_failed_wgc_marker_does_not_hide_capture_failure() -> None:
    text = """StartController:error Selected capture method is not supported
ERROR: failed to start WGC capture"""

    assert known_upstream_noise_lines(text) == frozenset()


def test_verified_activity_filters_optional_capability_panel_from_ai() -> None:
    text = """DailyTask:HOST_OKWW_DAILY_TRACE {"event": "capabilities"}
DailyTask:HOST_DAILY_ACTIVITY_PANEL {"labels": ["+40", "完成1次日常任务", "0/1"]}
DailyTask:HOST_DAILY_ACTIVITY_CLAIM_VERIFIED {"points": 140, "target": 100}
DailyTask:Daily Task Completed"""

    assert known_upstream_noise_lines(text) == frozenset({1, 2})


def test_unverified_activity_keeps_capability_panel_for_failure_diagnosis() -> None:
    text = """DailyTask:HOST_OKWW_DAILY_TRACE {"event": "capabilities"}
DailyTask:HOST_DAILY_ACTIVITY_PANEL {"labels": ["+40", "完成1次日常任务", "0/1"]}
DailyTask:HOST_DAILY_ACTIVITY_CLAIM_UNVERIFIED {"points": 0, "target": 100}"""

    assert known_upstream_noise_lines(text) == frozenset()


def test_wuwa_agent_archives_provider_token_usage() -> None:
    facts = RunFacts(
        overall_status="completed",
        reason="Daily Task Completed",
        duration_seconds=1,
        issues=[ReportItem("run-failure", "主流程失败：测试异常")],
        evidence={"line_refs": ["L1"]},
    )
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content='{"summary":"鸣潮日常完成",'
                    '"wording":{"run-failure":"主流程失败：测试异常"},'
                    '"analysis":{"anomalies":[{"message":"发现日志边界",'
                    '"evidence_refs":["L1"],"confidence":"high"}]}}'
                )
            )
        ],
        usage=SimpleNamespace(prompt_tokens=80, completion_tokens=20, total_tokens=100),
    )

    with patch("wuwa_auto.reporting.summarizer.get_secret") as secret, patch(
        "wuwa_auto.reporting.summarizer.OpenAI"
    ) as client_class:
        secret.side_effect = lambda name: {
            "DEEPSEEK_API_KEY": "test",
            "DEEPSEEK_MODEL": "test-model",
        }.get(name, "")
        client_class.return_value.chat.completions.create.return_value = response
        narrative = summarize_with_ai(facts)

    assert narrative.token_usage["input_tokens"] == 80
    assert narrative.token_usage["output_tokens"] == 20
    assert narrative.token_usage["output_input_ratio"] == 0.25
    assert narrative.analysis["anomalies"][0]["evidence_refs"] == ["L1"]


def test_clean_completed_run_discards_non_impacting_ai_diagnostics() -> None:
    facts = RunFacts(
        overall_status="completed",
        reason="Daily Task Completed",
        duration_seconds=1,
        evidence={"line_refs": ["L1"]},
    )
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content='{"summary":"鸣潮日常完成","wording":{},'
                    '"analysis":{"anomalies":[{"message":"可选任务未执行",'
                    '"evidence_refs":["L1"],"confidence":"high"}]}}'
                )
            )
        ],
        usage=SimpleNamespace(prompt_tokens=80, completion_tokens=20, total_tokens=100),
    )

    with patch("wuwa_auto.reporting.summarizer.get_secret") as secret, patch(
        "wuwa_auto.reporting.summarizer.OpenAI"
    ) as client_class:
        secret.side_effect = lambda name: {
            "DEEPSEEK_API_KEY": "test",
            "DEEPSEEK_MODEL": "test-model",
        }.get(name, "")
        client_class.return_value.chat.completions.create.return_value = response
        narrative = summarize_with_ai(facts)

    assert narrative.analysis == {}
    assert narrative.issues == []


def test_ai_summary_cannot_reverse_program_status() -> None:
    failed = RunFacts(
        overall_status="failed",
        reason="failed",
        duration_seconds=1,
    )
    completed = RunFacts(
        overall_status="completed",
        reason="completed",
        duration_seconds=1,
    )

    assert _safe_summary("任务未失败", failed) == "鸣潮日常失败，耗时1秒"
    assert _safe_summary("本轮完成但领取失败", completed) == "鸣潮日常完成，耗时1秒"
    assert _safe_summary("失败但已完成", failed) == "鸣潮日常失败，耗时1秒"
    assert _safe_summary("完成但奖励未领取", completed) == "鸣潮日常完成，耗时1秒"
    assert _safe_summary("鸣潮日常完成，获得五星武器", completed) == (
        "鸣潮日常完成，耗时1秒"
    )
    assert _safe_summary("鸣潮日常完成，累计9999次", completed) == (
        "鸣潮日常完成，耗时1秒"
    )
    assert _safe_summary("鸣潮日常完成，活跃度999点", completed) == (
        "鸣潮日常完成，耗时1秒"
    )
    assert _safe_summary("鸣潮日常完成，耗时1秒", completed) == (
        "鸣潮日常完成，耗时1秒"
    )
    assert _safe_summary("鸣潮日常完成，耗时9999秒", completed) == (
        "鸣潮日常完成，耗时1秒"
    )
    assert _safe_summary("鸣潮日常完成，耗时约1秒", completed) == (
        "鸣潮日常完成，耗时1秒"
    )
    assert _safe_summary("鸣潮日常完成，累计1次", completed) == (
        "鸣潮日常完成，耗时1秒"
    )
    for status, text in (
        ("unknown", "鸣潮状态未确认但奖励领取成功"),
        ("in_progress", "鸣潮日常进行中但本轮成功"),
        ("stalled", "鸣潮日常卡住但任务成功"),
        ("failed", "鸣潮日常失败但奖励领取成功"),
        ("completed", "鸣潮日常完成但任务未成功"),
    ):
        facts = RunFacts(overall_status=status, reason=status, duration_seconds=1)
        assert _safe_summary(text, facts) == {
            "unknown": "鸣潮日常状态未确认，耗时1秒",
            "in_progress": "鸣潮日常仍在进行，耗时1秒",
            "stalled": "鸣潮日常疑似卡住，耗时1秒",
            "failed": "鸣潮日常失败，耗时1秒",
            "completed": "鸣潮日常完成，耗时1秒",
        }[status]
    for status, forbidden in (
        ("unknown", "鸣潮日常状态未确认，耗时1秒"),
        ("in_progress", "鸣潮日常仍在进行，耗时1秒"),
        ("stalled", "鸣潮日常疑似卡住，耗时1秒"),
    ):
        facts = RunFacts(overall_status=status, reason=status, duration_seconds=1)
        assert _safe_summary("鸣潮日常完成", facts) == forbidden


def test_invalid_wuwa_ai_response_still_keeps_billed_usage() -> None:
    facts = RunFacts(
        overall_status="completed",
        reason="completed",
        duration_seconds=1,
        evidence={"line_refs": ["L1"]},
    )
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"summary":"bad"}'))],
        usage=SimpleNamespace(prompt_tokens=90, completion_tokens=10, total_tokens=100),
    )

    with patch("wuwa_auto.reporting.summarizer.get_secret") as secret, patch(
        "wuwa_auto.reporting.summarizer.OpenAI"
    ) as client_class:
        secret.side_effect = lambda name: {
            "DEEPSEEK_API_KEY": "test",
            "DEEPSEEK_MODEL": "test-model",
        }.get(name, "")
        client_class.return_value.chat.completions.create.return_value = response
        narrative, ai_used = summarize_report(facts)

    assert ai_used is False
    assert narrative.token_usage["input_tokens"] == 90
    assert narrative.token_usage["output_tokens"] == 10


def test_fallback_report_redacts_secret_fact_text() -> None:
    facts = RunFacts(
        overall_status="failed",
        reason="failed",
        duration_seconds=1,
        issues=[ReportItem("run-failure", "主流程失败：token=SUPERSECRET123")],
    )

    narrative = build_fallback_narrative(facts)

    assert "SUPERSECRET123" not in str(narrative)


def test_card_narrative_redacts_ai_text() -> None:
    narrative = _redact_narrative(
        NarrativeReport(
            summary="鸣潮完成",
            daily=["token=SUPERSECRET123"],
            weekly=[],
            followup=[],
            issues=["api_key=ANOTHERSECRET"],
        )
    )

    assert "SUPERSECRET123" not in str(narrative)
    assert "ANOTHERSECRET" not in str(narrative)


def test_clean_success_cannot_render_agent_only_anomalies() -> None:
    narrative = NarrativeReport(
        summary="鸣潮日常完成",
        daily=[],
        weekly=[],
        followup=[],
        issues=[],
        analysis={"anomalies": [{"message": "可选任务未执行"}]},
    )
    clean = RunFacts(
        overall_status="completed",
        reason="completed",
        duration_seconds=1,
    )
    impacted = RunFacts(
        overall_status="partial_success",
        reason="partial",
        duration_seconds=1,
        issues=[ReportItem("issue", "真实异常")],
    )

    assert _should_show_agent_diagnostics(clean, narrative) is False
    assert _should_show_agent_diagnostics(impacted, narrative) is True


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


def test_verified_post_claim_total_does_not_report_optional_panel_tasks(
    tmp_path: Path,
) -> None:
    text = """
DailyTask:HOST_DAILY_ACTIVITY_PANEL {"labels": ["+40", "完成1次日常任务", "0/1", "140", "活跃度", "20", "40", "60", "80"]}
DailyTask:HOST_DAILY_ACTIVITY_ALREADY_SETTLED {"source": "pre_claim_panel_labels"}
DailyTask:HOST_DAILY_ACTIVITY_CLAIM_ACTION {"upstream_click": false, "host_clicks": 0}
DailyTask:HOST_DAILY_ACTIVITY_CLAIM_VERIFIED {"points": 140, "target": 100, "complete": true}
DailyTask:Daily Task Completed
"""

    facts = parse_run(_result(tmp_path, text))

    assert facts.overall_status == "completed"
    assert facts.issues == []
    assert [item.text for item in facts.daily] == [
        "每日活跃度已确认达到100（当前140点，奖励状态已结算）"
    ]


def test_unverified_daily_activity_is_reported_even_before_claim_click(
    tmp_path: Path,
) -> None:
    text = """
DailyTask:HOST_DAILY_ACTIVITY_CLAIM_UNVERIFIED {"points": 20, "target": 100, "reason": "daily activity below threshold: points=20, target=100"}
DailyTask:Daily Task exception stopped
"""
    facts = parse_run(
        _result(
            tmp_path,
            text,
            status="failed",
            reason="Daily Task exception stopped",
        )
    )

    assert [item.text for item in facts.issues] == [
        "主流程失败：Daily Task exception stopped",
        "每日活跃度奖励未确认：daily activity below threshold: points=20, target=100",
    ]


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
        "梦魇巢穴传送未确认生效，已跳过1处"
    ]
    assert facts.overall_status == "partial_success"


def test_host_nightmare_travel_marker_uses_precise_wording(tmp_path: Path) -> None:
    text = """
NightmareNestTask:HOST_NIGHTMARE_TRAVEL_NOT_CONFIRMED target=go_nest:48:28 reason=button_still_visible_after_retry
DailyTask:Daily Task Completed
"""
    facts = parse_run(_result(tmp_path, text))

    assert [item.text for item in facts.issues] == [
        "梦魇巢穴传送未确认生效，已跳过1处"
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
    assert [item.text for item in facts.followup] == [
        "讨伐强敌第2项 1次",
        "吸收声骸1次",
    ]


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
FarmEchoTask:farm echo walk_find_echo None
FarmEchoTask:left_click claim_cancel_button_hcenter_vcenter (769, 900)
DailyTask:Daily Task Completed
"""
    facts = parse_run(_result(tmp_path, text, config={}))
    assert facts.followup[0].text == "讨伐强敌第2项 1次"


def test_unconfirmed_farm_echo_result_is_not_reported_as_kill(
    tmp_path: Path,
) -> None:
    text = """
FarmEchoTask:info_set Teleport to Boss Boss Challenge 1
FarmEchoTask:start wait in combat
FarmEchoTask:farm echo walk_find_echo None
DailyTask:Daily Task Completed
"""
    facts = parse_run(_result(tmp_path, text, config={}))
    assert all(item.item_id != "boss-challenge" for item in facts.followup)


def test_confirmed_retry_uses_structured_absorption_count(tmp_path: Path) -> None:
    text = """
FarmEchoTask:farm echo walk_find_echo True
FarmEchoTask:left_click claim_cancel_button_hcenter_vcenter (769, 900)
FarmEchoTask:farm echo walk_find_echo True
"""
    facts = parse_run(
        _result(
            tmp_path,
            text,
            config={
                "boss_challenge_index": 2,
                "workflow_task": "farm_echo_confirmed_retry",
                "confirmed_farm_echo_absorption_count": 2,
            },
        )
    )
    narrative = build_fallback_narrative(facts)

    assert [item.text for item in facts.followup] == [
        "讨伐强敌第2项 2次",
        "吸收声骸2次",
    ]
    assert narrative.summary.startswith("鸣潮后续任务完成")


def test_confirmed_absorption_total_proves_matching_boss_clears(
    tmp_path: Path,
) -> None:
    facts = parse_run(
        _result(
            tmp_path,
            "FarmEchoTask:left_click claim_cancel_button_hcenter_vcenter (769, 900)\n",
            config={
                "boss_challenge_index": 2,
                "workflow_task": "farm_echo_confirmed_retry",
                "confirmed_farm_echo_absorption_count": 5,
            },
        )
    )

    assert [item.text for item in facts.followup] == [
        "讨伐强敌第2项 5次",
        "吸收声骸5次",
    ]


def test_recovered_farm_echo_reports_exact_total_and_recovery_event(
    tmp_path: Path,
) -> None:
    text = "\n".join(
        [
            "FarmEchoTask:farm echo walk_find_echo True",
            "FarmEchoTask:left_click claim_cancel_button_hcenter_vcenter (769, 900)",
        ]
        * 5
    )
    result = _result(
        tmp_path,
        text,
        config={
            "boss_challenge_index": 2,
            "workflow_task": "daily",
            "farm_echo_recovery": {
                "triggered": True,
                "target_count": 5,
                "recovery_attempts": 2,
                "retry_completed": 2,
                "total_completed": 5,
                "first_safe_recovery": True,
                "final_safe_recovery": None,
            },
        },
    )

    facts = parse_run(result)

    assert facts.overall_status == "completed"
    assert [item.text for item in facts.followup] == [
        "讨伐强敌第2项 5次",
        "讨伐中途倒地2次，已自动恢复并补吸收声骸2次",
        "吸收声骸5次",
    ]


def test_recovered_realm_defeat_keeps_full_realm_label(
    tmp_path: Path,
) -> None:
    result = _result(
        tmp_path,
        "HOST_FARM_ECHO_REALM_DEFEAT_CONFIRMED\n",
        config={
            "boss_challenge_index": 2,
            "workflow_task": "daily",
            "farm_echo_recovery": {
                "triggered": True,
                "target_count": 5,
                "recovery_attempts": 1,
                "retry_completed": 5,
                "total_completed": 5,
                "recoveries": [{"success": True, "realm_defeat": True}],
            },
        },
    )

    facts = parse_run(result)

    assert [item.text for item in facts.followup] == [
        "讨伐强敌第2项 5次",
        "讨伐副本团灭1次，已自动恢复并补吸收声骸5次",
        "吸收声骸5次",
    ]


def test_incomplete_recovery_does_not_claim_exit_and_heal(tmp_path: Path) -> None:
    result = _result(
        tmp_path,
        "HOST_FARM_ECHO_REVIVE_DIALOG_CONFIRMED\n",
        status="failed",
        config={
            "workflow_task": "farm_echo_confirmed_retry",
            "farm_echo_recovery": {
                "triggered": True,
                "target_count": 5,
                "recovery_attempts": 2,
                "retry_completed": 0,
                "total_completed": 0,
                "first_safe_recovery": True,
                "final_safe_recovery": True,
            },
        },
    )

    facts = parse_run(result)

    issue_text = [item.text for item in facts.issues]
    assert "讨伐中途倒地2次，已自动恢复并重试；声骸累计吸收0/5次" in issue_text
    assert all("退本回血" not in text for text in issue_text)


def test_entry_retry_does_not_claim_a_death_recovery(tmp_path: Path) -> None:
    text = (
        "HOST_FARM_ECHO_BOSS_PAGE_RESELECTED\n"
        "HOST_FARM_ECHO_ABSORPTION_CONFIRMED 5/5"
    )
    result = _result(
        tmp_path,
        text,
        config={
            "boss_challenge_index": 2,
            "workflow_task": "daily",
            "farm_echo_recovery": {
                "triggered": True,
                "target_count": 5,
                "recovery_attempts": 0,
                "entry_retry_attempts": 1,
                "retry_completed": 5,
                "total_completed": 5,
            },
        },
    )

    facts = parse_run(result)

    assert [item.text for item in facts.followup] == [
        "讨伐强敌第2项 5次",
        "吸收声骸5次",
    ]
    assert all("倒地" not in item.text for item in facts.followup)


def test_absorption_timeout_reports_partial_target(tmp_path: Path) -> None:
    text = """
FarmEchoTask:farm echo walk_find_echo True
FarmEchoTask:left_click claim_cancel_button_hcenter_vcenter (769, 900)
FarmEchoTask:farm echo walk_find_echo True
"""
    facts = parse_run(
        _result(
            tmp_path,
            text,
            status="failed",
            reason="FarmEcho absorption target timed out after 3600 seconds",
            config={
                "boss_challenge_index": 2,
                "workflow_task": "farm_echo_confirmed_retry",
                "target_count": 5,
                "confirmed_farm_echo_absorption_count": 2,
            },
        )
    )

    assert [item.text for item in facts.followup] == [
        "讨伐强敌第2项 2次",
        "吸收声骸2次",
    ]
    assert facts.issues[-1].text == "声骸吸收目标仅完成2/5次"


def test_pre_daily_boss_failure_and_daily_success_is_partial(
    tmp_path: Path,
) -> None:
    text = """
FarmEchoTask:farm echo walk_find_echo True
FarmEchoTask:left_click claim_cancel_button_hcenter_vcenter (769, 900)
FarmEchoTask:farm echo walk_find_echo True
FarmEchoTask:left_click claim_cancel_button_hcenter_vcenter (769, 900)
DailyTask:Daily Task Completed
"""
    facts = parse_run(
        _result(
            tmp_path,
            text,
            status="failed",
            reason="pre-daily FarmEcho failed; DailyTask completed",
            config={
                "boss_challenge_index": 2,
                "workflow_task": "daily",
                "confirmed_farm_echo_absorption_count": 2,
                "farm_echo_absorption_target": 5,
                "daily_sequence": {
                    "boss_status": "failed",
                    "daily_status": "success",
                    "settled": True,
                },
            },
        )
    )

    assert facts.overall_status == "partial_success"
    assert [item.text for item in facts.followup] == [
        "讨伐强敌第2项 2次",
        "吸收声骸2次",
    ]
    assert [item.text for item in facts.issues] == [
        "主流程失败：pre-daily FarmEcho failed; DailyTask completed",
        "声骸吸收目标仅完成2/5次",
    ]


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


def test_ai_cannot_append_success_clause_to_negative_daily_activity_fact(
    tmp_path: Path,
) -> None:
    text = """
DailyTask:HOST_DAILY_ACTIVITY_CLAIM_UNVERIFIED {"points": 80, "target": 100, "reason": "not settled"}
DailyTask:Daily Task exception stopped
"""
    facts = parse_run(
        _result(tmp_path, text, status="failed", reason="Daily Task exception stopped")
    )

    _summary, wording = _validate_wording(
        {
            "summary": "失败",
            "wording": {
                "run-failure": "主流程失败，但本轮已完成",
                "daily-activity-unverified": "每日活跃度奖励未确认，但已完成危行任务且奖励已领取",
            },
        },
        facts,
    )

    assert wording["run-failure"] == "主流程失败：Daily Task exception stopped"
    assert wording["daily-activity-unverified"].startswith("每日活跃度奖励未确认")
    assert "奖励已领取" not in wording["daily-activity-unverified"]


def test_ai_cannot_add_unobserved_numbers_to_fact_wording() -> None:
    facts = RunFacts(
        overall_status="completed",
        reason="completed",
        duration_seconds=1,
        daily=[ReportItem("daily-activity-verified", "每日活跃度已确认达到100（当前140点，奖励状态已结算）")],
    )

    _summary, wording = _validate_wording(
        {
            "summary": "完成",
            "wording": {
                "daily-activity-verified": "每日活跃度已确认达到100（当前140点，额外获得999点，奖励状态已结算）"
            },
        },
        facts,
    )

    assert wording["daily-activity-verified"] == facts.daily[0].text


def test_ai_cannot_add_unobserved_drops_or_characters_to_fact_wording() -> None:
    facts = RunFacts(
        overall_status="completed",
        reason="completed",
        duration_seconds=1,
        followup=[ReportItem("echo-picked", "吸收声骸1次")],
    )

    _summary, wording = _validate_wording(
        {
            "summary": "完成",
            "wording": {"echo-picked": "吸收声骸1次，获得五星武器"},
        },
        facts,
    )

    assert wording["echo-picked"] == facts.followup[0].text


def test_ai_cannot_promote_daily_activity_capability_gap_to_success(
    tmp_path: Path,
) -> None:
    text = """
DailyTask:HOST_DAILY_ACTIVITY_PANEL {"labels": ["+40", "完成1次日常任务", "0/1"]}
DailyTask:HOST_DAILY_ACTIVITY_CLAIM_UNVERIFIED {"points": 0, "target": 100, "reason": "not settled"}
DailyTask:Daily Task exception stopped
"""
    facts = parse_run(
        _result(tmp_path, text, status="failed", reason="Daily Task exception stopped")
    )
    wording = {item.item_id: "已完成" for item in [*facts.daily, *facts.issues]}

    _summary, validated = _validate_wording(
        {"summary": "失败", "wording": wording},
        facts,
    )

    assert validated["daily-activity-unsupported-daily-quest"].startswith(
        "每日活跃任务未完成"
    )
    assert validated["daily-activity-unverified"].startswith(
        "每日活跃度奖励未确认"
    )


def test_unknown_activity_task_is_reported_without_false_zero_upper_bound(
    tmp_path: Path,
) -> None:
    text = """
DailyTask:HOST_DAILY_ACTIVITY_PANEL {"labels": ["+100", "完成1个危行任务", "0/1"]}
DailyTask:HOST_DAILY_ACTIVITY_CLAIM_UNVERIFIED {"points": 0, "target": 100, "reason": "not settled"}
DailyTask:Daily Task exception stopped
"""
    facts = parse_run(
        _result(tmp_path, text, status="failed", reason="Daily Task exception stopped")
    )

    assert any(item.item_id == "daily-activity-unknown-unknown-1" for item in facts.issues)
    assert all(item.item_id != "daily-activity-capability-gap" for item in facts.issues)
    wording = {item.item_id: "已完成" for item in [*facts.daily, *facts.issues]}
    _summary, validated = _validate_wording(
        {"summary": "失败", "wording": wording},
        facts,
    )
    assert validated["daily-activity-unknown-unknown-1"].startswith("每日活跃任务未完成")
