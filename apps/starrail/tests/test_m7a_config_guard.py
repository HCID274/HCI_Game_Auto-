"""Regression tests for host-side M7A configuration protection."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from starrail_auto.m7a.config_guard import (
    LAST_KNOWN_GOOD_NAME,
    M7AConfigProtectionError,
    prepare_m7a_config,
)


def _config(plan: list[list[object]]) -> bytes:
    return yaml.safe_dump(
        {
            "game_path": r"D:\Games\StarRail.exe",
            "launcher_path": r"D:\Games\launcher.exe",
            "power_plan": plan,
            "use_reserved_trailblaze_power": False,
            "use_fuel": False,
        },
        allow_unicode=True,
        sort_keys=False,
    ).encode("utf-8")


def _write(path: Path, content: bytes) -> None:
    path.write_bytes(content)


def test_prepare_creates_versioned_backup_and_recovery_baseline(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    backup_dir = tmp_path / "backups"
    original = _config([["侵蚀隧洞", "睿治之径", 132]])
    _write(config_path, original)

    session = prepare_m7a_config(config_path, backup_dir)

    assert session.before.content == original
    assert session.before.backup_path.is_file()
    assert (backup_dir / LAST_KNOWN_GOOD_NAME).read_bytes() == original
    assert (backup_dir / "manifest.json").is_file()


def test_invalid_yaml_is_restored_from_last_known_good(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    backup_dir = tmp_path / "backups"
    trusted = _config([["侵蚀隧洞", "睿治之径", 132]])
    backup_dir.mkdir()
    _write(backup_dir / LAST_KNOWN_GOOD_NAME, trusted)
    _write(config_path, b"power_plan: [")

    session = prepare_m7a_config(config_path, backup_dir)

    assert session.before.content == trusted
    assert config_path.read_bytes() == trusted


def test_probable_default_reset_is_restored_before_launch(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    backup_dir = tmp_path / "backups"
    trusted = _config(
        [["侵蚀隧洞", "睿治之径", 132], ["饰品提取", "鎏金追忆", 80]]
    )
    reset = _config([])
    backup_dir.mkdir()
    _write(backup_dir / LAST_KNOWN_GOOD_NAME, trusted)
    _write(config_path, reset)

    session = prepare_m7a_config(config_path, backup_dir)

    assert session.before.content == trusted
    assert config_path.read_bytes() == trusted


def test_unsafe_live_plan_loss_is_restored_and_stops_run(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    backup_dir = tmp_path / "backups"
    trusted = _config(
        [["侵蚀隧洞", "睿治之径", 132], ["饰品提取", "鎏金追忆", 80]]
    )
    _write(config_path, trusted)
    session = prepare_m7a_config(config_path, backup_dir)
    _write(config_path, _config([]))

    with pytest.raises(M7AConfigProtectionError, match="不可能的跳变"):
        session.verify_live()

    assert config_path.read_bytes() == trusted
    assert list((backup_dir / "versions").glob("*_restored_*.yaml"))


def test_normal_prefix_completion_and_count_decrease_are_accepted(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    backup_dir = tmp_path / "backups"
    _write(
        config_path,
        _config(
            [
                ["拟造花萼（赤）", "「世界尽头」酒馆", 9],
                ["侵蚀隧洞", "睿治之径", 135],
                ["饰品提取", "鎏金追忆", 80],
            ]
        ),
    )
    session = prepare_m7a_config(config_path, backup_dir)
    updated = _config(
        [["侵蚀隧洞", "睿治之径", 132], ["饰品提取", "鎏金追忆", 80]]
    )
    _write(config_path, updated)

    session.verify_live()
    finalized = session.finalize()

    assert finalized.content == updated
    assert (backup_dir / LAST_KNOWN_GOOD_NAME).read_bytes() == updated


def test_impossible_large_prefix_loss_is_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    backup_dir = tmp_path / "backups"
    trusted = _config(
        [["侵蚀隧洞", "睿治之径", 132], ["饰品提取", "鎏金追忆", 80]]
    )
    _write(config_path, trusted)
    session = prepare_m7a_config(config_path, backup_dir)
    _write(config_path, _config([["饰品提取", "鎏金追忆", 80]]))

    with pytest.raises(M7AConfigProtectionError, match="不可能的跳变"):
        session.verify_live()

    assert config_path.read_bytes() == trusted


def test_invalid_config_without_recovery_point_blocks_launch(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    backup_dir = tmp_path / "backups"
    _write(config_path, b"power_plan: [")

    with pytest.raises(M7AConfigProtectionError, match="无法解析"):
        prepare_m7a_config(config_path, backup_dir)


def test_prepare_keeps_recovery_baseline_on_unsafe_prelaunch_transition(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    backup_dir = tmp_path / "backups"
    trusted = _config(
        [["侵蚀隧洞", "睿治之径", 132], ["饰品提取", "鎏金追忆", 80]]
    )
    backup_dir.mkdir()
    _write(backup_dir / LAST_KNOWN_GOOD_NAME, trusted)
    unsafe = _config([["饰品提取", "鎏金追忆", 80]])
    _write(config_path, unsafe)

    session = prepare_m7a_config(config_path, backup_dir)

    assert (backup_dir / LAST_KNOWN_GOOD_NAME).read_bytes() == trusted
    assert session.before.content == unsafe
