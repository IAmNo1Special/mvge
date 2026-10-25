from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from mvge.cli.commands.tome import (
    _format_timestamp,
    _render_tome_list,
    get_tome_dir,
    tome_app,
)
from mvge.cli.main import app
from mvge.tome.handle import TomeHandleFactory
from mvge.tome.types import (
    TomeEntry,
    TomeEntryType,
    TomeMetadata,
    TomeVersionError,
)


def _make_meta(tome_id: str, cwd: str) -> TomeMetadata:
    return TomeMetadata(
        id=tome_id,
        created_at="2024-01-01T12:00:00",
        cwd=cwd,
        parent_tome_id=None,
        active_leaf_id=None,
        schema_version="1.0",
    )


runner = CliRunner()


def test_tome_no_subcommand_prints_help() -> None:
    result = runner.invoke(app, ["tome"])
    assert result.exit_code == 0
    assert "Session tome management" in result.stdout
    assert "Commands" in result.stdout
    assert "Missing command" not in result.output


class TestTomeCommands:
    def test_tome_app_exists(self) -> None:
        assert tome_app is not None
        assert isinstance(tome_app, typer.Typer)

    def test_get_tome_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Asserts the default sessions directory, so clear the override the
        # suite-wide hermeticity fixture installs.
        monkeypatch.delenv("MVGEOS_GLOBAL_DIR", raising=False)
        result = get_tome_dir()
        assert result == Path.home() / ".agents" / "sessions"

    def test_tome_list_help(self) -> None:
        result = runner.invoke(tome_app, ["list", "--help"])
        assert result.exit_code == 0
        assert "List all tomes" in result.stdout

    def test_tome_show_help(self) -> None:
        result = runner.invoke(tome_app, ["show", "--help"])
        assert result.exit_code == 0
        assert "Show tome details" in result.stdout

    def test_tome_export_help(self) -> None:
        result = runner.invoke(tome_app, ["export", "--help"])
        assert result.exit_code == 0
        assert "Export a tome" in result.stdout

    def test_tome_create_help(self) -> None:
        result = runner.invoke(tome_app, ["create", "--help"])
        assert result.exit_code == 0
        assert "Create a new tome" in result.stdout

    @patch("mvge.cli.commands.tome.Path.cwd")
    @patch("mvge.cli.commands.tome.get_factory")
    def test_tome_list_empty(
        self, mock_factory: MagicMock, mock_cwd: MagicMock
    ) -> None:
        mock_factory_instance = MagicMock()
        mock_factory_instance.list_tomes.return_value = []
        mock_factory.return_value = mock_factory_instance
        mock_cwd.return_value = Path("/test")

        result = runner.invoke(tome_app, ["list"])
        assert result.exit_code == 0

    @patch("mvge.cli.commands.tome.Path.cwd")
    @patch("mvge.cli.commands.tome.get_factory")
    def test_tome_list_with_sessions(
        self, mock_factory: MagicMock, mock_cwd: MagicMock
    ) -> None:
        mock_meta = MagicMock()
        mock_meta.id = "tome_abc123"
        mock_meta.created_at = "2024-01-01T12:00:00"
        mock_meta.cwd = "/test"
        mock_meta.active_leaf_id = "leaf_123"

        mock_factory_instance = MagicMock()
        mock_factory_instance.list_tomes.return_value = [mock_meta]
        mock_factory.return_value = mock_factory_instance
        mock_cwd.return_value = Path("/test")

        result = runner.invoke(tome_app, ["list"])
        assert result.exit_code == 0
        assert "tome_abc" in result.stdout

    @patch("mvge.cli.commands.tome.Path.cwd")
    @patch("mvge.cli.commands.tome.get_factory")
    def test_tome_show_not_found(
        self, mock_factory: MagicMock, mock_cwd: MagicMock
    ) -> None:
        mock_factory_instance = MagicMock()
        mock_factory_instance.open_tome.return_value = None
        mock_factory.return_value = mock_factory_instance
        mock_cwd.return_value = Path("/test")

        result = runner.invoke(tome_app, ["show", "nonexistent"])
        assert result.exit_code == 1
        assert "Tome not found" in result.stdout

    @patch("mvge.cli.commands.tome.Path.cwd")
    @patch("mvge.cli.commands.tome.get_factory")
    def test_tome_show_found(
        self, mock_factory: MagicMock, mock_cwd: MagicMock
    ) -> None:
        mock_meta = MagicMock()
        mock_meta.id = "tome_abc123"
        mock_meta.created_at = "2024-01-01T12:00:00"
        mock_meta.cwd = "/test"
        mock_meta.active_leaf_id = "leaf_123"

        mock_entry = MagicMock()
        mock_entry.type.value = "message"
        mock_entry.timestamp = 1786553222.331
        mock_entry.payload = {"key": "value"}

        mock_factory_instance = MagicMock()
        mock_factory_instance.open_tome.return_value = mock_meta
        mock_factory_instance.get_entries.return_value = [mock_entry]
        mock_factory.return_value = mock_factory_instance
        mock_cwd.return_value = Path("/test")

        result = runner.invoke(tome_app, ["show", "tome_abc123"])
        assert result.exit_code == 0
        assert "tome_abc" in result.stdout
        assert "2026-08-12T16:47:02.331000+00:00" in result.stdout

    @patch("mvge.cli.commands.tome.Path.cwd")
    @patch("mvge.cli.commands.tome.get_factory")
    def test_tome_show_format_markdown_matches_export(
        self, mock_factory: MagicMock, mock_cwd: MagicMock
    ) -> None:
        mock_meta = MagicMock()
        mock_meta.id = "tome_abc123"
        mock_meta.created_at = "2024-01-01T12:00:00"
        mock_meta.cwd = "/test"
        mock_meta.parent_tome_id = None
        mock_meta.active_leaf_id = "leaf_123"
        mock_meta.schema_version = "1.0"

        mock_entry = MagicMock()
        mock_entry.type.value = "message"
        mock_entry.timestamp = 1786553222.331
        mock_entry.payload = {"key": "value"}

        mock_factory_instance = MagicMock()
        mock_factory_instance.open_tome.return_value = mock_meta
        mock_factory_instance.get_entries.return_value = [mock_entry]
        mock_factory.return_value = mock_factory_instance
        mock_cwd.return_value = Path("/test")

        show_res = runner.invoke(
            tome_app, ["show", "tome_abc123", "--format", "markdown"]
        )
        export_res = runner.invoke(
            tome_app, ["export", "tome_abc123", "--format", "markdown"]
        )
        assert show_res.exit_code == 0
        assert export_res.exit_code == 0
        assert show_res.stdout == export_res.stdout
        assert "2026-08-12T16:47:02.331000+00:00" in show_res.stdout

    @patch("mvge.cli.commands.tome.Path.cwd")
    @patch("mvge.cli.commands.tome.get_factory")
    def test_tome_show_format_json_matches_export(
        self, mock_factory: MagicMock, mock_cwd: MagicMock
    ) -> None:
        mock_meta = MagicMock()
        mock_meta.id = "tome_abc123"
        mock_meta.created_at = "2024-01-01T12:00:00"
        mock_meta.cwd = "/test"
        mock_meta.parent_tome_id = None
        mock_meta.active_leaf_id = "leaf_123"
        mock_meta.schema_version = "1.0"

        mock_entry = MagicMock()
        mock_entry.id = "entry_123"
        mock_entry.parent_id = None
        mock_entry.type.value = "message"
        mock_entry.timestamp = 1786553222.331
        mock_entry.payload = {"key": "value"}

        mock_factory_instance = MagicMock()
        mock_factory_instance.open_tome.return_value = mock_meta
        mock_factory_instance.get_entries.return_value = [mock_entry]
        mock_factory.return_value = mock_factory_instance
        mock_cwd.return_value = Path("/test")

        show_res = runner.invoke(tome_app, ["show", "tome_abc123", "--format", "json"])
        export_res = runner.invoke(
            tome_app, ["export", "tome_abc123", "--format", "json"]
        )
        assert show_res.exit_code == 0
        assert export_res.exit_code == 0
        assert show_res.stdout == export_res.stdout
        assert "2026-08-12T16:47:02.331000+00:00" in show_res.stdout

    @patch("mvge.cli.commands.tome.Path.cwd")
    @patch("mvge.cli.commands.tome.get_factory")
    def test_tome_show_invalid_format_exits_with_error(
        self, mock_factory: MagicMock, mock_cwd: MagicMock
    ) -> None:
        mock_meta = MagicMock()
        mock_meta.id = "tome_abc123"
        mock_factory_instance = MagicMock()
        mock_factory_instance.open_tome.return_value = mock_meta
        mock_factory.return_value = mock_factory_instance

        result = runner.invoke(tome_app, ["show", "tome_abc123", "--format", "invalid"])
        assert result.exit_code == 1
        assert "Unknown format" in result.stdout

    @patch("mvge.cli.commands.tome.Path.cwd")
    @patch("mvge.cli.commands.tome.get_factory")
    def test_tome_export_json(
        self, mock_factory: MagicMock, mock_cwd: MagicMock
    ) -> None:
        mock_meta = MagicMock()
        mock_meta.id = "tome_abc123"
        mock_meta.created_at = "2024-01-01T12:00:00"
        mock_meta.cwd = "/test"
        mock_meta.parent_tome_id = None
        mock_meta.active_leaf_id = "leaf_123"
        mock_meta.schema_version = "1.0"

        mock_entry = MagicMock()
        mock_entry.id = "entry_123"
        mock_entry.parent_id = None
        mock_entry.type.value = "message"
        mock_entry.timestamp = 123.456
        mock_entry.payload = {"key": "value"}

        mock_factory_instance = MagicMock()
        mock_factory_instance.open_tome.return_value = mock_meta
        mock_factory_instance.get_entries.return_value = [mock_entry]
        mock_factory.return_value = mock_factory_instance
        mock_cwd.return_value = Path("/test")

        result = runner.invoke(tome_app, ["export", "tome_abc123", "--format", "json"])
        assert result.exit_code == 0
        assert "tome_abc123" in result.stdout

    @patch("mvge.cli.commands.tome.Path.cwd")
    @patch("mvge.cli.commands.tome.get_factory")
    def test_tome_export_markdown(
        self, mock_factory: MagicMock, mock_cwd: MagicMock
    ) -> None:
        mock_meta = MagicMock()
        mock_meta.id = "tome_abc123"
        mock_meta.created_at = "2024-01-01T12:00:00"
        mock_meta.cwd = "/test"
        mock_meta.parent_tome_id = None
        mock_meta.active_leaf_id = "leaf_123"
        mock_meta.schema_version = "1.0"

        mock_entry = MagicMock()
        mock_entry.type.value = "message"
        mock_entry.timestamp = 123.456
        mock_entry.payload = {"key": "value"}

        mock_factory_instance = MagicMock()
        mock_factory_instance.open_tome.return_value = mock_meta
        mock_factory_instance.get_entries.return_value = [mock_entry]
        mock_factory.return_value = mock_factory_instance
        mock_cwd.return_value = Path("/test")

        result = runner.invoke(
            tome_app,
            ["export", "tome_abc123", "--format", "markdown"],
        )
        assert result.exit_code == 0
        assert "tome_abc" in result.stdout

    @patch("mvge.cli.commands.tome.Path.cwd")
    @patch("mvge.cli.commands.tome.get_factory")
    def test_tome_export_tool_result_json(
        self, mock_factory: MagicMock, mock_cwd: MagicMock
    ) -> None:
        import json

        mock_meta = MagicMock()
        mock_meta.id = "tome_abc123"
        mock_meta.created_at = "2024-01-01T12:00:00"
        mock_meta.cwd = "/test"
        mock_meta.parent_tome_id = None
        mock_meta.active_leaf_id = "leaf_123"
        mock_meta.schema_version = "1.0"

        mock_entry = MagicMock()
        mock_entry.id = "entry_tool_1"
        mock_entry.parent_id = "entry_prev"
        mock_entry.type.value = "message"
        mock_entry.timestamp = 1786553222.331
        mock_entry.payload = {
            "role": "tool",
            "content": [{"type": "text", "text": "file content here"}],
            "model": None,
            "provider": None,
        }

        mock_factory_instance = MagicMock()
        mock_factory_instance.open_tome.return_value = mock_meta
        mock_factory_instance.get_entries.return_value = [mock_entry]
        mock_factory.return_value = mock_factory_instance
        mock_cwd.return_value = Path("/test")

        result = runner.invoke(tome_app, ["export", "tome_abc123", "--format", "json"])
        assert result.exit_code == 0
        parsed = json.loads(result.stdout)
        assert parsed["metadata"]["id"] == "tome_abc123"
        assert len(parsed["entries"]) == 1
        assert parsed["entries"][0]["payload"]["role"] == "tool"
        assert parsed["entries"][0]["payload"]["content"] == [
            {"type": "text", "text": "file content here"}
        ]

    @patch("mvge.cli.commands.tome.Path.cwd")
    @patch("mvge.cli.commands.tome.get_factory")
    def test_tome_export_tool_result_markdown(
        self, mock_factory: MagicMock, mock_cwd: MagicMock
    ) -> None:
        mock_meta = MagicMock()
        mock_meta.id = "tome_abc123"
        mock_meta.created_at = "2024-01-01T12:00:00"
        mock_meta.cwd = "/test"
        mock_meta.parent_tome_id = None
        mock_meta.active_leaf_id = "leaf_123"
        mock_meta.schema_version = "1.0"

        mock_entry = MagicMock()
        mock_entry.id = "entry_tool_1"
        mock_entry.parent_id = "entry_prev"
        mock_entry.type.value = "message"
        mock_entry.timestamp = 1786553222.331
        mock_entry.payload = {
            "role": "tool",
            "content": [{"type": "text", "text": "file content here"}],
            "model": None,
            "provider": None,
        }

        mock_factory_instance = MagicMock()
        mock_factory_instance.open_tome.return_value = mock_meta
        mock_factory_instance.get_entries.return_value = [mock_entry]
        mock_factory.return_value = mock_factory_instance
        mock_cwd.return_value = Path("/test")

        result = runner.invoke(
            tome_app, ["export", "tome_abc123", "--format", "markdown"]
        )
        assert result.exit_code == 0
        assert "# Tome: tome_abc" in result.stdout
        assert "## message" in result.stdout
        assert '"role": "tool"' in result.stdout
        assert '"text": "file content here"' in result.stdout

    @patch("mvge.cli.commands.tome.Path.cwd")
    @patch("mvge.cli.commands.tome.get_factory")
    def test_tome_export_not_found(
        self, mock_factory: MagicMock, mock_cwd: MagicMock
    ) -> None:
        mock_factory_instance = MagicMock()
        mock_factory_instance.open_tome.return_value = None
        mock_factory.return_value = mock_factory_instance

        result = runner.invoke(tome_app, ["export", "nonexistent"])
        assert result.exit_code == 1
        assert "Tome not found" in result.stdout

    @patch("mvge.cli.commands.tome.Path.cwd")
    @patch("mvge.cli.commands.tome.get_factory")
    def test_tome_create(self, mock_factory: MagicMock, mock_cwd: MagicMock) -> None:
        mock_cwd.return_value = Path("/test")
        mock_write = MagicMock()
        mock_write.tome_id = "tome_abc123"
        mock_write.path = Path("/test/tome_abc123.jsonl")

        mock_factory_instance = MagicMock()
        mock_factory_instance.create_tome.return_value = mock_write
        mock_factory.return_value = mock_factory_instance

        result = runner.invoke(tome_app, ["create"])
        assert result.exit_code == 0
        assert "Created tome: tome_abc" in result.stdout

    @patch("mvge.cli.commands.tome.Path.cwd")
    @patch("mvge.cli.commands.tome.get_factory")
    def test_tome_fork_short_id(
        self, mock_factory: MagicMock, mock_cwd: MagicMock
    ) -> None:
        mock_meta = MagicMock()
        mock_meta.id = "parent_tome_123456"
        mock_meta.cwd = "/test"

        forked_write = MagicMock()
        forked_write.tome_id = "forked_tome_654321"
        forked_write.path = Path("/test/forked_tome_654321.jsonl")

        mock_factory_instance = MagicMock()
        mock_factory_instance.open_tome.return_value = mock_meta
        mock_factory_instance.get_leaf_id.return_value = "leaf_001"
        mock_factory_instance.get_entry.return_value = MagicMock()
        mock_factory_instance.create_branched_tome.return_value = forked_write
        mock_factory.return_value = mock_factory_instance

        result = runner.invoke(tome_app, ["fork", "parent_t"])
        assert result.exit_code == 0
        assert "Forked tome: forked_t" in result.stdout
        assert "Parent: parent_t" in result.stdout

    @patch("mvge.cli.commands.tome.Path.cwd")
    @patch("mvge.cli.commands.tome.get_factory")
    def test_tome_show_unsupported_version_exits_with_error(
        self, mock_factory: MagicMock, mock_cwd: MagicMock
    ) -> None:
        mock_factory_instance = MagicMock()
        mock_factory_instance.open_tome.side_effect = TomeVersionError(9, "bad")
        mock_factory.return_value = mock_factory_instance
        mock_cwd.return_value = Path("/test")

        result = runner.invoke(tome_app, ["show", "v9"])
        assert result.exit_code == 1
        assert "Unsupported tome version" in result.stdout

    @patch("mvge.cli.commands.tome.Path.cwd")
    @patch("mvge.cli.commands.tome.get_factory")
    def test_tome_export_invalid_format_exits_with_error(
        self, mock_factory: MagicMock, mock_cwd: MagicMock
    ) -> None:
        mock_factory_instance = MagicMock()
        mock_factory_instance.open_tome.return_value = _make_meta(
            "tome_abc123", "/test"
        )
        mock_factory_instance.get_entries.return_value = []
        mock_factory.return_value = mock_factory_instance
        mock_cwd.return_value = Path("/test")

        result = runner.invoke(
            tome_app, ["export", "tome_abc123", "--format", "invalid"]
        )
        assert result.exit_code == 1
        assert "Unknown format" in result.stdout

    @patch("mvge.cli.commands.tome.Path.cwd")
    @patch("mvge.cli.commands.tome.get_factory")
    def test_tome_export_writes_output_file(
        self, mock_factory: MagicMock, mock_cwd: MagicMock, tmp_path: Path
    ) -> None:
        mock_factory_instance = MagicMock()
        mock_factory_instance.open_tome.return_value = _make_meta(
            "tome_abc123", "/test"
        )
        mock_factory_instance.get_entries.return_value = []
        mock_factory.return_value = mock_factory_instance
        mock_cwd.return_value = Path("/test")
        output = tmp_path / "export.json"

        result = runner.invoke(
            tome_app, ["export", "tome_abc123", "--output", str(output)]
        )
        assert result.exit_code == 0
        assert output.exists()

    @patch("mvge.cli.commands.tome.Path.cwd")
    @patch("mvge.cli.commands.tome.get_factory")
    def test_tome_create_with_parent_branches(
        self, mock_factory: MagicMock, mock_cwd: MagicMock
    ) -> None:
        mock_cwd.return_value = Path("/test")
        mock_write = MagicMock()
        mock_write.tome_id = "tome_forked1"
        mock_write.path = Path("/test/tome_forked1.jsonl")

        mock_factory_instance = MagicMock()
        mock_factory_instance.create_branched_tome.return_value = mock_write
        mock_factory.return_value = mock_factory_instance

        result = runner.invoke(tome_app, ["create", "--parent", "parent_1"])
        assert result.exit_code == 0
        assert "Created tome: tome_for" in result.stdout
        mock_factory_instance.create_branched_tome.assert_called_once_with(
            parent_tome_id="parent_1", cwd=str(Path("/test"))
        )

    @patch("mvge.cli.commands.tome.Path.cwd")
    @patch("mvge.cli.commands.tome.get_factory")
    def test_tome_create_parent_failure_exits_with_error(
        self, mock_factory: MagicMock, mock_cwd: MagicMock
    ) -> None:
        mock_cwd.return_value = Path("/test")
        mock_factory_instance = MagicMock()
        mock_factory_instance.create_branched_tome.side_effect = ValueError("nope")
        mock_factory.return_value = mock_factory_instance

        result = runner.invoke(tome_app, ["create", "--parent", "missing"])
        assert result.exit_code == 1
        assert "Failed to create tome" in result.stdout

    @patch("mvge.cli.commands.tome.Path.cwd")
    @patch("mvge.cli.commands.tome.get_factory")
    def test_tome_fork_without_leaf_exits_with_error(
        self, mock_factory: MagicMock, mock_cwd: MagicMock
    ) -> None:
        mock_factory_instance = MagicMock()
        mock_factory_instance.open_tome.return_value = _make_meta(
            "parent_tome_1", "/test"
        )
        mock_factory_instance.get_leaf_id.return_value = None
        mock_factory.return_value = mock_factory_instance
        mock_cwd.return_value = Path("/test")

        result = runner.invoke(tome_app, ["fork", "parent_tome_1"])
        assert result.exit_code == 1
        assert "No leaf ID available" in result.stdout

    @patch("mvge.cli.commands.tome.Path.cwd")
    @patch("mvge.cli.commands.tome.get_factory")
    def test_tome_fork_unknown_leaf_exits_with_error(
        self, mock_factory: MagicMock, mock_cwd: MagicMock
    ) -> None:
        mock_factory_instance = MagicMock()
        mock_factory_instance.open_tome.return_value = _make_meta(
            "parent_tome_1", "/test"
        )
        mock_factory_instance.get_entry.return_value = None
        mock_factory.return_value = mock_factory_instance
        mock_cwd.return_value = Path("/test")

        result = runner.invoke(tome_app, ["fork", "parent_tome_1", "--leaf", "nope"])
        assert result.exit_code == 1
        assert "Leaf entry not found" in result.stdout

    def test_tome_verify_missing_tome_reports_issue(self, tmp_path: Path) -> None:
        with patch("mvge.cli.commands.tome.get_tome_dir", return_value=tmp_path):
            result = runner.invoke(tome_app, ["verify", "missing"])
            assert result.exit_code == 1
            assert "integrity issue" in result.stdout
            assert "Line 0:" in result.stdout

    def test_format_timestamp_passes_through_strings(self) -> None:
        assert _format_timestamp("already-a-string") == "already-a-string"

    def test_tome_fork_with_leaf_ancestors(self, tmp_path: Path) -> None:
        import tempfile

        def _msg(
            write_handle, entry_id: str, role: str, content: str, parent_id: str | None
        ) -> TomeEntry:
            entry = TomeEntry(
                id=entry_id,
                parent_id=parent_id,
                type=TomeEntryType.MESSAGE,
                timestamp=1000.0,
                payload={"role": role, "content": content},
            )
            write_handle.append(entry)
            return entry

        with tempfile.TemporaryDirectory() as tmp_tome_dir:
            tome_dir = Path(tmp_tome_dir)
            factory = TomeHandleFactory(tome_dir)
            write = factory.create_tome("/tmp")
            tome_id = write.tome_id

            e1 = _msg(write, "e1", "user", "turn 1 req", None)
            write.append_leaf(e1.id)

            e2 = _msg(write, "e2", "assistant", "turn 1 resp", e1.id)
            write.append_leaf(e2.id)

            e3 = _msg(write, "e3", "user", "turn 2 req", e2.id)
            write.append_leaf(e3.id)

            with patch("mvge.cli.commands.tome.get_tome_dir", return_value=tome_dir):
                result = runner.invoke(tome_app, ["fork", tome_id, "--leaf", e2.id])
                assert result.exit_code == 0
                assert "Forked tome:" in result.stdout

                # Verify on disk
                fresh_factory = TomeHandleFactory(tome_dir)
                tomes = fresh_factory.list_tomes()
                forked_meta = next(t for t in tomes if t.id != tome_id)
                assert forked_meta.parent_tome_id == tome_id
                assert forked_meta.active_leaf_id == e2.id

                entries = fresh_factory.get_entries(forked_meta.id)
                assert len(entries) == 2
                assert entries[0].id == e1.id
                assert entries[0].parent_id is None
                assert entries[1].id == e2.id
                assert entries[1].parent_id == e1.id


