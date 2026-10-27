from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

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
from mvge.cli.commands.info import _render_snapshot, info_app

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


class TestInfoCommand:
    def test_info_help(self) -> None:
        result = runner.invoke(info_app, ["--help"])
        assert result.exit_code == 0
        assert "info" in result.output.lower()

    @patch("mvge.cli.commands.info._assemble")
    def test_info_renders_agent_name_and_model(self, mock_assemble: MagicMock) -> None:
        mock_assemble.return_value = _make_snapshot()
        result = runner.invoke(info_app, [])
        assert result.exit_code == 0
        assert "test-agent" in result.output
        assert "nvidia/nemotron-3-ultra-550b-a55b:free" in result.output

    @patch("mvge.cli.commands.info._assemble")
    def test_info_spells_section_shows_name_and_source(
        self, mock_assemble: MagicMock
    ) -> None:
        mock_assemble.return_value = _make_snapshot()
        result = runner.invoke(info_app, [])
        assert result.exit_code == 0
        assert "bash" in result.output
        assert "builtin" in result.output
        assert "custom_tool" in result.output
        assert "rune" in result.output

    @patch("mvge.cli.commands.info._assemble")
    def test_info_runes_section_shows_scope_and_enabled(
        self, mock_assemble: MagicMock
    ) -> None:
        mock_assemble.return_value = _make_snapshot()
        result = runner.invoke(info_app, [])
        assert result.exit_code == 0
        assert "my_rune" in result.output
        assert "user" in result.output
        assert "1.0.0" in result.output
        assert "enabled" in result.output.lower() or "True" in result.output

    @patch("mvge.cli.commands.info._assemble")
    def test_info_config_section_shows_value_and_layer(
        self, mock_assemble: MagicMock
    ) -> None:
        mock_assemble.return_value = _make_snapshot()
        result = runner.invoke(info_app, [])
        assert result.exit_code == 0
        assert "model" in result.output
        assert "agent" in result.output
        assert "defaults" in result.output
        assert "max_tokens" in result.output

    @patch("mvge.cli.commands.info._assemble")
    def test_info_skills_section_shows_name_and_path(
        self, mock_assemble: MagicMock
    ) -> None:
        mock_assemble.return_value = _make_snapshot()
        result = runner.invoke(info_app, [])
        assert result.exit_code == 0
        assert "my-skill" in result.output
        assert "project" in result.output
        assert "/tmp/skills/my-skill" in result.output

    @patch("mvge.cli.commands.info._assemble")
    def test_info_diagnostics_section_shows_kind_and_name(
        self, mock_assemble: MagicMock
    ) -> None:
        mock_assemble.return_value = _make_snapshot()
        result = runner.invoke(info_app, [])
        assert result.exit_code == 0
        assert "shadowed_rune" in result.output
        assert "duplicate-rune" in result.output

    @patch("mvge.cli.commands.info._assemble")
    def test_info_read_only_does_not_call_set(self, mock_assemble: MagicMock) -> None:
        mock_assemble.return_value = _make_snapshot()
        result = runner.invoke(info_app, [])
        assert result.exit_code == 0

    @patch("mvge.cli.commands.info._assemble")
    def test_info_read_only_no_config_mutation(
        self, mock_assemble: MagicMock, tmp_path: Path
    ) -> None:
        config_file = tmp_path / "config.json"
        config_file.write_text('{"model": "default-model"}', encoding="utf-8")

        mock_assemble.return_value = _make_snapshot()
        result = runner.invoke(info_app, [])
        assert result.exit_code == 0
        assert config_file.read_text(encoding="utf-8") == '{"model": "default-model"}'

    @patch("mvge.cli.commands.info._assemble")
    def test_info_default_agent_name(self, mock_assemble: MagicMock) -> None:
        mock_assemble.return_value = _make_snapshot()
        result = runner.invoke(info_app, [])
        assert result.exit_code == 0
        mock_assemble.assert_called_once_with("default-mvge", None)

    @patch("mvge.cli.commands.info._assemble")
    def test_info_agent_name_option(self, mock_assemble: MagicMock) -> None:
        mock_assemble.return_value = _make_snapshot()
        result = runner.invoke(info_app, ["--agent-name", "custom-agent"])
        assert result.exit_code == 0
        mock_assemble.assert_called_once_with("custom-agent", None)

    @patch("mvge.cli.commands.info._assemble")
    def test_info_extension_dir_option(self, mock_assemble: MagicMock) -> None:
        mock_assemble.return_value = _make_snapshot()
        result = runner.invoke(info_app, ["--extension-dir", "/tmp/ext"])
        assert result.exit_code == 0
        mock_assemble.assert_called_once_with("default-mvge", "/tmp/ext")


class TestRenderSnapshot:
    def test_render_empty_snapshot(self) -> None:
        snap = RuntimeSnapshot(
            agent_name="empty-agent",
            model="test/model",
        )
        output = _render_snapshot(snap)
        assert "empty-agent" in output
        assert "test/model" in output

    def test_render_all_sections_present(self) -> None:
        snap = _make_snapshot()
        output = _render_snapshot(snap)
        assert "Spells" in output
        assert "Runes" in output
        assert "Config" in output
        assert "Skills" in output
        assert "Diagnostics" in output

    def test_render_spells_show_source_rune(self) -> None:
        snap = _make_snapshot()
        output = _render_snapshot(snap)
        assert "my_rune" in output

    def test_render_prompt_source(self) -> None:
        snap = _make_snapshot()
        output = _render_snapshot(snap)
        assert "builtin" in output

    def test_render_empty_spells(self) -> None:
        snap = RuntimeSnapshot(agent_name="test", model="m")
        output = _render_snapshot(snap)
        assert "Spells" in output

    def test_render_empty_runes(self) -> None:
        snap = RuntimeSnapshot(agent_name="test", model="m")
        output = _render_snapshot(snap)
        assert "Runes" in output

    def test_render_empty_diagnostics(self) -> None:
        snap = RuntimeSnapshot(agent_name="test", model="m")
        output = _render_snapshot(snap)
        assert "Diagnostics" in output
