"""Typed failures shared by UU adapters."""

from pathlib import Path


class UuStartupError(RuntimeError):
    def __init__(
        self,
        step_name: str,
        reason: str,
        *,
        retryable: bool = True,
        screenshot_path: Path | None = None,
        restarts_used: int = 0,
    ) -> None:
        self.step_name = step_name
        self.reason = reason
        self.retryable = retryable
        self.screenshot_path = screenshot_path
        self.restarts_used = restarts_used
        detail = f"{step_name}: {reason}"
        if screenshot_path is not None:
            detail += f"; screenshot={screenshot_path}"
        if not retryable:
            detail += "; retryable=false"
        super().__init__(detail)


class UuStartupFinalError(RuntimeError):
    def __init__(self, last_error: UuStartupError, restarts_used: int) -> None:
        self.last_error = last_error
        self.restarts_used = restarts_used
        super().__init__(
            f"UU startup failed after {restarts_used} restart(s): {last_error}"
        )

