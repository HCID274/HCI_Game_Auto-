import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from wuwa_auto.okww.runner import OkRunResult
from wuwa_auto.reporting.day_rollup import build_daily_rollup
from wuwa_auto.reporting.models import NarrativeReport, ReportItem, RunFacts
from wuwa_auto.reporting.noise import known_upstream_noise_lines
from wuwa_auto.reporting.parser import build_wuwa_agent_evidence, parse_run
from wuwa_auto.reporting.prompting import compose_report_messages
from wuwa_auto.reporting.service import (
    _archive_stem,
    _redact_narrative,
    _should_show_agent_diagnostics,
)
from wuwa_auto.reporting.summarizer import (
    _consume_completion,
    _parse_agent_report,
    _safe_summary,
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


def _ok_result(
    tmp_path: Path,
    *,
    run_id: str,
    workflow: str,
    status: str,
    log_text: str,
) -> OkRunResult:
    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True)
    log_path = run_dir / "ok-current-run.log"
    log_path.write_text(log_text, encoding="utf-8")
    result = OkRunResult(
        run_id=run_id,
        status=status,
        reason="completed" if status == "success" else "failed",
        started_at=f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:8]}T13:00:00+09:00",
        finished_at=f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:8]}T13:10:00+09:00",
        duration_seconds=600,
        log_slice_path=str(log_path),
        evidence_path=None,
        config={"workflow_task": workflow, "boss_challenge_index": 2},
        exit_code=0 if status == "success" else 1,
    )
    (run_dir / "result.json").write_text(
        json.dumps(asdict(result), ensure_ascii=False),
        encoding="utf-8",
    )
    return result


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
    assert "不能互相改名" in system_text
    assert "summary只概括 confirmed_results" in system_text
    assert "跨栏目移动、遗漏" in system_text
    assert "相同项数和相同顺序" in system_text
    assert "不得合并两项" in system_text
    assert "不要求逐字照抄" in system_text
    assert "过滤固定无害噪声" in system_text


