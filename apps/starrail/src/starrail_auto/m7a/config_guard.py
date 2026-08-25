"""Protect the external M7A configuration without patching upstream code."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from starrail_auto.m7a.config import M7A_CONFIG_BACKUP_DIR, M7A_CONFIG_PATH

log = logging.getLogger(__name__)

MAX_NORMAL_PLAN_REDUCTION = 30
LAST_KNOWN_GOOD_NAME = "last-known-good.yaml"
MANIFEST_NAME = "manifest.json"


class M7AConfigProtectionError(RuntimeError):
    """Raised after an unsafe M7A configuration was blocked or restored."""


@dataclass(frozen=True)
class ConfigSnapshot:
    content: bytes
    data: dict[str, Any]
    sha256: str
    backup_path: Path


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _load(content: bytes, *, source: Path) -> dict[str, Any]:
    try:
        loaded = yaml.safe_load(content.decode("utf-8-sig"))
    except (UnicodeError, yaml.YAMLError) as exc:
        raise M7AConfigProtectionError(f"配置无法解析: {source}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise M7AConfigProtectionError(f"配置根节点不是映射: {source}")

    power_plan = loaded.get("power_plan")
    if not isinstance(power_plan, list):
        raise M7AConfigProtectionError(f"power_plan 不是列表: {source}")
    for index, entry in enumerate(power_plan, start=1):
        if not isinstance(entry, list) or len(entry) < 3:
            raise M7AConfigProtectionError(f"power_plan 第{index}项结构无效: {source}")
        instance_type, instance_name, count = entry[:3]
        if not isinstance(instance_type, str) or not instance_type.strip():
            raise M7AConfigProtectionError(f"power_plan 第{index}项类型无效: {source}")
        if not isinstance(instance_name, str) or not instance_name.strip():
            raise M7AConfigProtectionError(f"power_plan 第{index}项名称无效: {source}")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise M7AConfigProtectionError(f"power_plan 第{index}项次数无效: {source}")
    for key in ("game_path", "launcher_path"):
        if not isinstance(loaded.get(key), str) or not loaded[key].strip():
            raise M7AConfigProtectionError(f"缺少有效的 {key}: {source}")
    return loaded


def _read(path: Path) -> tuple[bytes, dict[str, Any]]:
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise M7AConfigProtectionError(f"配置无法读取: {path}: {exc}") from exc
    return content, _load(content, source=path)


def _plan(data: dict[str, Any]) -> list[tuple[str, str, int]]:
    return [
        (str(entry[0]).strip(), str(entry[1]).strip(), int(entry[2]))
        for entry in data["power_plan"]
    ]


def _plan_transition_is_safe(
    before: dict[str, Any],
    after: dict[str, Any],
) -> bool:
    old = _plan(before)
    new = _plan(after)
    if old == new:
        return True
    old_total = sum(entry[2] for entry in old)
    new_total = sum(entry[2] for entry in new)
    if new_total > old_total:
        return False
    if not new:
        unlimited = bool(before.get("use_reserved_trailblaze_power")) or bool(
            before.get("use_fuel")
        )
        return unlimited or old_total <= MAX_NORMAL_PLAN_REDUCTION

    for start in range(len(old)):
        suffix = old[start:]
        if len(new) != len(suffix):
            continue
        identities_match = all(
            current[:2] == previous[:2]
            for previous, current in zip(suffix, new, strict=True)
        )
        counts_decrease = all(
            current[2] <= previous[2]
            for previous, current in zip(suffix, new, strict=True)
        )
        if identities_match and counts_decrease:
            unlimited = bool(before.get("use_reserved_trailblaze_power")) or bool(
                before.get("use_fuel")
            )
            return unlimited or old_total - new_total <= MAX_NORMAL_PLAN_REDUCTION
    return False


def _write_manifest(
    backup_dir: Path,
    *,
    event: str,
    snapshot: ConfigSnapshot,
) -> None:
    manifest = {
        "event": event,
        "recorded_at": datetime.now().astimezone().isoformat(),
        "sha256": snapshot.sha256,
        "backup_path": str(snapshot.backup_path),
        "power_plan": [list(entry) for entry in _plan(snapshot.data)],
    }
    _atomic_write(
        backup_dir / MANIFEST_NAME,
        json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
    )


def _snapshot(
    config_path: Path,
    backup_dir: Path,
    *,
    label: str,
) -> ConfigSnapshot:
    content, data = _read(config_path)
    digest = _sha256(content)
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = backup_dir / "versions" / f"{timestamp}_{label}_{digest[:12]}.yaml"
    _atomic_write(backup_path, content)
    return ConfigSnapshot(content, data, digest, backup_path)


class M7AConfigSession:
    """One guarded M7A launch, anchored to its pre-launch snapshot."""

    def __init__(
        self,
        *,
        config_path: Path,
        backup_dir: Path,
        before: ConfigSnapshot,
    ) -> None:
        self.config_path = config_path
        self.backup_dir = backup_dir
        self.before = before

    def verify_live(self) -> None:
        """Restore the pre-launch bytes and stop when upstream mutates unsafely."""
        try:
            content, data = _read(self.config_path)
        except M7AConfigProtectionError as exc:
            self._restore_before(f"运行期间配置损坏: {exc}")
        if content == self.before.content:
            return
        if not _plan_transition_is_safe(self.before.data, data):
            self._restore_before("运行期间体力计划发生不可能的跳变")

    def finalize(self) -> ConfigSnapshot:
        """Accept a valid M7A result as the next recovery baseline."""
        self.verify_live()
        snapshot = _snapshot(self.config_path, self.backup_dir, label="after")
        _atomic_write(self.backup_dir / LAST_KNOWN_GOOD_NAME, snapshot.content)
        _write_manifest(self.backup_dir, event="run-finalized", snapshot=snapshot)
        log.info(
            "M7A config finalized: sha256=%s backup=%s",
            snapshot.sha256,
            snapshot.backup_path,
        )
        return snapshot

    def _restore_before(self, reason: str) -> None:
        _atomic_write(self.config_path, self.before.content)
        restored = _snapshot(self.config_path, self.backup_dir, label="restored")
        _write_manifest(self.backup_dir, event="unsafe-change-restored", snapshot=restored)
        log.error(
            "M7A config protection restored pre-launch snapshot: reason=%s sha256=%s backup=%s",
            reason,
            restored.sha256,
            restored.backup_path,
        )
        raise M7AConfigProtectionError(reason)


def prepare_m7a_config(
    config_path: Path = M7A_CONFIG_PATH,
    backup_dir: Path = M7A_CONFIG_BACKUP_DIR,
) -> M7AConfigSession:
    """Validate, recover if necessary, and freeze one pre-launch snapshot."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    last_known_good = backup_dir / LAST_KNOWN_GOOD_NAME
    has_last_known_good = last_known_good.is_file()
    try:
        current_content, current_data = _read(config_path)
    except M7AConfigProtectionError as exc:
        if not has_last_known_good:
            log.error("M7A config preflight failed without recovery point: %s", exc)
            raise
        trusted_content, trusted_data = _read(last_known_good)
        _atomic_write(config_path, trusted_content)
        log.error("M7A config preflight restored last-known-good: %s", exc)
        current_content, current_data = trusted_content, trusted_data

    trusted_data: dict[str, Any] | None = None
    if has_last_known_good:
        trusted_content, trusted_data = _read(last_known_good)
        if not _plan_transition_is_safe(trusted_data, current_data):
            trusted_total = sum(entry[2] for entry in _plan(trusted_data))
            current_total = sum(entry[2] for entry in _plan(current_data))
            if current_total == 0 and trusted_total > MAX_NORMAL_PLAN_REDUCTION:
                _atomic_write(config_path, trusted_content)
                log.error(
                    "M7A config preflight blocked probable default reset: old_total=%d new_total=%d",
                    trusted_total,
                    current_total,
                )
                current_content, current_data = trusted_content, trusted_data

    snapshot = _snapshot(config_path, backup_dir, label="before")
    if not has_last_known_good or _plan_transition_is_safe(trusted_data, current_data):
        _atomic_write(last_known_good, current_content)
    _write_manifest(backup_dir, event="run-prepared", snapshot=snapshot)
    log.info(
        "M7A config prepared: sha256=%s plan_items=%d backup=%s",
        snapshot.sha256,
        len(_plan(snapshot.data)),
        snapshot.backup_path,
    )
    return M7AConfigSession(
        config_path=config_path,
        backup_dir=backup_dir,
        before=snapshot,
    )


__all__ = [
    "M7AConfigProtectionError",
    "M7AConfigSession",
    "prepare_m7a_config",
]
