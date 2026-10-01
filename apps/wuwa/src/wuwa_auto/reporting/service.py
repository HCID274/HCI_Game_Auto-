"""生成、归档并发送鸣潮日报；预览只写本地 ``.preview.json``，不发送。"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from game_automation_core.reporting.archive import write_json_archive
from game_automation_core.reporting.feishu import build_sectioned_card, card_text
from game_automation_core.reporting.redact import redact_sensitive_data
from game_automation_core.reporting.report import SKIPPED

from wuwa_auto.integrations.feishu import send_report_card
from wuwa_auto.reporting.boss_names import load_boss_names
from wuwa_auto.reporting.day_rollup import build_daily_rollup
from wuwa_auto.reporting.parser import parse_run
from wuwa_auto.reporting.report import build_report
from wuwa_auto.settings import REPORTS_DIR, RUNS_DIR

log = logging.getLogger(__name__)


def _publish(
    result: Any, cleanup: dict[str, Any] | None, *, allow_send: bool
) -> tuple[Path, str]:
    boss_names = load_boss_names()
    facts = parse_run(result, cleanup, boss_names=boss_names)
    facts = build_daily_rollup(result, facts, boss_names=boss_names)
    report = build_report(facts, finished_at=datetime.fromisoformat(result.finished_at))
    card = report.to_card()
    sent = send_report_card(card) if allow_send else False

    # 合并了同日多次运行的日报单独命名，不覆盖单次运行自己的归档。
    stem = f"{result.run_id}_daily_rollup" if len(facts.sources) > 1 else str(result.run_id)
    path = REPORTS_DIR / f"{stem}{'.json' if allow_send else '.preview.json'}"
    write_json_archive(
        path,
        {
            "run_id": result.run_id,
            "sent": sent,
            "preview": not allow_send,
            "facts": redact_sensitive_data(facts.to_dict()),
            "report": report.to_dict(),
            "feishu_card": card,
        },
    )
    return path, card_text(card)


def report_run(result: Any, cleanup: Any | None = None, *, allow_send: bool = True) -> Path:
    """工作流结束时调用一次：生成唯一的最终卡片、发送并归档。"""

    path, _ = _publish(
        result, cleanup.to_dict() if cleanup is not None else None, allow_send=allow_send
    )
    log.info("Wuwa report archived: %s", path)
    return path


def _archived_cleanup(run_id: str) -> dict[str, Any] | None:
    """收尾结果不在 result.json 里，预览时从当次的正式归档取回。"""

    for name in (f"{run_id}.json", f"{run_id}_daily_rollup.json"):
        path = REPORTS_DIR / name
        if path.is_file():
            facts = json.loads(path.read_text(encoding="utf-8")).get("facts") or {}
            return facts.get("cleanup") or None
    return None


def preview_archived_run(run_id: str = "latest") -> tuple[Path, str]:
    """用已归档的运行结果重建卡片，返回预览文件和卡片文字。"""

    from wuwa_auto.okww.runner import OkRunResult

    if run_id == "latest":
        runs = sorted(
            path.parent for path in RUNS_DIR.glob("*/result.json")
        )
        if not runs:
            raise SystemExit("no archived Wuwa run was found")
        run_dir = runs[-1]
    else:
        run_dir = RUNS_DIR / run_id
    result_path = run_dir / "result.json"
    if not result_path.is_file():
        raise SystemExit(f"run result not found: {result_path}")
    result = OkRunResult(**json.loads(result_path.read_text(encoding="utf-8")))
    return _publish(result, _archived_cleanup(result.run_id), allow_send=False)


def report_version_day_deferred(
    outcome: Any,
    rerun_at: datetime,
    *,
    allow_send: bool = True,
) -> Path:
    """版本日早上只更新客户端、日常改到当晚时，发一张黄卡说明改约。"""

    now = datetime.now()
    card = build_sectioned_card(
        title=f"🕒 鸣潮 版本更新，日常改到 {rerun_at:%H:%M} · {now:%m-%d %H:%M}",
        template="orange",
        lead="",
        sections=[
            (
                "今日任务",
                f"{SKIPPED} 日常任务：客户端已更新到新版本，{rerun_at:%m-%d %H:%M} 自动重跑",
            )
        ],
    )
    sent = send_report_card(card) if allow_send else False
    suffix = ".json" if allow_send else ".preview.json"
    path = REPORTS_DIR / f"version_day_deferred_{now:%Y%m%d_%H%M%S}{suffix}"
    write_json_archive(
        path,
        {
            "kind": "version_day_deferred",
            "sent": sent,
            "preview": not allow_send,
            "rerun_at": rerun_at.isoformat(),
            "launcher_actions": list(outcome.launcher_actions),
            "evidence": list(outcome.evidence_paths),
            "feishu_card": card,
        },
    )
    log.info("Wuwa version-day deferral report archived: %s", path)
    return path
