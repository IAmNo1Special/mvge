from __future__ import annotations

import asyncio
import sys
from io import StringIO
from pathlib import Path

import typer
from mvgeos_agent.snapshot import RuntimeSnapshot
from rich import box
from rich.console import Console
from rich.table import Table

from mvgeos_cli.agent_name import resolve_agent_name
from mvgeos_cli.commands.build import _assemble
from mvgeos_cli.console import clip_text, format_error, get_console, is_utf8_stream

console = get_console()

info_app = typer.Typer(
    name="info",
    help="Display the runtime snapshot as rich tables (read-only).",
)

PATH_MAX_WIDTH = 50


def _clip_path(value: str, ascii_only: bool) -> str:
    return clip_text(value, PATH_MAX_WIDTH - 2, ascii_only)


def _render_snapshot(snap: RuntimeSnapshot, ascii_only: bool = False) -> str:
    """Render a RuntimeSnapshot as human-readable rich tables.

    Returns the rendered text so callers can assert on it in tests.
    """
    out = StringIO()
    box_style = box.ASCII if ascii_only else box.HEAVY_HEAD
    render_console = Console(file=out, width=200, record=True, force_terminal=False)

    render_console.print(f"[bold]Agent:[/bold] {snap.agent_name}")
    render_console.print(f"[bold]Model:[/bold] {snap.model}")
    render_console.print()

    _render_spells(render_console, snap, box_style=box_style, ascii_only=ascii_only)
    _render_runes(render_console, snap, box_style=box_style, ascii_only=ascii_only)
    _render_config(render_console, snap, box_style=box_style, ascii_only=ascii_only)
    _render_prompt(render_console, snap, box_style=box_style, ascii_only=ascii_only)
    _render_skills(render_console, snap, box_style=box_style, ascii_only=ascii_only)
    _render_diagnostics(
        render_console, snap, box_style=box_style, ascii_only=ascii_only
    )

    return out.getvalue()


def _render_spells(
    console: Console,
    snap: RuntimeSnapshot,
    box_style: box.Box | None = None,
    ascii_only: bool = False,
) -> None:
    table = Table(title="Spells", show_lines=False, box=box_style)
    table.add_column("Name", style="cyan")
    table.add_column("Source", style="green")
    table.add_column("Source Rune", style="yellow")
    table.add_column("Description")

    for spell in snap.spells:
        source_rune = spell.source_rune if spell.source_rune is not None else "-"
        table.add_row(
            spell.name,
            spell.source.value,
            source_rune,
            spell.description or "-",
        )

    if not snap.spells:
        table.add_row("[dim](none)", "", "", "")

    console.print(table)


def _render_runes(
    console: Console,
    snap: RuntimeSnapshot,
    box_style: box.Box | None = None,
    ascii_only: bool = False,
) -> None:
    table = Table(title="Runes", show_lines=False, box=box_style)
    table.add_column("Name", style="cyan")
    table.add_column("Scope", style="green")
    table.add_column("Version", style="yellow")
    table.add_column("Enabled", style="magenta")
    table.add_column("Path", width=PATH_MAX_WIDTH)
    table.add_column("Entry Point")
    table.add_column("Hooks")

    for rune in snap.runes:
        hooks = ", ".join(rune.hooks) if rune.hooks else "-"
        table.add_row(
            rune.name,
            rune.scope,
            rune.version,
            str(rune.enabled),
            _clip_path(rune.path, ascii_only),
            rune.entry_point or "-",
            hooks,
        )

    if not snap.runes:
        table.add_row("[dim](none)", "", "", "", "", "", "")

    console.print(table)


def _render_config(
    console: Console,
    snap: RuntimeSnapshot,
    box_style: box.Box | None = None,
    ascii_only: bool = False,
) -> None:
    table = Table(title="Config", show_lines=False, box=box_style)
    table.add_column("Key", style="cyan")
    table.add_column("Value", style="green")
    table.add_column("Layer", style="yellow")
    table.add_column("Source File", width=PATH_MAX_WIDTH)

    for entry in snap.config:
        source_file = entry.source_file if entry.source_file is not None else "-"
        table.add_row(
            entry.key,
            str(entry.value),
            entry.layer,
            _clip_path(source_file, ascii_only),
        )

    if not snap.config:
        table.add_row("[dim](none)", "", "", "")

    console.print(table)


