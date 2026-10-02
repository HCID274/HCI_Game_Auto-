"""Stable host-owned state detection for FarmEcho realm failures."""

from __future__ import annotations

import re
from typing import Protocol

REALM_DEFEAT_MARKER = "HOST_FARM_ECHO_REALM_DEFEAT_CONFIRMED"
REVIVE_DIALOG_MARKER = "HOST_FARM_ECHO_REVIVE_DIALOG_CONFIRMED"
PARTY_MEMBER_UNAVAILABLE_MARKER = (
    "HOST_FARM_ECHO_PARTY_MEMBER_UNAVAILABLE_CONFIRMED"
)
REVIVE_DIALOG_HEAL_RECOVERY_COMPLETED_MARKER = (
    "HOST_FARM_ECHO_REVIVE_DIALOG_HEAL_RECOVERY_COMPLETED"
)
REALM_DEFEAT_HEAL_RECOVERY_COMPLETED_MARKER = (
    "HOST_FARM_ECHO_REALM_DEFEAT_HEAL_RECOVERY_COMPLETED"
)
PARTY_MEMBER_HEAL_RECOVERY_COMPLETED_MARKER = (
    "HOST_FARM_ECHO_PARTY_MEMBER_HEAL_RECOVERY_COMPLETED"
)

_DEFEAT_TITLE = re.compile(r"(?:挑战失败|Challenge\s*Failed)", re.IGNORECASE)
_EXIT_BUTTON = re.compile(r"(?:退出副本|Exit\s*(?:Domain|Challenge))", re.IGNORECASE)
_RETRY_BUTTON = re.compile(r"(?:重新挑战|Retry|Challenge\s*Again)", re.IGNORECASE)
_REVIVE_TITLE = re.compile(r"(?:选择复苏物品|Select\s*Revival)", re.IGNORECASE)
_CONFIRM_BUTTON = re.compile(r"(?:确认|Confirm)", re.IGNORECASE)
_NO_REVIVAL_ITEM = re.compile(
    r"(?:暂无可用的意识恢复物品|No\s+available\s+revival)",
    re.IGNORECASE,
)


class RealmStateTask(Protocol):
    def wait_ocr(self, *args: object, **kwargs: object) -> object: ...

    def wait_click_ocr(self, *args: object, **kwargs: object) -> object: ...

    def in_team(self) -> object: ...

    def send_key(self, *args: object, **kwargs: object) -> object: ...

    def log_info(self, message: object) -> object: ...


def party_member_unavailable(
    task: RealmStateTask,
    message: object,
) -> bool:
    """Confirm a blocked switch while the party HUD is still visible.

    Upstream raises ``failed switch chars`` only after ten seconds of trying
    to switch to the selected party member.  In the affected non-revivable
    realm, the dead portrait remains in the party HUD, so the normal revive
    dialog is absent.  Requiring both facts avoids treating a normal combat
    exit or target loss as a character death.
    """
    if str(message).strip() != "failed switch chars":
        return False
    try:
        state = task.in_team()
    except Exception:
        return False
    if not isinstance(state, (tuple, list)) or len(state) < 3:
        return False
    in_team, _, party_size = state[:3]
    return bool(
        in_team
        and isinstance(party_size, int)
        and party_size >= 2
    )


def revival_item_unavailable_visible(
    task: RealmStateTask,
    *,
    time_out: float = 1.5,
) -> bool:
    """Confirm the low-health/dead-party prompt inside an active challenge."""
    prompt = task.wait_ocr(
        0.25,
        0.10,
        0.75,
        0.35,
        match=_NO_REVIVAL_ITEM,
        time_out=time_out,
        settle_time=0.2,
        raise_if_not_found=False,
    )
    if not prompt:
        return False
    try:
        state = task.in_team()
    except Exception:
        return False
    if not isinstance(state, (tuple, list)) or len(state) < 3:
        return False
    in_team, _, party_size = state[:3]
    return bool(in_team and isinstance(party_size, int) and party_size >= 2)