def test_same_day_successful_daily_and_followup_are_rolled_up(
    tmp_path: Path,
) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    daily_result = _ok_result(
        tmp_path,
        run_id="20260809_132616",
        workflow="daily",
        status="success",
        log_text=(
            "2026-08-09 13:30:00 DailyTask:TacetTask:start walk_to_treasure\n"
            "2026-08-09 13:31:00 DailyTask:HOST_DAILY_ACTIVITY_CLAIM_VERIFIED "
            '{"points": 140, "target": 100}\n'
            "2026-08-09 13:32:00 DailyTask:Daily Task Completed\n"
        ),
    )
    daily_facts = RunFacts(
        overall_status="completed",
        reason="Daily Task Completed",
        duration_seconds=600,
        workflow_task="daily",
        daily=[ReportItem("daily-activity", "领取每日活跃度奖励（活跃度140点，已确认100%）")],
    )
    (reports / "20260809_132616.json").write_text(
        json.dumps(
            {"run_id": daily_result.run_id, "facts": daily_facts.to_dict()},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    followup_result = _ok_result(
        tmp_path,
        run_id="20260809_170403_farm_echo_confirmed_retry",
        workflow="farm_echo_confirmed_retry",
        status="success",
        log_text=(
            "2026-08-09 17:10:00 FarmEchoTask:start wait in combat\n"
            "2026-08-09 17:14:00 FarmEchoTask:farm echo walk_find_echo True\n"
        ),
    )
    followup_facts = RunFacts(
        overall_status="completed",
        reason="FarmEcho absorption confirmed 5/5 echoes",
        duration_seconds=603,
        workflow_task="farm_echo_confirmed_retry",
        followup=[
            ReportItem("boss-challenge", "讨伐强敌第2项 5次"),
            ReportItem("echo-picked", "吸收声骸5次"),
        ],
    )

    with patch("wuwa_auto.reporting.day_rollup.REPORTS_DIR", reports), patch(
        "wuwa_auto.reporting.day_rollup.RUNS_DIR", tmp_path / "runs"
    ):
        rolled_up = build_daily_rollup(followup_result, None, followup_facts)

    assert rolled_up.overall_status == "completed"
    assert [item.text for item in rolled_up.daily] == [
        "无音区清剿1场，消耗60结晶波片",
        "每日活跃度已确认达到100（当前140点，奖励状态已结算）",
    ]
    assert [item.text for item in rolled_up.followup] == [
        "讨伐强敌第2项 5次",
        "吸收声骸5次",
    ]
    assert rolled_up.issues == []
    assert rolled_up.evidence["source"] == (
        "20260809_132616,20260809_170403_farm_echo_confirmed_retry"
    )


def test_latest_failed_followup_overrides_earlier_success(
    tmp_path: Path,
) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    daily_result = _ok_result(
        tmp_path,
        run_id="20260811_053000_daily",
        workflow="daily",
        status="success",
        log_text="DailyTask:Daily Task Completed\n",
    )
    daily_facts = RunFacts(
        overall_status="completed",
        reason="Daily Task Completed",
        duration_seconds=600,
        workflow_task="daily",
        daily=[ReportItem("daily", "今日任务已完成")],
    )
    (reports / f"{daily_result.run_id}.json").write_text(
        json.dumps(
            {"run_id": daily_result.run_id, "facts": daily_facts.to_dict()},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    earlier = _ok_result(
        tmp_path,
        run_id="20260811_060000_farm_echo_confirmed_retry",
        workflow="farm_echo_confirmed_retry",
        status="success",
        log_text="FarmEchoTask:HOST_FARM_ECHO_ABSORPTION_CONFIRMED 1/1\n",
    )
    stale_rollup = RunFacts(
        overall_status="completed",
        reason="old success",
        duration_seconds=600,
        workflow_task="daily",
        followup=[ReportItem("echo-picked", "吸收声骸1次")],
        evidence={"source": f"{daily_result.run_id},{earlier.run_id}"},
    )
    (reports / f"{earlier.run_id}_daily_rollup.json").write_text(
        json.dumps(
            {"run_id": earlier.run_id, "facts": stale_rollup.to_dict()},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    latest = _ok_result(
        tmp_path,
        run_id="20260811_120000_farm_echo_confirmed_retry",
        workflow="farm_echo_confirmed_retry",
        status="failed",
        log_text="FarmEchoTask:HOST_FARM_ECHO_REALM_DEFEAT_CONFIRMED\n",
    )
    latest_facts = RunFacts(
        overall_status="failed",
        reason="absorbed 0/1",
        duration_seconds=600,
        workflow_task="farm_echo_confirmed_retry",
        issues=[ReportItem("run-failure", "讨伐失败：吸收0/1")],
    )

    with patch("wuwa_auto.reporting.day_rollup.REPORTS_DIR", reports), patch(
        "wuwa_auto.reporting.day_rollup.RUNS_DIR", tmp_path / "runs"
    ):
        rolled_up = build_daily_rollup(latest, None, latest_facts)

    assert rolled_up.overall_status == "partial_success"
    assert rolled_up.followup == []
    assert [item.text for item in rolled_up.issues] == [
        "讨伐后续阶段：讨伐失败：吸收0/1"
    ]
    assert rolled_up.evidence["source"] == (
        "20260811_053000_daily,"
        "20260811_120000_farm_echo_confirmed_retry"
    )


def test_standalone_followup_without_daily_stays_a_phase_report(tmp_path: Path) -> None:
    result = _ok_result(
        tmp_path,
        run_id="20260810_170403_farm_echo_confirmed_retry",
        workflow="farm_echo_confirmed_retry",
        status="success",
        log_text="FarmEchoTask:farm echo walk_find_echo True\n",
    )
    facts = RunFacts(
        overall_status="completed",
        reason="completed",
        duration_seconds=1,
        workflow_task="farm_echo_confirmed_retry",
        followup=[ReportItem("echo-picked", "吸收声骸1次")],
    )
    reports = tmp_path / "reports"
    reports.mkdir()

    with patch("wuwa_auto.reporting.day_rollup.REPORTS_DIR", reports), patch(
        "wuwa_auto.reporting.day_rollup.RUNS_DIR", tmp_path / "runs"
    ):
        assert build_daily_rollup(result, None, facts) is facts


def test_rollup_preview_uses_a_distinct_archive_name() -> None:
    result = SimpleNamespace(run_id="20260809_170403_farm_echo_confirmed_retry")
    facts = RunFacts(
        overall_status="completed",
        reason="completed",
        duration_seconds=1,
        evidence={"source": "daily-run,farm-run"},
    )

    assert _archive_stem(result, facts).endswith("_daily_rollup")


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
                    '"daily":[],"weekly":[],"followup":[],'
                    '"issues":["主流程失败：测试异常"]}'
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
    assert narrative.issues == ["主流程失败：测试异常"]
    create_kwargs = client_class.return_value.chat.completions.create.call_args.kwargs
    assert create_kwargs["extra_body"] == {"thinking": {"type": "disabled"}}


def test_wuwa_agent_keeps_more_than_the_core_default_evidence_window() -> None:
    lines = [f"DailyTask: progress {index}" for index in range(1, 501)]

    evidence = build_wuwa_agent_evidence("\n".join(lines))

    assert len(evidence["line_refs"]) == 500
    assert "L500" in evidence["line_refs"]


def test_deepseek_length_response_retries_with_larger_output_budget() -> None:
    facts = RunFacts(
        overall_status="failed",
        reason="test failure",
        duration_seconds=1,
        issues=[ReportItem("failure", "程序确认失败")],
        evidence={"line_refs": ["L1"]},
    )
    first = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=""),
                finish_reason="length",
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=6_000,
            completion_tokens=8_192,
            total_tokens=14_192,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=8_192),
        ),
    )
    second = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content='{"summary":"恢复生成","daily":[],"weekly":[],'
                    '"followup":[],"issues":["任意模型措辞"]}'
                ),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=6_000,
            completion_tokens=200,
            total_tokens=6_200,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=0),
        ),
    )

    with patch("wuwa_auto.reporting.summarizer.get_secret") as secret, patch(
        "wuwa_auto.reporting.summarizer.OpenAI"
    ) as client_class:
        secret.side_effect = lambda name: {
            "DEEPSEEK_API_KEY": "test",
            "DEEPSEEK_MODEL": "test-model",
        }.get(name, "")
        create = client_class.return_value.chat.completions.create
        create.side_effect = [first, second]
        narrative = summarize_with_ai(facts)

    assert create.call_count == 2
    assert create.call_args_list[0].kwargs["max_tokens"] == 8_192
    assert create.call_args_list[1].kwargs["max_tokens"] == 16_384
    assert narrative.token_usage["input_tokens"] == 12_000
    assert narrative.token_usage["output_tokens"] == 8_392
    assert narrative.token_usage["attempts"] == 2
    assert narrative.token_usage["finish_reason"] == "stop"
    assert narrative.issues == ["程序确认失败"]


