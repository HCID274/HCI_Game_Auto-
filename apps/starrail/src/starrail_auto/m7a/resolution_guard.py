"""三月七助手跑完以前，让星铁一直读到 1920x1080 窗口分辨率，结束后放回用户自己的设置。

三月七助手启动游戏前把注册表里的游戏分辨率临时改成 1920x1080 窗口，游戏窗口出现约 20 秒后就改回
用户的设置（本机是 2560x1440 全屏，见上游 tasks/game/__init__.py 的 start_local_game）。游戏加载慢时
读到的已是改回去的全屏设置，三月七助手报“游戏分辨率不正确”并重开游戏，最多开 3 次：11-01 重开 1 次，
11-02 用满 3 次才成功。提前写好 1920x1080 窗口，三月七助手看到已经一致就不改、也不改回。
用户原值同时存进备份文件：运行中途被强行结束（停电、重启）没来得及放回时，下次运行结束后照样放回。
"""

from __future__ import annotations

import json
import logging
import os
import winreg
from collections.abc import Iterator
from contextlib import contextmanager

from starrail_auto.m7a.config import M7A_CONFIG_BACKUP_DIR

log = logging.getLogger(__name__)

BACKUP_PATH = M7A_CONFIG_BACKUP_DIR / "game-resolution-before-run.bin"

# 与三月七助手 utils/registry/star_rail_setting.py 相同：先找国服，再找国际服。
GAME_REGISTRY_KEYS = (r"Software\miHoYo\崩坏：星穹铁道", r"Software\Cognosphere\Star Rail")
RESOLUTION_VALUE_NAME = "GraphicsSettings_PCResolution_h431323223"
# 主屏比 1920x1080 大时三月七助手要窗口模式（本机 2560x1440，见 docs/架构蓝图.md）；换 1080p 主屏要改成全屏。
AUTOMATION_RESOLUTION = {"width": 1920, "height": 1080, "isFullScreen": False}
# 与三月七助手写入的格式一致：JSON 加结尾 \0。
AUTOMATION_SETTING = (json.dumps(AUTOMATION_RESOLUTION) + "\x00").encode("utf-8")
_AUTOMATION_FIELDS = {key.lower(): value for key, value in AUTOMATION_RESOLUTION.items()}


def _open_game_key(access: int) -> winreg.HKEYType | None:
    for path in GAME_REGISTRY_KEYS:
        try:
            return winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, access)
        except FileNotFoundError:
            continue
    return None


def _read_setting() -> bytes | None:
    key = _open_game_key(winreg.KEY_QUERY_VALUE)
    if key is None:
        return None
    with key:
        try:
            value, kind = winreg.QueryValueEx(key, RESOLUTION_VALUE_NAME)
        except FileNotFoundError:
            return None
    return value if kind == winreg.REG_BINARY else None


def _write_setting(value: bytes) -> None:
    key = _open_game_key(winreg.KEY_SET_VALUE)
    if key is None:
        raise FileNotFoundError("Star Rail registry key not found")
    with key:
        winreg.SetValueEx(key, RESOLUTION_VALUE_NAME, 0, winreg.REG_BINARY, value)


def _is_automation_setting(raw: bytes) -> bool:
    try:
        data = json.loads(raw.decode("utf-8").strip("\x00"))
    except ValueError:
        return False
    return isinstance(data, dict) and {
        str(key).lower(): value for key, value in data.items()
    } == _AUTOMATION_FIELDS


def _text(raw: bytes) -> str:
    return raw.decode("utf-8", "replace").strip("\x00")


def _hold() -> bytes | None:
    """写入 1920x1080 窗口；返回结束时要放回的用户设置，不用放回时返回 None。"""
    try:
        original = _read_setting()
        if original is None:
            return None
        if _is_automation_setting(original):
            if not BACKUP_PATH.exists():
                return None
            # 上次运行中途被强行结束，注册表停在 1920x1080 窗口，用户原值还在备份文件里。
            original = BACKUP_PATH.read_bytes()
        else:
            BACKUP_PATH.parent.mkdir(parents=True, exist_ok=True)
            with BACKUP_PATH.open("wb") as backup:
                backup.write(original)
                os.fsync(backup.fileno())  # 先落盘再改注册表：断电也不会留下空的、残缺的备份
            _write_setting(AUTOMATION_SETTING)
    except OSError as exc:
        log.warning("cannot hold Star Rail resolution; M7A will set it itself: %s", exc)
        return None
    log.info(
        "held Star Rail resolution at 1920x1080 windowed until M7A finishes; user setting: %s",
        _text(original),
    )
    return original


def _restore(original: bytes) -> None:
    try:
        current = _read_setting()
        if current is not None and not _is_automation_setting(current):
            # 运行期间有人在游戏里改过，留着新的。
            log.info("Star Rail resolution changed during the run; keeping %s", _text(current))
        else:
            _write_setting(original)
            log.info("restored Star Rail resolution: %s", _text(original))
        BACKUP_PATH.unlink(missing_ok=True)
    except OSError as exc:
        # 备份文件留着，下次运行结束后再放回。
        log.warning("cannot restore Star Rail resolution %s: %s", _text(original), exc)


@contextmanager
def hold_automation_resolution() -> Iterator[None]:
    """读写注册表失败只记日志：三月七助手仍会自己改分辨率，不因此拖垮日常。"""
    original = _hold()
    try:
        yield
    finally:
        if original is not None:
            _restore(original)


__all__ = ["AUTOMATION_SETTING", "hold_automation_resolution"]
