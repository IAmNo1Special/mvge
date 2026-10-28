from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any

import typer
from rich import box
from rich.console import Console
from rich.table import Table

from mvge.agent.agent_name import resolve_active_agent_name
from mvge.cli.console import clip_text, format_error, get_console, is_utf8_stream
from mvge.cli.dynamic_commands import (
    cli_project_dir,
    configured_rune_layers,
    get_extension_dirs,
)
from mvge.core.constants import DEFAULT_AGENT_NAME
from mvge.core.layers import sessions_dir
from mvge.runes.codecs import load_session_codecs
from mvge.tome.handle import TomeHandleFactory
from mvge.tome.types import (
    TomeEntry,
    TomeEntryType,
    TomeMetadata,
    TomeVersionError,
)

logger = logging.getLogger(__name__)

console = get_console()
tome_app = typer.Typer(name="tome", help="Session tome management")

CWD_MAX_WIDTH = 50


@tome_app.callback(invoke_without_command=True)
def tome_callback(ctx: typer.Context) -> None:
    """Session tome management."""
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())


def get_tome_dir() -> Path:
    tome_dir = sessions_dir()
    tome_dir.mkdir(parents=True, exist_ok=True)
    return tome_dir


def get_factory(
    agent_name: str = DEFAULT_AGENT_NAME,
    *,
    project_dir: Path | None = None,
    global_dir: Path | None = None,
    extension_dir: str | None = None,
) -> TomeHandleFactory:
    """Defaults are the loader's defaults, not an empty resolution.

    ``project_dir`` defaults to ``None`` but is replaced with the CLI's own
    project anchor below, because every caller here is running *in* a project
    and Rune loading already resolves with that anchor. A bare ``get_factory()``
    that omitted it would resolve a smaller stack than the session that wrote
    the tome.
    """
    """Tome factory with rune-provided session codecs discovered.

    Codecs are opt-in through ``session_codecs`` in rune manifests; the
    built-in Tome v1 codec always stays first.

    A codec is a Rune capability, so this asks the same resolver Rune
    loading asks. Resolving with fewer inputs than loading would mean a codec
    that loads in a session is missing when the same tome is inspected from
    the CLI -- the file would then read as empty rather than fail loudly.
    """
    codecs, diagnostics = load_session_codecs(
        get_extension_dirs(
            agent_name,
            project_dir=project_dir if project_dir is not None else cli_project_dir(),
            extras=configured_rune_layers(agent_name),
            global_dir=global_dir,
            extension_dir=extension_dir,
        )
    )
    for diag in diagnostics:
        logger.warning(
            "Session codec issue in rune %s: %s", diag.rune_name, diag.message
        )
    return TomeHandleFactory(get_tome_dir(), codecs=codecs)


@tome_app.command("list")
def tome_list() -> None:
    """List all tomes."""
    ascii_only = not is_utf8_stream(sys.stdout)

    console.print(_render_tome_list(get_factory().list_tomes(), ascii_only=ascii_only))


def _render_tome_list(metas: list[TomeMetadata], ascii_only: bool = False) -> str:
    """Render tome metadata as a Rich table string (for testing/assertions)."""
    out = StringIO()
    box_style = box.ASCII if ascii_only else box.HEAVY_HEAD
    render_console = Console(file=out, width=150, record=True, force_terminal=False)
    table = Table(title="Mvge Tomes", box=box_style)
    table.add_column("ID", style="cyan", width=10)
    table.add_column("Created", style="green", width=21)
    table.add_column("CWD", style="yellow", width=CWD_MAX_WIDTH)
    table.add_column("Active Leaf", style="magenta", width=15)

    for meta in metas:
        table.add_row(
            meta.id[:8],
            meta.created_at[:19],
            clip_text(meta.cwd, CWD_MAX_WIDTH - 2, ascii_only),
            meta.active_leaf_id or "-",
        )

    render_console.print(table)
    return out.getvalue()


def _format_timestamp(ts: Any) -> str:
    """Format a timestamp float/int as ISO 8601 string."""
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, UTC).isoformat()
    return str(ts)


