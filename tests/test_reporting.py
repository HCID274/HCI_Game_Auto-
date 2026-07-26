"""Regression tests for structured reporting and card rendering."""

import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from feishu_notify import send_starrail_report_card
from reporting.ai_summarizer import _build_ai_input, build_fallback_narrative
from reporting.config import (
    load_report_prompt,
    load_reporting_context,
    load_reporting_preferences,
    load_training_context,
)
from reporting.log_parser import parse_m7a_run
from reporting.models import StaminaRun
from reporting.reminders import format_active_reminders
from reporting.report_service import (
    _title_for,
    report_cutoff,
    report_main_run,
    wait_for_report_boundary,
)
from reporting.training_plan import load_training_plan, reconcile_training_plan


SUCCESS_LOG = """\
|                                                 开始执行体力计划                                                  |
2026-07-25 06:03:16,957 | INFO | 执行体力计划 [1/4]: 饰品提取 - 鎏金追忆, 计划次数: 31
---------------------------------- 开始刷饰品提取 - 鎏金追忆，总计1轮，每轮包含6次 ----------------------------------
2026-07-25 06:03:31,253 | INFO | 开拓力: 245/300
2026-07-25 06:06:13,944 | INFO | 第1次副本完成
2026-07-25 06:06:41,880 | INFO | 副本任务完成
2026-07-25 06:06:41,881 | INFO | 体力计划剩余: 饰品提取 - 鎏金追忆, 剩余次数: 25
2026-07-25 06:06:41,881 | INFO | 执行体力计划 [2/4]: 侵蚀隧洞 - 魔占之径, 计划次数: 98
2026-07-25 06:06:46,096 | INFO | 开拓力: 6/300
2026-07-25 06:06:46,097 | INFO | 开拓力 < 40
2026-07-25 06:06:46,097 | INFO | 无法执行: 侵蚀隧洞 - 魔占之径，保留该计划
----------------------------------------------------- 今日实训 ------------------------------------------------------
2026-07-25 06:07:02,497 | INFO | 登录游戏: 已完成 +  (+100分)
2026-07-25 06:07:02,497 | INFO | 派遣委托或收取1次委托奖励: 待完成
2026-07-25 06:07:12,567 | INFO | 完成任务: 派遣委托或收取1次委托奖励 (+100分)，当前分数: 400/500
2026-07-25 06:07:38,259 | INFO | 完成任务: 使用1次「万能合成机」 (+100分)，当前分数: 500/500
2026-07-25 06:07:50,773 | INFO | 每日实训已完成
------------------------------------------------- 每日实训奖励完成 --------------------------------------------------
|                                                     停止运行                                                      |
"""


