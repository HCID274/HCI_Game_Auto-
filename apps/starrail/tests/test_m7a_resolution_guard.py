import pytest

from starrail_auto.m7a import resolution_guard
from starrail_auto.m7a.resolution_guard import AUTOMATION_SETTING, hold_automation_resolution

# 本机现状：用户自己的设置是 2560x1440 全屏（三月七助手改回时的写法）。
USER_SETTING = b'{"width": 2560, "height": 1440, "isFullScreen": true}\x00'


@pytest.fixture
def registry(monkeypatch, tmp_path):
    state = {"value": USER_SETTING, "writes": []}

    def write(value):
        state["writes"].append(value)
        state["value"] = value

    monkeypatch.setattr(resolution_guard, "_read_setting", lambda: state["value"])
    monkeypatch.setattr(resolution_guard, "_write_setting", write)
    monkeypatch.setattr(resolution_guard, "BACKUP_PATH", tmp_path / "backup.bin")
    return state


def test_holds_1080p_window_for_the_whole_m7a_run_and_restores_the_user_setting(registry):
    with hold_automation_resolution():
        # 三月七助手看到已是 1920x1080 窗口就不改、也不改回；游戏什么时候读都对。
        assert registry["value"] == AUTOMATION_SETTING
        assert resolution_guard.BACKUP_PATH.read_bytes() == USER_SETTING

    assert registry["value"] == USER_SETTING
    assert not resolution_guard.BACKUP_PATH.exists()


def test_the_next_run_restores_what_a_killed_run_left_behind(registry):
    # 上次运行中途停电：注册表停在 1920x1080 窗口，用户原值只在备份文件里。
    registry["value"] = AUTOMATION_SETTING
    resolution_guard.BACKUP_PATH.write_bytes(USER_SETTING)

    with hold_automation_resolution():
        assert registry["value"] == AUTOMATION_SETTING

    assert registry["value"] == USER_SETTING
    assert not resolution_guard.BACKUP_PATH.exists()


def test_restores_the_user_setting_when_the_run_fails(registry):
    with pytest.raises(RuntimeError):
        with hold_automation_resolution():
            raise RuntimeError("M7A failed")

    assert registry["value"] == USER_SETTING


def test_leaves_a_matching_setting_untouched(registry):
    # 游戏自己写回时不带空格。
    registry["value"] = b'{"width":1920,"height":1080,"isFullScreen":false}\x00'

    with hold_automation_resolution():
        pass

    assert registry["writes"] == []


def test_keeps_a_setting_changed_in_game_during_the_run(registry):
    changed = b'{"width": 2560, "height": 1440, "isFullScreen": false}\x00'

    with hold_automation_resolution():
        registry["value"] = changed

    assert registry["value"] == changed


def test_no_game_registry_means_no_writes(registry):
    registry["value"] = None

    with hold_automation_resolution():
        pass

    assert registry["writes"] == []


def test_registry_errors_never_change_the_run_result(registry, monkeypatch):
    def denied(_value):
        raise PermissionError("access denied")

    monkeypatch.setattr(resolution_guard, "_write_setting", denied)
    with hold_automation_resolution():
        result = "daily ok"
    assert result == "daily ok"
    assert registry["value"] == USER_SETTING

    writes = []

    def fails_on_restore(value):
        writes.append(value)
        if len(writes) > 1:
            raise PermissionError("access denied")
        registry["value"] = value

    monkeypatch.setattr(resolution_guard, "_write_setting", fails_on_restore)
    with hold_automation_resolution():
        result = "daily ok"
    assert result == "daily ok"
    assert resolution_guard.BACKUP_PATH.read_bytes() == USER_SETTING  # 留给下次运行放回
