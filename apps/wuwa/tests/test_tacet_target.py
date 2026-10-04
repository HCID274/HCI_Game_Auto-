from types import SimpleNamespace

import pytest

from wuwa_auto.okww.tacet_target import (
    TACET_TARGET_MARKER,
    install_tacet_target_by_name,
)

# 2026-10-04 首屏：3.7 在顶部插入沉心域、烬心域，玄幽东岳从第 2 行变成第 4 行。
ROWS_1004 = ["沉心域无音区", "烬心域无音区", "方擎西峰无音区", "玄幽东岳无音区"]
BUTTON_YS = [437, 641, 845, 1049]


def _task(rows: list[str], button_ys: list[int], *, travel: str = "team_close"):
    class TacetTask:
        def teleport_to_tacet(self, index: int) -> bool:
            raise AssertionError("upstream index click must not run")

    install_tacet_target_by_name(TacetTask)
    task = TacetTask()
    task.clicked = []
    task.logs = []
    # 行名比按钮靠上一点，并且故意打乱顺序，验证按位置排序后配对。
    task.ocr = lambda *a, **k: [
        SimpleNamespace(name=name, y=y - 60) for name, y in reversed(list(zip(rows, BUTTON_YS)))
    ]
    task.find_feature = lambda *a, **k: [SimpleNamespace(name="boss_proceed", y=y) for y in reversed(button_ys)]
    task.box_of_screen = lambda *a: None
    task.info_set = lambda *a: None
    task.sleep = lambda *a: None
    task.log_info = task.logs.append
    task.click = lambda box, **k: task.clicked.append(box.y)
    task.wait_feature = lambda *a, **k: SimpleNamespace(name=travel)
    return task


def test_picks_the_named_row_even_after_new_maps_push_it_down() -> None:
    task = _task(ROWS_1004, BUTTON_YS)

    assert task.teleport_to_tacet(1) is True

    assert task.clicked == [1049]
    assert task.logs == [f"{TACET_TARGET_MARKER} 玄幽东岳无音区"]


def test_reports_whether_the_team_screen_opened() -> None:
    assert _task(ROWS_1004, BUTTON_YS, travel="gray_teleport").teleport_to_tacet(1) is False


def test_missing_target_fails_instead_of_clicking_another_row() -> None:
    task = _task(["沉心域无音区", "烬心域无音区", "方擎西峰无音区", "落日堤屿无音区"], BUTTON_YS)

    with pytest.raises(RuntimeError, match="玄幽东岳无音区 not found"):
        task.teleport_to_tacet(1)
    assert task.clicked == []


def test_target_without_a_visible_button_fails() -> None:
    task = _task(ROWS_1004, BUTTON_YS[:3])

    with pytest.raises(RuntimeError, match="not found"):
        task.teleport_to_tacet(1)
    assert task.clicked == []
