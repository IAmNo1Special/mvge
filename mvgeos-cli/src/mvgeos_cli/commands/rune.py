from __future__ import annotations

import typer
from mvgeos_runes.installer import install_rune, list_installed_runes, uninstall_rune
from rich.table import Table

from mvgeos_cli.console import format_error, get_console

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
                "[yellow]No extension runes installed in ~/.agents/extensions[/yellow]"
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