class TrainingContextTests(unittest.TestCase):
    def test_report_prompt_includes_few_shot_examples(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prompt_path = Path(directory) / "prompt.md"
            examples_path = Path(directory) / "examples.md"
            standard_path = Path(directory) / "standard.md"
            prompt_path.write_text("主规则", encoding="utf-8")
            examples_path.write_text("正确输出示例", encoding="utf-8")
            standard_path.write_text("固定输出标准", encoding="utf-8")

            prompt = load_report_prompt(prompt_path, examples_path, standard_path)

        self.assertIn("主规则", prompt)
        self.assertIn("正确输出示例", prompt)
        self.assertIn("固定输出标准", prompt)

    def test_custom_preferences_are_not_loaded_as_system_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            prompt_path = directory_path / "prompt.md"
            examples_path = directory_path / "examples.md"
            standard_path = directory_path / "standard.md"
            preferences_path = directory_path / "preferences.md"
            prompt_path.write_text("核心协议", encoding="utf-8")
            examples_path.write_text("固定示例", encoding="utf-8")
            standard_path.write_text("不可覆盖", encoding="utf-8")
            preferences_path.write_text("额外关注远坂凛", encoding="utf-8")

            prompt = load_report_prompt(prompt_path, examples_path, standard_path)
            preferences = load_reporting_preferences(preferences_path)

        self.assertNotIn("额外关注远坂凛", prompt)
        self.assertEqual(preferences, "额外关注远坂凛")

    def test_commented_example_does_not_become_active_context(self) -> None:
        content = """\
- 角色：
<!--
- 角色：Archer
- 关联副本或关键词：鎏金追忆
-->
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "training_context.md"
            path.write_text(content, encoding="utf-8")
            context = load_training_context(path)

        self.assertNotIn("character_goals", context)

    def test_blank_fields_do_not_consume_the_following_markdown_line(self) -> None:
        content = """\
- 角色：
- 培养目标：
- 关联副本或关键词：

## 今天的安排
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "training_context.md"
            path.write_text(content, encoding="utf-8")
            context = load_training_context(path)

        self.assertNotIn("character_goals", context)

    def test_markdown_context_is_loaded_and_fields_are_extracted(self) -> None:
        content = """\
# 培养计划

- 角色：Archer
- 培养目标：遗器与行迹
- 关联副本或关键词：鎏金追忆、魔占之径

今天优先刷遗器。
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "training_context.md"
            path.write_text(content, encoding="utf-8")
            context = load_training_context(path)

        self.assertIn("今天优先刷遗器", context["training_context_markdown"])
        self.assertEqual(context["character_goals"][0]["character"], "Archer")
        self.assertEqual(
            context["character_goals"][0]["keywords"],
            ["鎏金追忆", "魔占之径"],
        )


class TrainingPlanTests(unittest.TestCase):
    PLAN = """\
# 星铁养成计划

## 进行中

- [ ] `rin-trace` 远坂凛｜行迹材料
  - 关联副本：拟造花萼（赤）·海原电视塔
  - 完成条件：关联副本计划已全部完成

- [ ] `archer-relic` Archer｜遗器
  - 关联副本：待填写
  - 完成条件：人工确认

## 已完成

- 暂无
"""

    def test_markdown_plan_is_structured_and_keeps_unmapped_todos(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "training_plan.md"
            path.write_text(self.PLAN, encoding="utf-8")
            plan = load_training_plan(path)

        self.assertEqual(len(plan.active_goals), 2)
        self.assertEqual(plan.active_goals[0].character, "远坂凛")
        self.assertEqual(plan.active_goals[1].dungeon, "待填写")

    def test_explicit_zero_remaining_completes_and_persists_goal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "training_plan.md"
            path.write_text(self.PLAN, encoding="utf-8")
            result = reconcile_training_plan(
                [
                    StaminaRun(
                        name="拟造花萼（赤） - 海原电视塔",
                        remaining_plan_count=0,
                        status="completed",
                    )
                ],
                completed_at=datetime(2026, 7, 26, 6, 8),
                path=path,
            )

            persisted = load_training_plan(path)

        self.assertEqual(
            [goal.goal_id for goal in result.completed_this_run],
            ["rin-trace"],
        )
        completed = next(goal for goal in persisted.goals if goal.goal_id == "rin-trace")
        self.assertTrue(completed.completed)
        self.assertEqual(completed.completed_at, "2026-07-26")
        self.assertIn("剩余计划0次", completed.evidence)

    def test_partial_run_without_remaining_count_does_not_complete_goal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "training_plan.md"
            path.write_text(self.PLAN, encoding="utf-8")
            result = reconcile_training_plan(
                [
                    StaminaRun(
                        name="拟造花萼（赤） - 海原电视塔",
                        planned_count=54,
                        rounds=1,
                        rewards_per_round=24,
                        status="completed",
                    )
                ],
                completed_at=datetime(2026, 7, 26, 6, 8),
                path=path,
            )

        self.assertFalse(result.completed_this_run)
        self.assertFalse(result.goals[0].completed)


class ReminderTests(unittest.TestCase):
    def test_monthly_card_countdown_is_derived_from_expiry_date(self) -> None:
        content = """\
- [ ] `monthly-card` 月卡
  - 到期日期：2026-08-03
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reminders.md"
            path.write_text(content, encoding="utf-8")
            today = format_active_reminders(date(2026, 7, 26), path)
            tomorrow = format_active_reminders(date(2026, 7, 27), path)

        self.assertEqual(today, ["距离月卡过期还有8天"])
        self.assertEqual(tomorrow, ["距离月卡过期还有7天"])


class LogParserTests(unittest.TestCase):
    def test_training_plan_mapping_ignores_dungeon_separator_style(self) -> None:
        report = parse_m7a_run(
            "2026-07-26 06:00:00,000 | INFO | 执行体力计划 [1/1]: "
            "拟造花萼（赤） - 海原电视塔, 计划次数: 54",
            now=datetime(2026, 7, 26, 6, 0),
            preferences={
                "character_goals": [
                    {
                        "character": "远坂凛",
                        "goal": "行迹材料",
                        "keywords": ["拟造花萼（赤）·海原电视塔"],
                    }
                ]
            },
        )

        self.assertEqual(
            report.stamina_runs[0].character_context,
            "用于培养远坂凛（行迹材料）",
        )

    def test_extracts_specific_stamina_and_daily_results(self) -> None:
        preferences = {
            "character_goals": [
                {
                    "character": "Archer",
                    "goal": "遗器培养",
                    "keywords": ["鎏金追忆"],
                }
            ]
        }
        report = parse_m7a_run(
            SUCCESS_LOG,
            now=datetime(2026, 7, 25, 6, 8),
            preferences=preferences,
        )

        self.assertEqual(report.daily_status, "completed")
        self.assertEqual(report.daily_score, "500/500")
        self.assertEqual(
            report.daily_completed_this_run,
            ["派遣委托或收取1次委托奖励", "使用1次「万能合成机」"],
        )
        self.assertTrue(report.stopped_normally)
        self.assertEqual(report.stamina_start, 245)
        self.assertEqual(report.stamina_end, 6)
        self.assertEqual(len(report.stamina_runs), 2)
        stamina = report.stamina_runs[0]
        self.assertEqual(stamina.name, "饰品提取 - 鎏金追忆")
        self.assertEqual(stamina.rounds, 1)
        self.assertEqual(stamina.rewards_per_round, 6)
        self.assertEqual(stamina.remaining_plan_count, 25)
        self.assertEqual(stamina.character_context, "用于培养Archer（遗器培养）")
        self.assertEqual(
            report.stamina_runs[1].reason,
            "开拓力 < 40，保留该计划",
        )

    def test_repeated_interface_failures_are_classified_as_stalled(self) -> None:
        lines = [
            (
                f"2026-07-17 13:41:{index:02d},000 | WARNING | "
                "未识别出任何界面，请确保游戏画面干净，按ESC后重试"
            )
            for index in range(10, 16)
        ]
        report = parse_m7a_run(
            "\n".join(lines),
            now=datetime(2026, 7, 17, 13, 41, 20),
        )

        self.assertEqual(report.overall_status, "stalled")
        self.assertIn("重复6次", report.current_reason)
        self.assertIn("未识别出任何界面", report.current_reason)

    def test_stale_log_reports_last_progress_and_duration(self) -> None:
        content = (
            "|                                                   开始差分宇宙                                                    |\n"
            "2026-07-17 13:45:00,000 | INFO | 当前进度：(13/13) 第三位面-首领"
        )
        report = parse_m7a_run(
            content,
            now=datetime(2026, 7, 17, 14, 0),
        )

        self.assertEqual(report.overall_status, "stalled")
        self.assertEqual(
            report.current_task,
            "差分宇宙：当前进度：(13/13) 第三位面-首领",
        )
        self.assertIn("15分钟无进展", report.current_reason)
        self.assertIn("第三位面-首领", report.current_reason)

    def test_differential_universe_updates_current_task(self) -> None:
        content = """\
|                                               开始领取每日实训奖励                                                |
2026-07-17 13:55:17,360 | INFO | 每日实训已完成
2026-07-17 13:55:31,977 | INFO | 开始「差分宇宙」
2026-07-17 14:17:40,821 | INFO | 尝试进入战斗
"""
        report = parse_m7a_run(
            content,
            now=datetime(2026, 7, 17, 14, 19, 46),
            run_stage="旧版硬超时：差分宇宙仍在运行，但达到30分钟上限",
            force_failed=True,
        )

        self.assertEqual(report.current_task, "差分宇宙：尝试进入战斗")

    def test_detected_character_is_not_linked_to_an_unrelated_plan(self) -> None:
        content = """\
2026-07-25 06:02:51,752 | INFO | 培养目标Archer的待刷副本:
2026-07-25 06:02:51,752 | INFO | 饰品提取 - 孽果盘生
2026-07-25 06:02:51,752 | INFO | 准备发送 winotify 通知（级别：全部，图片：否）
2026-07-25 06:03:16,957 | INFO | 执行体力计划 [1/4]: 饰品提取 - 鎏金追忆, 计划次数: 31
2026-07-25 06:06:41,880 | INFO | 副本任务完成
"""
        report = parse_m7a_run(content, now=datetime(2026, 7, 25, 6, 7))
        self.assertEqual(report.stamina_runs[0].character_context, "")


class FallbackNarrativeTests(unittest.TestCase):
    def test_ai_input_exposes_only_ordered_daily_events(self) -> None:
        report = parse_m7a_run(
            SUCCESS_LOG,
            now=datetime(2026, 7, 25, 6, 8),
        )

        ai_input = _build_ai_input(report)

        self.assertEqual(
            [event["type"] for event in ai_input["ordered_daily_events"]],
            ["stamina", "daily_task", "daily_task", "daily_result"],
        )
        self.assertNotIn("daily_initial_completed", ai_input)
        self.assertEqual(
            ai_input["ordered_daily_events"][0]["name"],
            "饰品提取·鎏金追忆",
        )
        self.assertEqual(
            ai_input["ordered_daily_events"][1]["name"],
            "派遣委托",
        )
        self.assertFalse(
            ai_input["ordered_daily_events"][0]["plan_fully_completed"]
        )

    def test_daily_contains_short_numbered_actions_and_stamina(self) -> None:
        report = parse_m7a_run(
            SUCCESS_LOG,
            now=datetime(2026, 7, 25, 6, 8),
            preferences={
                "character_goals": [
                    {
                        "character": "Archer",
                        "goal": "遗器培养",
                        "keywords": ["鎏金追忆"],
                    }
                ]
            },
        )
        narrative = build_fallback_narrative(report)

        self.assertTrue(narrative.daily.startswith("1. 饰品提取·鎏金追忆"))
        self.assertNotIn("未完成但不影响", narrative.daily)
        self.assertIn(
            "1. 饰品提取·鎏金追忆（Archer 遗器培养）6次，剩余计划25次",
            narrative.daily,
        )
        self.assertIn("2. 派遣委托", narrative.daily)
        self.assertIn("3. 万能合成机", narrative.daily)
        self.assertTrue(narrative.daily.endswith("4. 每日实训完成 500/500"))
        self.assertNotIn("1轮", narrative.daily)
        self.assertNotIn("每轮", narrative.daily)
        self.assertEqual(narrative.routine_tasks, ["领取每日实训奖励"])
        self.assertTrue(
            any("魔占之径：未执行" in item for item in narrative.other_tasks)
        )
        self.assertFalse(narrative.issues)

    def test_completed_stamina_without_remaining_count_is_explicit(self) -> None:
        content = """\
2026-07-21 06:03:00,000 | INFO | 执行体力计划 [1/1]: 拟造花萼（赤） - 海原电视塔, 计划次数: 18
----------------------------- 开始刷拟造花萼（赤） - 海原电视塔，总计1轮，每轮包含18次 ------------------------------
2026-07-21 06:07:50,251 | INFO | 副本任务完成
2026-07-21 06:08:38,814 | INFO | 每日实训已完成
|                                                     停止运行                                                      |
"""
        report = parse_m7a_run(content, now=datetime(2026, 7, 21, 6, 9))
        narrative = build_fallback_narrative(report)

        self.assertIn(
            "1. 拟造花萼（赤）·海原电视塔 18次，已完成",
            narrative.daily,
        )

    def test_final_failure_cannot_use_completed_title(self) -> None:
        report = parse_m7a_run(
            SUCCESS_LOG,
            now=datetime(2026, 7, 25, 6, 8),
            run_stage="后续任务超时",
            force_failed=True,
        )
        title, template = _title_for(report, datetime(2026, 7, 25, 8, 0))

        self.assertTrue(title.startswith("❌️ 星铁失败"))
        self.assertEqual(template, "red")

class ReportBoundaryTests(unittest.TestCase):
    def test_scheduled_run_uses_same_day_eight_oclock(self) -> None:
        started = datetime(2026, 7, 25, 6, 0)
        self.assertEqual(report_cutoff(started), datetime(2026, 7, 25, 8, 0))

    def test_manual_run_after_eight_gets_two_hours(self) -> None:
        started = datetime(2026, 7, 25, 13, 30)
        self.assertEqual(report_cutoff(started), started + timedelta(hours=2))

    def test_stop_marker_finishes_wait_without_reaching_cutoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.log"
            path.write_text("停止运行", encoding="utf-8")
            now = datetime(2026, 7, 25, 6, 10)
            content, cutoff_reached, report_time = wait_for_report_boundary(
                path,
                0,
                started_at=datetime(2026, 7, 25, 6, 0),
                now_fn=lambda: now,
                sleep_fn=lambda _seconds: None,
            )

        self.assertIn("停止运行", content)
        self.assertFalse(cutoff_reached)
        self.assertEqual(report_time, now)

    def test_completed_log_sends_one_final_card_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "2026-07-25.log"
            path.write_text(SUCCESS_LOG, encoding="utf-8")
            with patch(
                "reporting.report_service.send_starrail_report_card",
                return_value=True,
            ) as send, patch(
                "reporting.report_service.summarize_report",
                return_value=(
                    build_fallback_narrative(
                        parse_m7a_run(
                            SUCCESS_LOG,
                            now=datetime(2026, 7, 25, 6, 8),
                        )
                    ),
                    False,
                ),
            ), patch("reporting.report_service._archive_report"):
                report_main_run(
                    log_path=path,
                    offset=0,
                    started_at=datetime(2026, 7, 25, 6, 0),
                    exit_code=0,
                    stage="",
                    retries=0,
                )

        send.assert_called_once()
        self.assertTrue(send.call_args.kwargs["title"].startswith("✅️ 星铁完成"))
        self.assertIn("每日实训完成 500/500", send.call_args.kwargs["daily"])


class FeishuCardTests(unittest.TestCase):
    def test_card_keeps_daily_first_and_numbers_other_tasks(self) -> None:
        with patch("feishu_notify._send_payload", return_value=True) as send:
            result = send_starrail_report_card(
                title="✅️ 星铁完成 07-25 06:08 重试0",
                template="green",
                daily="1. 饰品提取·鎏金追忆 6次\n2. 每日实训完成 500/500",
                routine_tasks=[
                    "领取每日实训奖励",
                    "领取委托奖励",
                    "领取无名勋礼奖励",
                ],
                other_tasks=["侵蚀隧洞·魔占之径：未执行（开拓力不足）"],
                current_task="",
                issues=[],
                training_todos=["远坂凛：行迹材料", "Archer：遗器"],
                reminders=["距离月卡过期还有8天"],
            )

        self.assertTrue(result)
        payload = send.call_args.args[0]
        elements = payload["card"]["elements"]
        self.assertIn("**每日实训**", elements[0]["text"]["content"])
        self.assertIn("**日常**", elements[2]["text"]["content"])
        self.assertIn("1. 领取每日实训奖励", elements[2]["text"]["content"])
        self.assertIn("3. 领取无名勋礼奖励", elements[2]["text"]["content"])
        self.assertIn("**后续完成**", elements[4]["text"]["content"])
        self.assertIn("1. 侵蚀隧洞", elements[4]["text"]["content"])
        self.assertIn("**养成计划待办**", elements[6]["text"]["content"])
        self.assertIn("2. Archer：遗器", elements[6]["text"]["content"])
        self.assertIn("**提醒**", elements[8]["text"]["content"])
        self.assertIn("距离月卡过期还有8天", elements[8]["text"]["content"])


if __name__ == "__main__":
    unittest.main()
