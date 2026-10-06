"""Regression tests for Star Rail parsing, card wording and the report service."""

import json
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from game_automation_core.reporting.feishu import card_text

from starrail_auto.m7a.power_plan import load_power_plan_remaining
from starrail_auto.reporting.models import RunReport, StaminaRun
from starrail_auto.reporting.parser import parse_m7a_run
from starrail_auto.reporting.reminders import format_active_reminders
from starrail_auto.reporting.report import build_report
from starrail_auto.reporting.service import (
    preview_archived_run,
    report_main_run,
    send_short_report,
)
from starrail_auto.reporting.training_plan import (
    TrainingGoal,
    TrainingPlan,
    load_training_plan,
    reconcile_training_plan,
)

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

# 2026-09-29 真实失败的精简切片：贪饕教程弹窗挡住战斗，之后界面一直识别不出来。
TUTORIAL_BLOCKED_LOG = """\
|                                                 开始执行体力计划                                                  |
2026-09-29 05:32:56,342 | INFO | 执行体力计划 [1/3]: 饰品提取 - 西风丛中, 计划次数: 2
---------------------------------- 开始刷饰品提取 - 西风丛中，总计1轮，每轮包含2次 ----------------------------------
2026-09-29 05:35:02,704 | INFO | 第1次副本完成
2026-09-29 05:35:29,250 | INFO | 副本任务完成
2026-09-29 05:35:29,250 | INFO | 体力计划已完成: 饰品提取 - 西风丛中
2026-09-29 05:35:29,251 | INFO | 执行体力计划 [2/3]: 侵蚀隧洞 - 睿治之径, 计划次数: 100
---------------------------------- 开始刷侵蚀隧洞 - 睿治之径，总计1轮，每轮包含4次 ----------------------------------
2026-09-29 05:36:37,353 | INFO | 进入战斗
2026-09-29 06:06:43,008 | ERROR | 战斗超时
2026-09-29 06:06:43,008 | ERROR | 检测到该次副本未正常运行，重试：1/3
2026-09-29 06:07:47,379 | ERROR | 当前界面：未知
2026-09-29 06:07:47,380 | ERROR | 无法识别当前游戏界面
2026-09-29 06:07:47,380 | ERROR | 请关闭帧率监控HUD、微星小飞机、游戏加加、HDR或N卡游戏滤镜等等任何可能影响游戏画面的软件
2026-09-29 06:07:47,381 | ERROR | 执行体力计划时出错: 无法识别当前游戏界面，保留该计划
2026-09-29 06:07:47,381 | INFO | 执行体力计划 [3/3]: 饰品提取 - 鎏金追忆, 计划次数: 100
2026-09-29 06:08:50,539 | ERROR | 执行体力计划时出错: 无法识别当前游戏界面，保留该计划
|                                                    开始清体力                                                     |
2026-09-29 06:09:54,197 | ERROR | 发生错误 无法识别当前游戏界面
"""

RIN_TRACE = TrainingGoal("rin-trace", "远坂凛", "行迹材料", dungeon="拟造花萼（赤）·海原电视塔")
ARCHER_RELIC = TrainingGoal("archer-relic", "Archer", "遗器", dungeon="饰品提取·鎏金追忆")


def _report(run: RunReport, *, plan: TrainingPlan = TrainingPlan(), reminders=()):
    return build_report(
        run,
        plan=plan,
        reminders=list(reminders),
        finished_at=datetime(2026, 7, 25, 6, 8),
        duration_seconds=2460,
    )


def _stamina(card) -> list[str]:
    """卡片「体力去向」一栏的逐行内容。"""
    text = dict(card.notes).get("体力去向", "")
    return text.splitlines() if text else []


