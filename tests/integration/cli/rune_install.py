"""Integration tests for the ``mvgeos rune install`` round trip.

The invariant under test is that the layer the installer writes to and
the layer the runtime discovers from are the same directory. They were
two independent decisions — install hardcoded ``~/.agents/extensions``
while discovery read ``$MVGEOS_GLOBAL_DIR`` — so a rune installed with
the override set reported success and then loaded nowhere.
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


def _write_rune(root: Path, name: str, command: str) -> Path:
    """Create a minimal installable rune exposing one CLI command."""
    source = root / name
    source.mkdir(parents=True)
    (source / "manifest.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "0.1.0",
                "description": "Round-trip rune",
                "entry_point": "rune.py",
                "commands": [command],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (source / "cli.py").write_text(
        "import typer\n"
        "app = typer.Typer()\n"
        "@app.command()\n"
        "def ping():\n"
        "    print('ROUNDTRIP OK')\n",
        encoding="utf-8",
    )
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


def test_install_lands_in_the_discovered_layer(
    cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Installing under the override writes to the override, not the real home."""
    global_dir = _isolate_home(tmp_path, monkeypatch)
    source = _write_rune(tmp_path / "src", "roundtrip-rune", "roundtrip-cmd")

    result = cli_runner.invoke(app, ["rune", "install", str(source)])
    assert result.exit_code == 0, result.output

    assert (global_dir / "extensions" / "roundtrip-rune" / "manifest.json").is_file()
    assert not (tmp_path / "fake_home" / ".agents").exists()


def test_installed_rune_is_discoverable_by_the_runtime(
    cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rune installed under the override is registered as a CLI command.

    This is the round trip that broke: install reported success, but the
    command never appeared because discovery read a different directory.
    """
    _isolate_home(tmp_path, monkeypatch)
    source = _write_rune(tmp_path / "src", "roundtrip-rune", "roundtrip-cmd")

    result = cli_runner.invoke(app, ["rune", "install", str(source)])
    assert result.exit_code == 0, result.output

    help_res = cli_runner.invoke(app, ["--help"])
    assert help_res.exit_code == 0
    assert "roundtrip-cmd" in help_res.output
