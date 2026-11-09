import pytest


@pytest.fixture(autouse=True)
def _no_real_memory_preflight(monkeypatch) -> None:
    # 开跑前的内存检查会真的结束 Minecraft；测试里一律换成“什么都不做”。
    monkeypatch.setattr(
        "starrail_auto.workflows.daily.free_memory_for_games", lambda: []
    )


@pytest.fixture(autouse=True)
def _no_real_game_resolution(monkeypatch) -> None:
    # 分辨率看守会真的改星铁注册表；测试里当作没装游戏，什么都不写。
    monkeypatch.setattr(
        "starrail_auto.m7a.resolution_guard._read_setting", lambda: None
    )


@pytest.fixture(autouse=True)
def _no_real_topmost_guard(monkeypatch) -> None:
    # 置顶窗口看守会真的把别的程序的置顶窗口压下去；测试里只空转，不碰真窗口。
    monkeypatch.setattr(
        "game_automation_core.windows.topmost_guard.TopmostCoverGuard.sweep",
        lambda self: None,
    )
