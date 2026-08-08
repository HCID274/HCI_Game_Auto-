"""Host-owned execution tracing for the installed OK-WW daily task.

The bundled OK-WW files are treated as replaceable upstream artifacts.  This
module adds observability by wrapping methods in memory inside the worker
process; it does not write to the installation or change task behavior.
"""

from __future__ import annotations

import json
import re
from functools import wraps
from typing import Any, Callable

from wuwa_auto.okww.daily_capabilities import capability_matrix

TRACE_MARKER = "HOST_OKWW_DAILY_TRACE"
CAPABILITIES_MARKER = "HOST_OKWW_DAILY_CAPABILITIES"


def _json_value(value: Any, *, limit: int = 240) -> Any:
    """Keep trace payloads small and free of opaque runtime objects."""

    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "..."
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item, limit=limit) for item in list(value)[:40]]
    if isinstance(value, dict):
        return {
            str(key): _json_value(item, limit=limit)
            for key, item in list(value.items())[:40]
        }
    name = getattr(value, "name", None)
    if name:
        return str(name)
    cache_key = getattr(value, "cache_key", None)
    if cache_key:
        return str(cache_key)
    return type(value).__name__


def _log(task: Any, event: str, **fields: Any) -> None:
    log_info = getattr(task, "log_info", None)
    if not callable(log_info):
        return
    payload = {
        "event": event,
        **{key: _json_value(value) for key, value in fields.items()},
    }
    log_info(f"{TRACE_MARKER} {json.dumps(payload, ensure_ascii=False)}")


