from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import click
import pytest
from click.testing import CliRunner

from mvge.cli.dynamic_commands import (
    discover_installed_rune_commands,
    get_extension_dirs,
    load_rune_cli_command,
)


def test_get_extension_dirs_covers_every_layer_and_puts_the_env_var_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The precedence order, asserted as an order.

    ``MVGE_EXTENSION_DIR`` moved from first to last: it is an extra, and
    every extra in the engine resolves after the stack. It used to outrank
    everything, which is the opposite of ``--extension-dir``.
    """
    custom_ext = tmp_path / "custom_ext"
    custom_ext.mkdir()
    monkeypatch.setenv("MVGE_EXTENSION_DIR", str(custom_ext))

    proj_dir = tmp_path / "project"
    dot_agents_ext = proj_dir / ".agents" / "extensions"
    dot_agents_ext.mkdir(parents=True)

    global_dir = tmp_path / "global"
    global_ext = global_dir / "extensions"
    global_ext.mkdir(parents=True)
    agent_ext = global_dir / "agents" / "coder" / "extensions"
    agent_ext.mkdir(parents=True)

    dirs = get_extension_dirs("coder", project_dir=proj_dir, global_dir=global_dir)

    assert dirs == [global_ext, agent_ext, dot_agents_ext, custom_ext]


def test_get_extension_dirs_nonexistent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MVGE_EXTENSION_DIR", str(tmp_path / "nonexistent"))
    dirs = get_extension_dirs(
        "coder", project_dir=tmp_path / "empty", global_dir=tmp_path / "empty_global"
    )
    assert dirs == []


def test_get_extension_dirs_drops_layers_that_are_not_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discovery filters to what exists; the resolver does not.

    Installation needs the whole stack whether or not it is on disk, so the
    filtering lives in the discovery helper rather than in resolution.
    Stated here so the two do not quietly merge again.
    """
    monkeypatch.delenv("MVGE_EXTENSION_DIR", raising=False)
    global_dir = tmp_path / "global"

    assert get_extension_dirs("coder", global_dir=global_dir) == []

    (global_dir / "extensions").mkdir(parents=True)

    assert get_extension_dirs("coder", global_dir=global_dir) == [
        global_dir / "extensions"
    ]


def test_discover_installed_rune_commands(tmp_path: Path) -> None:
    ext_dir = tmp_path / "extensions"
    ext_dir.mkdir()

    # 1. Non-directory file
    (ext_dir / "README.txt").write_text("hello", encoding="utf-8")

    # 2. Subdir without manifest
    (ext_dir / "orphan").mkdir()

    # 3. Disabled rune
    disabled_dir = ext_dir / "disabled"
    disabled_dir.mkdir()
    (disabled_dir / "manifest.json").write_text(
        json.dumps(
            {
                "name": "disabled",
                "version": "0.1.0",
                "enabled": False,
                "commands": ["dis-cmd"],
            }
        ),
        encoding="utf-8",
    )

    # 4. Valid rune
    valid_dir = ext_dir / "valid"
    valid_dir.mkdir()
    (valid_dir / "manifest.json").write_text(
        json.dumps(
            {
                "name": "valid",
                "version": "0.1.0",
                "enabled": True,
                "commands": ["val-cmd1", "val-cmd2"],
            }
        ),
        encoding="utf-8",
    )

    cmd_map = discover_installed_rune_commands(global_dir=tmp_path)
    assert "val-cmd1" in cmd_map
    assert "val-cmd2" in cmd_map
    assert "dis-cmd" not in cmd_map
    assert cmd_map["val-cmd1"][0] == valid_dir


def test_discover_installed_rune_commands_oserror(tmp_path: Path) -> None:
    ext_dir = tmp_path / "extensions"
    ext_dir.mkdir()
    with patch.object(Path, "iterdir", side_effect=PermissionError("denied")):
        cmd_map = discover_installed_rune_commands(global_dir=tmp_path)
        assert cmd_map == {}


def test_load_rune_cli_command_missing(tmp_path: Path) -> None:
    cmd = load_rune_cli_command("nonexistent", global_dir=tmp_path)
    assert cmd is None


