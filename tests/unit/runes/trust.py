from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from mvge.agent.function_spell import discover_spells_from_dir
from mvge.core.layers import RuneScope
from mvge.runes.loader import load_runes_from_paths
from mvge.runes.trust import (
    WorkspaceTrustStatus,
    WorkspaceTrustStore,
    canonicalize_workspace_path,
    compute_project_agents_digest,
)
from mvge.runes.types import DiagnosticKind


def test_canonicalize_workspace_path_resolves_symlinks(tmp_path: Path) -> None:
    real_dir = tmp_path / "real_project"
    real_dir.mkdir()
    symlink_dir = tmp_path / "link_project"
    symlink_dir.symlink_to(real_dir)

    canonical = canonicalize_workspace_path(symlink_dir)
    assert canonical == real_dir.resolve()


def test_canonicalize_workspace_path_darwin_casing(tmp_path: Path) -> None:
    target_dir = tmp_path / "my_project"
    target_dir.mkdir()

    mock_fcntl = MagicMock()
    c_buf = str(target_dir.resolve()).encode("utf-8") + b"\0"

    def _fake_fcntl(fd: int, op: int, arg: bytearray) -> int:
        arg[: len(c_buf)] = c_buf
        return 0

    mock_fcntl.fcntl.side_effect = _fake_fcntl
    mock_fcntl.F_GETPATH = 50

    with (
        patch("sys.platform", "darwin"),
        patch.dict("sys.modules", {"fcntl": mock_fcntl}),
        patch("os.open", return_value=99),
        patch("os.close"),
    ):
        result = canonicalize_workspace_path(target_dir)
        assert result == target_dir.resolve()


def test_canonicalize_workspace_path_windows_normcase(tmp_path: Path) -> None:
    target_dir = tmp_path / "MyProject"
    target_dir.mkdir()

    with patch("sys.platform", "win32"):
        result = canonicalize_workspace_path(target_dir)
        assert str(result) == os.path.normcase(str(target_dir.resolve()))


def test_compute_project_agents_digest_empty(tmp_path: Path) -> None:
    digest = compute_project_agents_digest(tmp_path)
    assert digest == "sha256:empty"


def test_compute_project_agents_digest_with_files(tmp_path: Path) -> None:
    ext_dir = tmp_path / ".agents" / "extensions" / "my_rune"
    ext_dir.mkdir(parents=True)
    rune_file = ext_dir / "rune.py"
    rune_file.write_text("print('hello')", encoding="utf-8")

    spells_dir = tmp_path / ".agents" / "spells"
    spells_dir.mkdir(parents=True)
    spell_file = spells_dir / "test.py"
    spell_file.write_text("def spell(): pass", encoding="utf-8")

    digest1 = compute_project_agents_digest(tmp_path)
    assert digest1.startswith("sha256:")
    assert digest1 != "sha256:empty"

    # Same content gives identical digest
    assert compute_project_agents_digest(tmp_path) == digest1

    # Modification alters digest
    rune_file.write_text("print('modified')", encoding="utf-8")
    digest2 = compute_project_agents_digest(tmp_path)
    assert digest2 != digest1

    # Additional file alters digest
    (spells_dir / "other.py").write_text("# other", encoding="utf-8")
    digest3 = compute_project_agents_digest(tmp_path)
    assert digest3 != digest2


def test_trust_store_persistence_and_pending_journal(tmp_path: Path) -> None:
    trust_file = tmp_path / "approval" / "trust.json"
    pending_file = tmp_path / "approval" / "trust.json.pending"
    store = WorkspaceTrustStore(trust_file=trust_file)

    proj_dir = tmp_path / "workspace_a"
    proj_dir.mkdir()

    # Record trust
    rec = store.set_trust(proj_dir, WorkspaceTrustStatus.TRUSTED)
    assert rec.status == WorkspaceTrustStatus.TRUSTED
    assert trust_file.exists()
    assert not pending_file.exists()

    # Re-open store from disk
    store2 = WorkspaceTrustStore(trust_file=trust_file)
    assert store2.check_trust(proj_dir) == WorkspaceTrustStatus.TRUSTED

    # Crash scenario: pending journal exists
    pending_data = {
        "version": 1,
        "entries": {
            str(canonicalize_workspace_path(proj_dir)): {
                "canonical_path": str(canonicalize_workspace_path(proj_dir)),
                "digest": compute_project_agents_digest(proj_dir),
                "status": "declined",
                "updated_at": "2026-10-19T00:00:00Z",
                "trust_parent": False,
            }
        },
    }
    pending_file.write_text(json.dumps(pending_data), encoding="utf-8")

    # On new store init, pending file is replayed and replaces trust_file
    store3 = WorkspaceTrustStore(trust_file=trust_file)
    assert store3.check_trust(proj_dir) == WorkspaceTrustStatus.DECLINED
    assert not pending_file.exists()


def test_trust_store_digest_mismatch_forces_unspecified(tmp_path: Path) -> None:
    trust_file = tmp_path / "approval" / "trust.json"
    store = WorkspaceTrustStore(trust_file=trust_file)

    proj_dir = tmp_path / "workspace_b"
    proj_dir.mkdir()
    ext_dir = proj_dir / ".agents" / "extensions" / "rune_x"
    ext_dir.mkdir(parents=True)
    (ext_dir / "rune.py").write_text("x = 1", encoding="utf-8")

    store.set_trust(proj_dir, WorkspaceTrustStatus.TRUSTED)
    assert store.check_trust(proj_dir) == WorkspaceTrustStatus.TRUSTED

    # Modifying project file alters digest, returning UNSPECIFIED
    (ext_dir / "rune.py").write_text("x = 2", encoding="utf-8")
    assert store.check_trust(proj_dir) == WorkspaceTrustStatus.UNSPECIFIED


