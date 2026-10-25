"""Integration tests for the ``mvgeos rune uninstall`` command.

Uninstall removes a directory tree, so the cases that matter are that it
targets the same layer the installer wrote to, that it refuses to walk
out of that layer, and that it reports honestly when there is nothing to
remove.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mvge.cli.main import app


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


def _write_rune(root: Path, name: str) -> Path:
    source = root / name
    source.mkdir(parents=True)
    (source / "manifest.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "0.1.0",
                "description": "Disposable rune",
                "entry_point": "rune.py",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (source / "rune.py").write_text("# entry point\n", encoding="utf-8")
    return source


def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point HOME and the global layer at temp paths, return the global layer."""
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    global_dir = tmp_path / "global_agents"
    global_dir.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("MVGEOS_GLOBAL_DIR", str(global_dir))
    return global_dir


def test_uninstall_removes_the_installed_rune(
    cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rune installed under the override can be uninstalled from it."""
    global_dir = _isolate_home(tmp_path, monkeypatch)
    source = _write_rune(tmp_path / "src", "disposable-rune")

    installed = cli_runner.invoke(app, ["rune", "install", str(source)])
    assert installed.exit_code == 0, installed.output
    assert (global_dir / "extensions" / "disposable-rune").is_dir()

    removed = cli_runner.invoke(app, ["rune", "uninstall", "disposable-rune"])

    assert removed.exit_code == 0, removed.output
    assert not (global_dir / "extensions" / "disposable-rune").exists()


def test_uninstall_reports_when_the_rune_is_absent(
    cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_home(tmp_path, monkeypatch)

    result = cli_runner.invoke(app, ["rune", "uninstall", "never-installed"])

    assert result.exit_code == 1
    assert "not installed" in result.output


def test_uninstall_leaves_sibling_runes_intact(
    cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removing one rune must not disturb another in the same layer."""
    global_dir = _isolate_home(tmp_path, monkeypatch)
    keep = _write_rune(tmp_path / "src", "keeper-rune")
    drop = _write_rune(tmp_path / "src", "droppable-rune")
    assert cli_runner.invoke(app, ["rune", "install", str(keep)]).exit_code == 0
    assert cli_runner.invoke(app, ["rune", "install", str(drop)]).exit_code == 0

    result = cli_runner.invoke(app, ["rune", "uninstall", "droppable-rune"])

    assert result.exit_code == 0, result.output
    assert (global_dir / "extensions" / "keeper-rune").is_dir()
    assert not (global_dir / "extensions" / "droppable-rune").exists()


def test_uninstall_refuses_to_escape_the_extensions_layer(
    cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A traversal name is rejected before any filesystem write happens."""
    _isolate_home(tmp_path, monkeypatch)
    outside = tmp_path / "precious"
    outside.mkdir()
    (outside / "keep.txt").write_text("do not delete", encoding="utf-8")

    result = cli_runner.invoke(app, ["rune", "uninstall", "../precious"])

    assert result.exit_code == 1
    assert (outside / "keep.txt").is_file()


def test_uninstall_targets_the_override_not_the_real_home(
    cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same name installed in the real home survives an override-scoped run."""
    _isolate_home(tmp_path, monkeypatch)
    real_home_extensions = tmp_path / "fake_home" / ".agents" / "extensions"
    real_home_extensions.mkdir(parents=True)
    (real_home_extensions / "disposable-rune").mkdir()
    (real_home_extensions / "disposable-rune" / "manifest.json").write_text(
        json.dumps({"name": "disposable-rune", "version": "9.9.9"}), encoding="utf-8"
    )

    result = cli_runner.invoke(app, ["rune", "uninstall", "disposable-rune"])

    assert result.exit_code == 1
    assert (real_home_extensions / "disposable-rune").is_dir()