def test_load_rune_cli_command_cli_py_click_command(tmp_path: Path) -> None:
    ext_dir = tmp_path / "extensions" / "click-rune"
    ext_dir.mkdir(parents=True)
    (ext_dir / "manifest.json").write_text(
        json.dumps(
            {
                "name": "click-rune",
                "version": "0.1.0",
                "commands": ["my-click"],
            }
        ),
        encoding="utf-8",
    )
    (ext_dir / "cli.py").write_text(
        "import click\n@click.command()\ndef app():\n    click.echo('CLICK RUN')\n",
        encoding="utf-8",
    )

    cmd = load_rune_cli_command("my-click", global_dir=tmp_path)
    assert cmd is not None
    assert isinstance(cmd, click.Command)
    runner = CliRunner()
    res = runner.invoke(cmd, [])
    assert res.exit_code == 0
    assert "CLICK RUN" in res.output


def test_load_rune_cli_command_rune_py_typer_and_click(tmp_path: Path) -> None:
    ext_dir = tmp_path / "extensions" / "rune-typer"
    ext_dir.mkdir(parents=True)
    (ext_dir / "manifest.json").write_text(
        json.dumps(
            {
                "name": "rune-typer",
                "version": "0.1.0",
                "commands": ["typer-cmd"],
            }
        ),
        encoding="utf-8",
    )
    (ext_dir / "rune.py").write_text(
        "import typer\n"
        "app = typer.Typer()\n"
        "@app.command()\n"
        "def sub():\n"
        "    print('TYPER RUNE OK')\n",
        encoding="utf-8",
    )

    cmd = load_rune_cli_command("typer-cmd", global_dir=tmp_path)
    assert cmd is not None
    runner = CliRunner()
    res = runner.invoke(cmd, ["sub"])
    assert res.exit_code == 0
    assert "TYPER RUNE OK" in res.output


def test_load_rune_cli_command_rune_factory_sync_and_async(tmp_path: Path) -> None:
    ext_dir = tmp_path / "extensions" / "factory-advanced"
    ext_dir.mkdir(parents=True)
    (ext_dir / "manifest.json").write_text(
        json.dumps(
            {
                "name": "factory-advanced",
                "version": "0.1.0",
                "commands": ["async-cmd", "sync-noarg-cmd", "sync-none-cmd"],
            }
        ),
        encoding="utf-8",
    )
    (ext_dir / "rune.py").write_text(
        "async def rune_factory(api):\n"
        "    async def async_h(args):\n"
        "        return f'ASYNC: {args}'\n"
        "    def sync_noarg():\n"
        "        return 'NOARG OK'\n"
        "    def sync_none(args):\n"
        "        return None\n"
        "    api.register_command('async-cmd', handler=async_h)\n"
        "    api.register_command('sync-noarg-cmd', handler=sync_noarg)\n"
        "    api.register_command('sync-none-cmd', handler=sync_none)\n",
        encoding="utf-8",
    )

    runner = CliRunner()

    cmd_async = load_rune_cli_command("async-cmd", global_dir=tmp_path)
    assert cmd_async is not None
    res = runner.invoke(cmd_async, ["foo", "bar"])
    assert res.exit_code == 0
    assert "ASYNC: foo bar" in res.output

    cmd_noarg = load_rune_cli_command("sync-noarg-cmd", global_dir=tmp_path)
    assert cmd_noarg is not None
    res_noarg = runner.invoke(cmd_noarg, [])
    assert res_noarg.exit_code == 0
    assert "NOARG OK" in res_noarg.output

    cmd_none = load_rune_cli_command("sync-none-cmd", global_dir=tmp_path)
    assert cmd_none is not None
    res_none = runner.invoke(cmd_none, ["test"])
    assert res_none.exit_code == 0
    assert res_none.output == ""


def test_load_rune_cli_command_cli_py_syntax_error(tmp_path: Path) -> None:
    ext_dir = tmp_path / "extensions" / "broken-cli"
    ext_dir.mkdir(parents=True)
    (ext_dir / "manifest.json").write_text(
        json.dumps(
            {
                "name": "broken-cli",
                "version": "0.1.0",
                "commands": ["broken-cmd"],
            }
        ),
        encoding="utf-8",
    )
    (ext_dir / "cli.py").write_text("def syntax error (", encoding="utf-8")
    assert load_rune_cli_command("broken-cmd", global_dir=tmp_path) is None


