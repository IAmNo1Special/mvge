from __future__ import annotations

import typer
from rich.table import Table

from mvge.cli.console import format_error, get_console
from mvge.core.layers import extensions_dir
from mvge.runes.installer import (
    adopt_installed_rune,
    consent_rune,
    install_rune,
    list_installed_runes,
    uninstall_rune,
)
from mvge.runes.pinning import read_pin, verify_rune_pin

console = get_console()

rune_app = typer.Typer(name="rune", help="Extension rune management")


@rune_app.callback(invoke_without_command=True)
def rune_callback(ctx: typer.Context) -> None:
    """Extension rune management."""
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())


@rune_app.command("install")
def rune_install(
    spec: str = typer.Argument(..., help="Rune name from marketplace or Git URL"),
    confirm_python_deps: bool = typer.Option(
        False,
        "--confirm-python-deps",
        help=(
            "Install the rune's declared python_deps without prompting. "
            "A manifest is authored by whoever published the rune, so its "
            "dependencies are an instruction to fetch packages from PyPI."
        ),
    ),
) -> None:
    """Install an extension rune from the marketplace or a Git URL."""
    try:
        dest = install_rune(spec, confirm_python_deps=confirm_python_deps)
        console.print(f"[green]Successfully installed rune '{spec}' to {dest}[/green]")
    except Exception as exc:
        console.print(format_error(exc))
        raise typer.Exit(1) from exc


@rune_app.command("list")
def rune_list() -> None:
    """List installed extension runes."""
    try:
        installed = list_installed_runes()
        if not installed:
            console.print(
                f"[yellow]No extension runes installed in {extensions_dir()}[/yellow]"
            )
            return

        table = Table(
            title="Installed Runes", show_header=True, header_style="bold magenta"
        )
        table.add_column("Name", style="cyan")
        table.add_column("Version", style="green")
        table.add_column("Types", style="yellow")
        table.add_column("Description")

        for r in installed:
            raw_types = r.get("types", [])
            types_str = (
                ", ".join(raw_types)
                if isinstance(raw_types, list) and raw_types
                else str(r.get("type", "") or "none")
            )
            table.add_row(
                str(r.get("name", "")),
                str(r.get("version", "unknown")),
                types_str,
                str(r.get("description", "")),
            )
        console.print(table)
    except Exception as exc:
        console.print(format_error(exc))
        raise typer.Exit(1) from exc


@rune_app.command("consent")
def rune_consent(
    name: str | None = typer.Argument(
        None,
        help="Installed rune name to consent to its declared capabilities.",
    ),
    all_runes: bool = typer.Option(
        False,
        "--all",
        help=(
            "Consent to every installed rune's declared capabilities. "
            "Legacy runes (no capabilities field) are skipped: they are "
            "ungated and have nothing to consent to."
        ),
    ),
) -> None:
    """Consent to an installed rune's declared capabilities.

    Installing already consents to what the manifest declares at
    install time. This command is the re-consent path: a rune
    installed before install-time consent existed, or a rune whose
    declaration widened after install (a new capability, or wider
    write roots). It consents to exactly what the installed manifest
    declares now — a widened declaration is never granted by loading
    alone, only by this explicit act.
    """
    try:
        if all_runes:
            for r in list_installed_runes():
                rname = str(r.get("name", ""))
                if not rname:
                    continue
                try:
                    granted = consent_rune(rname)
                except ValueError as exc:
                    console.print(f"[yellow]Skipping '{rname}': {exc}[/yellow]")
                    continue
                console.print(
                    "[green]Consented to "
                    f"'{rname}': {', '.join(granted) or 'none'}[/green]"
                )
            return
        if name is None:
            console.print("[red]Provide a rune name or --all.[/red]")
            raise typer.Exit(1)
        granted = consent_rune(name)
        console.print(
            f"[green]Consented to '{name}': {', '.join(granted) or 'none'}[/green]"
        )
    except typer.Exit:
        raise
    except Exception as exc:
        console.print(format_error(exc))
        raise typer.Exit(1) from exc


@rune_app.command("trust")
def rune_trust(
    adopt: str | None = typer.Option(
        None,
        "--adopt",
        help=(
            "Record an operator review of an installed rune's current tree, "
            "writing a manual provenance sidecar for it. The remedy for "
            "sidecar-less legacy runes and deliberate local edits; it "
            "asserts the tree was reviewed, not that it is benign."
        ),
    ),
) -> None:
    """Show pin status for installed runes, or adopt one with --adopt."""
    try:
        if adopt is not None:
            dest = adopt_installed_rune(adopt)
            console.print(
                f"[green]Adopted rune '{adopt}': recorded its current tree "
                f"hash in {dest}[/green]"
            )
            return
        installed = list_installed_runes()
        if not installed:
            console.print(
                f"[yellow]No extension runes installed in {extensions_dir()}[/yellow]"
            )
            return
        table = Table(title="Rune Trust", show_header=True, header_style="bold magenta")
        table.add_column("Name", style="cyan")
        table.add_column("Source", style="yellow")
        table.add_column("Pin", style="green")
        for r in installed:
            name = str(r.get("name", ""))
            rune_dir = extensions_dir() / name
            pin = read_pin(rune_dir)
            verdict = verify_rune_pin(rune_dir, name)
            source = pin.source_kind if pin is not None else "unknown"
            table.add_row(name, source, verdict.status)
        console.print(table)
    except Exception as exc:
        console.print(format_error(exc))
        raise typer.Exit(1) from exc


@rune_app.command("uninstall")
def rune_uninstall(
    name: str = typer.Argument(..., help="Rune name to uninstall"),
) -> None:
    """Uninstall an installed extension rune."""
    try:
        success = uninstall_rune(name)
        if success:
            console.print(f"[green]Successfully uninstalled rune '{name}'[/green]")
        else:
            console.print(f"[yellow]Rune '{name}' is not installed[/yellow]")
            raise typer.Exit(1)
    except Exception as exc:
        console.print(format_error(exc))
        raise typer.Exit(1) from exc
