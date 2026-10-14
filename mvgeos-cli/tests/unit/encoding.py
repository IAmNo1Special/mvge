from __future__ import annotations

import io
import sys
from unittest.mock import patch

from mvgeos_agent.snapshot import (
    RuntimeSnapshot,
    SnapshotRune,
    SnapshotSkill,
    SnapshotSpell,
    SpellSource,
)
from typer.testing import CliRunner

from mvgeos_cli.commands.info import _render_snapshot, info
from mvgeos_cli.console import clip_text, configure_streams, get_console, is_utf8_stream
from mvgeos_cli.main import app

runner = CliRunner()


def _make_simple_snapshot() -> RuntimeSnapshot:
    return RuntimeSnapshot(
        agent_name="encoding-agent",
        model="nvidia/nemotron-3-ultra-550b-a55b:free",
        spells=[
            SnapshotSpell(
                name="bash",
                description="Run shell command",
                source=SpellSource.BUILTIN,
                parameters={},
            ),
        ],
    )


def test_is_utf8_stream_with_utf8() -> None:
    buf = io.BytesIO()
    stream = io.TextIOWrapper(buf, encoding="utf-8")
    assert is_utf8_stream(stream) is True


def test_is_utf8_stream_with_cp1252() -> None:
    buf = io.BytesIO()
    stream = io.TextIOWrapper(buf, encoding="cp1252")
    assert is_utf8_stream(stream) is False


def test_render_snapshot_ascii_only() -> None:
    snap = _make_simple_snapshot()
    output = _render_snapshot(snap, ascii_only=True)
    assert "+---" in output or "+=" in output or "|" in output
    assert "encoding-agent" in output
    # Ensure no non-ASCII box characters (e.g. ┌, ─, │) are present
    assert all(ord(ch) < 128 for ch in output)


def test_render_snapshot_utf8_default() -> None:
    snap = _make_simple_snapshot()
    output = _render_snapshot(snap, ascii_only=False)
    assert "encoding-agent" in output
    # UTF-8 table rendering produces characters outside 0x00-0x7F
    assert any(ord(ch) >= 128 for ch in output)


def test_info_cp1252_stream_execution() -> None:
    snap = _make_simple_snapshot()
    with patch("mvgeos_cli.commands.info._assemble") as mock_assemble:
        mock_assemble.return_value = snap

        buf = io.BytesIO()
        cp1252_stream = io.TextIOWrapper(buf, encoding="cp1252", errors="strict")

        with patch.object(sys, "stdout", cp1252_stream):
            info(ctx=None, agent_name="default-mvge", extension_dir=None, output=None)

        rendered = buf.getvalue().decode("cp1252")
        assert "encoding-agent" in rendered


def test_cli_help_no_em_dash() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "\u2014" not in result.output
    assert "MvgeOS - a Python-based AI coding agent" in result.output


def test_get_console_non_utf8_safe_box() -> None:
    buf = io.BytesIO()
    cp1252_stream = io.TextIOWrapper(buf, encoding="cp1252")
    c = get_console(file=cp1252_stream)
    assert c.safe_box is True


def test_configure_streams_executes() -> None:
    # Verify configure_streams runs without error
    configure_streams()


def test_clip_text_short_unchanged() -> None:
    assert clip_text("/short/path", 50) == "/short/path"
    assert clip_text("-", 50) == "-"


def test_clip_text_utf8_ellipsis() -> None:
    long_path = "/a" + "/b" * 80
    clipped = clip_text(long_path, 50)
    assert clipped.endswith("…")
    assert "…" not in long_path
    assert long_path not in clipped
    assert len(clipped) == 50


def test_clip_text_ascii_ellipsis() -> None:
    long_path = "/a" + "/b" * 80
    clipped = clip_text(long_path, 50, ascii_only=True)
    assert clipped.endswith("...")
    assert "…" not in clipped
    assert long_path not in clipped
    assert len(clipped) == 50


def test_clip_text_zero_width_unchanged() -> None:
    assert clip_text("/a/b/c", 0) == "/a/b/c"


def _make_rune_snapshot() -> RuntimeSnapshot:
    return RuntimeSnapshot(
        agent_name="path-agent",
        model="nvidia/nemotron-3-ultra-550b-a55b:free",
        runes=[
            SnapshotRune(
                name="long_rune",
                version="1.0.0",
                description="A rune with a very long path",
                scope="user",
                path="/a" + "/b" * 90,
                enabled=True,
                hooks=["turn_start"],
                entry_point="main.py",
            ),
        ],
        skills=[
            SnapshotSkill(
                name="long_skill",
                description="A skill with a very long path",
                scope="user",
                path="/x" + "/y" * 90,
                version="2.0.0",
            ),
        ],
    )


def test_render_snapshot_ellipsizes_long_path() -> None:
    snap = _make_rune_snapshot()
    output = _render_snapshot(snap, ascii_only=False)
    assert "…" in output
    assert "/a" + "/b" * 90 not in output
    assert "/x" + "/y" * 90 not in output


def test_render_snapshot_short_path_kept_full() -> None:
    snap = RuntimeSnapshot(
        agent_name="path-agent",
        model="nvidia/nemotron-3-ultra-550b-a55b:free",
        runes=[
            SnapshotRune(
                name="rune",
                version="1.0.0",
                description="desc",
                scope="user",
                path="/short/rune/path",
                enabled=True,
            ),
        ],
    )
    output = _render_snapshot(snap, ascii_only=False)
    assert "/short/rune/path" in output
    assert "…" not in output


def test_render_snapshot_ascii_long_path_uses_ascii_ellipsis() -> None:
    snap = _make_rune_snapshot()
    output = _render_snapshot(snap, ascii_only=True)
    assert "..." in output
    assert "…" not in output
    assert all(ord(ch) < 128 for ch in output)