def test_render_tome_list_ellipsizes_long_cwd() -> None:
    long_cwd = "/a" + "/b" * 90
    meta = _make_meta("tome_abc123", long_cwd)
    output = _render_tome_list([meta], ascii_only=False)
    assert "…" in output
    assert long_cwd not in output


def test_render_tome_list_short_cwd_kept_full() -> None:
    meta = _make_meta("tome_abc123", "/short/cwd")
    output = _render_tome_list([meta], ascii_only=False)
    assert "/short/cwd" in output
    assert "…" not in output


def test_render_tome_list_ascii_long_cwd_uses_ascii_ellipsis() -> None:
    long_cwd = "/a" + "/b" * 90
    meta = _make_meta("tome_abc123", long_cwd)
    output = _render_tome_list([meta], ascii_only=True)
    assert "..." in output
    assert "…" not in output
    assert all(ord(ch) < 128 for ch in output)


def test_tome_verify_help() -> None:
    result = runner.invoke(tome_app, ["verify", "--help"])
    assert result.exit_code == 0
    assert "Verify integrity of a tome session file" in result.stdout


def test_tome_verify_valid_session(tmp_path: Path) -> None:
    factory = TomeHandleFactory(tmp_path)
    tome_id = factory.create_tome("/workspace").tome_id
    factory.open_write(tome_id).append(
        TomeEntry(
            id="m1",
            parent_id=None,
            type=TomeEntryType.MESSAGE,
            timestamp=1000.0,
            payload={"role": "user", "content": "hi"},
        )
    )

    with patch("mvge.cli.commands.tome.get_tome_dir", return_value=tmp_path):
        result = runner.invoke(tome_app, ["verify", tome_id])
        assert result.exit_code == 0
        assert "is valid" in result.stdout