class TestTrainingPlan:
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

    def test_m7a_power_plan_snapshot_is_loaded(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text(
            "power_plan:\n  - [饰品提取, 鎏金追忆, 25]\n  - [拟造花萼（赤）, 海原电视塔, 29]\n",
            encoding="utf-8",
        )
        assert load_power_plan_remaining(path) == {
            "饰品提取 - 鎏金追忆": 25,
            "拟造花萼（赤） - 海原电视塔": 29,
        }

    def test_markdown_plan_is_structured_and_keeps_unmapped_todos(self, tmp_path: Path) -> None:
        path = tmp_path / "training_plan.md"
        path.write_text(self.PLAN, encoding="utf-8")
        plan = load_training_plan(path)

        assert [goal.character for goal in plan.active_goals] == ["远坂凛", "Archer"]
        assert plan.active_goals[1].dungeon == "待填写"

    def test_explicit_zero_remaining_completes_and_persists_goal(self, tmp_path: Path) -> None:
        path = tmp_path / "training_plan.md"
        path.write_text(self.PLAN, encoding="utf-8")
        result = reconcile_training_plan(
            [StaminaRun(name="拟造花萼（赤） - 海原电视塔", remaining_plan_count=0, status="completed")],
            completed_at=datetime(2026, 7, 26, 6, 8),
            path=path,
        )
        persisted = load_training_plan(path)

        assert [goal.goal_id for goal in result.completed_this_run] == ["rin-trace"]
        completed = next(goal for goal in persisted.goals if goal.goal_id == "rin-trace")
        assert completed.completed and completed.completed_at == "2026-07-26"
        assert "剩余计划0次" in completed.evidence

    def test_preview_reconciliation_never_writes_the_plan(self, tmp_path: Path) -> None:
        path = tmp_path / "training_plan.md"
        path.write_text(self.PLAN, encoding="utf-8")
        result = reconcile_training_plan(
            [StaminaRun(name="拟造花萼（赤） - 海原电视塔", remaining_plan_count=0, status="completed")],
            completed_at=datetime(2026, 7, 26, 6, 8),
            path=path,
            persist=False,
        )

        assert result.completed_this_run
        assert path.read_text(encoding="utf-8") == self.PLAN

    def test_partial_run_without_remaining_count_does_not_complete_goal(self, tmp_path: Path) -> None:
        path = tmp_path / "training_plan.md"
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

        assert not result.completed_this_run
        assert not result.goals[0].completed


def test_monthly_card_countdown_is_derived_from_expiry_date(tmp_path: Path) -> None:
    path = tmp_path / "reminders.md"
    path.write_text("- [ ] `monthly-card` 月卡\n  - 到期日期：2026-08-03\n", encoding="utf-8")

    assert format_active_reminders(date(2026, 7, 26), path) == ["距离月卡过期还有8天"]
    assert format_active_reminders(date(2026, 7, 27), path) == ["距离月卡过期还有7天"]


class TestParser:
    def test_extracts_specific_stamina_and_daily_results(self) -> None:
        report = parse_m7a_run(
            SUCCESS_LOG,
            now=datetime(2026, 7, 25, 6, 8),
            training_goals=[ARCHER_RELIC],
        )

        assert report.daily_status == "completed"
        assert report.daily_score == "500/500"
        assert report.daily_completed_this_run == ["派遣委托或收取1次委托奖励", "使用1次「万能合成机」"]
        assert report.daily_unfinished == []
        assert report.stopped_normally
        assert report.rewards == ["每日实训"]
        stamina, skipped = report.stamina_runs
        assert (stamina.name, stamina.rounds, stamina.rewards_per_round) == ("饰品提取 - 鎏金追忆", 1, 6)
        assert stamina.remaining_plan_count == 25
        assert stamina.trainee == "Archer 遗器"
        assert skipped.status == "skipped"
        assert skipped.reason == "开拓力 < 40，保留该计划"

    def test_m7a_full_width_colons_still_report_every_dungeon(self) -> None:
        # M7A v2026.10.3（10-04 起）把“计划次数”“剩余次数”后的冒号改成全角，
        # 旧规则漏掉了所有刷副本记录；以下是 10-05 日志原句。
        content = """\
2026-10-05 05:32:52,259 | INFO | 执行体力计划 [1/5]: 凝滞虚影 - 塞壬之形, 计划次数： 5
2026-10-05 05:33:00,942 | INFO | 开拓力: 239/300
---------------------------------- 开始刷凝滞虚影 - 塞壬之形，总计1轮，每轮包含5次 ----------------------------------
2026-10-05 05:35:43,510 | INFO | 第1次副本完成
2026-10-05 05:36:22,968 | INFO | 副本任务完成
2026-10-05 05:36:22,968 | INFO | 体力计划已完成: 凝滞虚影 - 塞壬之形
2026-10-05 05:36:22,968 | INFO | 执行体力计划 [2/5]: 拟造花萼（赤） - 「世界尽头」酒馆, 计划次数： 78
--------------------------- 开始刷拟造花萼（赤） - 「世界尽头」酒馆，总计1轮，每轮包含9次 ---------------------------
2026-10-05 05:38:52,009 | INFO | 第1次副本完成
2026-10-05 05:39:31,237 | INFO | 副本任务完成
2026-10-05 05:39:31,238 | INFO | 体力计划剩余: 拟造花萼（赤） - 「世界尽头」酒馆, 剩余次数： 69
2026-10-05 05:39:31,238 | INFO | 执行体力计划 [3/5]: 侵蚀隧洞 - 密伶之径, 计划次数： 100
2026-10-05 05:39:35,483 | INFO | 开拓力 < 40
2026-10-05 05:39:35,483 | INFO | 无法执行: 侵蚀隧洞 - 密伶之径，保留该计划
"""
        report = parse_m7a_run(content, now=datetime(2026, 10, 5, 5, 40))

        assert report.other_tasks == []
        assert _stamina(_report(report)) == [
            "✅ 凝滞虚影·塞壬之形：5 次，计划已完成",
            "✅ 拟造花萼（赤）·「世界尽头」酒馆：9 次，计划还剩 69 次",
            "⏸️ 侵蚀隧洞·密伶之径：开拓力 < 40，保留该计划",
        ]

    def test_same_plan_multiple_batches_are_accumulated(self) -> None:
        content = """\
|                                                 开始执行体力计划                                                  |
2026-07-30 10:09:18,440 | INFO | 执行体力计划 [1/7]: 拟造花萼（赤） - 海原电视塔, 计划次数: 127
----------------------------- 开始刷拟造花萼（赤） - 海原电视塔，总计1轮，每轮包含24次 ------------------------------
2026-07-30 10:13:26,092 | INFO | 第1次副本完成
2026-07-30 10:13:36,515 | INFO | 副本任务完成
------------------------------ 开始刷拟造花萼（赤） - 海原电视塔，总计1轮，每轮包含4次 ------------------------------
2026-07-30 10:15:07,461 | INFO | 第1次副本完成
2026-07-30 10:15:17,872 | INFO | 副本任务完成
2026-07-30 10:15:17,872 | INFO | 体力计划剩余: 拟造花萼（赤） - 海原电视塔, 剩余次数: 99
"""
        report = parse_m7a_run(content, now=datetime(2026, 7, 30, 10, 16))

        assert len(report.stamina_runs) == 1
        stamina = report.stamina_runs[0]
        assert (stamina.completed_instances, stamina.rounds, stamina.rewards_per_round) == (28, 2, None)
        assert stamina.remaining_plan_count == 99

    def test_activity_priority_keeps_activity_and_own_plan_counters(self) -> None:
        content = """\
2026-07-27 10:47:48,707 | INFO | 位面分裂剩余次数：12
---------------------------------- 开始刷饰品提取 - 鎏金追忆，总计1轮，每轮包含6次 ----------------------------------
2026-07-27 10:51:09,352 | INFO | 副本任务完成
---------------------------------- 开始刷饰品提取 - 鎏金追忆，总计1轮，每轮包含1次 ----------------------------------
2026-07-27 10:52:28,569 | INFO | 副本任务完成
|                                                 开始执行体力计划                                                  |
2026-07-27 10:52:28,570 | INFO | 执行体力计划 [1/7]: 拟造花萼（赤） - 海原电视塔, 计划次数: 30
------------------------------ 开始刷拟造花萼（赤） - 海原电视塔，总计1轮，每轮包含1次 ------------------------------
2026-07-27 10:53:30,503 | INFO | 副本任务完成
2026-07-27 10:53:30,503 | INFO | 体力计划剩余: 拟造花萼（赤） - 海原电视塔, 剩余次数: 29
2026-07-27 10:54:38,753 | INFO | 每日实训已完成
|                                                     停止运行                                                      |
"""
        report = parse_m7a_run(
            content,
            now=datetime(2026, 7, 27, 10, 55),
            training_goals=[RIN_TRACE],
            power_plan_remaining={"饰品提取 - 鎏金追忆": 25},
        )

        activity, plan = report.stamina_runs
        assert activity.source == "activity"
        assert (activity.completed_instances, activity.remaining_plan_count) == (7, 25)
        assert activity.activity_remaining_count == 5
        assert _stamina(_report(report)) == [
            "✅ 位面分裂：饰品提取·鎏金追忆：7 次，计划还剩 25 次，双倍还剩 5 次",
            "✅ 拟造花萼（赤）·海原电视塔（远坂凛 行迹材料）：1 次，计划还剩 29 次",
        ]
        assert plan.trainee == "远坂凛 行迹材料"

    def test_training_plan_mapping_ignores_dungeon_separator_style(self) -> None:
        report = parse_m7a_run(
            "2026-07-26 06:00:00,000 | INFO | 执行体力计划 [1/1]: 拟造花萼（赤） - 海原电视塔, 计划次数: 54",
            now=datetime(2026, 7, 26, 6, 0),
            training_goals=[RIN_TRACE],
        )

        assert report.stamina_runs[0].trainee == "远坂凛 行迹材料"

    def test_unfilled_training_dungeon_never_matches(self) -> None:
        report = parse_m7a_run(
            "2026-07-26 06:00:00,000 | INFO | 执行体力计划 [1/1]: 饰品提取 - 鎏金追忆, 计划次数: 5",
            now=datetime(2026, 7, 26, 6, 0),
            training_goals=[TrainingGoal("x", "远坂凛", "遗器", dungeon="待填写")],
        )

        assert report.stamina_runs[0].trainee == ""

    def test_detected_character_is_not_linked_to_an_unrelated_plan(self) -> None:
        content = """\
2026-07-25 06:02:51,752 | INFO | 培养目标Archer的待刷副本:
2026-07-25 06:02:51,752 | INFO | 饰品提取 - 孽果盘生
2026-07-25 06:02:51,752 | INFO | 准备发送 winotify 通知（级别：全部，图片：否）
2026-07-25 06:03:16,957 | INFO | 执行体力计划 [1/4]: 饰品提取 - 鎏金追忆, 计划次数: 31
2026-07-25 06:03:17,957 | INFO | 执行体力计划 [2/4]: 饰品提取 - 孽果盘生, 计划次数: 8
"""
        report = parse_m7a_run(content, now=datetime(2026, 7, 25, 6, 7))

        assert [item.trainee for item in report.stamina_runs] == ["", "Archer"]

    def test_same_cycle_already_settled_is_idempotent_success(self) -> None:
        content = """\
2026-08-11 16:20:00,000 | INFO | 每日实训尚未刷新
|                                                     停止运行                                                      |
"""
        report = parse_m7a_run(content, now=datetime(2026, 8, 11, 16, 21))
        card = _report(report)

        assert (report.daily_status, report.overall_status) == ("completed", "completed")
        assert report.daily_already_settled and report.rewards == []
        assert card.status == "completed"
        assert card.tasks == ["✅ 每日实训：本刷新周期已经完成"]

    def test_repeated_interface_failures_are_classified_as_stalled(self) -> None:
        lines = [
            f"2026-07-17 13:41:{index:02d},000 | WARNING | 未识别出任何界面，请确保游戏画面干净，按ESC后重试"
            for index in range(10, 16)
        ]
        report = parse_m7a_run("\n".join(lines), now=datetime(2026, 7, 17, 13, 41, 20))

        assert report.overall_status == "stalled"
        assert "重复6次" in report.current_reason
        assert "未识别出任何界面" in report.current_reason

    def test_stale_log_reports_last_progress_and_duration(self) -> None:
        content = (
            "|                                                   开始差分宇宙                                                    |\n"
            "2026-07-17 13:45:00,000 | INFO | 当前进度：(13/13) 第三位面-首领"
        )
        report = parse_m7a_run(content, now=datetime(2026, 7, 17, 14, 0))

        assert report.overall_status == "stalled"
        assert report.current_task == "差分宇宙：当前进度：(13/13) 第三位面-首领"
        assert "15分钟无进展" in report.current_reason

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
            run_stage="超时",
            force_failed=True,
        )

        assert report.current_task == "差分宇宙：尝试进入战斗"

    def test_explicit_completed_plan_sets_authoritative_zero_remaining(self) -> None:
        content = """\
|                                                 开始执行体力计划                                                  |
2026-07-22 06:03:00,000 | INFO | 执行体力计划 [1/1]: 拟造花萼（赤） - 海原电视塔, 计划次数: 1
------------------------------ 开始刷拟造花萼（赤） - 海原电视塔，总计1轮，每轮包含1次 ------------------------------
2026-07-22 06:04:00,000 | INFO | 副本任务完成
2026-07-22 06:04:00,001 | INFO | 体力计划已完成: 拟造花萼（赤） - 海原电视塔
2026-07-22 06:05:00,000 | INFO | 每日实训已完成
|                                                     停止运行                                                      |
"""
        report = parse_m7a_run(content, now=datetime(2026, 7, 22, 6, 5))

        assert report.stamina_runs[0].remaining_plan_count == 0
        assert "✅ 拟造花萼（赤）·海原电视塔：1 次，计划已完成" in _stamina(_report(report))

    def test_default_stamina_does_not_reuse_the_skipped_plan(self) -> None:
        content = """\
|                                                 开始执行体力计划                                                  |
2026-07-03 06:05:31,747 | INFO | 执行体力计划 [1/1]: 饰品提取 - 孽果盘生, 计划次数: 8
2026-07-03 06:05:45,583 | INFO | 开拓力 < 40
2026-07-03 06:05:45,583 | INFO | 无法执行: 饰品提取 - 孽果盘生，保留该计划
|                                                    开始清体力                                                     |
--------------------------- 开始刷拟造花萼（赤） - 「世界尽头」酒馆，总计1轮，每轮包含3次 ---------------------------
2026-07-03 06:07:07,551 | INFO | 副本任务完成
2026-07-03 06:07:50,000 | INFO | 每日实训已完成
|                                                     停止运行                                                      |
"""
        report = parse_m7a_run(content, now=datetime(2026, 7, 3, 6, 8))

        assert [(item.name, item.source, item.status) for item in report.stamina_runs] == [
            ("饰品提取 - 孽果盘生", "plan", "skipped"),
            ("拟造花萼（赤） - 「世界尽头」酒馆", "default", "completed"),
        ]
        assert _stamina(_report(report)) == [
            "⏸️ 饰品提取·孽果盘生：开拓力 < 40，保留该计划",
            "✅ 清体力：拟造花萼（赤）·「世界尽头」酒馆：3 次",
        ]

    def test_redemption_codes_are_hidden_and_counts_kept(self) -> None:
        content = """\
2026-07-04 06:02:37,287 | ERROR | 兑换码使用失败: secret-one (1/3)
2026-07-04 06:03:09,644 | INFO | 兑换码使用成功: secret-two (2/3)
2026-07-04 06:03:23,178 | INFO | 兑换码使用成功: secret-three (3/3)
2026-07-04 06:03:28,507 | INFO | 成功使用了2个兑换码:
2026-07-04 06:03:28,508 | INFO | SECRETCODE12
"""
        report = parse_m7a_run(content, now=datetime(2026, 7, 4, 6, 4))

        assert report.other_tasks == ["兑换码：成功2个，失败1个"]
        assert report.errors == []
        assert "secret" not in str(report.to_dict()).casefold()

    def test_stalled_on_a_redemption_line_still_hides_the_code(self) -> None:
        content = "2026-07-04 06:03:09,644 | INFO | 兑换码使用成功: secret-two (1/3)"
        report = parse_m7a_run(content, now=datetime(2026, 7, 4, 6, 30))

        assert report.overall_status == "stalled"
        assert "secret" not in card_text(_report(report).to_card()).casefold()

    def test_divergent_universe_facts_are_all_retained(self) -> None:
        content = """\
2026-07-27 10:54:56,036 | INFO | 差分宇宙积分：0 / 18000
2026-07-27 11:18:00,485 | INFO | 本次差分宇宙用时：22 分钟 16 秒
2026-07-27 11:18:07,548 | INFO | 已记录差分宇宙次数：今日 1 次，本周 1 次
2026-07-27 11:18:07,548 | INFO | 差分宇宙已完成
2026-07-27 11:18:19,746 | INFO | 模拟宇宙奖励已领取
2026-07-27 11:18:30,037 | INFO | 差分宇宙积分：18000 / 18000
"""
        report = parse_m7a_run(content, now=datetime(2026, 7, 27, 11, 19))

        assert report.other_tasks == [
            "差分宇宙：完成1次，用时22分16秒，今日1次，本周1次",
            "差分宇宙积分 18000/18000",
        ]
        assert report.rewards == ["模拟宇宙"]

    def test_only_confirmed_rewards_are_reported(self) -> None:
        content = """\
2026-07-24 06:02:32,288 | INFO | 历战余响本周可领取奖励次数：0/3
================================================== 检测到委托奖励 ===================================================
2026-07-24 06:03:00,000 | INFO | 委托派遣中，目前没有可领取的奖励帕！
--------------------------------------------------- 委托奖励完成 ----------------------------------------------------
2026-07-24 06:03:30,000 | INFO | 领取巡星之礼奖励完成
2026-07-24 06:04:00,000 | INFO | 邮件奖励已领取
2026-07-24 06:05:00,000 | INFO | 每日实训已完成
"""
        report = parse_m7a_run(content, now=datetime(2026, 7, 24, 6, 5))

        assert report.other_tasks == ["历战余响：本周剩余奖励次数 0/3"]
        assert report.rewards == ["巡星之礼", "邮件", "每日实训"]

    def test_plan_error_and_m7a_errors_explain_a_failed_run(self) -> None:
        report = parse_m7a_run(
            TUTORIAL_BLOCKED_LOG,
            now=datetime(2026, 9, 29, 6, 10),
            run_stage="实训未达标",
            force_failed=True,
        )

        statuses = [(item.name, item.status) for item in report.stamina_runs]
        assert statuses == [
            ("饰品提取 - 西风丛中", "completed"),
            ("侵蚀隧洞 - 睿治之径", "failed"),
            ("饰品提取 - 鎏金追忆", "failed"),
        ]
        assert report.stamina_runs[1].reason == "无法识别当前游戏界面，保留该计划"
        assert report.errors == ["战斗超时", "检测到该次副本未正常运行，重试：1/3", "无法识别当前游戏界面"]
        assert report.current_task == "清体力"