def _wrap_method(
    task_class: type[Any],
    method_name: str,
    *,
    event: str,
    before: Callable[[Any, tuple[Any, ...], dict[str, Any]], dict[str, Any]]
    | None = None,
    after: Callable[[Any, Any], dict[str, Any]] | None = None,
) -> None:
    original = getattr(task_class, method_name, None)
    if not callable(original) or getattr(original, "__wuwa_host_trace__", False):
        return

    @wraps(original)
    def traced(self: Any, *args: Any, **kwargs: Any) -> Any:
        fields = before(self, args, kwargs) if before else {}
        _log(self, f"{event}_start", **fields)
        try:
            result = original(self, *args, **kwargs)
        except Exception as exc:
            _log(
                self,
                f"{event}_error",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise
        _log(
            self,
            f"{event}_end",
            **(after(self, result) if after else {"result": result}),
        )
        return result

    traced.__wuwa_host_trace__ = True
    setattr(task_class, method_name, traced)


def _task_config(task: Any) -> dict[str, Any]:
    config = getattr(task, "config", {})
    return config if isinstance(config, dict) else {}


def _daily_run_before(task: Any, _args: tuple[Any, ...], _kwargs: dict[str, Any]) -> dict[str, Any]:
    config = _task_config(task)
    executor = getattr(task, "executor", None)
    onetime_tasks = getattr(executor, "onetime_tasks", []) if executor else []
    available = [
        getattr(candidate, "__class__", type(candidate)).__name__
        for candidate in onetime_tasks or []
    ]
    _log(
        task,
        "capabilities",
        configured_daily={
            "which_to_farm": config.get("Which to Farm"),
            "which_tacet": config.get("Which Tacet Suppression to Farm"),
            "which_forgery": config.get("Which Forgery Challenge to Farm"),
            "farm_nightmare_for_daily_echo": config.get(
                "Farm Nightmare Nest for Daily Echo"
            ),
            "additional_tasks": config.get(
                "Additional Tasks to Run After Daily Task"
            ),
        },
        supported_tasks=getattr(task, "support_tasks", []),
        activity_capability_matrix=capability_matrix(),
        configured_onetime_tasks=available,
    )
    return {"config": config}


def _run_task_before(_task: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    selected = args[0] if args else kwargs.get("task")
    if isinstance(selected, type):
        selected_name = selected.__name__
    else:
        selected_name = (
            getattr(selected, "__class__", type(selected)).__name__
            if selected is not None
            else None
        )
    return {
        "selected_task": selected_name,
    }


def _open_daily_after(_task: Any, result: Any) -> dict[str, Any]:
    if isinstance(result, (list, tuple)) and len(result) == 2:
        return {"used_stamina": result[0], "daily_reward_ready": result[1]}
    return {"result": result}


def _farm_before(_task: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    used_stamina = kwargs.get("used_stamina")
    if used_stamina is None and len(args) >= 2:
        used_stamina = args[1]
    daily = kwargs.get("daily")
    if daily is None and args:
        daily = args[0]
    return {"daily": daily, "used_stamina": used_stamina}


def _nest_init_after(task: Any, _result: Any) -> dict[str, Any]:
    return {"queue": [getattr(action, "__name__", str(action)) for action in task.queues]}


def _nest_target_after(_task: Any, result: Any) -> dict[str, Any]:
    return {
        "target": getattr(result, "cache_key", None),
        "target_type": type(result).__name__ if result is not None else None,
    }


def _install_nightmare_ocr_trace(task_class: type[Any]) -> None:
    """Record the exact count OCR used to select a nightmare target."""

    original = getattr(task_class, "ocr", None)
    if not callable(original) or getattr(original, "__wuwa_host_ocr_trace__", False):
        return

    @wraps(original)
    def traced(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = original(self, *args, **kwargs)
        try:
            x, y = float(args[0]), float(args[1])
        except (IndexError, TypeError, ValueError):
            x = y = -1
        match = kwargs.get("match")
        is_count_region = abs(x - 0.35) < 0.01 and abs(y - 0.13) < 0.01
        if is_count_region and isinstance(match, re.Pattern):
            names = [str(getattr(item, "name", item)) for item in result or []]
            _log(self, "nightmare_count_ocr", names=names, pattern=match.pattern)
        return result

    traced.__wuwa_host_ocr_trace__ = True
    setattr(task_class, "ocr", traced)


def install_daily_trace(
    daily_task_class: type[Any],
    *,
    nightmare_task_class: type[Any] | None = None,
    tacet_task_class: type[Any] | None = None,
    forgery_task_class: type[Any] | None = None,
    simulation_task_class: type[Any] | None = None,
) -> None:
    """Install idempotent, behavior-preserving trace wrappers."""

    _wrap_method(
        daily_task_class,
        "run",
        event="daily_run",
        before=_daily_run_before,
    )
    _wrap_method(
        daily_task_class,
        "open_daily",
        event="open_daily",
        after=_open_daily_after,
    )
    _wrap_method(
        daily_task_class,
        "run_task_by_class",
        event="dispatch_subtask",
        before=_run_task_before,
        after=lambda _task, result: {"result": result},
    )
    for name in ("claim_daily", "claim_mail", "claim_battle_pass", "run_additional_tasks"):
        _wrap_method(daily_task_class, name, event=name)

    if nightmare_task_class is not None:
        _install_nightmare_ocr_trace(nightmare_task_class)
        _wrap_method(nightmare_task_class, "run", event="nightmare_run")
        _wrap_method(nightmare_task_class, "run_capture_mode", event="nightmare_capture")
        _wrap_method(nightmare_task_class, "_init_queue", event="nightmare_init_queue", after=_nest_init_after)
        _wrap_method(nightmare_task_class, "get_nest_to_go", event="nightmare_find_target", after=_nest_target_after)
        _wrap_method(nightmare_task_class, "combat_nest", event="nightmare_combat")

    farm_methods = (
        (tacet_task_class, "farm_tacet", "tacet"),
        (forgery_task_class, "farm_forgery", "forgery"),
        (simulation_task_class, "farm_simulation", "simulation"),
    )
    for task_class, method_name, event in farm_methods:
        if task_class is not None:
            _wrap_method(task_class, method_name, event=event, before=_farm_before)