def realm_defeat_visible(task: RealmStateTask, *, time_out: float = 1.5) -> bool:
    """Require the title and both actions before classifying a defeat screen."""
    title = task.wait_ocr(
        0.30,
        0.12,
        0.70,
        0.36,
        match=_DEFEAT_TITLE,
        time_out=time_out,
        settle_time=0.2,
        raise_if_not_found=False,
    )
    if not title:
        return False
    exit_button = task.wait_ocr(
        0.20,
        0.75,
        0.50,
        0.93,
        match=_EXIT_BUTTON,
        time_out=time_out,
        settle_time=0.1,
        raise_if_not_found=False,
    )
    retry_button = task.wait_ocr(
        0.50,
        0.75,
        0.80,
        0.93,
        match=_RETRY_BUTTON,
        time_out=time_out,
        settle_time=0.1,
        raise_if_not_found=False,
    )
    return bool(exit_button and retry_button)


def revive_dialog_visible(task: RealmStateTask, *, time_out: float = 1.5) -> bool:
    """Recognize the individual-character revival dialog by two OCR facts."""
    title = task.wait_ocr(
        0.10,
        0.08,
        0.90,
        0.30,
        match=_REVIVE_TITLE,
        time_out=time_out,
        settle_time=0.2,
        raise_if_not_found=False,
    )
    if not title:
        return False
    confirm = task.wait_ocr(
        0.52,
        0.62,
        0.85,
        0.90,
        match=_CONFIRM_BUTTON,
        time_out=time_out,
        settle_time=0.1,
        raise_if_not_found=False,
    )
    return bool(confirm)


def click_realm_defeat_exit(task: RealmStateTask) -> None:
    """Leave a failed realm so the proven waypoint-heal flow can run."""

    if not realm_defeat_visible(task, time_out=5):
        raise RuntimeError("realm defeat screen is no longer visible")
    clicked = task.wait_click_ocr(
        0.20,
        0.75,
        0.50,
        0.93,
        match=_EXIT_BUTTON,
        time_out=5,
        settle_time=0.2,
        raise_if_not_found=False,
        after_sleep=2,
    )
    if not clicked:
        raise RuntimeError("could not click Exit on realm defeat screen")


TUTORIAL_OVERLAY_MARKER = "HOST_FARM_ECHO_TUTORIAL_OVERLAY_DISMISSED"
_TUTORIAL_HINT = re.compile(r"(?:切换至最后一页|可关闭界面)")
_TUTORIAL_MAX_PAGES = 6


def tutorial_overlay_visible(task: RealmStateTask, *, time_out: float = 1.5) -> bool:
    """识别 3.7 起的玩法教程弹窗（如「咎锁遗患」），底部固定提示翻到最后一页才能关。"""
    return bool(
        task.wait_ocr(
            0.25,
            0.88,
            0.75,
            0.99,
            match=_TUTORIAL_HINT,
            time_out=time_out,
            settle_time=0.2,
            raise_if_not_found=False,
        )
    )


def dismiss_tutorial_overlay(task: RealmStateTask) -> bool:
    """翻页（D）到最后一页后按 ESC 关闭；没有弹窗返回 False。

    弹窗出现在进世界后第一时间，上游 ensure_main 的 ESC 循环关不掉它，
    所以由 is_main 钩子调用本函数，不能反过来依赖 ensure_main。
    """
    if not tutorial_overlay_visible(task, time_out=1):
        return False
    task.log_info(TUTORIAL_OVERLAY_MARKER)
    for _ in range(_TUTORIAL_MAX_PAGES):
        task.send_key("d", after_sleep=1)
        if not tutorial_overlay_visible(task, time_out=1):
            break
    task.send_key("esc", after_sleep=1)
    if tutorial_overlay_visible(task, time_out=1):
        raise RuntimeError("tutorial overlay could not be dismissed")
    return True