class TestCard:
    def test_success_card_is_a_short_checklist(self) -> None:
        report = parse_m7a_run(
            SUCCESS_LOG,
            now=datetime(2026, 7, 25, 6, 8),
            training_goals=[ARCHER_RELIC],
        )
        card = _report(
            report,
            plan=TrainingPlan(goals=(RIN_TRACE,)),
            reminders=["距离月卡过期还有8天"],
        )

        assert card.status == "completed"
        assert card.title == "✅ 星铁 全部完成 · 07-25 06:08"
        assert card.problems == []
        assert card.tasks == [
            "✅ 每日实训 500/500（本次补做：派遣委托、万能合成机）",
            "✅ 领取奖励：每日实训",
        ]
        assert _stamina(card) == [
            "✅ 饰品提取·鎏金追忆（Archer 遗器）：6 次，计划还剩 25 次",
            "⏸️ 侵蚀隧洞·魔占之径：开拓力 < 40，保留该计划",
        ]
        text = card_text(card.to_card())
        # 体力去向单独一栏，紧跟今日任务、排在养成待办前。
        assert text.index("**今日任务**") < text.index("**体力去向**") < text.index("**养成待办**")
        assert "**养成待办**\n1. 远坂凛：行迹材料" in text
        assert "**提醒**\n1. 距离月卡过期还有8天" in text
        assert "用时 41分钟" in text

    def test_failed_card_leads_with_where_it_stopped(self) -> None:
        report = parse_m7a_run(
            TUTORIAL_BLOCKED_LOG,
            now=datetime(2026, 9, 29, 6, 10),
            run_stage="实训未达标",
            force_failed=True,
        )
        card = _report(report)

        assert card.status == "failed"
        assert card.problems == [
            "停在「清体力」",
            "三月七助手报错：战斗超时；检测到该次副本未正常运行，重试：1/3；无法识别当前游戏界面",
        ]
        assert card.tasks == ["❌ 每日实训：没完成（分数没读到）"]
        assert _stamina(card) == [
            "✅ 饰品提取·西风丛中：2 次，计划已完成",
            "❌ 侵蚀隧洞·睿治之径：中途出错（无法识别当前游戏界面，保留该计划）",
            "❌ 饰品提取·鎏金追忆：中途出错（无法识别当前游戏界面，保留该计划）",
        ]
        text = card_text(card.to_card())
        assert text.index("**异常记录**") < text.index("**今日任务**")

    def test_daily_failure_lists_missing_tasks_and_watchdog_stage(self) -> None:
        content = """\
2026-07-17 13:40:00,000 | INFO | 使用支援角色并获得战斗胜利1次: 待完成
2026-07-17 13:41:00,000 | INFO | 完成任务: 派遣委托或收取1次委托奖励 (+100分)，当前分数: 400/500
2026-07-17 13:42:00,000 | INFO | 每日实训未完成
"""
        report = parse_m7a_run(
            content,
            now=datetime(2026, 7, 17, 13, 44),
            run_stage="日志",
            force_failed=True,
        )
        card = _report(report)

        assert card.tasks[0] == "❌ 每日实训 400/500（还差：使用支援角色并获得战斗胜利1次）"
        assert card.problems[0] == "三月七助手日志长时间不更新，被看门狗停止"

    def test_nonzero_exit_is_red_even_when_the_daily_is_done(self) -> None:
        report = parse_m7a_run(
            SUCCESS_LOG, now=datetime(2026, 7, 25, 6, 8), run_stage="M7A", force_failed=True
        )

        assert report.daily_status == "completed"
        assert _report(report).status == "failed"

    def test_completed_run_with_a_broken_plan_is_partial(self) -> None:
        run = RunReport(
            overall_status="completed",
            daily_status="completed",
            daily_score="500/500",
            stopped_normally=True,
            stamina_runs=[StaminaRun(name="侵蚀隧洞 - 睿治之径", status="failed", reason="战斗超时")],
            errors=["战斗超时"],
        )
        card = _report(run)

        assert card.status == "partial"
        assert card.problems == ["三月七助手报错：战斗超时"]

    def test_completed_training_goal_is_reported_as_a_task(self) -> None:
        done = TrainingGoal("rin-trace", "远坂凛", "行迹材料", completed=True)
        run = RunReport(overall_status="completed", daily_status="completed", stopped_normally=True)

        card = _report(run, plan=TrainingPlan(goals=(done,), completed_this_run=(done,)))

        assert "✅ 养成计划完成：远坂凛 行迹材料" in card.tasks
        assert ("养成待办", []) in card.notes