def _render_tome_export(
    meta: TomeMetadata,
    entries: list[TomeEntry],
    format: str,
    factory: TomeHandleFactory | None = None,
    tome_ref: str | None = None,
    agent_name: str | None = None,
) -> str:
    """Render tome metadata and entries as JSON, Markdown, or ATIF trajectory.

    ``tome_ref`` is the original locator (id or explicit path) the user gave;
    it is threaded through so explicit paths keep resolving to the pointed-at
    file instead of being re-resolved through the header id.

    ``agent_name`` is resolved by the command from the engine's agent-name
    module -- a Tome never records it, so the CLI supplies the Mvge this
    invocation is running rather than letting the export invent one.
    """
    if format in ("atif", "trajectory"):
        if factory is not None:
            data = factory.export_atif_trajectory(
                tome_ref or meta.id, agent_name or resolve_active_agent_name()
            )
            return json.dumps(data, indent=2)
        steps = []
        for e in entries:
            if e.type == TomeEntryType.MESSAGE:
                steps.append(
                    {
                        "step_id": e.id,
                        "role": e.payload.get("role", "user"),
                        "content": str(e.payload.get("content", "")),
                        "timestamp": e.timestamp,
                    }
                )
        return json.dumps(
            {
                "trajectory_id": meta.id,
                "agent_name": resolve_active_agent_name(),
                "model": meta.model or "",
                "created_at": meta.created_at,
                "steps": steps,
                "metrics": {},
                "completed": True,
            },
            indent=2,
        )
    elif format == "json":
        data = {
            "metadata": {
                "id": meta.id,
                "created_at": meta.created_at,
                "cwd": meta.cwd,
                "parent_tome_id": meta.parent_tome_id,
                "active_leaf_id": meta.active_leaf_id,
                "schema_version": meta.schema_version,
            },
            "entries": [
                {
                    "id": e.id,
                    "parent_id": e.parent_id,
                    "type": e.type.value if hasattr(e.type, "value") else str(e.type),
                    "timestamp": _format_timestamp(e.timestamp),
                    "payload": e.payload,
                }
                for e in entries
            ],
        }
        return json.dumps(data, indent=2)
    elif format in ("markdown", "md"):
        lines = [
            f"# Tome: {meta.id[:8]}",
            f"Created: {meta.created_at}",
            f"CWD: {meta.cwd}",
            "",
        ]
        for entry in entries:
            entry_type = (
                entry.type.value if hasattr(entry.type, "value") else str(entry.type)
            )
            lines.append(f"## {entry_type} ({_format_timestamp(entry.timestamp)})")
            lines.append("```json")
            lines.append(json.dumps(entry.payload, indent=2))
            lines.append("```")
            lines.append("")
        return "\n".join(lines)
    else:
        raise ValueError(f"Unknown format: {format}")


def _open_tome_or_exit(factory: TomeHandleFactory, tome_id: str) -> TomeMetadata:
    try:
        meta = factory.open_tome(tome_id)
    except TomeVersionError as e:
        console.print(format_error(f"Unsupported tome version: {e}"))
        raise typer.Exit(1) from None
    if meta is None:
        console.print(format_error(f"Tome not found: {tome_id}"))
        raise typer.Exit(1) from None
    return meta


@tome_app.command("show")
def tome_show(
    tome_id: str = typer.Argument(..., help="Tome ID to show"),
    format: str | None = typer.Option(
        None,
        "--format",
        "-f",
        help="Output format (json, markdown, md, atif, trajectory)",
    ),
) -> None:
    """Show tome details."""
    factory = get_factory()
    meta = _open_tome_or_exit(factory, tome_id)

    # Thread the user's locator through: an explicit path must keep
    # resolving to the pointed-at file, never re-resolve via the header id
    # (which may not exist in the session dir, or may collide with one).
    tome_entries = factory.get_entries(tome_id)

    if format is not None:
        try:
            output_text = _render_tome_export(
                meta, tome_entries, format, factory=factory, tome_ref=tome_id
            )
            console.print(output_text)
            return
        except ValueError:
            console.print(format_error(f"Unknown format: {format}"))
            raise typer.Exit(1) from None

    console.print(f"[bold]Tome:[/bold] {meta.id[:8]}")
    console.print(f"[bold]Created:[/bold] {meta.created_at}")
    console.print(f"[bold]CWD:[/bold] {meta.cwd}")
    console.print(f"[bold]Active Leaf:[/bold] {meta.active_leaf_id or '-'}")
    console.print(f"[bold]Entries:[/bold] {len(tome_entries)}")
    console.print()

    for entry in tome_entries:
        entry_type = (
            entry.type.value if hasattr(entry.type, "value") else str(entry.type)
        )
        console.print(f"  [{entry_type}] {_format_timestamp(entry.timestamp)}")
        console.print(f"    {entry.payload}")


@tome_app.command("export")
def tome_export(
    tome_id: str = typer.Argument(..., help="Tome ID to export"),
    format: str = typer.Option(
        "json",
        "--format",
        "-f",
        help="Export format (json, markdown, md, atif, trajectory)",
    ),
    output: str | None = typer.Option(None, "--output", "-o", help="Output file"),
) -> None:
    """Export a tome to JSON, Markdown, or ATIF trajectory."""
    factory = get_factory()
    meta = _open_tome_or_exit(factory, tome_id)

    entries = factory.get_entries(tome_id)

    try:
        output_text = _render_tome_export(
            meta, entries, format, factory=factory, tome_ref=tome_id
        )
    except ValueError:
        console.print(format_error(f"Unknown format: {format}"))
        raise typer.Exit(1) from None

    if output:
        Path(output).write_text(output_text, encoding="utf-8")
        console.print(f"[green]Exported to {output}[/green]")
    else:
        console.print(output_text)


