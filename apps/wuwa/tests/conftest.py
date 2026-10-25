import pytest


@pytest.fixture(autouse=True)
def _no_real_memory_preflight(monkeypatch) -> None:
    # 开跑前的内存检查会真的结束 Minecraft；测试里一律换成“什么都不做”。
    monkeypatch.setattr("wuwa_auto.daily.free_memory_for_games", lambda: [])


@pytest.fixture(autouse=True)
def _no_real_birthday_invite_clicks(monkeypatch) -> None:
    # 生日弹窗处理会真的截屏、点游戏；只在它自己的用例里直接调用。
    monkeypatch.setattr("wuwa_auto.okww.confirmed_retry.accept_birthday_invite", lambda: False)
    monkeypatch.setattr("wuwa_auto.okww.runner.accept_birthday_invite", lambda: False)