class TestService:
    def test_main_run_sends_one_card_and_archives_facts(self, tmp_path: Path) -> None:
        log_path = tmp_path / "2026-07-25.log"
        log_path.write_text(SUCCESS_LOG, encoding="utf-8")
        with patch("starrail_auto.reporting.service.REPORTS_DIR", tmp_path / "reports"), patch(
            "starrail_auto.reporting.service.send_card", return_value=True
        ) as send, patch(
            "starrail_auto.reporting.service.load_training_plan", return_value=TrainingPlan()
        ), patch(
            "starrail_auto.reporting.service.reconcile_training_plan", return_value=TrainingPlan()
        ), patch(
            "starrail_auto.reporting.service.load_power_plan_remaining", return_value={}
        ), patch(
            "starrail_auto.reporting.service.format_active_reminders", return_value=[]
        ):
            archive = report_main_run(
                log_path=log_path,
                offset=0,
                exit_code=0,
                stage="",
                retries=0,
                duration_seconds=600,
            )

        send.assert_called_once()
        title = send.call_args.args[0]["card"]["header"]["title"]["content"]
        assert title.startswith("✅ 星铁 全部完成")
        data = json.loads(archive.read_text(encoding="utf-8"))
        assert archive.name == "2026-07-25_0.json"
        assert data["sent"] is True and data["exit_code"] == 0
        assert data["facts"]["daily_score"] == "500/500"
        assert data["report"]["tasks"][0].startswith("✅ 每日实训 500/500")

    def test_secrets_in_m7a_errors_never_reach_the_card_or_archive(self, tmp_path: Path) -> None:
        log_path = tmp_path / "2026-07-25.log"
        log_path.write_text(
            SUCCESS_LOG
            + "2026-07-25 06:08:00,000 | ERROR | 发生错误 请求失败 token=SUPERSECRET123 "
            "https://open.feishu.cn/open-apis/bot/v2/hook/HOOKSECRET456\n",
            encoding="utf-8",
        )
        with patch("starrail_auto.reporting.service.REPORTS_DIR", tmp_path / "reports"), patch(
            "starrail_auto.reporting.service.send_card", return_value=True
        ), patch(
            "starrail_auto.reporting.service.load_training_plan", return_value=TrainingPlan()
        ), patch(
            "starrail_auto.reporting.service.reconcile_training_plan", return_value=TrainingPlan()
        ), patch(
            "starrail_auto.reporting.service.load_power_plan_remaining", return_value={}
        ), patch(
            "starrail_auto.reporting.service.format_active_reminders", return_value=[]
        ):
            archive = report_main_run(
                log_path=log_path, offset=0, exit_code=0, stage="", retries=0
            )

        text = archive.read_text(encoding="utf-8")
        assert "SUPERSECRET123" not in text
        assert "HOOKSECRET456" not in text

    def test_archived_run_is_rebuilt_as_a_preview_without_sending(self, tmp_path: Path) -> None:
        reports = tmp_path / "reports"
        reports.mkdir()
        log_path = tmp_path / "2026-09-29.log"
        log_path.write_text(
            TUTORIAL_BLOCKED_LOG + "2026-09-29 22:00:00,000 | INFO | 每日实训已完成\n",
            encoding="utf-8",
        )
        # 旧格式归档：没有退出码，只有当时的事实。
        (reports / "2026-09-29_0.json").write_text(
            json.dumps(
                {
                    "source": {"path": str(log_path), "offset": 0},
                    "facts": {
                        "overall_status": "failed",
                        "run_stage": "实训未达标",
                        "last_log_at": "2026-09-29T06:09:54.197000",
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        with patch("starrail_auto.reporting.service.REPORTS_DIR", reports), patch(
            "starrail_auto.reporting.service.send_card"
        ) as send, patch(
            "starrail_auto.reporting.service.load_training_plan", return_value=TrainingPlan()
        ), patch(
            "starrail_auto.reporting.service.reconcile_training_plan", return_value=TrainingPlan()
        ) as reconcile, patch(
            "starrail_auto.reporting.service.load_power_plan_remaining", return_value={}
        ), patch(
            "starrail_auto.reporting.service.format_active_reminders", return_value=[]
        ):
            preview, text = preview_archived_run("latest")

        send.assert_not_called()
        # 预览不能改写用户的养成计划。
        assert reconcile.call_args.kwargs["persist"] is False
        assert preview.name == "2026-09-29_0.preview.json"
        assert text.startswith("❌ 星铁 失败")
        # 当晚 22:00 的另一轮日志不能混进早上这次的预览。
        assert "❌ 每日实训：没完成" in text

    def test_short_report_uses_the_same_card_shape(self) -> None:
        with patch("starrail_auto.reporting.service.send_card", return_value=True) as send:
            send_short_report(game="星铁收尾", problems=["UU 加速器进程没能退出"])

        text = card_text(send.call_args.args[0])
        assert text.startswith("❌ 星铁收尾 失败")
        assert "1. UU 加速器进程没能退出" in text

    def test_short_success_card_is_never_an_empty_card(self) -> None:
        with patch("starrail_auto.reporting.service.send_card", return_value=True) as send:
            send_short_report(game="星铁 universe", problems=[])

        assert send.call_args.args[0]["card"]["elements"]


class TestWorkflowReport:
    """汇报出错时的兜底：不发假绿卡，也绝不改变任务退出码。"""

    def _execute(self, exit_code: int, *, send_card_error: bool = False):
        from starrail_auto.m7a.models import RunResult
        from starrail_auto.workflows import daily

        result = RunResult(exit_code, stage="M7A" if exit_code else "")
        send = patch(
            "starrail_auto.reporting.service.send_card",
            side_effect=ValueError("bad webhook") if send_card_error else None,
            return_value=True,
        )
        with patch.object(daily, "_setup_logging"), patch.object(
            daily, "_run", return_value=result
        ), patch.object(
            daily, "report_main_run", side_effect=ValueError("day 2026-02-30 is out of range")
        ), send as sent:
            code = daily.execute_task("main")
        return code, sent

    def test_report_failure_sends_an_orange_fallback_and_keeps_the_exit_code(self) -> None:
        code, sent = self._execute(0)

        assert code == 0
        text = card_text(sent.call_args.args[0])
        assert text.startswith("⚠️ 星铁 部分完成")
        assert "日报生成出错" in text

    def test_fallback_send_errors_cannot_replace_the_exit_code(self) -> None:
        code, _ = self._execute(3, send_card_error=True)

        assert code == 3
