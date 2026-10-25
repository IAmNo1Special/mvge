from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mvge.cli.main import app

runner = CliRunner()


def test_uninstalled_skill_command_not_in_help(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When skills-bridge rune is not installed, 'skill' is not in CLI help."""
    empty_global = tmp_path / "global_empty"
    empty_global.mkdir()
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(empty_global))

    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "skill" not in result.output.split()


def test_installed_skills_bridge_dynamic_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When skills-bridge rune is installed, 'skill' dynamically mounts on CLI."""
    global_dir = tmp_path / "global_agents"
    ext_dir = global_dir / "extensions" / "skills-bridge"
    ext_dir.mkdir(parents=True)

    manifest_data = {
        "name": "skills-bridge",
        "version": "0.1.0",
        "description": "Official Agent Skills bridge",
        "commands": ["skill"],
    }
    (ext_dir / "manifest.json").write_text(
        json.dumps(manifest_data, indent=2), encoding="utf-8"
    )

    (ext_dir / "cli.py").write_text(
        "import typer\n"
        "app = typer.Typer(help='Inspect and clean up agent skills.')\n"
        "@app.command('list')\n"
        "def skill_list():\n"
        "    print('OK: No skills found.')\n"
        "@app.command('validate')\n"
        "def skill_validate(target: str):\n"
        "    print(f'OK: Validated {target}')\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(global_dir))

    # 1. Command appears in --help
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "skill" in result.output

    # 2. Subcommand list executes
    result_list = runner.invoke(app, ["skill", "list"])
    assert result_list.exit_code == 0
    assert "OK: No skills found." in result_list.output

    # 3. Subcommand validate executes
    result_val = runner.invoke(app, ["skill", "validate", "my-skill"])
    assert result_val.exit_code == 0
    assert "OK: Validated my-skill" in result_val.output
