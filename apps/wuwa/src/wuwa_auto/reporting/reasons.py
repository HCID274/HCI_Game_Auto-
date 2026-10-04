"""把宿主流程和 OK-WW 的英文失败原因翻译成卡片上的中文说明。

原因串由 ``daily.py`` 拼成：先讨伐后日常的组合运行写作
``pre-daily FarmEcho failed: <讨伐原因>; DailyTask failed: <日常原因>``，
每段原因后面还可能跟着 ``; key=value`` 诊断字段，这些只留在归档里。
没收录的原因原样截短展示，不猜测含义。
"""

from __future__ import annotations

import re

# 原因串里的阶段前缀 -> (阶段键, 卡片上的中文前缀)
_PHASES = {
    "pre-daily FarmEcho failed": ("boss", "讨伐"),
    "DailyTask failed": ("daily", "日常"),
}
_PHASE_SPLIT = re.compile(r"(?:^|;\s*)(pre-daily FarmEcho failed|DailyTask failed):\s*")
_DIAGNOSTIC_FIELD = re.compile(r"^\w+=")
# 包装前缀 -> 内层原因无法翻译时使用的中文前缀
_WRAPPERS = (
    (re.compile(r"^OK-WW failure marker: "), "OK-WW 报错："),
    (re.compile(r"^daily retry \d+: "), "日常重试时脚本异常："),
    (re.compile(r"^[\w ]+? exception: "), "脚本异常："),
)
_RULES = tuple(
    (re.compile(pattern), template)
    for pattern, template in (
        (
            r"confirmed retry returned early: \w+=(?P<done>\d+)/(?P<target>\d+)",
            "OK-WW 讨伐提前结束（吸收声骸 {done}/{target}）",
        ),
        (
            r"FarmEcho recovery incomplete: \w+ (?P<done>\d+)/(?P<target>\d+)",
            "讨伐自动恢复后仍没打完（吸收声骸 {done}/{target}）",
        ),
        (
            r"absorption target timed out after (?P<seconds>\d+) seconds",
            "讨伐超过 {seconds} 秒还没打完",
        ),
        (
            r"DailyTask skipped until FarmEcho reaches (?P<target>\d+/\d+)",
            "要等讨伐打到 {target} 才开始，本轮跳过",
        ),
        (
            r"did not hand off to FarmEcho within (?P<seconds>\d+) seconds",
            "OK-WW 启动后 {seconds} 秒内没有开始讨伐",
        ),
        (
            r"below threshold(?: after claim)?: points=(?P<points>\d+), target=(?P<target>\d+)",
            "每日活跃度没达标（{points}/{target}）",
        ),
        (r"daily activity unverified", "每日活跃度奖励没确认到账"),
        (r"completed without verified daily activity claim", "日常跑完了，但每日活跃度奖励没确认领取"),
        (r"Daily Task exception stopped", "OK-WW 日常任务异常中止"),
        (
            r"UU startup failed after (?P<count>\d+) restart",
            "UU 加速器启动失败（重启 {count} 次后放弃）",
        ),
        (r"client window disappeared", "游戏窗口中途消失（闪退或被关闭）"),
        (r"startup network retry", "游戏启动时网络连不上，重试用完"),
        (r"launcher update stalled", "官方启动器更新卡住"),
        (r"did not reach a stable in-world HUD", "游戏没能进入主界面"),
        (r"worker exited before the completion marker", "OK-WW 没跑完就退出了"),
        (r"worker exited with code (?P<code>-?\d+)", "OK-WW 异常退出（代码 {code}）"),
        (r"log stalled", "OK-WW 日志长时间没有更新，判定卡住"),
        (r"could not bind the current active character", "OK-WW 识别不到当前角色"),
        (r"interactive desktop is blocked", "桌面被锁屏或系统弹窗挡住"),
        (r"produced no current-run log before startup deadline", "OK-WW 启动后一直没有开始运行"),
        (r"daily world-state recovery failed", "日常中断后没能把游戏恢复到大世界"),
    )
)
MAX_RAW_CHARS = 80


def _shorten(text: str) -> str:
    return text if len(text) <= MAX_RAW_CHARS else f"{text[:MAX_RAW_CHARS]}…"


def _translate(clause: str) -> str:
    fallback_prefix = ""
    for wrapper, prefix in _WRAPPERS:
        if wrapper.match(clause):
            clause = wrapper.sub("", clause, count=1)
            fallback_prefix = prefix
            break
    for pattern, template in _RULES:
        match = pattern.search(clause)
        if match:
            return template.format(**match.groupdict())
    return f"{fallback_prefix}{_shorten(clause)}"


def _main_clause(segment: str) -> str:
    clauses = [part.strip() for part in segment.split(";")]
    return next(
        (part for part in clauses if part and not _DIAGNOSTIC_FIELD.match(part)),
        "",
    )


def explain_failure(reason: str) -> list[tuple[str | None, str]]:
    """每个失败阶段一句中文，连同阶段键（boss/daily）返回；没有阶段前缀的为 None。"""

    parts = _PHASE_SPLIT.split(reason.strip())
    segments = [(None, "", parts[0])] if parts[0].strip() else []
    segments += [
        (*_PHASES[parts[index]], parts[index + 1]) for index in range(1, len(parts), 2)
    ]
    explained: list[tuple[str | None, str]] = []
    for phase, label, segment in segments:
        clause = _main_clause(segment)
        if not clause:
            continue
        text = _translate(clause)
        line = (phase, f"{label}：{text}" if label else text)
        if line not in explained:
            explained.append(line)
    return explained
