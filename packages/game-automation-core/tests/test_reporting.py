import json
from types import SimpleNamespace

from game_automation_core.reporting.agent import (
    build_evidence_bundle,
    diagnostic_lines,
    diagnostics_match_status,
    redact_sensitive_data,
    token_usage_from_response,
    validate_diagnostics,
)
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


def test_evidence_bundle_is_line_addressable_and_redacts_secrets() -> None:
    bundle = build_evidence_bundle(
        game="wuwa",
        source="current.log",
        log_text="""DailyTask: completed\n兑换码使用成功: secret-code (1/1)\nERROR: retry\n""",
    )

    assert bundle["schema_version"] == "report-agent-evidence.v1"
    assert bundle["line_refs"] == ["L1", "L2", "L3"]
    assert "secret-code" not in str(bundle)

    generic = build_evidence_bundle(
        game="wuwa",
        log_text="FEISHU_WEBHOOK_SECRET=abc123 api_key:xyz token=tok123 Bearer abc.def",
    )
    generic_text = str(generic)
    assert "abc123" not in generic_text
    assert "xyz" not in generic_text
    assert "tok123" not in generic_text
    assert "abc.def" not in generic_text

    structured = build_evidence_bundle(
        game="wuwa",
        log_text='{"api_key":"SUPERSECRET123","token":"TOK123"}',
    )
    structured_text = str(structured)
    assert "SUPERSECRET123" not in structured_text
    assert "TOK123" not in structured_text


def test_evidence_bundle_bounds_a_single_oversized_line() -> None:
    bundle = build_evidence_bundle(
        game="wuwa",
        log_text="ERROR: " + "x" * 20_000,
        max_chars=100,
    )

    assert sum(len(str(item["text"])) + 14 for item in bundle["lines"]) <= 100


def test_evidence_bundle_ignores_lines_without_renumbering_refs() -> None:
    bundle = build_evidence_bundle(
        game="wuwa",
        log_text="""TaskExecutor:install ocr translations error for zh_CN
DailyTask: open daily panel
StartController:NVIDIA RTX Dynamic Vibrance is enabled and may cause malfunctions!
ERROR: final failure marker""",
        ignored_line_numbers={1, 3},
    )

    assert bundle["line_refs"] == ["L2", "L4"]
    assert [entry["line"] for entry in bundle["lines"]] == [2, 4]


def test_long_evidence_keeps_final_error_marker() -> None:
    lines = [f"DailyTask: progress {index}" for index in range(1, 301)]
    lines[-1] = "ERROR: final failure marker"
    bundle = build_evidence_bundle(game="wuwa", log_text="\n".join(lines))

    assert "L300" in bundle["line_refs"]


def test_evidence_budget_prioritizes_final_error_over_long_prefix() -> None:
    lines = [f"DailyTask: {'x' * 1000} {index}" for index in range(1, 300)]
    lines.append("ERROR: final failure marker")
    bundle = build_evidence_bundle(game="wuwa", log_text="\n".join(lines))

    assert "L300" in bundle["line_refs"]


def test_evidence_budget_keeps_last_real_line_after_filtered_tail() -> None:
    lines = [f"DailyTask: progress {index}" for index in range(1, 277)]
    lines.append("ERROR: final real failure")
    lines.extend(f"TaskExecutor: startup noise {index}" for index in range(277, 301))
    bundle = build_evidence_bundle(
        game="wuwa",
        log_text="\n".join(lines),
        max_chars=1_000,
        ignored_line_numbers=set(range(278, 302)),
    )

    assert "L277" in bundle["line_refs"]


def test_diagnostics_require_real_evidence_refs() -> None:
    evidence = {"line_refs": ["L3"]}
    analysis = validate_diagnostics(
        {
            "root_cause": "重试由错误触发",
            "root_cause_refs": ["L3", "L99"],
            "anomalies": [
                {
                    "message": "发现重试",
                    "evidence_refs": ["L3"],
                    "confidence": "high",
                },
                {
                    "message": "无证据的猜测",
                    "evidence_refs": ["L99"],
                    "confidence": "high",
                },
            ],
        },
        evidence,
    )

    assert analysis["root_cause_refs"] == ["L3"]
    assert len(analysis["anomalies"]) == 1
    assert diagnostic_lines(analysis)[0].startswith("AI分析：")


def test_malformed_diagnostic_refs_are_ignored_without_raising() -> None:
    assert validate_diagnostics(
        {"anomalies": [{"message": "bad", "evidence_refs": None}]},
        {"line_refs": ["L1"]},
    ) == {}


def test_token_usage_supports_openai_style_response() -> None:
    response = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=25,
            total_tokens=125,
        )
    )

    usage = token_usage_from_response(response, model="test-model")

    assert usage.available is True
    assert usage.input_tokens == 100
    assert usage.output_tokens == 25
    assert usage.output_input_ratio == 0.25
    assert usage.to_dict()["model"] == "test-model"


def test_token_usage_reads_nested_cache_and_reasoning_details() -> None:
    response = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=25,
            total_tokens=125,
            prompt_tokens_details=SimpleNamespace(cached_tokens=40),
            completion_tokens_details=SimpleNamespace(reasoning_tokens=5),
        )
    )

    usage = token_usage_from_response(response)

    assert usage.cached_input_tokens == 40
    assert usage.reasoning_tokens == 5


def test_redact_sensitive_data_covers_nested_fact_strings() -> None:
    value = redact_sensitive_data(
        {"warnings": ["token=SUPERSECRET123"], "reason": "ok"}
    )

    assert "SUPERSECRET123" not in str(value)
    assert value["reason"] == "ok"


def test_diagnostics_cannot_reverse_failed_or_unconfirmed_status() -> None:
    assert not diagnostics_match_status(
        {"root_cause": "奖励领取成功"},
        "failed",
    )
    assert not diagnostics_match_status(
        {"anomalies": [{"message": "任务成功"}]},
        "unknown",
    )
    assert not diagnostics_match_status(
        {"uncertainties": ["奖励已领取"]},
        "unknown",
    )