@tome_app.command("replay")
def tome_replay(
    tome_id: str = typer.Argument(..., help="Tome ID to replay"),
) -> None:
    """Replay a session's turns sequentially."""
    factory = get_factory()
    meta = _open_tome_or_exit(factory, tome_id)
    steps = factory.replay_tome_trajectory(tome_id)

    console.print(
        f"[bold cyan]Replaying Tome:[/bold cyan] {meta.id[:8]} "
        f"[dim]({meta.model or 'unknown'})[/dim]"
    )
    console.print(f"[dim]Created: {meta.created_at} | CWD: {meta.cwd}[/dim]\n")

    for i, step in enumerate(steps, 1):
        role_style = (
            "green"
            if step.role == "user"
            else ("cyan" if step.role == "assistant" else "yellow")
        )
        role_tag = step.role.upper()
        time_str = _format_timestamp(step.timestamp)
        console.print(
            f"[{role_style}][Step {i}] {role_tag}[/{role_style}] "
            f"[dim]({time_str})[/dim]"
        )
        if step.reasoning_content:
            console.print(
                f"  [dim italic]Contemplation: {step.reasoning_content}[/dim italic]"
            )
        if step.content:
            console.print(f"  {step.content}")
        if step.tool_calls:
            for call in step.tool_calls:
                args_json = json.dumps(call.get("args", {}))
                console.print(
                    f"  [magenta]Tool Call:[/magenta] {call.get('name')} "
                    f"[dim]{args_json}[/dim]"
                )
        if step.tool_call_id:
            console.print(f"  [dim]Call ID: {step.tool_call_id}[/dim]")
        console.print()


@tome_app.command("create")
def tome_create(
    cwd: str | None = typer.Option(
        None, "--cwd", help="Working directory (defaults to current)"
    ),
    parent: str | None = typer.Option(None, "--parent", help="Parent tome ID"),
) -> None:
    """Create a new tome."""
    factory = get_factory()

    if cwd is None:
        cwd = str(Path.cwd())

    if parent is not None:
        try:
            handle = factory.create_branched_tome(parent_tome_id=parent, cwd=cwd)
        except (KeyError, ValueError) as e:
            console.print(format_error(f"Failed to create tome: {e}"))
            raise typer.Exit(1) from e
    else:
        handle = factory.create_tome(cwd)
    console.print(f"[green]Created tome: {handle.tome_id[:8]}[/green]")
    console.print(f"[dim]File: {handle.path}[/dim]")


@tome_app.command("fork")
def tome_fork(
    tome_id: str = typer.Argument(..., help="Tome ID to fork from"),
    leaf_id: str | None = typer.Option(
        None, "--leaf", "-l", help="Leaf entry ID to fork at (default: current leaf)"
    ),
) -> None:
    """Fork a tome, creating a new branched tome."""
    factory = get_factory()
    meta = _open_tome_or_exit(factory, tome_id)

    target_leaf = leaf_id or factory.get_leaf_id(tome_id)
    if target_leaf is None:
        console.print(format_error("No leaf ID available. Specify --leaf."))
        raise typer.Exit(1)

    if factory.get_entry(tome_id, target_leaf) is None:
        console.print(format_error(f"Leaf entry not found: {target_leaf}"))
        raise typer.Exit(1)

    try:
        forked = factory.create_branched_tome(
            parent_tome_id=tome_id,
            cwd=meta.cwd,
            fork_from_leaf_id=target_leaf,
        )
        console.print(f"[green]Forked tome: {forked.tome_id[:8]}[/green]")
        console.print(f"[dim]File: {forked.path}[/dim]")
        console.print(f"[dim]Parent: {meta.id[:8]}[/dim]")
    except (KeyError, ValueError) as e:
        console.print(format_error(f"Failed to fork tome: {e}"))
        raise typer.Exit(1) from e


@tome_app.command("verify")
def tome_verify(
    tome_id: str = typer.Argument(..., help="Tome ID to verify"),
) -> None:
    """Verify integrity of a tome session file."""
    factory = get_factory()

    try:
        report = factory.verify_integrity(tome_id)
    except ValueError as e:
        console.print(format_error(f"Failed to verify tome: {e}"))
        raise typer.Exit(1) from e
    if report.valid:
        console.print(f"[green]Tome {report.tome_id[:8]} is valid.[/green]")
        console.print(
            f"[dim]Total lines: {report.total_lines}, "
            f"Valid entries: {report.valid_entries_count}[/dim]"
        )
    else:
        console.print(
            format_error(
                f"Tome {report.tome_id} has {len(report.issues)} integrity issue(s):"
            )
        )
        for issue in report.issues:
            console.print(f"  [red]Line {issue.line_number}:[/red] {issue.message}")
        raise typer.Exit(1)