def test_streamed_wuwa_response_is_joined_and_keeps_usage() -> None:
    chunks = [
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content='{"summary":"完成",'),
                    finish_reason=None,
                )
            ],
            usage=None,
        ),
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content='"daily":[],"weekly":[],"followup":[],"issues":[]}'
                    ),
                    finish_reason="stop",
                )
            ],
            usage=None,
        ),
        SimpleNamespace(
            choices=[],
            usage=SimpleNamespace(
                prompt_tokens=100,
                completion_tokens=25,
                total_tokens=125,
            ),
        ),
    ]

    content, usage = _consume_completion(iter(chunks), model="test-model")

    assert json.loads(content)["summary"] == "完成"
    assert usage["input_tokens"] == 100
    assert usage["output_tokens"] == 25
    assert usage["finish_reason"] == "stop"


def test_agent_report_uses_full_sections_without_fact_id_wording_map() -> None:
    facts = RunFacts(
        overall_status="completed",
        reason="completed",
        duration_seconds=1,
        daily=[ReportItem("daily", "程序确认的日常事实")],
        followup=[ReportItem("followup", "程序确认的后续事实")],
    )

    narrative = _parse_agent_report(
        {
            "summary": "鸣潮日常完成",
            "daily": ["自然语言日常汇报"],
            "weekly": [],
            "followup": ["自然语言后续汇报"],
            "issues": [],
        },
        facts,
        token_usage={},
    )

    assert narrative.daily == ["自然语言日常汇报"]
    assert narrative.followup == ["自然语言后续汇报"]


def test_impacted_report_cannot_replace_program_facts_with_hallucinated_causes() -> None:
    facts = RunFacts(
        overall_status="failed",
        reason="active character bind failed",
        duration_seconds=61,
        followup=[ReportItem("rebind", "Worker重绑定1次")],
        issues=[ReportItem("failure", "当前角色绑定失败")],
    )

    narrative = _parse_agent_report(
        {
            "summary": "HUD不稳定且三次重试耗尽，任务失败",
            "daily": [],
            "weekly": [],
            "followup": ["未进入HUD"],
            "issues": ["三次重试耗尽"],
        },
        facts,
        token_usage={},
    )

    assert narrative.summary == "鸣潮日常失败，耗时1分1秒"
    assert narrative.followup == ["Worker重绑定1次"]
    assert narrative.issues == ["当前角色绑定失败"]


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
                    content='{"summary":"鸣潮日常完成","daily":[],'
                    '"weekly":[],"followup":[],"issues":["可选任务未执行"]}'
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
    assert _safe_summary("鸣潮日常完成，耗时1秒", completed) == (
        "鸣潮日常完成，耗时1秒"
    )
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


