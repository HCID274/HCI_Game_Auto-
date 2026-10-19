import pytest


@pytest.fixture(autouse=True)
def _no_real_memory_preflight(monkeypatch) -> None:
    # 开跑前的内存检查会真的结束 Minecraft；测试里一律换成“什么都不做”。
    monkeypatch.setattr("wuwa_auto.daily.free_memory_for_games", lambda: [])
