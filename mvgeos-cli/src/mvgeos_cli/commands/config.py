from __future__ import annotations

import asyncio
import json

import typer
from mvgeos_agent.agent_name import validate_agent_name
from mvgeos_agent.config_manager import ConfigManager
from mvgeos_provider.model_registry import CatalogRefresh, RefreshStatus
from mvgeos_provider.registry import refresh_catalog

from mvgeos_cli.agent_name import resolve_agent_name
from mvgeos_cli.console import format_error, get_console

console = get_console()

config_app = typer.Typer(name="config", help="Configuration management")


@config_app.callback(invoke_without_command=True)
def config_callback(ctx: typer.Context) -> None:
    """Configuration management."""
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())


def _get_manager(agent_name: str, allow_create: bool = False) -> ConfigManager:
    """Create a ConfigManager for the given agent name."""
    try:
        validate_agent_name(agent_name, allow_create=allow_create)
    except ValueError as err:
        console.print(format_error(err))
        raise typer.Exit(1) from None
    return ConfigManager(agent_name=agent_name)


@config_app.command("show")
def config_show(
    ctx: typer.Context,
    agent_name: str | None = typer.Option(
        None,
        "--agent-name",
        help="Agent name (defaults to the canonical agent)",
    ),
) -> None:
    """Display current configuration with provenance."""
    # --agent-name may come from before the subcommand; resolve it
    # against the group context before anything uses it.
    agent_name = resolve_agent_name(ctx, agent_name)
    mgr = _get_manager(agent_name)
    merged = mgr.load()
    output = {
        key: {"value": val.value, "layer": val.layer.value}
        for key, val in merged.items()
    }
    console.print_json(json.dumps(output, indent=2, default=str))


@config_app.command("set")
def config_set(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="Config key to set"),
    value: str = typer.Argument(..., help="Value to set"),
    agent_name: str | None = typer.Option(
        None,
        "--agent-name",
        help="Agent name (defaults to the canonical agent)",
    ),
) -> None:
    """Set a configuration value in the agent-scope file."""
    # --agent-name may come from before the subcommand; resolve it
    # against the group context before anything uses it.
    agent_name = resolve_agent_name(ctx, agent_name)
    mgr = _get_manager(agent_name)

    try:
        parsed_value = json.loads(value)
    except json.JSONDecodeError:
        parsed_value = value

    try:
        mgr.set(key, parsed_value)
    except ValueError as err:
        console.print(format_error(err))
        raise typer.Exit(1) from None

    console.print(f"[green]Set {key} = {parsed_value}[/green]")


@config_app.command("get")
def config_get(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="Config key to get"),
    agent_name: str | None = typer.Option(
        None,
        "--agent-name",
        help="Agent name (defaults to the canonical agent)",
    ),
) -> None:
    """Get a configuration value with provenance."""
    # --agent-name may come from before the subcommand; resolve it
    # against the group context before anything uses it.
    agent_name = resolve_agent_name(ctx, agent_name)
    mgr = _get_manager(agent_name)
    val = mgr.get(key)
    output = json.dumps({"value": val.value, "layer": val.layer.value}, default=str)
    console.print(output)


@config_app.command("reset")
def config_reset(
    ctx: typer.Context,
    agent_name: str | None = typer.Option(
        None,
        "--agent-name",
        help="Agent name (defaults to the canonical agent)",
    ),
) -> None:
    """Reset agent-scope configuration to defaults."""
    # --agent-name may come from before the subcommand; resolve it
    # against the group context before anything uses it.
    agent_name = resolve_agent_name(ctx, agent_name)
    mgr = _get_manager(agent_name)
    mgr.reset()
    console.print("[green]Configuration reset to defaults[/green]")


@config_app.command("path")
def config_path(
    ctx: typer.Context,
    agent_name: str | None = typer.Option(
        None,
        "--agent-name",
        help="Agent name (defaults to the canonical agent)",
    ),
) -> None:
    """Show the agent-scope config file path."""
    # --agent-name may come from before the subcommand; resolve it
    # against the group context before anything uses it.
    agent_name = resolve_agent_name(ctx, agent_name)
    mgr = _get_manager(agent_name)
    console.print(str(mgr.agent_config_path))


def _print_change(label: str, model_ids: tuple[str, ...], style: str) -> None:
    """Print one bucket of catalog changes, or say it was empty."""
    if not model_ids:
        console.print(f"  {label}: none")
        return
    console.print(f"  {label} ({len(model_ids)}):")
    for mid in model_ids:
        console.print(f"    [{style}]{mid}[/{style}]")


def _report_refresh(result: CatalogRefresh) -> None:
    """Render a catalog refresh and choose the process exit code."""
    if result.status is RefreshStatus.UNREACHABLE:
        console.print(
            "[red]Could not reach OpenRouter[/red] -- the shipped catalog is "
            "still in use and nothing was overwritten."
        )
        raise typer.Exit(1)

    if result.status is RefreshStatus.CACHE_FRESH:
        console.print(
            "The catalog cache is still fresh (24h TTL); nothing to fetch. "
            "Pass [bold]--force[/bold] to refresh anyway."
        )
        return

    console.print(
        f"[green]Catalog refreshed from OpenRouter: {result.total} models[/green]"
    )
    _print_change("Added", result.added, "green")
    _print_change("Removed", result.removed, "yellow")
    _print_change("No longer free", result.no_longer_free, "bold red")
    if result.no_longer_free:
        console.print("  (free before, now retired or priced)")
    console.print(f"Cache written to {result.cache_path}")


@config_app.command("refresh-models")
def config_refresh_models(
    force: bool = typer.Option(
        False,
        "--force",
        help="Bypass the 24-hour catalog cache TTL and re-fetch now",
    ),
) -> None:
    """Re-fetch the model catalog from OpenRouter and report what changed.

    The shipped catalog is a static snapshot, so a free-tier id in it can
    quietly stop being free.  This reports ids added, ids removed, and every id
    that used to be free and no longer is -- retired from the catalog or repriced
    -- so the loss surfaces here rather than as a failed request later.

    The catalog is never pruned: ids are reported, not dropped, so an
    unreachable Realm cannot take the shipped catalog away.
    """
    _report_refresh(asyncio.run(refresh_catalog(force_refresh=force)))