def test_client_restart_is_not_reported_as_an_extra_death(tmp_path: Path) -> None:
    result = _result(
        tmp_path,
        "HOST_FARM_ECHO_REALM_DEFEAT_CONFIRMED\n",
        status="failed",
        reason=(
            "FarmEcho recovery incomplete: absorbed 0/1; "
            "recoveries=2; retry=maximum retry count exhausted"
        ),
        config={
            "workflow_task": "farm_echo_confirmed_retry",
            "farm_echo_recovery": {
                "triggered": True,
                "target_count": 1,
                "recovery_attempts": 2,
                "retry_completed": 0,
                "total_completed": 0,
                "first_safe_recovery": True,
                "final_safe_recovery": True,
                "recoveries": [
                    {
                        "success": True,
                        "realm_defeat": True,
                        "kind": "death_recovery",
                    },
                    {
                        "success": True,
                        "realm_defeat": False,
                        "kind": "client_restart",
                        "reason": "client restarted once to restore upstream combat",
                    },
                ],
            },
        },
    )

    facts = parse_run(result)

    issue_text = [item.text for item in facts.issues]
    assert (
        "讨伐副本团灭1次，客户端重启1次，已自动恢复并重试；"
        "声骸累计吸收0/1次"
    ) in issue_text
    assert all("中途倒地" not in text for text in issue_text)
    assert "recoveries=1" in facts.reason
    assert all("recoveries=2" not in text for text in issue_text)


def test_worker_rebind_failure_is_reported_without_fabricating_death(
    tmp_path: Path,
) -> None:
    result = _result(
        tmp_path,
        "FarmEchoTask:could not find char 0 please check current char\n",
        status="failed",
        reason="FarmEcho recovery incomplete: absorbed 0/1; recoveries=0",
        config={
            "workflow_task": "farm_echo_confirmed_retry",
            "target_count": 1,
            "confirmed_farm_echo_absorption_count": 0,
            "farm_echo_recovery": {
                "triggered": True,
                "target_count": 1,
                "total_completed": 0,
                "retry_completed": 0,
                "recovery_attempts": 0,
                "combat_rebind_attempts": 1,
                "client_restart_triggered": False,
                "retry_runs": 1,
                "retry_limit": 3,
                "recoveries": [],
            },
        },
    )

    facts = parse_run(result)
    issue_text = [item.text for item in facts.issues]

    assert (
        "上游战斗劣化后Worker重绑定1次，已执行1/3次Worker重试，仍未恢复"
        in issue_text
    )
    assert "worker_retries=1/3" in facts.reason
    assert all("倒地" not in text and "团灭" not in text for text in issue_text)


def test_progress_driven_worker_retries_do_not_report_a_false_total_cap(
    tmp_path: Path,
) -> None:
    result = _result(
        tmp_path,
        "HOST_FARM_ECHO_ABSORPTION_CONFIRMED 5/5\n",
        status="success",
        reason="FarmEcho recovered and completed 5/5",
        config={
            "workflow_task": "farm_echo_confirmed_retry",
            "target_count": 5,
            "confirmed_farm_echo_absorption_count": 5,
            "farm_echo_recovery": {
                "triggered": True,
                "target_count": 5,
                "total_completed": 5,
                "retry_completed": 5,
                "recovery_attempts": 0,
                "combat_rebind_attempts": 1,
                "client_restart_triggered": False,
                "retry_runs": 5,
                "retry_limit": None,
                "progress_driven_retries": True,
                "recoveries": [],
            },
        },
    )

    facts = parse_run(result)
    followup = [item.text for item in facts.followup]

    assert (
        "上游战斗劣化后Worker重绑定1次，已执行5次Worker重试"
        "（有吸收进度不设总上限），任务已恢复"
    ) in followup
    assert "worker_retries=5 (progress-driven)" in facts.reason
    assert all("5/3" not in text for text in followup)


def test_progress_worker_boundary_is_preserved_after_success(tmp_path: Path) -> None:
    result = _result(
        tmp_path,
        "RuntimeError: confirmed retry exhausted its bounded combat attempts: "
        "absorbed=3/5\nHOST_FARM_ECHO_ABSORPTION_CONFIRMED 2/2\n",
        status="success",
        reason="FarmEcho recovered and completed 5/5",
        config={
            "workflow_task": "farm_echo_confirmed_retry",
            "target_count": 5,
            "confirmed_farm_echo_absorption_count": 5,
            "farm_echo_recovery": {
                "triggered": True,
                "target_count": 5,
                "initial_completed": 3,
                "retry_completed": 2,
                "total_completed": 5,
                "recovery_attempts": 0,
                "combat_rebind_attempts": 0,
                "client_restart_triggered": False,
                "retry_runs": 1,
                "retry_limit": None,
                "progress_driven_retries": True,
                "recoveries": [],
            },
        },
    )

    facts = parse_run(result)

    assert any(
        item.text
        == "首个Worker达到尝试上限时确认3/5，后续Worker续跑1次并补吸收2次，"
        "累计5/5（有吸收进度不设总上限），任务已恢复"
        for item in facts.followup
    )
    assert not facts.issues


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
    unknown = next(
        item for item in facts.issues
        if item.item_id == "daily-activity-unknown-unknown-1"
    )
    assert unknown.text.startswith("每日活跃任务未完成")
