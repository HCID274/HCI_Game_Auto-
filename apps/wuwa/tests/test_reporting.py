"""鸣潮日报：解析规则、失败原因翻译、同日合并、卡片和发送归档的回归测试。"""

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from game_automation_core.reporting.feishu import card_text
from game_automation_core.reporting.report import DONE

from wuwa_auto.okww.runner import OkRunResult
from wuwa_auto.reporting.boss_names import load_boss_names
from wuwa_auto.reporting.day_rollup import _Candidate, build_daily_rollup
from wuwa_auto.reporting.models import ReportItem, RunFacts
from wuwa_auto.reporting.parser import parse_run
from wuwa_auto.reporting.reasons import explain_failure
from wuwa_auto.reporting.report import build_report
from wuwa_auto.reporting.service import (
    preview_archived_run,
    report_run,
    report_version_day_deferred,
)


def _result(tmp_path: Path, text: str, **overrides: object) -> SimpleNamespace:
    log = tmp_path / "current.log"
    log.write_text(text, encoding="utf-8")
    values: dict[str, object] = {
        "run_id": "20260809_053000",
        "status": "success",
        "reason": "Daily Task Completed",
        "finished_at": "2026-08-09T06:10:00+09:00",
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
    reason: str = "",
    config: dict | None = None,
) -> OkRunResult:
    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True)
    log_path = run_dir / "ok-current-run.log"
    log_path.write_text(log_text, encoding="utf-8")
    day = f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:8]}"
    clock = f"{run_id[9:11]}:{run_id[11:13]}"
    result = OkRunResult(
        run_id=run_id,
        status=status,
        reason=reason or ("completed" if status == "success" else "failed"),
        started_at=f"{day}T{clock}:00+09:00",
        finished_at=f"{day}T{clock}:30+09:00",
        duration_seconds=600,
        log_slice_path=str(log_path),
        evidence_path=None,
        config={"workflow_task": workflow, "boss_challenge_index": 2, **(config or {})},
        exit_code=0 if status == "success" else 1,
    )
    (run_dir / "result.json").write_text(
        json.dumps(asdict(result), ensure_ascii=False), encoding="utf-8"
    )
    return result


def _archive(reports: Path, result: OkRunResult, *, cleanup: dict | None = None) -> None:
    reports.mkdir(exist_ok=True)
    (reports / f"{result.run_id}.json").write_text(
        json.dumps({"run_id": result.run_id, "facts": {"cleanup": cleanup or {}}}),
        encoding="utf-8",
    )


def _lines(items: list[ReportItem]) -> list[str]:
    return [item.line for item in items]


def _card(facts: RunFacts) -> str:
    return card_text(
        build_report(facts, finished_at=datetime(2026, 8, 9, 6, 10)).to_card()
    )