def test_tome_verify_corrupted_session(tmp_path: Path) -> None:
    factory = TomeHandleFactory(tmp_path)
    tome_id = factory.create_tome("/workspace").tome_id
    tome_file = factory.tome_file(tome_id)
    with tome_file.open("a", encoding="utf-8") as f:
        f.write('{"id": "bad", "truncated": true\n')

    with patch("mvge.cli.commands.tome.get_tome_dir", return_value=tmp_path):
        result = runner.invoke(tome_app, ["verify", tome_id])
        assert result.exit_code == 1
        assert "integrity issue" in result.stdout
        assert "Line 2:" in result.stdout


def test_tome_replay_help() -> None:
    result = runner.invoke(tome_app, ["replay", "--help"])
    assert result.exit_code == 0
    assert "Replay" in result.stdout


def test_tome_export_atif(tmp_path: Path) -> None:
    factory = TomeHandleFactory(tmp_path)
    write = factory.create_tome("/workspace", tome_id="tome_atif_1")
    e1 = TomeEntry(
        id="m1",
        parent_id=None,
        type=TomeEntryType.MESSAGE,
        timestamp=1000.0,
        payload={"role": "user", "content": "hi"},
    )
    write.append(e1)
    write.append_leaf(e1.id)

    with patch("mvge.cli.commands.tome.get_tome_dir", return_value=tmp_path):
        result = runner.invoke(tome_app, ["export", "tome_atif_1", "--format", "atif"])
        assert result.exit_code == 0
        parsed = json.loads(result.stdout)
        assert parsed["trajectory_id"] == "tome_atif_1"
        assert len(parsed["steps"]) == 1
        assert parsed["steps"][0]["role"] == "user"