def _render_prompt(
    console: Console,
    snap: RuntimeSnapshot,
    box_style: box.Box | None = None,
    ascii_only: bool = False,
) -> None:
    if snap.prompt is None:
        return

    table = Table(title="Prompt", show_lines=False, box=box_style)
    table.add_column("Source", style="cyan")
    table.add_column("Path", style="yellow", width=PATH_MAX_WIDTH)
    table.add_column("Text")

    path = snap.prompt.path if snap.prompt.path is not None else "-"
    table.add_row(
        snap.prompt.source,
        _clip_path(path, ascii_only),
        snap.prompt.text or "-",
    )

    console.print(table)


def _render_skills(
    console: Console,
    snap: RuntimeSnapshot,
    box_style: box.Box | None = None,
    ascii_only: bool = False,
) -> None:
    table = Table(title="Skills", show_lines=False, box=box_style)
    table.add_column("Name", style="cyan")
    table.add_column("Scope", style="green")
    table.add_column("Path", style="yellow", width=PATH_MAX_WIDTH)
    table.add_column("Version")
    table.add_column("Description")

    for skill in snap.skills:
        table.add_row(
            skill.name,
            skill.scope,
            _clip_path(skill.path, ascii_only),
            skill.version or "-",
            skill.description or "-",
        )

    if not snap.skills:
        table.add_row("[dim](none)", "", "", "", "")

    console.print(table)


def _render_diagnostics(
    console: Console,
    snap: RuntimeSnapshot,
    box_style: box.Box | None = None,
    ascii_only: bool = False,
) -> None:
    table = Table(title="Diagnostics", show_lines=False, box=box_style)
    table.add_column("Kind", style="red")
    table.add_column("Target", style="yellow")
    table.add_column("Name", style="cyan")
    table.add_column("Scope")
    table.add_column("Path", width=PATH_MAX_WIDTH)
    table.add_column("Message")

    for diag in snap.diagnostics:
        scope = diag.scope if diag.scope is not None else "-"
        path = diag.path if diag.path else "-"
        table.add_row(
            diag.kind,
            diag.target,
            diag.name,
            scope,
            _clip_path(path, ascii_only),
            diag.message,
        )

    if not snap.diagnostics:
        table.add_row("[dim](none)", "", "", "", "", "")

    console.print(table)


@info_app.callback(invoke_without_command=True)
def info(
    ctx: typer.Context,
    agent_name: str | None = typer.Option(
        None,
        "--agent-name",
        help="Agent name for agent-specific rune directory",
    ),
    extension_dir: str | None = typer.Option(
        None,
        "--extension-dir",
        "-e",
        help="Path to extension runes directory",
    ),
    output: str | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Output file path (default: stdout)",
    ),
) -> None:
    """Display the runtime snapshot as rich tables.

    Read-only: does not mutate config or state.  Renders spells with
    source provenance, runes per scope with enabled state, config values
    with provenance layers, loaded skills with source, and diagnostics.
    """
    # --agent-name may come from before the subcommand; resolve it
    # against the group context before anything uses it.
    agent_name = resolve_agent_name(ctx, agent_name)
    try:
        snapshot = asyncio.run(_assemble(agent_name, extension_dir))
    except ValueError as exc:
        console.print(format_error(exc))
        raise typer.Exit(1) from None

    if output:
        render_text = _render_snapshot(snapshot, ascii_only=False)
        out_path = Path(output).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(render_text + "\n", encoding="utf-8")
        console.print(f"[green]Snapshot written to {out_path}[/green]")
    else:
        ascii_only = not is_utf8_stream(sys.stdout)
        render_text = _render_snapshot(snapshot, ascii_only=ascii_only)
        console.print(render_text, end="")
