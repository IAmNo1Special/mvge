from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from mvge.agent.snapshot import (
    RuntimeSnapshot,
    SnapshotConfigEntry,
    SnapshotDiagnostic,
    SnapshotPrompt,
    SnapshotRune,
    SnapshotSkill,
    SnapshotSpell,
    SpellSource,
)
from mvge.cli.commands.build import _assemble, build_app

runner = CliRunner()


def _make_snapshot() -> RuntimeSnapshot:
    return RuntimeSnapshot(
        agent_name="test-agent",
        model="nvidia/nemotron-3-ultra-550b-a55b:free",
        spells=[
            SnapshotSpell(
                name="bash",
                description="Run shell command",
                source=SpellSource.BUILTIN,
                parameters={"type": "object"},
            ),
            SnapshotSpell(
                name="custom_tool",
                description="A custom tool from rune",
                source=SpellSource.RUNE,
                source_rune="my_rune",
                parameters={},
            ),
        ],
        runes=[
            SnapshotRune(
                name="my_rune",
                version="1.0.0",
                description="A test rune",
                scope="user",
                path="/tmp/runes/my_rune",
                enabled=True,
                hooks=["turn_start"],
                entry_point="main.py",
                shortcuts=[],
                system_deps=["git"],
                python_deps=["pydantic"],
            ),
        ],
        config=[
            SnapshotConfigEntry(
                key="model",
                value="nvidia/nemotron-3-ultra-550b-a55b:free",
                layer="agent",
                source_file="/home/user/.agents/agents/test-agent/config.json",
            ),
            SnapshotConfigEntry(
                key="max_tokens",
                value=4096,
                layer="defaults",
                source_file=None,
            ),
        ],
        prompt=SnapshotPrompt(
            source="builtin",
            path=None,
            text="You are Mvge, a concise AI coding agent.",
        ),
        skills=[
            SnapshotSkill(
                name="my-skill",
                description="A test skill",
                scope="project",
                path="/tmp/skills/my-skill",
                version="1.0.0",
                license="MIT",
                compatibility="",
                allowed_tools="read write",
                disable_model_invocation=False,
            ),
        ],
        diagnostics=[
            SnapshotDiagnostic(
                kind="shadowed_rune",
                name="duplicate-rune",
                message="shadowed by user scope",
                scope="user",
                path="/tmp/dup_rune",
                target="rune",
            ),
        ],
    )