def test_tome_replay_execution(tmp_path: Path) -> None:
    factory = TomeHandleFactory(tmp_path)
    write = factory.create_tome("/workspace", tome_id="tome_replay_test")
    e1 = TomeEntry(
        id="m1",
        parent_id=None,
        type=TomeEntryType.MESSAGE,
        timestamp=1000.0,
        payload={"role": "user", "content": "solve x"},
    )
    e2 = TomeEntry(
        id="m2",
        parent_id="m1",
        type=TomeEntryType.MESSAGE,
        timestamp=1001.0,
        payload={
            "role": "assistant",
            "content": [
                {"type": "contemplation", "thinking": "Let's think"},
                {
                    "type": "spell_cast",
                    "id": "c1",
                    "spell": "solver",
                    "args": {"eq": "x=5"},
                },
                {"type": "text", "text": "x is 5"},
            ],
        },
    )
    e3 = TomeEntry(
        id="m3",
        parent_id="m2",
        type=TomeEntryType.MESSAGE,
        timestamp=1002.0,
        payload={
            "role": "tool",
            "tool_call_id": "c1",
            "content": "ok",
        },
    )
    write.append(e1)
    write.append_leaf(e1.id)
    write.append(e2)
    write.append_leaf(e2.id)
    write.append(e3)
    write.append_leaf(e3.id)

    with patch("mvge.cli.commands.tome.get_tome_dir", return_value=tmp_path):
        result = runner.invoke(tome_app, ["replay", "tome_replay_test"])
        assert result.exit_code == 0
        assert "Replaying Tome: tome_rep" in result.stdout
        assert "USER" in result.stdout
        assert "ASSISTANT" in result.stdout
        assert "Contemplation: Let's think" in result.stdout
        assert "Tool Call: solver" in result.stdout
        assert "Call ID: c1" in result.stdout
        assert "x is 5" in result.stdout