def test_trust_store_nearest_ancestor_inheritance(tmp_path: Path) -> None:
    trust_file = tmp_path / "approval" / "trust.json"
    store = WorkspaceTrustStore(trust_file=trust_file)

    parent_dir = tmp_path / "org_root"
    child_dir = parent_dir / "child_repo"
    child_dir.mkdir(parents=True)

    # Without trust_parent, child is unspecified
    store.set_trust(parent_dir, WorkspaceTrustStatus.TRUSTED, trust_parent=False)
    assert store.check_trust(child_dir) == WorkspaceTrustStatus.UNSPECIFIED

    # With trust_parent, child inherits TRUSTED
    store.set_trust(parent_dir, WorkspaceTrustStatus.TRUSTED, trust_parent=True)
    assert store.check_trust(child_dir) == WorkspaceTrustStatus.TRUSTED


def test_loader_skips_untrusted_project_runes(tmp_path: Path) -> None:
    trust_file = tmp_path / "approval" / "trust.json"
    store = WorkspaceTrustStore(trust_file=trust_file)

    user_dir = tmp_path / "user_exts"
    user_dir.mkdir()
    user_rune = user_dir / "user_rune"
    user_rune.mkdir()
    (user_rune / "manifest.json").write_text(
        json.dumps({"name": "user_rune", "version": "1.0.0", "description": "u"}),
        encoding="utf-8",
    )
    (user_rune / "rune.py").write_text("def rune_factory(api): pass", encoding="utf-8")

    proj_dir = tmp_path / "proj"
    proj_ext = proj_dir / ".agents" / "extensions" / "proj_rune"
    proj_ext.mkdir(parents=True)
    (proj_ext / "manifest.json").write_text(
        json.dumps({"name": "proj_rune", "version": "1.0.0", "description": "p"}),
        encoding="utf-8",
    )
    (proj_ext / "rune.py").write_text("def rune_factory(api): pass", encoding="utf-8")

    # Untrusted project
    loads, diags = load_runes_from_paths(
        [
            (user_dir, RuneScope.USER),
            (proj_dir / ".agents" / "extensions", RuneScope.PROJECT),
        ],
        trust_store=store,
    )

    loaded_names = [load.manifest.name for load in loads]
    assert "user_rune" in loaded_names
    assert "proj_rune" not in loaded_names

    warnings = [d for d in diags if d.kind == DiagnosticKind.LOAD_WARNING]
    assert len(warnings) >= 1
    assert any("untrusted" in w.message.lower() for w in warnings)

    # Now trust the project
    store.set_trust(proj_dir, WorkspaceTrustStatus.TRUSTED)
    loads2, diags2 = load_runes_from_paths(
        [
            (user_dir, RuneScope.USER),
            (proj_dir / ".agents" / "extensions", RuneScope.PROJECT),
        ],
        trust_store=store,
    )
    loaded_names2 = [load.manifest.name for load in loads2]
    assert "user_rune" in loaded_names2
    assert "proj_rune" in loaded_names2


def test_compute_project_agents_digest_ignores_bytecode_caches(
    tmp_path: Path,
) -> None:
    spells_dir = tmp_path / ".agents" / "spells"
    spells_dir.mkdir(parents=True)
    spell_file = spells_dir / "hello.py"
    spell_file.write_text("def hello(): pass", encoding="utf-8")
    ext_dir = tmp_path / ".agents" / "extensions" / "my_rune"
    ext_dir.mkdir(parents=True)
    (ext_dir / "rune.py").write_text("x = 1", encoding="utf-8")

    digest1 = compute_project_agents_digest(tmp_path)

    # Bytecode caches are derived artifacts of importing the sources above,
    # not source changes: they must not move the digest.
    cache_dir = spells_dir / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "hello.cpython-313.pyc").write_bytes(b"\x00\x01fake-pyc")
    (spells_dir / "stray.pyc").write_bytes(b"\x00\x01fake-pyc")
    ext_cache = ext_dir / "__pycache__"
    ext_cache.mkdir()
    (ext_cache / "rune.cpython-313.pyc").write_bytes(b"\x00\x01fake-pyc")
    assert compute_project_agents_digest(tmp_path) == digest1

    # A genuine source change must still move the digest.
    spell_file.write_text("def hello(): return 1", encoding="utf-8")
    assert compute_project_agents_digest(tmp_path) != digest1


def test_trust_store_survives_real_spell_import(tmp_path: Path) -> None:
    trust_file = tmp_path / "approval" / "trust.json"
    store = WorkspaceTrustStore(trust_file=trust_file)

    proj_dir = tmp_path / "proj"
    spells_dir = proj_dir / ".agents" / "spells"
    spells_dir.mkdir(parents=True)
    (spells_dir / "hello.py").write_text(
        "def hello():\n    return 'hi'\n", encoding="utf-8"
    )

    store.set_trust(proj_dir, WorkspaceTrustStatus.TRUSTED)
    assert store.check_trust(proj_dir) == WorkspaceTrustStatus.TRUSTED

    # Loading the spells is the step that writes __pycache__ next to them;
    # the next trust check must not treat that as a source change.
    spells = discover_spells_from_dir(spells_dir)
    assert [s.name for s in spells] == ["hello"]
    assert store.check_trust(proj_dir) == WorkspaceTrustStatus.TRUSTED
