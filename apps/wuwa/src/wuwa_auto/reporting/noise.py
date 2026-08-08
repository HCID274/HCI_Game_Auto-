"""Known OK-WW startup noise excluded from the AI evidence slice.

The deterministic parser still receives the complete current-run log.  This
module only identifies messages that historical successful runs emit during
initialisation and that do not describe a business-task result.  Line numbers
are returned instead of deleting text so diagnostics can continue to cite the
original log accurately.
"""

from __future__ import annotations

import re

_OCR_TRANSLATION_ERROR = re.compile(
    r"TaskExecutor:install ocr translations error for zh_CN",
    re.IGNORECASE,
)
_RTX_DYNAMIC_VIBRANCE = re.compile(
    r"(?:NVIDIA RTX Dynamic Vibrance enabled|"
    r"GPU driver post-processing detected features:.*RTX Dynamic Vibrance|"
    r"NVIDIA RTX Dynamic Vibrance is enabled and may cause malfunctions)",
    re.IGNORECASE,
)
_CAPTURE_FALLBACK = re.compile(
    r"Selected capture method is not supported",
    re.IGNORECASE,
)
_WGC_SUCCESS = re.compile(
    r"(?:windows_graphics:[^\n]*start(?:_or_stop)?[^\n]*WGC capture|"
    r"update:use WGC capture|"
    r"DeviceManager:capture method <class [^>]*WindowsGraphicsCaptureMethod>)",
    re.IGNORECASE,
)
_ACTIVITY_REWARD_VERIFIED = re.compile(
    r"HOST_DAILY_ACTIVITY_CLAIM_VERIFIED",
    re.IGNORECASE,
)
_SETTLED_ACTIVITY_PANEL_CONTEXT = re.compile(
    r"(?:HOST_DAILY_ACTIVITY_PANEL|"
    r"HOST_OKWW_DAILY_TRACE .*\"event\":\s*\"capabilities\")",
    re.IGNORECASE,
)


def known_upstream_noise_lines(log_text: str) -> frozenset[int]:
    """Return original line numbers for harmless OK-WW startup messages.

    The capture-method message is ignored only when the same run proves that
    WGC fallback was selected.  If no fallback appears, the message remains
    available to the Agent as a potentially causal startup failure.
    """

    lines = log_text.splitlines()
    activity_reward_verified = any(
        _ACTIVITY_REWARD_VERIFIED.search(line) for line in lines
    )
    # A later WGC line close to the unsupported-capture message is the
    # evidence that OK-WW recovered.  Do not let an unrelated WGC operation
    # much later in a long run hide a causal startup failure.
    recovery_lines = [
        number
        for number, line in enumerate(lines, 1)
        if _WGC_SUCCESS.search(line)
        and not re.search(
            r"(?:error|failed|failure|unavailable|unsupported)",
            line,
            re.IGNORECASE,
        )
    ]
    ignored: set[int] = set()
    for number, line in enumerate(lines, 1):
        is_startup_noise = (
            _OCR_TRANSLATION_ERROR.search(line)
            or _RTX_DYNAMIC_VIBRANCE.search(line)
            or (
                activity_reward_verified
                and _SETTLED_ACTIVITY_PANEL_CONTEXT.search(line)
            )
            or (
                _CAPTURE_FALLBACK.search(line)
                and any(
                    0 < recovery_line - number <= 20
                    for recovery_line in recovery_lines
                )
            )
        )
        if is_startup_noise:
            ignored.add(number)
    return frozenset(ignored)


__all__ = ["known_upstream_noise_lines"]