def test_render_tome_export_atif_no_factory() -> None:
    from mvge.cli.commands.tome import _render_tome_export

    meta = _make_meta("tome_fallback", "/workspace")
    e1 = TomeEntry(
        id="m1",
        parent_id=None,
        type=TomeEntryType.MESSAGE,
        timestamp=1000.0,
        payload={"role": "user", "content": "hi"},
    )
    out = _render_tome_export(meta, [e1], "atif", factory=None)
    parsed = json.loads(out)
    assert parsed["trajectory_id"] == "tome_fallback"
    assert len(parsed["steps"]) == 1


class TestTomeExplicitForeignPaths:
    """Regression tests: operating on session files by explicit path.

    Covers BUG-1 (raw traceback on foreign paths), BUG-2 (silent wrong-file
    reads when the header id collides with an in-dir tome), and BUG-3
    (bogus 'Header ID mismatch' from verify). Per the v0.4.9 design the
    header id is authoritative for explicit paths; the filename stem is
    irrelevant.
    """

    def _foreign_file(self, tmp_path: Path) -> tuple[Path, Path]:
        """Return (sessions_dir, foreign_file): a Tome v1 session file whose
        stem ('my.pi.session.backup') differs from its header id
        ('dotheaderid'), living outside the session dir."""
        sessions_dir = tmp_path / "sessions"
        factory = TomeHandleFactory(sessions_dir)
        write = factory.create_tome("/workspace", tome_id="dotheaderid")
        write.append(
            TomeEntry(
                id="m1",
                parent_id=None,
                type=TomeEntryType.MESSAGE,
                timestamp=1000.0,
                payload={"role": "user", "content": "hello"},
            )
        )
        write.append(
            TomeEntry(
                id="m2",
                parent_id="m1",
                type=TomeEntryType.MESSAGE,
                timestamp=1001.0,
                payload={"role": "assistant", "content": "hi there"},
            )
        )
        write.append_leaf("m2")
        foreign = tmp_path / "foreign" / "my.pi.session.backup.jsonl"
        foreign.parent.mkdir(parents=True)
        shutil.copy(sessions_dir / "dotheaderid.jsonl", foreign)
        # Truly foreign: the header id must NOT exist in the session dir,
        # otherwise the id re-resolves to the in-dir file (the BUG-2 setup).
        (sessions_dir / "dotheaderid.jsonl").unlink()
        return sessions_dir, foreign

    def test_show_explicit_foreign_path(self, tmp_path: Path) -> None:
        sessions_dir, foreign = self._foreign_file(tmp_path)
        with patch(
            "mvge.cli.commands.tome.get_factory",
            return_value=TomeHandleFactory(sessions_dir),
        ):
            result = runner.invoke(tome_app, ["show", str(foreign)])
        assert result.exit_code == 0, result.output
        assert "Entries: 3" in result.output

    def test_show_explicit_path_reads_pointed_file_not_id_collision(
        self, tmp_path: Path
    ) -> None:
        """BUG-2: when the header id collides with an in-dir tome, the CLI
        must read the file the user pointed at, not the in-dir original."""
        sessions_dir = tmp_path / "sessions"
        factory = TomeHandleFactory(sessions_dir)
        factory.create_tome("/workspace", tome_id="original0")  # 0 entries
        pointed = tmp_path / "foreign" / "normal-copy.jsonl"
        pointed.parent.mkdir(parents=True)
        shutil.copy(sessions_dir / "original0.jsonl", pointed)
        # Append a marker entry to the COPY via a factory rooted elsewhere.
        other = TomeHandleFactory(tmp_path / "other")
        writer = other.open_write(str(pointed))
        writer.append(
            TomeEntry(
                id="mx",
                parent_id=None,
                type=TomeEntryType.MESSAGE,
                timestamp=1000.0,
                payload={"role": "user", "content": "marker"},
            )
        )
        assert len(other.get_entries(str(pointed))) == 1
        assert len(factory.get_entries("original0")) == 0

        with patch(
            "mvge.cli.commands.tome.get_factory",
            return_value=TomeHandleFactory(sessions_dir),
        ):
            result = runner.invoke(tome_app, ["show", str(pointed)])
        assert result.exit_code == 0, result.output
        assert "Entries: 1" in result.output

    def test_export_json_explicit_foreign_path(self, tmp_path: Path) -> None:
        sessions_dir, foreign = self._foreign_file(tmp_path)
        with patch(
            "mvge.cli.commands.tome.get_factory",
            return_value=TomeHandleFactory(sessions_dir),
        ):
            result = runner.invoke(
                tome_app, ["export", str(foreign), "--format", "json"]
            )
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert parsed["metadata"]["id"] == "dotheaderid"
        assert len(parsed["entries"]) == 3

    def test_export_atif_explicit_foreign_path(self, tmp_path: Path) -> None:
        sessions_dir, foreign = self._foreign_file(tmp_path)
        with patch(
            "mvge.cli.commands.tome.get_factory",
            return_value=TomeHandleFactory(sessions_dir),
        ):
            result = runner.invoke(
                tome_app, ["export", str(foreign), "--format", "atif"]
            )
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert parsed["trajectory_id"] == "dotheaderid"
        assert len(parsed["steps"]) == 2

    def test_replay_explicit_foreign_path(self, tmp_path: Path) -> None:
        sessions_dir, foreign = self._foreign_file(tmp_path)
        with patch(
            "mvge.cli.commands.tome.get_factory",
            return_value=TomeHandleFactory(sessions_dir),
        ):
            result = runner.invoke(tome_app, ["replay", str(foreign)])
        assert result.exit_code == 0, result.output
        assert "Replaying Tome: dotheade" in result.output
        assert "hello" in result.output

    def test_fork_explicit_foreign_path(self, tmp_path: Path) -> None:
        sessions_dir, foreign = self._foreign_file(tmp_path)
        with patch(
            "mvge.cli.commands.tome.get_factory",
            return_value=TomeHandleFactory(sessions_dir),
        ):
            result = runner.invoke(tome_app, ["fork", str(foreign)])
        assert result.exit_code == 0, result.output
        assert "Forked tome:" in result.output

    def test_verify_explicit_foreign_path(self, tmp_path: Path) -> None:
        sessions_dir, foreign = self._foreign_file(tmp_path)
        with patch(
            "mvge.cli.commands.tome.get_factory",
            return_value=TomeHandleFactory(sessions_dir),
        ):
            result = runner.invoke(tome_app, ["verify", str(foreign)])
        assert result.exit_code == 0, result.output
        assert "is valid" in result.output


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
