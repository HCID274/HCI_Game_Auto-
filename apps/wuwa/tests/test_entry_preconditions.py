"""2026-09-16：剧情单人状态导致主界面判定失败，不能重跑七轮。"""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from wuwa_auto.okww.logs import (
    WORLD_TEAM_BLOCKED_REASON,
    is_farm_echo_world_team_blocked,
)
from wuwa_auto.okww.recovery_flow import maybe_recover_farm_echo_death
from wuwa_auto.okww.runner import OkRunResult
from wuwa_auto.reporting.parser import WORLD_TEAM_BLOCKED, parse_run


BLOCKED = (
    "FarmEchoTask:info_set app Please start in game world and in team!\n"
    "Exception: Please start in game world and in team!\n"
)


@pytest.mark.parametrize("text,expected", [
    (BLOCKED, True),
    ("Please start in game world and in team!", False),
    (BLOCKED.splitlines()[0], False),
    ("HOST_FARM_ECHO_GAMEPLAY_HANDOFF\n" + BLOCKED, False),
    ("RuntimeError: Teleport to boss failed", False),
])
def test_only_confirmed_startup_prerequisite_is_terminal(text, expected):
    assert is_farm_echo_world_team_blocked(text) is expected


def test_blocked_startup_preserves_evidence_without_input_or_retry(tmp_path, monkeypatch):
    log = tmp_path / "ok-current-run.log"
    log.write_text(BLOCKED, encoding="utf-8")
    result = OkRunResult(
        run_id="blocked", status="failed", reason="old generic failure",
        started_at="2026-09-16T05:41:08+09:00",
        finished_at="2026-09-16T05:52:42+09:00", duration_seconds=694,
        log_slice_path=str(log), evidence_path="startup-failed.png",
        config={"workflow_task": "farm_echo_confirmed_retry", "target_count": 5},
        exit_code=1,
    )
    for name in ("run_confirmed_farm_echo_retry", "_recover_safely", "stop_daily_workers"):
        monkeypatch.setattr(
            "wuwa_auto.okww.recovery_flow." + name,
            Mock(side_effect=AssertionError("blocked prerequisite must not operate desktop")),
        )
    restart = Mock(side_effect=AssertionError("must not restart client"))
    fixed = maybe_recover_farm_echo_death(result, client_restart=restart)
    assert fixed.status == "failed"
    assert fixed.reason == WORLD_TEAM_BLOCKED_REASON
    assert fixed.evidence_path == result.evidence_path
    assert fixed.config["farm_echo_world_team_blocked"] is True
    assert "farm_echo_world_team_blocked" not in result.config
    assert (tmp_path / "result.json").is_file()
    assert parse_run(fixed).issues[0] == WORLD_TEAM_BLOCKED
    # 历史日志重放也能正确说明问题；成功的运行不能被旧错误说明污染。
    assert parse_run(result).issues[0] == WORLD_TEAM_BLOCKED
    successful = replace(result, status="success", reason="completed")
    assert WORLD_TEAM_BLOCKED not in parse_run(successful).issues
    assert maybe_recover_farm_echo_death(successful) is successful


def test_handoff_timeout_is_not_retried_for_an_hour(tmp_path, monkeypatch):
    log = tmp_path / "ok-current-run.log"
    log.write_text(
        "start_controller:started window size stable for 2s: 2560x1440\n",
        encoding="utf-8",
    )
    reason = "OK-WW initialized but did not hand off to FarmEcho within 600 seconds"
    result = OkRunResult(
        run_id="handoff",
        status="failed",
        reason=reason,
        started_at="2026-09-17T05:40:06+09:00",
        finished_at="2026-09-17T05:50:08+09:00",
        duration_seconds=602,
        log_slice_path=str(log),
        evidence_path="login-screen.png",
        config={"workflow_task": "farm_echo_confirmed_retry", "target_count": 5},
        exit_code=1,
    )
    retry = Mock(side_effect=AssertionError("handoff timeout must not repeat unchanged"))
    monkeypatch.setattr(
        "wuwa_auto.okww.recovery_flow.run_confirmed_farm_echo_retry",
        retry,
    )

    fixed = maybe_recover_farm_echo_death(result)

    assert fixed.reason == reason
    assert fixed.config["farm_echo_startup_handoff_timeout"] is True
    retry.assert_not_called()
