"""Phase 3: trust tiers, hash pinning, provenance, and revocation.

A pinned rune is unmodified, not benign: the load check proves the tree
on disk is the tree the installer (or an adopting operator) recorded,
nothing more. A mismatch, a missing sidecar, or a revocation refuses the
load with a diagnostic that names the rune and the remedy, and never
silently executes changed code.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from typer.testing import CliRunner

from mvge.cli.commands.rune import rune_app
from mvge.runes.installer import install_rune
from mvge.runes.loader import load_factory_from_manifest
from mvge.runes.manifest import load_manifest
from mvge.runes.pinning import (
    PIN_FILENAME,
    RunePin,
    adopt_rune_pin,
    compute_rune_tree_hash,
    is_dev_tree,
    read_pin,
    verify_rune_pin,
    write_pin,
)
from mvge.runes.types import DiagnosticKind


def _write_rune(
    parent: Path, name: str, body: str, entry_point: str = "rune.py"
) -> Path:
    rune_dir = parent / name
    rune_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": name,
        "version": "0.1.0",
        "description": "test rune",
        "entry_point": entry_point,
    }
    (rune_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (rune_dir / entry_point).write_text(body, encoding="utf-8")
    return rune_dir


def _factory_body() -> str:
    return "def rune_factory(api):\n    return None\n"


def _pin_dir(rune_dir: Path, *, source_kind: str = "local") -> RunePin:
    pin = RunePin(
        source_kind=source_kind,
        tree_hash=compute_rune_tree_hash(rune_dir),
    )
    write_pin(rune_dir, pin)
    return pin


def _load(rune_dir: Path) -> tuple[object | None, list[object]]:
    manifest = load_manifest(rune_dir)
    assert manifest is not None
    diagnostics: list = []
    factory = load_factory_from_manifest(manifest, rune_dir, diagnostics=diagnostics)
    return factory, diagnostics


def _messages(diagnostics: list) -> str:
    return "\n".join(str(d.message) for d in diagnostics)


class TestTreeHash:
    def test_hash_changes_on_source_edit_only(self, tmp_path: Path) -> None:
        rune_dir = _write_rune(tmp_path, "hashy", _factory_body())
        before = compute_rune_tree_hash(rune_dir)
        assert before.startswith("sha256:")
        # Generated state must not move the hash: caches, venvs, the
        # install id, and the sidecar itself are all excluded.
        (rune_dir / "__pycache__").mkdir()
        (rune_dir / "__pycache__" / "rune.cpython-313.pyc").write_bytes(b"junk")
        (rune_dir / ".venv" / "lib" / "site-packages").mkdir(parents=True)
        (rune_dir / ".venv" / "lib" / "site-packages" / "pkg.py").write_text("x")
        (rune_dir / ".install-id").write_text("abc\n", encoding="utf-8")
        (rune_dir / PIN_FILENAME).write_text("{}\n", encoding="utf-8")
        assert compute_rune_tree_hash(rune_dir) == before
        # A genuine source edit does move it.
        (rune_dir / "rune.py").write_text(
            _factory_body() + "# edit\n", encoding="utf-8"
        )
        assert compute_rune_tree_hash(rune_dir) != before

    def test_hash_is_deterministic(self, tmp_path: Path) -> None:
        rune_dir = _write_rune(tmp_path, "hashy", _factory_body())
        (rune_dir / "extra.py").write_text("x = 1\n", encoding="utf-8")
        assert compute_rune_tree_hash(rune_dir) == compute_rune_tree_hash(rune_dir)


class TestTamperAfterInstall:
    def test_edited_rune_refused_with_reinstall_remedy(self, tmp_path: Path) -> None:
        rune_dir = _write_rune(tmp_path, "tampered", _factory_body())
        _pin_dir(rune_dir, source_kind="catalog")
        factory, _ = _load(rune_dir)
        assert factory is not None

        (rune_dir / "rune.py").write_text(
            _factory_body() + "# attacker\n", encoding="utf-8"
        )
        factory, diagnostics = _load(rune_dir)
        assert factory is None
        assert any(d.kind == DiagnosticKind.LOAD_FAILURE for d in diagnostics)
        message = _messages(diagnostics)
        assert "tampered" in message
        assert "reinstall" in message.lower()

    def test_added_file_refused(self, tmp_path: Path) -> None:
        rune_dir = _write_rune(tmp_path, "added", _factory_body())
        _pin_dir(rune_dir, source_kind="catalog")
        (rune_dir / "evil.py").write_text("x = 1\n", encoding="utf-8")
        factory, diagnostics = _load(rune_dir)
        assert factory is None
        assert any(d.kind == DiagnosticKind.LOAD_FAILURE for d in diagnostics)


class TestLegacyMigration:
    def test_sidecarless_rune_refused_with_adopt_remedy(self, tmp_path: Path) -> None:
        rune_dir = _write_rune(tmp_path, "legacy", _factory_body())
        assert read_pin(rune_dir) is None
        factory, diagnostics = _load(rune_dir)
        assert factory is None
        assert any(d.kind == DiagnosticKind.LOAD_FAILURE for d in diagnostics)
        message = _messages(diagnostics)
        assert "legacy" in message
        assert "mvge rune trust --adopt legacy" in message

    def test_after_adopt_loads_after_further_edit_refuses_again(
        self, tmp_path: Path
    ) -> None:
        rune_dir = _write_rune(tmp_path, "legacy", _factory_body())
        factory, _ = _load(rune_dir)
        assert factory is None

        pin = adopt_rune_pin(rune_dir, "legacy")
        assert pin.source_kind == "manual"
        assert pin.tree_hash == compute_rune_tree_hash(rune_dir)
        factory, _ = _load(rune_dir)
        assert factory is not None

        (rune_dir / "rune.py").write_text(
            _factory_body() + "# edit\n", encoding="utf-8"
        )
        factory, diagnostics = _load(rune_dir)
        assert factory is None
        assert any(d.kind == DiagnosticKind.LOAD_FAILURE for d in diagnostics)


class TestRevocation:
    def test_blocklisted_rune_refused_despite_matching_hash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        global_dir = tmp_path / "global"
        monkeypatch.setenv("MVGE_GLOBAL_DIR", str(global_dir))
        blocklist = global_dir / "approval" / "rune-blocklist.json"
        blocklist.parent.mkdir(parents=True)
        blocklist.write_text(
            json.dumps({"version": 1, "revoked": ["blocked"]}), encoding="utf-8"
        )
        rune_dir = _write_rune(tmp_path, "blocked", _factory_body())
        _pin_dir(rune_dir, source_kind="catalog")
        assert verify_rune_pin(rune_dir, "blocked").status == "revoked"
        factory, diagnostics = _load(rune_dir)
        assert factory is None
        message = _messages(diagnostics)
        assert "blocked" in message
        assert "revok" in message.lower()

    def test_installer_refuses_revoked_catalog_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        global_dir = tmp_path / "global"
        monkeypatch.setenv("MVGE_GLOBAL_DIR", str(global_dir))
        entry = {
            "name": "bad-rune",
            "git": "https://github.com/org/bad.git",
            "path": "runes/bad-rune",
            "revoked": True,
        }
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.json.return_value = {"runes": {"bad-rune": entry}}
        mock_resp.raise_for_status.return_value = None
        target_dir = tmp_path / "extensions"
        with (
            patch("httpx.get", return_value=mock_resp),
            pytest.raises(ValueError, match="revoked"),
        ):
            install_rune("bad-rune", target_dir=target_dir)
        assert not (target_dir / "bad-rune").exists()


class TestProvenanceClasses:
    @pytest.mark.parametrize("source_kind", ["git", "catalog", "local", "manual"])
    def test_drift_after_manual_edit_refused(
        self, tmp_path: Path, source_kind: str
    ) -> None:
        rune_dir = _write_rune(tmp_path, f"drift-{source_kind}", _factory_body())
        _pin_dir(rune_dir, source_kind=source_kind)
        factory, _ = _load(rune_dir)
        assert factory is not None
        (rune_dir / "rune.py").write_text(
            _factory_body() + "# edit\n", encoding="utf-8"
        )
        factory, diagnostics = _load(rune_dir)
        assert factory is None
        assert any(d.kind == DiagnosticKind.LOAD_FAILURE for d in diagnostics)


class TestInstallerRecordsPin:
    def test_local_install_records_local_pin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MVGE_GLOBAL_DIR", str(tmp_path / "global"))
        source_dir = _write_rune(tmp_path / "src", "localrune", _factory_body())
        dest = install_rune(str(source_dir), target_dir=tmp_path / "extensions")
        pin = read_pin(dest)
        assert pin is not None
        assert pin.source_kind == "local"
        assert pin.tree_hash == compute_rune_tree_hash(dest)
        assert pin.local_path is not None

    def test_git_install_records_url_and_head(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MVGE_GLOBAL_DIR", str(tmp_path / "global"))
        git_url = "https://github.com/org/sample-rune.git"
        head = "a" * 40

        def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            result = MagicMock(returncode=0)
            if cmd[:2] == ["git", "clone"]:
                # A repo-root rune: the manifest sits at the clone root.
                dest_dir = Path(cmd[-1])
                dest_dir.mkdir(parents=True, exist_ok=True)
                (dest_dir / "manifest.json").write_text(
                    json.dumps(
                        {
                            "name": "sample-rune",
                            "version": "0.1.0",
                            "description": "test rune",
                            "entry_point": "rune.py",
                        }
                    ),
                    encoding="utf-8",
                )
                (dest_dir / "rune.py").write_text(_factory_body(), encoding="utf-8")
            elif cmd[:2] == ["git", "-C"]:
                result.stdout = head + "\n"
            return result

        with patch("subprocess.run", side_effect=fake_run):
            dest = install_rune(git_url, target_dir=tmp_path / "extensions")
        pin = read_pin(dest)
        assert pin is not None
        assert pin.source_kind == "git"
        assert pin.git_url == git_url
        assert pin.commit == head
        assert pin.tree_hash == compute_rune_tree_hash(dest)

    def test_catalog_install_records_commit_and_tree_hash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MVGE_GLOBAL_DIR", str(tmp_path / "global"))
        staged_hash_holder: dict[str, str] = {}

        def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            result = MagicMock(returncode=0)
            if cmd[:2] == ["git", "clone"]:
                dest_dir = Path(cmd[-1])
                rune_dir = _write_rune(dest_dir / "runes", "catrune", _factory_body())
                staged_hash_holder["hash"] = compute_rune_tree_hash(rune_dir)
            elif cmd[:2] == ["git", "-C"]:
                result.stdout = "b" * 40 + "\n"
            return result

        entry = {
            "name": "catrune",
            "git": "https://github.com/org/marketplace.git",
            "path": "runes/catrune",
            "commit": "b" * 40,
        }
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.json.return_value = {"runes": {"catrune": entry}}
        mock_resp.raise_for_status.return_value = None
        with (
            patch("httpx.get", return_value=mock_resp),
            patch("subprocess.run", side_effect=fake_run),
        ):
            dest = install_rune("catrune", target_dir=tmp_path / "extensions")
        pin = read_pin(dest)
        assert pin is not None
        assert pin.source_kind == "catalog"
        assert pin.commit == "b" * 40
        assert pin.tree_hash == staged_hash_holder["hash"]

    def test_catalog_install_refuses_tree_hash_mismatch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MVGE_GLOBAL_DIR", str(tmp_path / "global"))

        def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            result = MagicMock(returncode=0)
            if cmd[:2] == ["git", "clone"]:
                _write_rune(Path(cmd[-1]) / "runes", "catrune", _factory_body())
            elif cmd[:2] == ["git", "-C"]:
                result.stdout = "b" * 40 + "\n"
            return result

        entry = {
            "name": "catrune",
            "git": "https://github.com/org/marketplace.git",
            "path": "runes/catrune",
            "commit": "b" * 40,
            "tree_hash": "sha256:" + "0" * 64,
        }
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.json.return_value = {"runes": {"catrune": entry}}
        mock_resp.raise_for_status.return_value = None
        target_dir = tmp_path / "extensions"
        with (
            patch("httpx.get", return_value=mock_resp),
            patch("subprocess.run", side_effect=fake_run),
            pytest.raises(ValueError, match="tree_hash"),
        ):
            install_rune("catrune", target_dir=target_dir)
        assert not (target_dir / "catrune").exists()


def _git(cmd: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *cmd], cwd=str(cwd), check=True, capture_output=True, text=True
    ).stdout.strip()


class TestBranchDrift:
    def test_catalog_pin_installs_pinned_commit_after_origin_moves(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MVGE_GLOBAL_DIR", str(tmp_path / "global"))
        origin = tmp_path / "origin"
        origin.mkdir()
        _git(["init", "-q", "-b", "main"], origin)
        _git(["config", "user.email", "test@example.com"], origin)
        _git(["config", "user.name", "Test"], origin)
        rune_sub = origin / "runes" / "drift-rune"
        _write_rune(origin / "runes", "drift-rune", _factory_body())
        _git(["add", "."], origin)
        _git(["commit", "-qm", "v1"], origin)
        pinned_commit = _git(["rev-parse", "HEAD"], origin)
        pinned_hash = compute_rune_tree_hash(rune_sub)
        # Origin moves on: the catalog still pins commit X.
        (rune_sub / "rune.py").write_text(_factory_body() + "# v2\n", encoding="utf-8")
        _git(["add", "."], origin)
        _git(["commit", "-qm", "v2"], origin)
        assert _git(["rev-parse", "HEAD"], origin) != pinned_commit

        entry = {
            "name": "drift-rune",
            "git": str(origin),
            "path": "runes/drift-rune",
            "commit": pinned_commit,
            "tree_hash": pinned_hash,
        }
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.json.return_value = {"runes": {"drift-rune": entry}}
        mock_resp.raise_for_status.return_value = None
        with patch("httpx.get", return_value=mock_resp):
            dest = install_rune("drift-rune", target_dir=tmp_path / "extensions")
        assert (dest / "rune.py").read_text(encoding="utf-8") == _factory_body()
        pin = read_pin(dest)
        assert pin is not None
        assert pin.commit == pinned_commit
        assert pin.tree_hash == pinned_hash


class TestDevTreeExemption:
    """Pin verification applies to installed trees, not dev checkouts.

    A rune nested in a git checkout loads without a sidecar. A planted
    ``.git`` at or inside the rune dir itself is not a checkout root
    above the rune and must not exempt it. Revocation still wins even
    in a dev tree, and a tampered installed tree is still refused.
    """

    def _write_dev_rune(self, tmp_path: Path) -> tuple[Path, Path]:
        repo = tmp_path / "repo"
        rune_dir = _write_rune(repo / "runes", "devrune", _factory_body())
        (repo / ".git").mkdir()
        return repo, rune_dir

    def test_dev_tree_load_without_sidecar_passes(self, tmp_path: Path) -> None:
        _, rune_dir = self._write_dev_rune(tmp_path)
        assert read_pin(rune_dir) is None
        assert is_dev_tree(rune_dir) is True
        assert verify_rune_pin(rune_dir, "devrune").status == "ok"
        factory, diagnostics = _load(rune_dir)
        assert factory is not None, _messages(diagnostics)

    def test_dev_tree_with_git_file_ancestor_passes(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        rune_dir = _write_rune(repo / "runes", "devrune", _factory_body())
        (repo / ".git").write_text("gitdir: /elsewhere/worktree\n", encoding="utf-8")
        assert is_dev_tree(rune_dir) is True
        factory, _ = _load(rune_dir)
        assert factory is not None

    def test_installed_tree_without_sidecar_still_refused(self, tmp_path: Path) -> None:
        rune_dir = _write_rune(tmp_path, "installed", _factory_body())
        assert is_dev_tree(rune_dir) is False
        assert verify_rune_pin(rune_dir, "installed").status == "missing"
        factory, diagnostics = _load(rune_dir)
        assert factory is None
        assert "mvge rune trust --adopt installed" in _messages(diagnostics)

    def test_installed_tree_with_matching_sidecar_passes(self, tmp_path: Path) -> None:
        rune_dir = _write_rune(tmp_path, "installed", _factory_body())
        _pin_dir(rune_dir, source_kind="catalog")
        assert verify_rune_pin(rune_dir, "installed").status == "ok"
        factory, _ = _load(rune_dir)
        assert factory is not None

    def test_planted_git_dir_inside_rune_does_not_exempt(self, tmp_path: Path) -> None:
        rune_dir = _write_rune(tmp_path, "planted", _factory_body())
        (rune_dir / ".git").mkdir()
        assert is_dev_tree(rune_dir) is False
        assert verify_rune_pin(rune_dir, "planted").status == "missing"
        factory, _ = _load(rune_dir)
        assert factory is None

    def test_planted_git_file_inside_rune_does_not_exempt(self, tmp_path: Path) -> None:
        rune_dir = _write_rune(tmp_path, "planted", _factory_body())
        (rune_dir / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")
        assert is_dev_tree(rune_dir) is False
        factory, _ = _load(rune_dir)
        assert factory is None

    def test_tamper_after_install_still_refused(self, tmp_path: Path) -> None:
        rune_dir = _write_rune(tmp_path, "tampered", _factory_body())
        _pin_dir(rune_dir, source_kind="catalog")
        (rune_dir / "rune.py").write_text(
            _factory_body() + "# attacker\n", encoding="utf-8"
        )
        assert verify_rune_pin(rune_dir, "tampered").status == "mismatch"
        factory, _ = _load(rune_dir)
        assert factory is None

    def test_revoked_dev_tree_still_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        global_dir = tmp_path / "global"
        monkeypatch.setenv("MVGE_GLOBAL_DIR", str(global_dir))
        blocklist = global_dir / "approval" / "rune-blocklist.json"
        blocklist.parent.mkdir(parents=True)
        blocklist.write_text(
            json.dumps({"version": 1, "revoked": ["devrune"]}), encoding="utf-8"
        )
        _, rune_dir = self._write_dev_rune(tmp_path)
        assert verify_rune_pin(rune_dir, "devrune").status == "revoked"
        factory, _ = _load(rune_dir)
        assert factory is None


class TestTrustCommand:
    def test_trust_adopt_cli(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        global_dir = tmp_path / "global"
        monkeypatch.setenv("MVGE_GLOBAL_DIR", str(global_dir))
        ext_dir = global_dir / "extensions"
        _write_rune(ext_dir, "legacy", _factory_body())
        runner = CliRunner()
        result = runner.invoke(rune_app, ["trust", "--adopt", "legacy"])
        assert result.exit_code == 0, result.output
        assert "legacy" in result.output
        pin = read_pin(ext_dir / "legacy")
        assert pin is not None
        assert pin.source_kind == "manual"
        factory, _ = _load(ext_dir / "legacy")
        assert factory is not None

    def test_trust_adopt_unknown_rune_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MVGE_GLOBAL_DIR", str(tmp_path / "global"))
        result = CliRunner().invoke(rune_app, ["trust", "--adopt", "nope"])
        assert result.exit_code != 0

    def test_trust_status_table(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        global_dir = tmp_path / "global"
        monkeypatch.setenv("MVGE_GLOBAL_DIR", str(global_dir))
        ext_dir = global_dir / "extensions"
        _write_rune(ext_dir, "legacy", _factory_body())
        runner = CliRunner()
        result = runner.invoke(rune_app, ["trust"])
        assert result.exit_code == 0, result.output
        assert "legacy" in result.output
        assert "missing" in result.output
        assert runner.invoke(rune_app, ["trust", "--adopt", "legacy"]).exit_code == 0
        result = runner.invoke(rune_app, ["trust"])
        assert "legacy" in result.output
        assert "ok" in result.output
