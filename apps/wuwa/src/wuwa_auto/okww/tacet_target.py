"""按名字选无音区，不按 F2 列表的第几行。

版本更新会在无音区列表顶部插入新地图（1004：玄幽东岳从第 2 行挤到第 4 行，第 2 行
变成传送不了的烬心域）。这里在列表首屏按名字找到目标行，点它那一行的「前往」，
之后与上游 click_on_book_target 相同。只在首屏里找；新地图多到把目标挤出首屏时，
需要补滚动查找。
"""

from __future__ import annotations

import re
from typing import Any

from wuwa_auto.okww.daily_trace import _BOOK_TARGET_BUTTON_REGION, _BOOK_TARGET_ROW_REGION

# 改刷别的无音区就改这里，写游戏里显示的完整名字。
TACET_NAME = "玄幽东岳无音区"
TACET_TARGET_MARKER = "HOST_TACET_TARGET_BY_NAME"
_TACET_ROW = re.compile(r"无音区")
_TRAVEL_FEATURES = ["fast_travel_custom", "gray_teleport", "remove_custom", "team_close"]


def named_tacet_button(task: Any, name: str = TACET_NAME) -> Any:
    """行名和「前往」按钮各自从上到下排好，第 k 个无音区名对应第 k 个按钮。"""
    rows = sorted(
        task.ocr(*_BOOK_TARGET_ROW_REGION, match=_TACET_ROW, log=False) or [],
        key=lambda box: box.y,
    )
    buttons = sorted(
        task.find_feature(
            "boss_proceed",
            box=task.box_of_screen(*_BOOK_TARGET_BUTTON_REGION),
            threshold=0.8,
        )
        or [],
        key=lambda box: box.y,
    )
    names = [str(box.name) for box in rows]
    rank = next((i for i, row in enumerate(names) if name in row), None)
    if rank is None or rank >= len(buttons):
        raise RuntimeError(
            f"tacet {name} not found on the first page: rows={names}, buttons={len(buttons)}"
        )
    return buttons[rank]


def install_tacet_target_by_name(tacet_task_class: type[Any]) -> None:
    if not callable(getattr(tacet_task_class, "teleport_to_tacet", None)):
        raise RuntimeError("OK-WW TacetTask is incompatible: teleport_to_tacet missing")

    def teleport_to_tacet(self: Any, index: int) -> bool:
        self.info_set("Teleport to Tacet Suppression", TACET_NAME)
        self.sleep(0.5)
        button = named_tacet_button(self)
        self.log_info(f"{TACET_TARGET_MARKER} {TACET_NAME}")
        self.click(button, after_sleep=1)
        feature = self.wait_feature(
            _TRAVEL_FEATURES,
            time_out=10,
            settle_time=0.5,
            raise_if_not_found=True,
        )
        return feature.name == "team_close"

    tacet_task_class.teleport_to_tacet = teleport_to_tacet