def test_load_rune_cli_command_rune_py_click(tmp_path: Path) -> None:
    ext_dir = tmp_path / "extensions" / "click-in-rune"
    ext_dir.mkdir(parents=True)
    (ext_dir / "manifest.json").write_text(
        json.dumps(
            {
                "name": "click-in-rune",
                "version": "0.1.0",
                "commands": ["rune-click"],
            }
        ),
        encoding="utf-8",
    )
    (ext_dir / "rune.py").write_text(
        "import click\n"
        "@click.command()\n"
        "def rune_click():\n"
        "    click.echo('RUNE CLICK HIT')\n",
        encoding="utf-8",
    )
    cmd = load_rune_cli_command("rune-click", global_dir=tmp_path)
    assert cmd is not None
    res = CliRunner().invoke(cmd, [])
    assert res.exit_code == 0
    assert "RUNE CLICK HIT" in res.output


def test_load_rune_cli_command_typer_multiple_commands(tmp_path: Path) -> None:
    ext_dir = tmp_path / "extensions" / "multi-typer"
    ext_dir.mkdir(parents=True)
    (ext_dir / "manifest.json").write_text(
        json.dumps(
            {
                "name": "multi-typer",
                "version": "0.1.0",
                "commands": ["multi-cmd"],
            }
        ),
        encoding="utf-8",
    )
    (ext_dir / "cli.py").write_text(
        "import typer\n"
        "app = typer.Typer()\n"
        "@app.command()\n"
        "def c1(): click.echo('C1')\n"
        "@app.command()\n"
        "def c2(): click.echo('C2')\n",
        encoding="utf-8",
    )
    cmd = load_rune_cli_command("multi-cmd", global_dir=tmp_path)
    assert cmd is not None


def test_load_rune_cli_command_handler_none(tmp_path: Path) -> None:
    ext_dir = tmp_path / "extensions" / "none-handler"
    ext_dir.mkdir(parents=True)
    (ext_dir / "manifest.json").write_text(
        json.dumps(
            {
                "name": "none-handler",
                "version": "0.1.0",
                "commands": ["none-cmd"],
            }
        ),
        encoding="utf-8",
    )
    (ext_dir / "rune.py").write_text(
        "def rune_factory(api):\n    api.register_command('none-cmd', handler=None)\n",
        encoding="utf-8",
    )
    cmd = load_rune_cli_command("none-cmd", global_dir=tmp_path)
    assert cmd is not None
    res = CliRunner().invoke(cmd, [])
    assert res.exit_code == 0


def test_load_rune_cli_command_sanitizes_before_cli_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dynamic CLI loading is a rune entry point outside the loader.

    ``load_rune_cli_command`` executes a rune's ``cli.py`` directly
    rather than through ``load_factory_from_manifest``, and its
    strategy-1 failure is swallowed, so a bracketed NO_PROXY would
    silently make the command vanish instead of loading it. The
    module's top-level httpx client must build under a bracketed
    environment, with no helper call from the rune itself.
    """
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setenv("NO_PROXY", "[::1],localhost")
    ext_dir = tmp_path / "extensions" / "noproxy-rune"
    ext_dir.mkdir(parents=True)
    (ext_dir / "manifest.json").write_text(
        json.dumps(
            {
                "name": "noproxy-rune",
                "version": "0.1.0",
                "commands": ["noproxy-click"],
            }
        ),
        encoding="utf-8",
    )
    (ext_dir / "cli.py").write_text(
        "import httpx\n"
        "import click\n"
        "with httpx.Client():\n"
        "    pass\n"
        "@click.command()\n"
        "def app():\n"
        "    click.echo('NOPROXY RUN')\n",
        encoding="utf-8",
    )

    cmd = load_rune_cli_command("noproxy-click", global_dir=tmp_path)
    assert cmd is not None
    assert os.environ["NO_PROXY"] == "localhost"
    runner = CliRunner()
    res = runner.invoke(cmd, [])
    assert res.exit_code == 0
    assert "NOPROXY RUN" in res.output
