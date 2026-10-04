"""无音区传送不了时跳过，让日常继续领奖。

版本更新会在 F2 无音区列表里插入新地图，配置序号可能指到"附近信标无法快速到达"、
「前往」置灰的条目（1004）。上游 farm_tacet 等不到开始挑战就中止整个日常，重试也
点不亮按钮。这里只在面板确实显示该提示时跳过无音区；因此活跃度凑不满时只记录，
继续领邮件和通行证，由宿主按部分完成汇报、不再重试。
"""

from __future__ import annotations

import re
from typing import Any

from wuwa_auto.okww.daily_activity import DailyActivityVerificationError

TACET_UNREACHABLE_MARKER = "HOST_TACET_UNREACHABLE_SKIPPED"
ACTIVITY_SHORT_AFTER_TACET_SKIP_MARKER = "HOST_DAILY_ACTIVITY_SHORT_AFTER_TACET_SKIP"
_UNREACHABLE = re.compile(r"无法快速到达")


def tacet_unreachable_visible(task: Any) -> bool:
    """无音区详情面板底部的红色提示条，位于「追踪/前往」按钮上方。"""
    return bool(
        task.wait_ocr(
            0.65,
            0.78,
            1.0,
            0.93,
            match=_UNREACHABLE,
            time_out=2,
            settle_time=0.2,
            raise_if_not_found=False,
        )
    )


def install_tacet_unreachable_skip(daily_task_class: type[Any], tacet_task_class: type[Any]) -> None:
    farm_tacet = getattr(tacet_task_class, "farm_tacet", None)
    claim_daily = getattr(daily_task_class, "claim_daily", None)
    if not (callable(farm_tacet) and callable(claim_daily)):
        raise RuntimeError("OK-WW DailyTask/TacetTask is incompatible: tacet skip methods missing")
    if getattr(farm_tacet, "__wuwa_host_tacet_skip__", False):
        return
    skipped = False

    def host_farm_tacet(self: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal skipped
        try:
            return farm_tacet(self, *args, **kwargs)
        except Exception:
            if not tacet_unreachable_visible(self):
                raise
        skipped = True
        config = kwargs.get("config") or getattr(self, "config", None) or {}
        self.log_info(
            f"{TACET_UNREACHABLE_MARKER} index={config.get('Which Tacet Suppression to Farm')}"
        )
        self.ensure_main(time_out=30)
        return None

    def host_claim_daily(self: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return claim_daily(self, *args, **kwargs)
        except DailyActivityVerificationError as exc:
            if not skipped:
                raise
            self.log_info(f"{ACTIVITY_SHORT_AFTER_TACET_SKIP_MARKER} {exc}")
            return None

    host_farm_tacet.__wuwa_host_tacet_skip__ = True
    tacet_task_class.farm_tacet = host_farm_tacet
    daily_task_class.claim_daily = host_claim_daily
