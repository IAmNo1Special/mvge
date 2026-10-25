from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mvge.agent.environment import MvgeEnvironment
from mvge.agent.mvge import Mvge
from mvge.cli.dynamic_commands import (
    configured_rune_layers,
    discover_installed_rune_commands,
    get_extension_dirs,
)
from mvge.cli.main import app
from mvge.core.layers import resolve_rune_layers, resolve_rune_paths
from mvge.runes.loader import load_runes_from_paths
from mvge.runes.trust import WorkspaceTrustStatus, WorkspaceTrustStore


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


def test_uninstalled_rune_command_not_in_help(
    cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty_global = tmp_path / "global_empty"
    empty_global.mkdir()
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(empty_global))

    result = cli_runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "mock-cmd" not in result.output
    assert "mcp" not in result.output


def test_installed_rune_command_via_cli_py(
    cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_dir = tmp_path / "global_agents"
    ext_dir = global_dir / "extensions" / "mock-rune"
    ext_dir.mkdir(parents=True)

    # 1. manifest.json
    manifest_data = {
        "name": "mock-rune",
        "version": "0.1.0",
        "description": "Mock extension",
        "commands": ["mock-cmd"],
    }
    (ext_dir / "manifest.json").write_text(
        json.dumps(manifest_data, indent=2), encoding="utf-8"
    )

    # 2. cli.py
    (ext_dir / "cli.py").write_text(
        "import typer\n"
        "app = typer.Typer(help='Mock rune commands')\n"
        "@app.command()\n"
        "def ping():\n"
        "    print('MOCK PING OK')\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(global_dir))

    # Test that --help shows the dynamic command
    help_res = cli_runner.invoke(app, ["--help"])
    assert help_res.exit_code == 0
    assert "mock-cmd" in help_res.output

    # Test executing the dynamic subcommand
    res = cli_runner.invoke(app, ["mock-cmd", "ping"])
    assert res.exit_code == 0
    assert "MOCK PING OK" in res.output


def test_installed_rune_command_via_rune_factory(
    cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_dir = tmp_path / "global_agents"
    ext_dir = global_dir / "extensions" / "factory-rune"
    ext_dir.mkdir(parents=True)

    manifest_data = {
        "name": "factory-rune",
        "version": "0.1.0",
        "description": "Factory extension",
        "commands": ["factory-cmd"],
    }
    (ext_dir / "manifest.json").write_text(
        json.dumps(manifest_data, indent=2), encoding="utf-8"
    )

    (ext_dir / "rune.py").write_text(
        "def rune_factory(api):\n"
        "    def handler(args):\n"
        "        return f'FACTORY ECHO: {args}'\n"
        "    api.register_command(\n"
        "        'factory-cmd', description='Factory command', handler=handler\n"
        "    )\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(global_dir))

    res = cli_runner.invoke(app, ["factory-cmd", "hello-world"])
    assert res.exit_code == 0
    assert "FACTORY ECHO: hello-world" in res.output


class TestLoadingAndDiscoveryAgree:
    """Rune loading and Rune CLI command discovery resolve identically.

    The whole point of one resolver: the two ask the same question with the
    same inputs, so they cannot come to different answers. Asserted directly,
    because a resolver that merely *looks* shared still drifts.
    """

    @pytest.fixture
    def tree(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
        """One tree with a Rune in each layer that can be addressed."""
        global_dir = tmp_path / "global"
        project = tmp_path / "project"
        project.mkdir()
        for layer_dir in (
            global_dir / "extensions" / "user-rune",
            global_dir / "agents" / "coder" / "extensions" / "agent-rune",
            project / ".agents" / "extensions" / "project-rune",
        ):
            layer_dir.mkdir(parents=True)
            (layer_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "name": layer_dir.name,
                        "version": "0.1.0",
                        "description": layer_dir.name,
                        "commands": [f"{layer_dir.name}-cmd"],
                    }
                ),
                encoding="utf-8",
            )
        monkeypatch.setenv("MVGE_GLOBAL_DIR", str(global_dir))
        WorkspaceTrustStore().set_trust(project, WorkspaceTrustStatus.TRUSTED)
        return {"global_dir": global_dir, "project": project}

    def test_discovery_dirs_equal_the_resolved_rune_stack(
        self, tree: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MVGE_EXTENSION_DIR", raising=False)

        dirs = get_extension_dirs(
            "coder", project_dir=tree["project"], global_dir=tree["global_dir"]
        )

        assert dirs == resolve_rune_paths(
            "coder", project_dir=tree["project"], global_dir=tree["global_dir"]
        )

    def test_both_see_every_layered_rune(self, tree: dict) -> None:
        commands = discover_installed_rune_commands(
            "coder", project_dir=tree["project"], global_dir=tree["global_dir"]
        )

        assert set(commands) == {
            "user-rune-cmd",
            "agent-rune-cmd",
            "project-rune-cmd",
        }

    def test_a_rune_visible_to_discovery_is_one_loading_would_read(
        self, tree: dict
    ) -> None:
        """The user-visible consequence, stated in both vocabularies.

        Anything discovery can mount, the loader can find -- so a Rune that
        installs is a Rune that loads, which is the failure ADR-0014 was
        written to end.
        """
        commands = discover_installed_rune_commands(
            "coder", project_dir=tree["project"], global_dir=tree["global_dir"]
        )

        loads, _diagnostics = load_runes_from_paths(
            [
                (path, scope)
                for path, scope in (
                    (layer.path, layer.scope)
                    for layer in resolve_rune_layers(
                        "coder",
                        project_dir=tree["project"],
                        global_dir=tree["global_dir"],
                    )
                )
            ],
            "coder",
        )

        assert {load.manifest.name for load in loads} == {
            name.removesuffix("-cmd") for name in commands
        }

    @pytest.mark.asyncio
    async def test_discovery_matches_what_the_agent_actually_resolves(
        self, tree: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The strongest form of the equality: agent versus CLI, live.

        Every weaker comparison -- discovery against ``resolve_rune_paths``,
        or against the standard stack alone -- passes while a Rune the agent
        loads sits invisible to the CLI. This builds a real ``Mvge`` and
        compares the directories it would load from against the directories
        discovery scans.
        """
        monkeypatch.delenv("MVGE_EXTENSION_DIR", raising=False)

        # Built the way the CLI builds it: the project anchor is passed in
        # explicitly. An agent constructed without one has no project layer at
        # all, so comparing it against a project-anchored discovery would be
        # comparing two different questions.
        environment = MvgeEnvironment.resolve("coder", project_dir=tree["project"])
        agent = Mvge(
            api_key="key",
            name="coder",
            environment=environment,
            caller_dir=tree["project"],
        )
        try:
            discovered = get_extension_dirs(
                "coder",
                project_dir=tree["project"],
                global_dir=tree["global_dir"],
                extras=configured_rune_layers("coder", tree["project"]),
            )

            assert discovered == [
                layer.path
                for layer in agent.environment.rune_layers
                if layer.path.expanduser().is_dir()
            ]
        finally:
            await agent.close()

    def test_extension_dir_env_resolves_last(
        self, tree: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The env var is an extra, and extras resolve last.

        It used to be prepended, which meant it outranked the whole stack --
        the opposite of how ``--extension-dir`` and a configured ``rune_paths``
        are treated. One meaning of "extra directory", one position.
        """
        extra = tmp_path / "bespoke"
        extra.mkdir()
        monkeypatch.setenv("MVGE_EXTENSION_DIR", str(extra))

        dirs = get_extension_dirs(
            "coder", project_dir=tree["project"], global_dir=tree["global_dir"]
        )

        assert dirs[-1] == extra
        assert dirs[:-1] == resolve_rune_paths(
            "coder", project_dir=tree["project"], global_dir=tree["global_dir"]
        )

    def test_bare_extensions_dir_is_not_a_source(self, tmp_path, monkeypatch) -> None:
        """``<project>/extensions`` is gone, and that is deliberate.

        It is not a standard protocol name, and worse, any repository with an
        ordinary ``extensions/`` folder had its Rune manifests CLI-scanned
        whether or not the author meant Mvge to look there. Asserted as
        absent so it cannot come back unnoticed.
        """
        project = tmp_path / "project"
        (project / "extensions" / "bespoke-rune").mkdir(parents=True)
        (project / "extensions" / "bespoke-rune" / "manifest.json").write_text(
            json.dumps(
                {
                    "name": "bespoke-rune",
                    "version": "0.1.0",
                    "description": "x",
                    "commands": ["bespoke-cmd"],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.delenv("MVGE_EXTENSION_DIR", raising=False)

        commands = discover_installed_rune_commands("coder", project_dir=project)

        assert "bespoke-cmd" not in commands

    def test_project_layer_omitted_without_a_project_dir(
        self, tree: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No project dir means no project layer -- not the working directory."""
        monkeypatch.delenv("MVGE_EXTENSION_DIR", raising=False)
        monkeypatch.chdir(tree["project"])

        dirs = get_extension_dirs("coder", global_dir=tree["global_dir"])

        assert tree["project"] / ".agents" / "extensions" not in dirs
        assert "project-rune-cmd" not in discover_installed_rune_commands(
            "coder", global_dir=tree["global_dir"]
        )
