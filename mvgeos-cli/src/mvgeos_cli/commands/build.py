from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from mvgeos_agent.environment import MvgeEnvironment
from mvgeos_agent.mvge import Mvge
from mvgeos_agent.protocol import AgentFactory, MvgeAgent
from mvgeos_agent.snapshot import RuntimeSnapshot

from mvgeos_cli.agent_name import resolve_agent_name
from mvgeos_cli.console import format_error, get_console

console = get_console()

build_app = typer.Typer(
    name="build",
    help="Serialise the resolved runtime manifest (read-only).",
)


def _render_summary(snapshot: RuntimeSnapshot) -> str:
    """Render a condensed, human-readable view of the runtime snapshot."""
    lines: list[str] = []
    lines.append(f"Agent: {snapshot.agent_name}")
    lines.append(f"Model: {snapshot.model}")
    lines.append("")

    lines.append(f"Spells ({len(snapshot.spells)}):")
    for spell in snapshot.spells:
        if spell.source == "rune":
            lines.append(f"  {spell.name} [rune: {spell.source_rune}]")
        else:
            lines.append(f"  {spell.name} [builtin]")
    lines.append("")

    lines.append(f"Runes ({len(snapshot.runes)}):")
    for rune in snapshot.runes:
        lines.append(
            f"  {rune.name} v{rune.version} "
            f"(scope={rune.scope}, enabled={rune.enabled})"
        )
    lines.append("")

    lines.append(f"Config ({len(snapshot.config)}):")
    for entry in snapshot.config:
        src = entry.source_file or "inline"
        lines.append(
            f"  {entry.key} = {entry.value} (layer={entry.layer}, source={src})"
        )
    lines.append("")

    if snapshot.prompt is not None:
        lines.append("Prompt:")
        lines.append(f"  source={snapshot.prompt.source}")
        if snapshot.prompt.text:
            lines.append(f"  text={snapshot.prompt.text}")
        lines.append("")

    lines.append(f"Skills ({len(snapshot.skills)}):")
    for skill in snapshot.skills:
        lines.append(f"  {skill.name} v{skill.version} (scope={skill.scope})")
    lines.append("")

    lines.append(f"Diagnostics ({len(snapshot.diagnostics)}):")
    for diag in snapshot.diagnostics:
        scope = f", scope={diag.scope}" if diag.scope else ""
        lines.append(f"  [{diag.target}] {diag.name}{scope}: {diag.message}")

    return "\n".join(lines)


def _write_output(output: str, content: str) -> None:
    """Write rendered content to the output file, creating parent dirs."""
    out_path = Path(output).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content + "\n", encoding="utf-8")
    console.print(f"[green]Manifest written to {out_path}[/green]")


async def _assemble(
    agent_name: str,
    extension_dir: str | None,
    agent_factory: AgentFactory | None = None,
) -> RuntimeSnapshot:
    """Load runes and assemble the runtime snapshot (read-only).

    Creates an agent with an MvgeEnvironment, loads runes and skills
    from disk, and returns a serialisable RuntimeSnapshot. No API key
    or realm is required — this is a static introspection path.
    """
    env = MvgeEnvironment.resolve(agent_name=agent_name, project_dir=Path.cwd())
    agent: MvgeAgent | Mvge
    if agent_factory is not None:
        agent = agent_factory(
            api_key="",
            name=agent_name,
            extension_dir=extension_dir,
            environment=env,
        )
    else:
        agent = Mvge(
            api_key="",
            name=agent_name,
            extension_dir=extension_dir,
            environment=env,
        )
    if hasattr(agent, "load_runes"):
        await agent.load_runes()
    elif hasattr(agent, "_load_runes"):
        await agent._load_runes()
    return agent.build_snapshot()


@build_app.callback(invoke_without_command=True)
def build(
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
    format: str = typer.Option(
        "json",
        "--format",
        case_sensitive=False,
        help="Output format: json or summary",
    ),
    pretty: bool = typer.Option(
        True,
        "--pretty/--no-pretty",
        help="Pretty-print JSON output (json format only)",
    ),
) -> None:
    """Serialise the resolved runtime manifest.

    Read-only: does not mutate config or state.  Outputs a JSON document
    describing spells (with source provenance), runes per scope, config
    values with provenance layers, resolved prompt source, loaded skills,
    and accumulated diagnostics.  Use ``--format summary`` for a condensed
    view or ``--pretty``/``--no-pretty`` to control JSON indentation.
    """
    # --agent-name may come from before the subcommand; resolve it
    # against the group context before anything uses it.
    agent_name = resolve_agent_name(ctx, agent_name)
    if format not in ("json", "summary"):
        raise typer.BadParameter(
            f"Invalid format '{format}'. Choose from: json, summary"
        )

    try:
        snapshot = asyncio.run(_assemble(agent_name, extension_dir))
    except ValueError as exc:
        console.print(format_error(exc))
        raise typer.Exit(1) from None

    if format == "summary":
        rendered = _render_summary(snapshot)
        if output:
            _write_output(output, rendered)
        else:
            console.print(rendered)
        return

    json_str = snapshot.to_json(indent=2 if pretty else None)

    if output:
        _write_output(output, json_str)
    else:
        if pretty:
            console.print_json(json_str)
        else:
            console.print(json_str, highlight=False)