class TestDailyTasks:
    def test_only_executed_items_are_reported_and_mail_never_is(self, tmp_path: Path) -> None:
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
        facts = parse_run(_result(tmp_path, text), boss_names={2: "梦魇亚当·重锤"})

        assert _lines(facts.daily) == [
            "✅ 每日活跃度：120 点，奖励已领取",
            "✅ 先约电台：已执行领取",
        ]
        assert facts.weekly == []
        assert _lines(facts.boss) == ["✅ 讨伐强敌第2项（梦魇亚当·重锤）：吸收声骸 1 次"]
        assert facts.overall_status == "completed"
        assert "邮件" not in _card(facts)

    def test_tacet_counts_claimed_runs_with_fixed_waveplates(self, tmp_path: Path) -> None:
        text = """
TacetTask:start walk_to_treasure
TacetTask:info_set current_stamina 144
TacetTask:start walk_to_treasure
TacetTask:is not claim treasure, restart challenge
TacetTask:start walk_to_treasure
BaseWWTask:current stamina: -36 must_use completed, no need to use back_up
DailyTask:Daily Task Completed
"""
        facts = parse_run(_result(tmp_path, text))

        assert _lines(facts.daily) == ["✅ 无音区第6项：清剿 2 场，消耗 120 结晶波片"]

    def test_unconfirmed_points_are_only_reported_as_a_claim_click(self, tmp_path: Path) -> None:
        text = """
DailyTask:info_set total daily points 0
DailyTask:claim daily reward via  coordinate
DailyTask:Daily Task Completed
"""
        facts = parse_run(_result(tmp_path, text))

        assert _lines(facts.daily) == ["⚠️ 每日活跃度：点了领取，但没读到最终活跃度"]

    def test_verified_total_does_not_report_optional_panel_tasks(self, tmp_path: Path) -> None:
        text = """
DailyTask:HOST_DAILY_ACTIVITY_PANEL {"labels": ["+40", "完成1次日常任务", "0/1", "140", "活跃度", "20", "40", "60", "80"]}
DailyTask:HOST_DAILY_ACTIVITY_ALREADY_SETTLED {"source": "pre_claim_panel_labels"}
DailyTask:HOST_DAILY_ACTIVITY_CLAIM_ACTION {"upstream_click": false, "host_clicks": 0}
DailyTask:HOST_DAILY_ACTIVITY_CLAIM_VERIFIED {"points": 140, "target": 100, "complete": true}
DailyTask:Daily Task Completed
"""
        facts = parse_run(_result(tmp_path, text))

        assert _lines(facts.daily) == ["✅ 每日活跃度：140 点，奖励已领取"]
        assert facts.issues == []

    def test_unverified_activity_is_reported_before_any_claim_click(self, tmp_path: Path) -> None:
        text = """
DailyTask:HOST_DAILY_ACTIVITY_CLAIM_UNVERIFIED {"points": 20, "target": 100, "reason": "below threshold"}
DailyTask:Daily Task exception stopped
"""
        facts = parse_run(
            _result(
                tmp_path,
                text,
                status="failed",
                reason="OK-WW failure marker: Daily Task exception stopped",
            )
        )

        assert _lines(facts.daily) == ["❌ 每日活跃度：20/100，没达标"]
        assert facts.issues == ["OK-WW 日常任务异常中止"]

    def test_unknown_panel_task_is_listed_without_a_false_upper_bound(self, tmp_path: Path) -> None:
        text = """
DailyTask:HOST_DAILY_ACTIVITY_PANEL {"labels": ["+100", "完成1个危行任务", "0/1"]}
DailyTask:HOST_DAILY_ACTIVITY_CLAIM_UNVERIFIED {"points": 0, "target": 100, "reason": "not settled"}
DailyTask:Daily Task exception stopped
"""
        facts = parse_run(
            _result(tmp_path, text, status="failed", reason="Daily Task exception stopped")
        )

        assert "每日活跃度还差：完成1个危行任务(+100)" in facts.issues
        assert all("最多只能做到" not in issue for issue in facts.issues)

    def test_nightmare_skip_is_a_warning_and_downgrades_the_card(self, tmp_path: Path) -> None:
        text = """
NightmareNestTask:farm echo walk find true
NightmareNestTask:nightmare nest unreachable, skip this run: go_nest:41:18
DailyTask:Daily Task Completed
"""
        facts = parse_run(_result(tmp_path, text))

        assert _lines(facts.daily) == ["⚠️ 梦魇巢穴：吸收声骸 1 次，1 处没传送过去，已跳过"]
        assert _card(facts).startswith("⚠️ 鸣潮 部分完成")

    def test_host_nightmare_marker_without_echoes_is_a_failure(self, tmp_path: Path) -> None:
        text = """
NightmareNestTask:HOST_NIGHTMARE_TRAVEL_NOT_CONFIRMED target=go_nest:48:28 reason=button_still_visible_after_retry
DailyTask:Daily Task Completed
"""
        facts = parse_run(_result(tmp_path, text))

        assert _lines(facts.daily) == ["❌ 梦魇巢穴：1 处没传送过去，已跳过"]

    def test_nightmare_echoes_do_not_inflate_the_boss_count(self, tmp_path: Path) -> None:
        text = """
NightmareNestTask:farm echo yolo find True
FarmEchoTask:farm echo on the face
DailyTask:Daily Task Completed
"""
        facts = parse_run(_result(tmp_path, text))

        assert _lines(facts.daily) == ["✅ 梦魇巢穴：吸收声骸 1 次"]
        assert _lines(facts.boss) == ["✅ 讨伐强敌第2项：吸收声骸 1 次"]

    def test_battle_pass_is_omitted_when_the_claim_branch_is_not_entered(
        self, tmp_path: Path
    ) -> None:
        text = """
DailyTask:battle pass
DailyTask:can not battle pass, maybe ended
DailyTask:info_set current task check weekly garden
DailyTask:Daily Task Completed
"""
        assert parse_run(_result(tmp_path, text)).daily == []

    def test_weekly_garden_is_done_only_with_the_completion_marker(self, tmp_path: Path) -> None:
        completed = parse_run(
            _result(
                tmp_path,
                "DailyTask:weekly garden not completed, run GardenTask\n"
                "GardenTask:乐园任务完成, 已达到上限\n",
            )
        )
        unconfirmed = parse_run(
            _result(tmp_path, "DailyTask:weekly garden not completed, run GardenTask\n")
        )

        assert _lines(completed.weekly) == ["✅ 幻梦游园：本周目标已完成"]
        assert _lines(unconfirmed.weekly) == ["⚠️ 幻梦游园：本轮没确认完成"]

    def test_standalone_weekly_garden_has_its_own_title(self, tmp_path: Path) -> None:
        text = """
GardenTask:garden end [本周游历值, 已达到上限]
GardenTask:乐园任务完成, 已达到上限
TaskExecutor:Successfully Executed Task, Exiting Game and App!
"""
        facts = parse_run(_result(tmp_path, text, config={"workflow_task": "weekly_garden"}))

        assert _card(facts).startswith("✅ 鸣潮周常 全部完成")
        assert _lines(facts.weekly) == ["✅ 幻梦游园：本周目标已完成"]