class TestBuildCommand:
    def test_build_help(self) -> None:
        result = runner.invoke(build_app, ["--help"])
        assert result.exit_code == 0
        assert "build" in result.output.lower()

    @patch("mvge.cli.commands.build._assemble")
    def test_build_stdout_outputs_json(self, mock_assemble: MagicMock) -> None:
        mock_assemble.return_value = _make_snapshot()
        result = runner.invoke(build_app, [])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["agent_name"] == "test-agent"
        assert data["model"] == "nvidia/nemotron-3-ultra-550b-a55b:free"

    @patch("mvge.cli.commands.build._assemble")
    def test_build_stdout_contains_all_sections(self, mock_assemble: MagicMock) -> None:
        mock_assemble.return_value = _make_snapshot()
        result = runner.invoke(build_app, [])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "spells" in data
        assert "runes" in data
        assert "config" in data
        assert "prompt" in data
        assert "skills" in data
        assert "diagnostics" in data
        assert "model" in data

    @patch("mvge.cli.commands.build._assemble")
    def test_build_spells_have_source(self, mock_assemble: MagicMock) -> None:
        mock_assemble.return_value = _make_snapshot()
        result = runner.invoke(build_app, [])
        assert result.exit_code == 0
        data = json.loads(result.output)
        sources = [s["source"] for s in data["spells"]]
        assert "builtin" in sources
        assert "rune" in sources
        rune_spell = next(s for s in data["spells"] if s["source"] == "rune")
        assert rune_spell["source_rune"] == "my_rune"

    @patch("mvge.cli.commands.build._assemble")
    def test_build_runes_have_scope_and_enabled(self, mock_assemble: MagicMock) -> None:
        mock_assemble.return_value = _make_snapshot()
        result = runner.invoke(build_app, [])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["runes"]) == 1
        rune = data["runes"][0]
        assert rune["name"] == "my_rune"
        assert rune["scope"] == "user"
        assert rune["enabled"] is True

    @patch("mvge.cli.commands.build._assemble")
    def test_build_config_has_provenance(self, mock_assemble: MagicMock) -> None:
        mock_assemble.return_value = _make_snapshot()
        result = runner.invoke(build_app, [])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["config"]) == 2
        layers = [c["layer"] for c in data["config"]]
        assert "agent" in layers
        assert "defaults" in layers
        agent_entry = next(c for c in data["config"] if c["layer"] == "agent")
        assert agent_entry["source_file"] is not None
        defaults_entry = next(c for c in data["config"] if c["layer"] == "defaults")
        assert defaults_entry["source_file"] is None

    @patch("mvge.cli.commands.build._assemble")
    def test_build_prompt_has_source(self, mock_assemble: MagicMock) -> None:
        mock_assemble.return_value = _make_snapshot()
        result = runner.invoke(build_app, [])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["prompt"]["source"] == "builtin"
        assert data["prompt"]["text"] is not None

    @patch("mvge.cli.commands.build._assemble")
    def test_build_skills_have_scope(self, mock_assemble: MagicMock) -> None:
        mock_assemble.return_value = _make_snapshot()
        result = runner.invoke(build_app, [])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["skills"]) == 1
        skill = data["skills"][0]
        assert skill["name"] == "my-skill"
        assert skill["scope"] == "project"
        assert skill["path"] == "/tmp/skills/my-skill"

    @patch("mvge.cli.commands.build._assemble")
    def test_build_diagnostics_have_target(self, mock_assemble: MagicMock) -> None:
        mock_assemble.return_value = _make_snapshot()
        result = runner.invoke(build_app, [])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["diagnostics"]) == 1
        diag = data["diagnostics"][0]
        assert diag["target"] == "rune"
        assert diag["name"] == "duplicate-rune"

    @patch("mvge.cli.commands.build._assemble")
    def test_build_output_to_file(
        self, mock_assemble: MagicMock, tmp_path: Path
    ) -> None:
        mock_assemble.return_value = _make_snapshot()
        out_file = tmp_path / "manifest.json"
        result = runner.invoke(build_app, ["--output", str(out_file)])
        assert result.exit_code == 0
        assert out_file.exists()
        data = json.loads(out_file.read_text(encoding="utf-8"))
        assert data["agent_name"] == "test-agent"
        assert len(data["spells"]) == 2

    @patch("mvge.cli.commands.build._assemble")
    def test_build_output_shorthand(
        self, mock_assemble: MagicMock, tmp_path: Path
    ) -> None:
        mock_assemble.return_value = _make_snapshot()
        out_file = tmp_path / "manifest.json"
        result = runner.invoke(build_app, ["-o", str(out_file)])
        assert result.exit_code == 0
        assert out_file.exists()

    @patch("mvge.cli.commands.build._assemble")
    def test_build_output_creates_parent_dirs(
        self, mock_assemble: MagicMock, tmp_path: Path
    ) -> None:
        mock_assemble.return_value = _make_snapshot()
        out_file = tmp_path / "sub" / "dir" / "manifest.json"
        result = runner.invoke(build_app, ["--output", str(out_file)])
        assert result.exit_code == 0
        assert out_file.exists()

    @patch("mvge.cli.commands.build._assemble")
    def test_build_default_agent_name(self, mock_assemble: MagicMock) -> None:
        mock_assemble.return_value = _make_snapshot()
        result = runner.invoke(build_app, [])
        assert result.exit_code == 0
        mock_assemble.assert_called_once_with("default-mvge", None)

    @patch("mvge.cli.commands.build._assemble")
    def test_build_agent_name_option(self, mock_assemble: MagicMock) -> None:
        mock_assemble.return_value = _make_snapshot()
        result = runner.invoke(build_app, ["--agent-name", "custom-agent"])
        assert result.exit_code == 0
        mock_assemble.assert_called_once_with("custom-agent", None)

    @patch("mvge.cli.commands.build._assemble")
    def test_build_extension_dir_option(self, mock_assemble: MagicMock) -> None:
        mock_assemble.return_value = _make_snapshot()
        result = runner.invoke(build_app, ["--extension-dir", "/tmp/ext"])
        assert result.exit_code == 0
        mock_assemble.assert_called_once_with("default-mvge", "/tmp/ext")

    @patch("mvge.cli.commands.build._assemble")
    def test_build_read_only_does_not_call_set(self, mock_assemble: MagicMock) -> None:
        mock_assemble.return_value = _make_snapshot()
        result = runner.invoke(build_app, [])
        assert result.exit_code == 0

    @patch("mvge.cli.commands.build._assemble")
    def test_build_read_only_no_config_mutation(
        self, mock_assemble: MagicMock, tmp_path: Path
    ) -> None:
        config_file = tmp_path / "config.json"
        config_file.write_text('{"model": "default-model"}', encoding="utf-8")

        mock_assemble.return_value = _make_snapshot()
        result = runner.invoke(build_app, [])
        assert result.exit_code == 0
        assert config_file.read_text(encoding="utf-8") == '{"model": "default-model"}'

    @patch("mvge.cli.commands.build._assemble")
    def test_build_format_summary(self, mock_assemble: MagicMock) -> None:
        mock_assemble.return_value = _make_snapshot()
        result = runner.invoke(build_app, ["--format", "summary"])
        assert result.exit_code == 0
        output = result.output
        assert "Agent: test-agent" in output
        assert "Spells (2):" in output
        assert "Runes (1):" in output
        assert "my_rune" in output
        assert "Diagnostics (1):" in output
        assert "duplicate-rune" in output
        # summary is not raw JSON
        assert not output.lstrip().startswith("{")

    @patch("mvge.cli.commands.build._assemble")
    def test_build_format_summary_to_file(
        self, mock_assemble: MagicMock, tmp_path: Path
    ) -> None:
        mock_assemble.return_value = _make_snapshot()
        out_file = tmp_path / "manifest.txt"
        result = runner.invoke(
            build_app, ["--format", "summary", "--output", str(out_file)]
        )
        assert result.exit_code == 0
        assert out_file.exists()
        text = out_file.read_text(encoding="utf-8")
        assert "Agent: test-agent" in text
        assert "Spells (2):" in text

    @patch("mvge.cli.commands.build._assemble")
    def test_build_format_json_pretty(self, mock_assemble: MagicMock) -> None:
        mock_assemble.return_value = _make_snapshot()
        result = runner.invoke(build_app, ["--format", "json", "--pretty"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["agent_name"] == "test-agent"
        # pretty printing inserts indentation/newlines
        assert "\n  " in result.output

    @patch("mvge.cli.commands.build._assemble")
    def test_build_format_json_compact(self, mock_assemble: MagicMock) -> None:
        mock_assemble.return_value = _make_snapshot()
        result = runner.invoke(build_app, ["--format", "json", "--no-pretty"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["agent_name"] == "test-agent"
        # compact output has no indented newlines
        assert "\n  " not in result.output

    @patch("mvge.cli.commands.build._assemble")
    def test_build_default_json_unchanged(self, mock_assemble: MagicMock) -> None:
        mock_assemble.return_value = _make_snapshot()
        result = runner.invoke(build_app, [])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["agent_name"] == "test-agent"
        # default remains pretty
        assert "\n  " in result.output

    @patch("mvge.cli.commands.build._assemble")
    def test_build_invalid_format_rejected(self, mock_assemble: MagicMock) -> None:
        mock_assemble.return_value = _make_snapshot()
        result = runner.invoke(build_app, ["--format", "xml"])
        assert result.exit_code != 0
        mock_assemble.assert_not_called()


class TestAssemble:
    @patch("mvge.cli.commands.build.MvgeEnvironment")
    @patch("mvge.cli.commands.build.Mvge")
    def test_assemble_creates_environment(
        self,
        mock_agent_cls: MagicMock,
        mock_env_cls: MagicMock,
    ) -> None:
        mock_env = MagicMock()
        mock_env_cls.resolve.return_value = mock_env
        mock_agent = MagicMock()
        mock_agent._load_runes = AsyncMock()
        mock_agent.load_runes = AsyncMock()
        mock_agent.build_snapshot.return_value = _make_snapshot()
        mock_agent_cls.return_value = mock_agent

        result = asyncio.run(_assemble("test-agent", None))

        mock_env_cls.resolve.assert_called_once_with(
            agent_name="test-agent", project_dir=Path.cwd()
        )
        mock_agent_cls.assert_called_once_with(
            api_key="",
            name="test-agent",
            extension_dir=None,
            environment=mock_env,
        )
        assert isinstance(result, RuntimeSnapshot)

    @patch("mvge.cli.commands.build.MvgeEnvironment")
    @patch("mvge.cli.commands.build.Mvge")
    def test_assemble_loads_runes_and_builds_snapshot(
        self,
        mock_agent_cls: MagicMock,
        mock_env_cls: MagicMock,
    ) -> None:
        mock_env_cls.resolve.return_value = MagicMock()
        mock_agent = MagicMock()
        mock_agent.load_runes = AsyncMock()
        snapshot = _make_snapshot()
        mock_agent.build_snapshot.return_value = snapshot
        mock_agent_cls.return_value = mock_agent

        result = asyncio.run(_assemble("test-agent", "/tmp/ext"))

        mock_agent.load_runes.assert_awaited_once()
        mock_agent.build_snapshot.assert_called_once()
        assert result is snapshot

    @patch("mvge.cli.commands.build.MvgeEnvironment")
    @patch("mvge.cli.commands.build.Mvge")
    def test_assemble_passes_extension_dir(
        self,
        mock_agent_cls: MagicMock,
        mock_env_cls: MagicMock,
    ) -> None:
        mock_env = MagicMock()
        mock_env_cls.resolve.return_value = mock_env
        mock_agent = MagicMock()
        mock_agent.load_runes = AsyncMock()
        mock_agent.build_snapshot.return_value = _make_snapshot()
        mock_agent_cls.return_value = mock_agent

        asyncio.run(_assemble("test-agent", "/tmp/extensions"))

        mock_agent_cls.assert_called_once_with(
            api_key="",
            name="test-agent",
            extension_dir="/tmp/extensions",
            environment=mock_env,
        )
