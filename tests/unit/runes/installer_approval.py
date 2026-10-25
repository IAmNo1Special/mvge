from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import patch

from mvge.runes.installer import (
    install_rune,
    read_or_create_install_id,
    uninstall_rune,
)


def _create_rune_dir(path: Path, name: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    manifest_data = {
        "name": name,
        "version": "1.0.0",
        "description": f"Test rune {name}",
        "entry_point": "main.py",
    }
    (path / "manifest.json").write_text(json.dumps(manifest_data), encoding="utf-8")
    (path / "main.py").write_text("# entry point\n", encoding="utf-8")
    return path


def test_read_or_create_install_id_generates_uuid4(tmp_path: Path) -> None:
    first = read_or_create_install_id(tmp_path)
    assert uuid.UUID(first).version == 4
    assert (tmp_path / ".install-id").is_file()
    # Stable across reads.
    assert read_or_create_install_id(tmp_path) == first


def test_read_or_create_install_id_preserves_existing(tmp_path: Path) -> None:
    (tmp_path / ".install-id").write_text("existing-id\n", encoding="utf-8")
    assert read_or_create_install_id(tmp_path) == "existing-id"


def test_install_rune_writes_install_id(tmp_path: Path) -> None:
    source_dir = _create_rune_dir(tmp_path / "src_rune", "id_rune")
    dest = install_rune(str(source_dir), target_dir=tmp_path / "extensions")
    first = (dest / ".install-id").read_text(encoding="utf-8").strip()
    assert uuid.UUID(first).version == 4


def test_install_rune_update_preserves_install_id(tmp_path: Path) -> None:
    source_dir = _create_rune_dir(tmp_path / "src_rune", "id_rune")
    target_dir = tmp_path / "extensions"
    first_dest = install_rune(str(source_dir), target_dir=target_dir)
    first = (first_dest / ".install-id").read_text(encoding="utf-8").strip()

    # Simulate an update: reinstall from a fresh source tree.
    source_dir2 = _create_rune_dir(tmp_path / "src_rune2", "id_rune")
    second_dest = install_rune(str(source_dir2), target_dir=target_dir)
    second = (second_dest / ".install-id").read_text(encoding="utf-8").strip()
    assert second == first


def test_uninstall_approval_rune_purges_only_policy_file(tmp_path: Path) -> None:
    approval_dir = tmp_path / "approval-data"
    approval_dir.mkdir()
    policy = approval_dir / "policy.toml"
    audit = approval_dir / "audit.jsonl"
    policy.write_text("grants = []\n", encoding="utf-8")
    audit.write_text('{"decision": "allow"}\n', encoding="utf-8")

    target_dir = tmp_path / "extensions"
    _create_rune_dir(target_dir / "approval-rune", "approval-rune")

    with patch("mvge.runes.installer._approval_dir", return_value=approval_dir):
        assert uninstall_rune("approval-rune", target_dir=target_dir) is True

    assert not policy.exists()
    assert audit.is_file()
    assert not (target_dir / "approval-rune").exists()


def test_uninstall_other_rune_keeps_policy_file(tmp_path: Path) -> None:
    approval_dir = tmp_path / "approval-data"
    approval_dir.mkdir()
    policy = approval_dir / "policy.toml"
    policy.write_text("grants = []\n", encoding="utf-8")

    target_dir = tmp_path / "extensions"
    _create_rune_dir(target_dir / "other", "other")

    with patch("mvge.runes.installer._approval_dir", return_value=approval_dir):
        assert uninstall_rune("other", target_dir=target_dir) is True

    assert policy.is_file()


def test_uninstall_missing_rune_leaves_policy_alone(tmp_path: Path) -> None:
    approval_dir = tmp_path / "approval-data"
    approval_dir.mkdir()
    policy = approval_dir / "policy.toml"
    policy.write_text("grants = []\n", encoding="utf-8")

    with patch("mvge.runes.installer._approval_dir", return_value=approval_dir):
        assert uninstall_rune("approval", target_dir=tmp_path / "extensions") is False

    assert policy.is_file()


def test_uninstall_real_approval_rune_name_purges_policy(tmp_path: Path) -> None:
    """Uninstalling the real approval-rune name purges its policy file."""
    approval_dir = tmp_path / "approval-data"
    approval_dir.mkdir()
    policy = approval_dir / "policy.toml"
    audit = approval_dir / "audit.jsonl"
    policy.write_text("grants = []\n", encoding="utf-8")
    audit.write_text('{"decision": "allow"}\n', encoding="utf-8")

    target_dir = tmp_path / "extensions"
    _create_rune_dir(target_dir / "approval-rune", "approval-rune")

    with patch("mvge.runes.installer._approval_dir", return_value=approval_dir):
        assert uninstall_rune("approval-rune", target_dir=target_dir) is True

    assert not policy.exists()
    assert audit.is_file()
    assert not (target_dir / "approval-rune").exists()
