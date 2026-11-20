"""每天开跑前用三月七助手自带的更新程序装官方新版本；没装上就用旧版本照常跑。"""

from __future__ import annotations

import logging
import re
import subprocess
import time

import psutil

from starrail_auto.m7a.config import (
    M7A_ASSISTANT_PROCESS_NAME,
    M7A_LAUNCHER,
    M7A_LAUNCHER_PROCESS_NAME,
)
from starrail_auto.m7a.logs import capture_log_checkpoint, read_log_since
from starrail_auto.m7a.runner import stop_new_m7a_processes

log = logging.getLogger(__name__)

M7A_DIR = M7A_LAUNCHER.parent
UPDATER = M7A_DIR / "March7th Updater.exe"
VERSION_FILE = M7A_DIR / "assets" / "config" / "version.txt"
M7A_PROCESS_NAMES = {
    M7A_ASSISTANT_PROCESS_NAME,
    M7A_LAUNCHER_PROCESS_NAME,
    "march7th updater.exe",
}
# 下载加解压的上限（11-18 手动更新下载整包用了 2 分钟）；开始覆盖后换成覆盖的上限，
# 不在覆盖到一半时打断。
PREPARE_TIMEOUT = 20 * 60
COVER_TIMEOUT = 10 * 60
POLL_INTERVAL = 2.0
# 更新程序装完会自己打开三月七助手界面；等它启动完再关，接着照常开跑。
GUI_SETTLE_SECONDS = 15.0
# 更新程序写进三月七助手日志的话（v2026.11.19 的写法）。出错后它会弹窗等人点，
# 所以看到出错就直接结束它；认不出新写法时由超时兜底。
COVER_STARTED_MARKER = "开始覆盖安装"
NO_UPDATE_MARKER = "当前已是最新版本"
FAILURE_PATTERN = re.compile(r"(?:更新过程出错|更新被文件占用阻止)：(?P<reason>.+)")


def _version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "旧版本"
    except OSError:
        return "旧版本"


def _m7a_running() -> bool:
    return any(
        (proc.info["name"] or "").casefold() in M7A_PROCESS_NAMES
        for proc in psutil.process_iter(["name"])
    )


def _kill_tree(pid: int) -> None:
    try:
        root = psutil.Process(pid)
        targets = [*root.children(recursive=True), root]
    except psutil.NoSuchProcess:
        return
    for proc in targets:
        try:
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    psutil.wait_procs(targets, timeout=10)


def _wait_for_updater(updater: subprocess.Popen, checkpoint) -> str:
    """等更新程序自己退出；返回出错原因，正常退出返回空字符串。"""
    deadline = time.monotonic() + PREPARE_TIMEOUT
    covering = False
    while True:
        content = read_log_since(checkpoint)
        failure = FAILURE_PATTERN.search(content)
        if failure:
            return failure.group("reason").strip()
        if updater.poll() is not None:
            return ""
        if not covering and COVER_STARTED_MARKER in content:
            covering = True
            deadline = time.monotonic() + COVER_TIMEOUT
        if time.monotonic() >= deadline:
            return "覆盖新文件超时" if covering else "下载或解压超时"
        time.sleep(POLL_INTERVAL)


def _update() -> list[str]:
    old = _version()
    if not UPDATER.is_file():
        log.error("M7A updater is missing: %s", UPDATER)
        return [f"没找到三月七助手的更新程序，今天用的还是 {old}"]
    if _m7a_running():
        log.warning("M7A is already running; skip update")
        return [f"开跑前三月七助手已经开着，今天没更新，用的还是 {old}"]

    checkpoint = capture_log_checkpoint()
    started_at = time.time()
    updater = subprocess.Popen([str(UPDATER), "--start-minimized-to-tray"], cwd=M7A_DIR)
    log.info("M7A updater started: pid=%d version=%s", updater.pid, old)
    reason = _wait_for_updater(updater, checkpoint)
    if reason:
        log.error("M7A update failed: %s", reason)
        _kill_tree(updater.pid)
        return [f"三月七助手没能更新（{reason}），今天用的还是 {old}"]

    new = _version()
    if new != old:
        time.sleep(GUI_SETTLE_SECONDS)
        stop_new_m7a_processes(started_at)
        log.info("M7A updated: %s -> %s", old, new)
        return [f"三月七助手已更新：{old} → {new}"]
    if NO_UPDATE_MARKER in read_log_since(checkpoint):
        log.info("M7A is up to date: %s", old)
        return []
    log.error("M7A updater exited without changing version: %s", old)
    return [f"三月七助手的更新程序退出了，版本没变，今天用的还是 {old}"]


def update_m7a() -> list[str]:
    """装上官方新版本；返回写进日报提醒的话，已是最新时返回空列表。任何异常都不影响开跑。"""
    try:
        return _update()
    except Exception:
        log.exception("M7A update crashed; continuing with the installed version")
        return [f"三月七助手更新出错，今天用的还是 {_version()}"]
