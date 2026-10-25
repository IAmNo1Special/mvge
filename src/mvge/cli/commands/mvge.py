from __future__ import annotations

import typer
from rich.table import Table

from mvge.agent.installer import install_mvge, list_installed_mvges, uninstall_mvge
from mvge.cli.console import format_error, get_console

console = get_console()

mvge_app = typer.Typer(name="mvge", help="Mvge (agent) management")


@mvge_app.callback(invoke_without_command=True)
def mvge_callback(ctx: typer.Context) -> None:
    """Mvge (agent) management."""
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())


@mvge_app.command("install")
def mvge_install(
    spec: str = typer.Argument(
        ..., help="Mvge name from marketplace, Git URL, or local path"
    ),
    confirm_python_deps: bool | None = typer.Option(
        None,
        "--confirm-python-deps/--no-confirm-python-deps",
        help=(
            "Install the mvge's declared python_deps without prompting. "
            "A manifest is authored by whoever published the mvge, so its "
            "dependencies are an instruction to fetch packages from PyPI. "
            "Left unset, this prompts when stdin is a terminal and skips "
            "otherwise."
        ),
    ),
) -> None:
    """Install an agent mvge from the marketplace, Git URL, or local path."""
    try:
        dest = install_mvge(spec, confirm_python_deps=confirm_python_deps)
        console.print(
            f"[green]Successfully installed mvge '{dest.name}' to {dest}[/green]"
        )
    except Exception as exc:
        console.print(format_error(exc))
        raise typer.Exit(1) from exc


@mvge_app.command("list")
def mvge_list() -> None:
    """List installed agent mvges."""
    try:
        installed = list_installed_mvges()
        if not installed:
            console.print(
                "[yellow]No agent mvges installed in ~/.agents/agents[/yellow]"
            )
            return

        table = Table(
            title="Installed Mvges", show_header=True, header_style="bold magenta"
        )
        table.add_column("Name", style="cyan")
        table.add_column("Version", style="green")
        table.add_column("Spells", style="yellow")
        table.add_column("Description")

        for m in installed:
            spells_str = ", ".join(m.get("spells", [])) or "none"
            table.add_row(
                str(m.get("name", "")),
                str(m.get("version", "unknown")),
                spells_str,
                str(m.get("description", "")),
            )
        console.print(table)
    except Exception as exc:
        console.print(format_error(exc))
        raise typer.Exit(1) from exc


@mvge_app.command("uninstall")
def mvge_uninstall(
    name: str = typer.Argument(..., help="Mvge name to uninstall"),
) -> None:
    """Uninstall an installed agent mvge."""
    try:
        success = uninstall_mvge(name)
        if success:
            console.print(f"[green]Successfully uninstalled mvge '{name}'[/green]")
        else:
            console.print(f"[yellow]Mvge '{name}' is not installed[/yellow]")
            raise typer.Exit(1)
    except Exception as exc:
        console.print(format_error(exc))
        raise typer.Exit(1) from exc
