"""Unit tests for mvge.runes.deps python-dependency installation.

Two defects motivated this module. The rune installer shelled out to a
bare ``uv add``, which resolves against the *current working directory*,
so installing a rune rewrote the pyproject.toml and uv.lock of whatever
project the user happened to be standing in. It also swallowed every
error, so a failed dependency install was indistinguishable from success.

Runes run in-process against module-level registries owned by the host,
following the same constraint Pi and DeepSeek's harness both settled on:
one shared, host-singleton dependency tree. A per-rune virtualenv would
give a rune a private copy of the host packages and silently register
into a registry the agent loop never reads. So there is exactly one
environment, owned by the extensions directory, and the loader already
discovers it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mvge.runes.deps import (
    EXTENSIONS_PROJECT_NAME,
    confirm_python_deps_install,
    ensure_extensions_project,
    install_python_deps,
)


def test_ensure_extensions_project_creates_a_manifest(tmp_path: Path) -> None:
    """A bare extensions directory gains a private project for rune deps.

    Mirrors the one generated manifest per install root that Pi creates
    for its extensions: rune dependencies must not land in the manifest
    of whichever project the user invoked the CLI from.
    """
    project_dir = tmp_path / "extensions"

    ensure_extensions_project(project_dir)

    manifest = project_dir / "pyproject.toml"
    assert manifest.is_file()
    assert EXTENSIONS_PROJECT_NAME in manifest.read_text(encoding="utf-8")


def test_ensure_extensions_project_never_clobbers_an_existing_manifest(
    tmp_path: Path,
) -> None:
    """Re-running must not disturb deps an earlier install already added."""
    project_dir = tmp_path / "extensions"
    project_dir.mkdir()
    existing = project_dir / "pyproject.toml"
    existing.write_text(
        f'[project]\nname = "{EXTENSIONS_PROJECT_NAME}"\n'
        'dependencies = ["httpx>=0.27"]\n',
        encoding="utf-8",
    )

    ensure_extensions_project(project_dir)

    assert "httpx>=0.27" in existing.read_text(encoding="utf-8")


def test_install_python_deps_targets_the_extensions_project(tmp_path: Path) -> None:
    """Dependencies go to the extensions project, never the caller's cwd."""
    project_dir = tmp_path / "extensions"

    with patch("subprocess.run") as mock_run:
        install_python_deps(["httpx>=0.27"], env_project=project_dir, confirm=True)

    assert mock_run.call_count == 1
    argv = mock_run.call_args[0][0]
    assert argv[:2] == ["uv", "add"]
    assert "--project" in argv
    assert str(project_dir) in argv
    assert "httpx>=0.27" in argv


def test_install_python_deps_skips_unconfirmed_dependencies(tmp_path: Path) -> None:
    """An unreviewed install is skipped, not performed.

    The process is not a TTY under test, so the gate must fail closed.
    """
    project_dir = tmp_path / "extensions"

    with patch("subprocess.run") as mock_run:
        install_python_deps(["httpx>=0.27"], env_project=project_dir)

    mock_run.assert_not_called()


def test_install_python_deps_skips_explicitly_declined(tmp_path: Path) -> None:
    project_dir = tmp_path / "extensions"

    with patch("subprocess.run") as mock_run:
        install_python_deps(["httpx>=0.27"], env_project=project_dir, confirm=False)

    mock_run.assert_not_called()


def test_install_python_deps_reports_failure_instead_of_swallowing_it(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed dependency install is reported.

    The previous implementation caught the error and passed, so a rune
    could install cleanly and then fail at load with a bare ImportError
    that pointed nowhere near the cause.
    """
    project_dir = tmp_path / "extensions"

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        raise subprocess.CalledProcessError(1, cmd, stderr="resolution failed")

    with (
        patch("subprocess.run", side_effect=fake_run),
        caplog.at_level("WARNING"),
    ):
        result = install_python_deps(
            ["nonexistent-pkg-xyz"], env_project=project_dir, confirm=True
        )

    assert result is False
    assert "nonexistent-pkg-xyz" in caplog.text


def test_install_python_deps_reports_success(tmp_path: Path) -> None:
    project_dir = tmp_path / "extensions"

    with patch("subprocess.run", return_value=MagicMock(returncode=0)):
        result = install_python_deps(
            ["httpx>=0.27"], env_project=project_dir, confirm=True
        )

    assert result is True


def test_install_python_deps_ignores_blank_entries(tmp_path: Path) -> None:
    """Blank manifest entries are dropped rather than passed to uv."""
    project_dir = tmp_path / "extensions"

    with patch("subprocess.run") as mock_run:
        result = install_python_deps(
            ["  ", "httpx>=0.27", ""], env_project=project_dir, confirm=True
        )

    assert result is True
    argv = mock_run.call_args[0][0]
    assert argv.count("httpx>=0.27") == 1
    assert "" not in argv
    assert "  " not in argv


def test_confirm_gate_accepts_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda *_a: "y")

    assert confirm_python_deps_install(["httpx>=0.27"], confirm=None) is True


def test_confirm_gate_defaults_to_no(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anything that is not an explicit yes is a no."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda *_a: "")

    assert confirm_python_deps_install(["httpx>=0.27"], confirm=None) is False


def test_confirm_gate_fails_closed_without_a_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)

    assert confirm_python_deps_install(["httpx>=0.27"], confirm=None) is False


def test_manifest_python_deps_are_read_from_disk(tmp_path: Path) -> None:
    """The declared list comes from the manifest, not the caller."""
    rune_dir = tmp_path / "rune"
    rune_dir.mkdir()
    (rune_dir / "manifest.json").write_text(
        json.dumps({"name": "rune", "python_deps": ["httpx>=0.27"]}), encoding="utf-8"
    )

    with patch("subprocess.run") as mock_run:
        install_python_deps(
            ["httpx>=0.27"],
            env_project=tmp_path / "extensions",
            manifest_path=rune_dir / "manifest.json",
            confirm=True,
        )

    assert mock_run.call_count == 1


def test_deps_edge_cases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mvge.runes.deps import read_manifest_python_deps

    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda _: (_ for _ in ()).throw(EOFError))
    assert confirm_python_deps_install(["foo"], confirm=None) is False

    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{corrupt", encoding="utf-8")
    assert read_manifest_python_deps(bad_json) == []

    list_json = tmp_path / "list.json"
    list_json.write_text("['not', 'a', 'dict']", encoding="utf-8")
    assert read_manifest_python_deps(list_json) == []

    with patch("subprocess.run", side_effect=FileNotFoundError("no uv")):
        assert install_python_deps(["foo"], env_project=tmp_path, confirm=True) is False