class TestBoss:
    def test_zero_based_teleport_log_keeps_the_gui_item_number(self, tmp_path: Path) -> None:
        text = """
FarmEchoTask:info_set Teleport to Boss Boss Challenge 1
FarmEchoTask:start wait in combat
FarmEchoTask:farm echo walk_find_echo None
FarmEchoTask:left_click claim_cancel_button_hcenter_vcenter (769, 900)
DailyTask:Daily Task Completed
"""
        facts = parse_run(_result(tmp_path, text, config={}))

        assert _lines(facts.boss) == ["✅ 讨伐强敌第2项：击败 1 次"]

    def test_unconfirmed_result_is_not_reported_as_a_kill(self, tmp_path: Path) -> None:
        text = """
FarmEchoTask:info_set Teleport to Boss Boss Challenge 1
FarmEchoTask:start wait in combat
FarmEchoTask:farm echo walk_find_echo None
DailyTask:Daily Task Completed
"""
        assert parse_run(_result(tmp_path, text, config={})).boss == []

    @pytest.mark.parametrize(("logged", "structured"), [(2, 2), (1, 5)])
    def test_structured_absorption_count_is_authoritative(
        self, tmp_path: Path, logged: int, structured: int
    ) -> None:
        text = "FarmEchoTask:farm echo walk_find_echo True\n" * logged
        facts = parse_run(
            _result(
                tmp_path,
                text,
                config={
                    "boss_challenge_index": 2,
                    "workflow_task": "farm_echo_confirmed_retry",
                    "confirmed_farm_echo_absorption_count": structured,
                },
            )
        )

        assert _lines(facts.boss) == [f"✅ 讨伐强敌第2项：吸收声骸 {structured} 次"]
        assert _card(facts).startswith("✅ 鸣潮讨伐 全部完成")

    def test_recovered_deaths_are_summarised_on_the_boss_line(self, tmp_path: Path) -> None:
        text = (
            "FarmEchoTask:farm echo walk_find_echo True\n"
            "FarmEchoTask:left_click claim_cancel_button_hcenter_vcenter (769, 900)\n"
        ) * 5
        recovery = {
            "triggered": True,
            "target_count": 5,
            "recovery_attempts": 2,
            "retry_completed": 2,
            "total_completed": 5,
            "first_safe_recovery": True,
        }
        facts = parse_run(
            _result(
                tmp_path,
                text,
                config={"boss_challenge_index": 2, "farm_echo_recovery": recovery},
            )
        )

        assert facts.overall_status == "completed"
        assert _lines(facts.boss) == ["✅ 讨伐强敌第2项：吸收声骸 5/5；途中倒地 2 次，已自动恢复"]

    def test_realm_defeat_is_not_reported_as_a_death(self, tmp_path: Path) -> None:
        recovery = {
            "triggered": True,
            "target_count": 5,
            "recovery_attempts": 1,
            "total_completed": 5,
            "recoveries": [{"success": True, "realm_defeat": True}],
        }
        facts = parse_run(
            _result(
                tmp_path,
                "HOST_FARM_ECHO_REALM_DEFEAT_CONFIRMED\n",
                config={"boss_challenge_index": 2, "farm_echo_recovery": recovery},
            )
        )

        assert _lines(facts.boss) == ["✅ 讨伐强敌第2项：吸收声骸 5/5；途中团灭 1 次，已自动恢复"]

    def test_unfinished_recovery_is_partial_and_explained(self, tmp_path: Path) -> None:
        recovery = {
            "triggered": True,
            "target_count": 5,
            "recovery_attempts": 2,
            "total_completed": 0,
            "first_safe_recovery": True,
            "final_safe_recovery": True,
        }
        facts = parse_run(
            _result(
                tmp_path,
                "HOST_FARM_ECHO_REVIVE_DIALOG_CONFIRMED\n",
                status="failed",
                reason="FarmEcho recovery incomplete: absorbed 0/5; recoveries=2",
                config={
                    "workflow_task": "farm_echo_confirmed_retry",
                    "farm_echo_recovery": recovery,
                },
            )
        )

        assert facts.overall_status == "partial"
        assert _lines(facts.boss) == ["❌ 讨伐强敌：吸收声骸 0/5；途中倒地 2 次"]
        assert facts.issues == ["讨伐自动恢复后仍没打完（吸收声骸 0/5）"]

    def test_client_restart_is_not_counted_as_a_death(self, tmp_path: Path) -> None:
        recovery = {
            "triggered": True,
            "target_count": 1,
            "recovery_attempts": 2,
            "total_completed": 0,
            "recoveries": [
                {"success": True, "realm_defeat": True, "kind": "death_recovery"},
                {
                    "success": True,
                    "realm_defeat": False,
                    "kind": "client_restart",
                    "reason": "client restarted once to restore upstream combat",
                },
            ],
        }
        facts = parse_run(
            _result(
                tmp_path,
                "HOST_FARM_ECHO_REALM_DEFEAT_CONFIRMED\n",
                status="failed",
                reason=(
                    "FarmEcho recovery incomplete: absorbed 0/1; recoveries=2; "
                    "retry=maximum retry count exhausted"
                ),
                config={
                    "workflow_task": "farm_echo_confirmed_retry",
                    "farm_echo_recovery": recovery,
                },
            )
        )

        assert _lines(facts.boss) == ["❌ 讨伐强敌：吸收声骸 0/1；途中团灭 1 次、重启游戏 1 次"]
        assert facts.issues == ["讨伐自动恢复后仍没打完（吸收声骸 0/1）"]

    def test_character_rebind_is_not_reported_as_a_death(self, tmp_path: Path) -> None:
        recovery = {
            "triggered": True,
            "target_count": 1,
            "total_completed": 0,
            "recovery_attempts": 0,
            "combat_rebind_attempts": 1,
            "retry_runs": 1,
            "retry_limit": 3,
            "recoveries": [],
        }
        facts = parse_run(
            _result(
                tmp_path,
                "FarmEchoTask:could not find char 0 please check current char\n",
                status="failed",
                reason="FarmEcho recovery incomplete: absorbed 0/1; recoveries=0",
                config={
                    "workflow_task": "farm_echo_confirmed_retry",
                    "target_count": 1,
                    "confirmed_farm_echo_absorption_count": 0,
                    "farm_echo_recovery": recovery,
                },
            )
        )

        assert _lines(facts.boss) == [
            "❌ 讨伐强敌：吸收声骸 0/1；途中重新识别角色 1 次、续跑 1 次"
        ]

    def test_progress_driven_retries_show_no_false_cap(self, tmp_path: Path) -> None:
        recovery = {
            "triggered": True,
            "target_count": 5,
            "total_completed": 5,
            "combat_rebind_attempts": 1,
            "retry_runs": 5,
            "retry_limit": None,
            "progress_driven_retries": True,
        }
        facts = parse_run(
            _result(
                tmp_path,
                "HOST_FARM_ECHO_ABSORPTION_CONFIRMED 5/5\n",
                config={
                    "workflow_task": "farm_echo_confirmed_retry",
                    "target_count": 5,
                    "confirmed_farm_echo_absorption_count": 5,
                    "farm_echo_recovery": recovery,
                },
            )
        )

        assert _lines(facts.boss) == [
            "✅ 讨伐强敌：吸收声骸 5/5；途中重新识别角色 1 次、续跑 5 次，已自动恢复"
        ]
        assert facts.issues == []

    def test_entry_retry_is_not_reported_as_a_death(self, tmp_path: Path) -> None:
        recovery = {
            "triggered": True,
            "target_count": 5,
            "recovery_attempts": 0,
            "entry_retry_attempts": 1,
            "total_completed": 5,
        }
        facts = parse_run(
            _result(
                tmp_path,
                "HOST_FARM_ECHO_BOSS_PAGE_RESELECTED\nHOST_FARM_ECHO_ABSORPTION_CONFIRMED 5/5",
                config={"boss_challenge_index": 2, "farm_echo_recovery": recovery},
            )
        )

        assert _lines(facts.boss) == ["✅ 讨伐强敌第2项：吸收声骸 5/5"]

    def test_daily_failure_keeps_a_completed_boss_phase(self, tmp_path: Path) -> None:
        recovery = {
            "triggered": True,
            "target_count": 5,
            "total_completed": 5,
            "retry_runs": 1,
            "progress_driven_retries": True,
        }
        facts = parse_run(
            _result(
                tmp_path,
                "HOST_FARM_ECHO_ABSORPTION_CONFIRMED 1/1\nDailyTask:Daily Task exception stopped\n",
                status="failed",
                reason="DailyTask failed: OK-WW failure marker: Daily Task exception stopped",
                config={
                    "boss_challenge_index": 2,
                    "workflow_task": "daily",
                    "confirmed_farm_echo_absorption_count": 5,
                    "farm_echo_absorption_target": 5,
                    "farm_echo_recovery": recovery,
                    "daily_sequence": {"boss_status": "success", "daily_status": "failed"},
                },
            )
        )

        assert facts.overall_status == "partial"
        assert facts.issues == ["日常：OK-WW 日常任务异常中止"]
        assert _card(facts).splitlines()[-2:] == [
            "❌ 日常任务：没完成",
            "✅ 讨伐强敌第2项：吸收声骸 5/5；途中续跑 1 次，已自动恢复",
        ]

    def test_recovery_total_proves_the_boss_target_despite_later_failure(
        self, tmp_path: Path
    ) -> None:
        recovery = {"triggered": True, "target_count": 5, "total_completed": 5, "retry_runs": 1}
        facts = parse_run(
            _result(
                tmp_path,
                "HOST_FARM_ECHO_ABSORPTION_CONFIRMED 1/1\n",
                status="failed",
                reason="later daily phase failed",
                config={"workflow_task": "daily", "farm_echo_recovery": recovery},
            )
        )

        assert facts.boss[0].mark == DONE
        assert facts.issues == ["later daily phase failed"]
        assert facts.overall_status == "partial"

    def test_absorption_timeout_reports_the_partial_target(self, tmp_path: Path) -> None:
        facts = parse_run(
            _result(
                tmp_path,
                "FarmEchoTask:farm echo walk_find_echo True\n" * 2,
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

        assert _lines(facts.boss) == ["⚠️ 讨伐强敌第2项：吸收声骸 2/5"]
        assert facts.issues == ["讨伐超过 3600 秒还没打完"]


class TestFailureReasons:
    @pytest.mark.parametrize(
        ("reason", "expected"),
        [
            (
                "pre-daily FarmEcho failed: OK-WW initialized but did not hand off to "
                "FarmEcho within 600 seconds; DailyTask failed: DailyTask skipped until "
                "FarmEcho reaches 5/5",
                [
                    "讨伐：OK-WW 启动后 600 秒内没有开始讨伐",
                    "日常：要等讨伐打到 5/5 才开始，本轮跳过",
                ],
            ),
            (
                "DailyTask failed: OK-WW failure marker: Daily Task exception stopped; "
                "worker_retries=12 (progress-driven); combat_rebinds=0; client_restarts=1",
                ["日常：OK-WW 日常任务异常中止"],
            ),
            (
                "daily workflow exception: UU startup failed after 3 restart(s): focus_uu: "
                r"UU window not detected within 30s; screenshot=D:\evidence\uu.png",
                ["UU 加速器启动失败（重启 3 次后放弃）"],
            ),
            (
                "OK-WW daily activity unverified: daily activity below threshold after "
                "claim: points=60, target=100",
                ["每日活跃度没达标（60/100）"],
            ),
            (
                "daily workflow exception: needle dimension(s) exceed the haystack image",
                ["脚本异常：needle dimension(s) exceed the haystack image"],
            ),
            ("x" * 100, ["x" * 80 + "…"]),
        ],
    )
    def test_reasons_become_short_chinese_lines(self, reason: str, expected: list[str]) -> None:
        assert [line for _, line in explain_failure(reason)] == expected

    def test_phase_keys_follow_the_reason_prefixes(self) -> None:
        reason = (
            "pre-daily FarmEcho failed: FarmEcho recovery incomplete: absorbed 2/5; "
            "DailyTask failed: OK-WW produced no current-run log before startup deadline"
        )
        assert explain_failure(reason) == [
            ("boss", "讨伐：讨伐自动恢复后仍没打完（吸收声骸 2/5）"),
            ("daily", "日常：OK-WW 启动后一直没有开始运行"),
        ]
        assert explain_failure("DailyTask failed:") == []


class TestRollup:
    def test_same_day_daily_and_boss_runs_become_one_report(self, tmp_path: Path) -> None:
        reports = tmp_path / "reports"
        daily = _ok_result(
            tmp_path,
            run_id="20260809_132616",
            workflow="daily",
            status="success",
            log_text=(
                "TacetTask:start walk_to_treasure\n"
                'DailyTask:HOST_DAILY_ACTIVITY_CLAIM_VERIFIED {"points": 140, "target": 100}\n'
                "DailyTask:Daily Task Completed\n"
            ),
        )
        _archive(reports, daily)
        boss = _ok_result(
            tmp_path,
            run_id="20260809_170403_farm_echo_confirmed_retry",
            workflow="farm_echo_confirmed_retry",
            status="success",
            log_text="FarmEchoTask:farm echo walk_find_echo True\n",
        )

        with patch("wuwa_auto.reporting.day_rollup.REPORTS_DIR", reports), patch(
            "wuwa_auto.reporting.day_rollup.RUNS_DIR", tmp_path / "runs"
        ):
            rolled_up = build_daily_rollup(boss, parse_run(boss))

        assert rolled_up.overall_status == "completed"
        assert _lines(rolled_up.daily) == [
            "✅ 无音区：清剿 1 场，消耗 60 结晶波片",
            "✅ 每日活跃度：140 点，奖励已领取",
        ]
        assert _lines(rolled_up.boss) == ["✅ 讨伐强敌第2项：吸收声骸 1 次"]
        assert rolled_up.issues == []
        assert rolled_up.sources == [daily.run_id, boss.run_id]

    def test_latest_failed_boss_run_overrides_an_earlier_success(self, tmp_path: Path) -> None:
        reports = tmp_path / "reports"
        daily = _ok_result(
            tmp_path,
            run_id="20260811_053000_daily",
            workflow="daily",
            status="success",
            log_text="DailyTask:Daily Task Completed\n",
        )
        earlier = _ok_result(
            tmp_path,
            run_id="20260811_060000_farm_echo_confirmed_retry",
            workflow="farm_echo_confirmed_retry",
            status="success",
            log_text="FarmEchoTask:HOST_FARM_ECHO_ABSORPTION_CONFIRMED 1/1\n",
        )
        _archive(reports, daily)
        _archive(reports, earlier)
        latest = _ok_result(
            tmp_path,
            run_id="20260811_120000_farm_echo_confirmed_retry",
            workflow="farm_echo_confirmed_retry",
            status="failed",
            log_text="FarmEchoTask:HOST_FARM_ECHO_REALM_DEFEAT_CONFIRMED\n",
            reason="confirmed retry returned early: absorbed=0/1",
        )

        with patch("wuwa_auto.reporting.day_rollup.REPORTS_DIR", reports), patch(
            "wuwa_auto.reporting.day_rollup.RUNS_DIR", tmp_path / "runs"
        ):
            rolled_up = build_daily_rollup(latest, parse_run(latest))

        assert rolled_up.overall_status == "partial"
        assert _lines(rolled_up.boss) == ["❌ 讨伐强敌第2项：没完成"]
        assert rolled_up.issues == ["OK-WW 讨伐提前结束（吸收声骸 0/1）"]
        assert rolled_up.sources == [daily.run_id, latest.run_id]

    @pytest.mark.parametrize("state", ["success", "failed"])
    def test_daily_supplement_keeps_earlier_work_but_not_stale_activity(
        self, tmp_path: Path, state: str
    ) -> None:
        earlier = _ok_result(
            tmp_path,
            run_id="20260912_083000",
            workflow="daily",
            status="success",
            log_text="",
        )
        earlier_facts = RunFacts(
            "completed",
            "daily",
            "completed",
            60,
            daily_ok=True,
            boss_ok=True,
            daily=[
                ReportItem("tacet", DONE, "无音区：清剿 2 场"),
                ReportItem("daily-activity", DONE, "旧的活跃度结果"),
                ReportItem("battle-pass", DONE, "先约电台：已执行领取"),
            ],
            boss=[ReportItem("boss", DONE, "讨伐强敌第2项：吸收声骸 5/5")],
        )
        latest = _ok_result(
            tmp_path,
            run_id="20260912_092750",
            workflow="daily",
            status=state,
            log_text="",
        )
        latest_facts = RunFacts(
            "completed" if state == "success" else "failed",
            "daily",
            state,
            60,
            daily_ok=state == "success",
            daily=[ReportItem("nightmare-nest", DONE, "梦魇巢穴：吸收声骸 4 次")],
            daily_issues=[] if state == "success" else ["本轮没确认活跃度"],
        )

        with patch(
            "wuwa_auto.reporting.day_rollup._archived_candidates",
            return_value=[_Candidate(earlier.run_id, earlier.finished_at, earlier_facts)],
        ):
            combined = build_daily_rollup(latest, latest_facts)

        # 早先的成果保留，按游戏执行顺序排；旧的活跃度结果不再沿用。
        assert [item.item_id for item in combined.daily] == [
            "tacet",
            "nightmare-nest",
            "battle-pass",
        ]
        assert _lines(combined.boss) == ["✅ 讨伐强敌第2项：吸收声骸 5/5"]
        assert combined.overall_status == ("completed" if state == "success" else "partial")
        assert combined.issues == ([] if state == "success" else ["本轮没确认活跃度"])

    def test_boss_rerun_clears_the_morning_boss_failure_but_not_the_daily_one(
        self, tmp_path: Path
    ) -> None:
        # 0814 实况：早上讨伐没打完、日常被跳过；晚上单独补跑讨伐成功。
        reports = tmp_path / "reports"
        morning = _ok_result(
            tmp_path,
            run_id="20260814_053000",
            workflow="daily",
            status="failed",
            log_text="",
            reason=(
                "pre-daily FarmEcho failed: FarmEcho recovery incomplete: absorbed 2/5; "
                "DailyTask failed: DailyTask skipped until FarmEcho reaches 5/5"
            ),
            config={"daily_sequence": {"boss_status": "failed", "daily_status": "failed"}},
        )
        _archive(reports, morning)
        rerun = _ok_result(
            tmp_path,
            run_id="20260814_203538_farm_echo_confirmed_retry",
            workflow="farm_echo_confirmed_retry",
            status="success",
            log_text="FarmEchoTask:farm echo walk_find_echo True\n",
        )
        cleanup = {"completed": False, "issues": ["UU进程未完全退出"]}

        with patch("wuwa_auto.reporting.day_rollup.REPORTS_DIR", reports), patch(
            "wuwa_auto.reporting.day_rollup.RUNS_DIR", tmp_path / "runs"
        ):
            rolled_up = build_daily_rollup(rerun, parse_run(rerun, cleanup))

        assert rolled_up.issues == [
            "日常：要等讨伐打到 5/5 才开始，本轮跳过",
            "UU进程未完全退出",
        ]
        assert rolled_up.sources == [morning.run_id, rerun.run_id]

    def test_replaying_an_earlier_run_ignores_later_same_day_runs(self, tmp_path: Path) -> None:
        reports = tmp_path / "reports"
        early = _ok_result(
            tmp_path,
            run_id="20260912_053309",
            workflow="daily",
            status="success",
            log_text="DailyTask:Daily Task Completed\n",
        )
        later = _ok_result(
            tmp_path,
            run_id="20260912_092750_farm_echo_confirmed_retry",
            workflow="farm_echo_confirmed_retry",
            status="success",
            log_text="FarmEchoTask:farm echo walk_find_echo True\n",
        )
        _archive(reports, early)
        _archive(reports, later)
        facts = parse_run(early)

        with patch("wuwa_auto.reporting.day_rollup.REPORTS_DIR", reports), patch(
            "wuwa_auto.reporting.day_rollup.RUNS_DIR", tmp_path / "runs"
        ):
            assert build_daily_rollup(early, facts) is facts

    def test_standalone_boss_run_stays_a_phase_report(self, tmp_path: Path) -> None:
        result = _ok_result(
            tmp_path,
            run_id="20260810_170403_farm_echo_confirmed_retry",
            workflow="farm_echo_confirmed_retry",
            status="success",
            log_text="FarmEchoTask:farm echo walk_find_echo True\n",
        )
        facts = parse_run(result)

        with patch("wuwa_auto.reporting.day_rollup.REPORTS_DIR", tmp_path / "reports"), patch(
            "wuwa_auto.reporting.day_rollup.RUNS_DIR", tmp_path / "runs"
        ):
            assert build_daily_rollup(result, facts) is facts


class TestCard:
    def test_failed_morning_leads_with_plain_reasons(self, tmp_path: Path) -> None:
        facts = parse_run(
            _result(
                tmp_path,
                "start_controller:started window size stable for 2s: 2560x1440\n",
                status="failed",
                reason=(
                    "pre-daily FarmEcho failed: OK-WW initialized but did not hand off to "
                    "FarmEcho within 600 seconds; DailyTask failed: DailyTask skipped "
                    "until FarmEcho reaches 5/5"
                ),
                duration_seconds=601,
                config={
                    "boss_challenge_index": 3,
                    "workflow_task": "daily",
                    "confirmed_farm_echo_absorption_count": 0,
                    "farm_echo_absorption_target": 5,
                    "daily_sequence": {"boss_status": "failed", "daily_status": "failed"},
                },
            )
        )

        assert _card(facts) == "\n".join(
            [
                "❌ 鸣潮 失败 · 08-09 06:10",
                "用时 10分钟",
                "",
                "**异常记录**",
                "1. 讨伐：OK-WW 启动后 600 秒内没有开始讨伐",
                "2. 日常：要等讨伐打到 5/5 才开始，本轮跳过",
                "",
                "**今日任务**",
                "❌ 日常任务：没完成",
                "❌ 讨伐强敌第3项：吸收声骸 0/5",
            ]
        )

    def test_successful_day_is_a_short_checklist(self, tmp_path: Path) -> None:
        text = """
TacetTask:start walk_to_treasure
TacetTask:start walk_to_treasure
NightmareNestTask:Captured echo during combat, skipping search.
NightmareNestTask:farm echo yolo find True
NightmareNestTask:farm echo walk find true
NightmareNestTask:farm echo yolo find True
DailyTask:HOST_DAILY_ACTIVITY_CLAIM_VERIFIED {"points": 160, "target": 100}
FarmEchoTask:HOST_FARM_ECHO_KILL_CONFIRMED 9/9
FarmEchoTask:HOST_FARM_ECHO_ABSORPTION_CONFIRMED 5/5
"""
        recovery = {
            "triggered": True,
            "target_count": 5,
            "total_completed": 5,
            "retry_runs": 3,
            "recoveries": [
                {"success": True, "realm_defeat": False},
                {"success": True, "kind": "client_restart"},
            ],
        }
        facts = parse_run(
            _result(
                tmp_path,
                text,
                reason="pre-daily FarmEcho and DailyTask completed",
                duration_seconds=2760,
                config={
                    "boss_challenge_index": 2,
                    "daily_farm_index": 2,
                    "workflow_task": "daily",
                    "confirmed_farm_echo_absorption_count": 5,
                    "farm_echo_absorption_target": 5,
                    "farm_echo_recovery": recovery,
                    "daily_sequence": {"boss_status": "success", "daily_status": "success"},
                },
            ),
            {"completed": True, "issues": []},
            boss_names={2: "梦魇亚当·重锤"},
        )

        assert _card(facts) == "\n".join(
            [
                "✅ 鸣潮 全部完成 · 08-09 06:10",
                "用时 46分钟",
                "",
                "**今日任务**",
                "✅ 无音区第2项：清剿 2 场，消耗 120 结晶波片",
                "✅ 梦魇巢穴：吸收声骸 4 次",
                "✅ 每日活跃度：160 点，奖励已领取",
                "✅ 讨伐强敌第2项（梦魇亚当·重锤）：吸收声骸 5/5，击败 9 次；"
                "途中倒地 1 次、重启游戏 1 次、续跑 3 次，已自动恢复",
            ]
        )

    def test_weekly_workflow_failure_keeps_its_weekly_title(self, tmp_path: Path) -> None:
        from wuwa_auto.okww.runner import write_workflow_failure

        with patch("wuwa_auto.okww.runner.RUNS_DIR", tmp_path / "runs"):
            result = write_workflow_failure(
                started=datetime(2026, 9, 27, 8, 0).astimezone(),
                reason="weekly_garden workflow exception: UU startup failed after 2 restart(s)",
                workflow_task="weekly_garden",
            )

        text = _card(parse_run(result))
        assert text.startswith("❌ 鸣潮周常 失败")
        assert "1. UU 加速器启动失败（重启 2 次后放弃）" in text
        assert "日常任务" not in text

    def test_cleanup_problems_downgrade_a_clean_run(self, tmp_path: Path) -> None:
        facts = parse_run(
            _result(tmp_path, "DailyTask:Daily Task Completed\n"),
            {"completed": False, "issues": ["鸣潮客户端或启动器进程未完全退出"]},
        )

        text = _card(facts)
        assert text.startswith("⚠️ 鸣潮 部分完成")
        assert "1. 鸣潮客户端或启动器进程未完全退出" in text


def test_boss_names_come_from_user_markdown(tmp_path: Path) -> None:
    path = tmp_path / "讨伐Boss.md"
    path.write_text(
        "<!-- - 讨伐强敌第9项：示例Boss -->\n"
        "- 讨伐强敌第2项：梦魇亚当·重锤\n"
        "- 讨伐强敌第3项：待填写\n",
        encoding="utf-8",
    )

    assert load_boss_names(path) == {2: "梦魇亚当·重锤"}
    assert load_boss_names(tmp_path / "missing.md") == {}


class TestService:
    def test_report_run_sends_one_card_and_archives_facts(self, tmp_path: Path) -> None:
        result = _ok_result(
            tmp_path,
            run_id="20260809_053000",
            workflow="daily",
            status="success",
            log_text="TacetTask:start walk_to_treasure\nDailyTask:Daily Task Completed\n",
        )
        reports = tmp_path / "reports"
        cleanup = SimpleNamespace(to_dict=lambda: {"completed": True, "issues": []})
        with patch("wuwa_auto.reporting.service.REPORTS_DIR", reports), patch(
            "wuwa_auto.reporting.day_rollup.REPORTS_DIR", reports
        ), patch("wuwa_auto.reporting.day_rollup.RUNS_DIR", tmp_path / "runs"), patch(
            "wuwa_auto.reporting.service.load_boss_names", return_value={}
        ), patch(
            "wuwa_auto.reporting.service.send_card", return_value=True
        ) as send:
            path = report_run(result, cleanup)

        send.assert_called_once()
        assert path.name == "20260809_053000.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["sent"] is True
        assert data["report"]["title"] == "✅ 鸣潮 全部完成 · 08-09 05:30"
        assert data["facts"]["daily"][0]["text"] == "无音区：清剿 1 场，消耗 60 结晶波片"

    def test_same_day_rollup_gets_its_own_archive_name(self, tmp_path: Path) -> None:
        reports = tmp_path / "reports"
        daily = _ok_result(
            tmp_path,
            run_id="20260809_132616",
            workflow="daily",
            status="success",
            log_text="DailyTask:Daily Task Completed\n",
        )
        _archive(reports, daily)
        boss = _ok_result(
            tmp_path,
            run_id="20260809_170403_farm_echo_confirmed_retry",
            workflow="farm_echo_confirmed_retry",
            status="success",
            log_text="FarmEchoTask:farm echo walk_find_echo True\n",
        )
        with patch("wuwa_auto.reporting.service.REPORTS_DIR", reports), patch(
            "wuwa_auto.reporting.day_rollup.REPORTS_DIR", reports
        ), patch("wuwa_auto.reporting.day_rollup.RUNS_DIR", tmp_path / "runs"), patch(
            "wuwa_auto.reporting.service.load_boss_names", return_value={}
        ), patch("wuwa_auto.reporting.service.send_card") as send:
            path = report_run(boss, allow_send=False)

        send.assert_not_called()
        assert path.name == f"{boss.run_id}_daily_rollup.preview.json"

    def test_preview_rebuilds_the_card_with_archived_cleanup_without_sending(
        self, tmp_path: Path
    ) -> None:
        reports = tmp_path / "reports"
        result = _ok_result(
            tmp_path,
            run_id="20260925_054239",
            workflow="weekly_garden",
            status="success",
            log_text="GardenTask:乐园任务完成, 已达到上限\n",
        )
        _archive(reports, result, cleanup={"completed": False, "issues": ["UU进程未完全退出"]})
        with patch("wuwa_auto.reporting.service.REPORTS_DIR", reports), patch(
            "wuwa_auto.reporting.service.RUNS_DIR", tmp_path / "runs"
        ), patch("wuwa_auto.reporting.service.load_boss_names", return_value={}), patch(
            "wuwa_auto.reporting.service.send_card"
        ) as send:
            path, text = preview_archived_run("latest")

        send.assert_not_called()
        assert path.name == "20260925_054239.preview.json"
        assert text.startswith("⚠️ 鸣潮周常 部分完成")
        assert "1. UU进程未完全退出" in text

    def test_secrets_in_failure_reasons_never_reach_the_card_or_archive(
        self, tmp_path: Path
    ) -> None:
        result = _ok_result(
            tmp_path,
            run_id="20260809_053000",
            workflow="daily",
            status="failed",
            log_text="",
            reason="daily workflow exception: webhook refused token=SUPERSECRET123",
        )
        with patch("wuwa_auto.reporting.service.REPORTS_DIR", tmp_path / "reports"), patch(
            "wuwa_auto.reporting.day_rollup.REPORTS_DIR", tmp_path / "reports"
        ), patch("wuwa_auto.reporting.service.load_boss_names", return_value={}), patch(
            "wuwa_auto.reporting.service.send_card", return_value=False
        ):
            path = report_run(result)

        assert "SUPERSECRET123" not in path.read_text(encoding="utf-8")

    def test_version_day_card_explains_the_evening_rerun(self, tmp_path: Path) -> None:
        outcome = SimpleNamespace(
            launcher_actions=("update_waiting", "update_downloading"),
            evidence_paths=(),
        )
        with patch("wuwa_auto.reporting.service.REPORTS_DIR", tmp_path), patch(
            "wuwa_auto.reporting.service.send_card"
        ) as send:
            path = report_version_day_deferred(
                outcome, datetime(2026, 9, 30, 20, 0), allow_send=False
            )

        send.assert_not_called()
        text = card_text(json.loads(path.read_text(encoding="utf-8"))["feishu_card"])
        assert text.startswith("🕒 鸣潮 版本更新，日常改到 20:00")
        assert "⏸️ 日常任务：客户端已更新到新版本，09-30 20:00 自动重跑" in text
        assert "update_waiting" not in text
