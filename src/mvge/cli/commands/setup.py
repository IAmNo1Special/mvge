from __future__ import annotations

import asyncio
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

import typer
import typer._click as _click
from typer.core import TyperGroup

from mvge.agent.agent_name import validate_agent_name
from mvge.cli.agent_name import resolve_agent_name
from mvge.cli.console import format_error, get_console
from mvge.core.constants import DEFAULT_AGENT_NAME
from mvge.core.layers import resolve_rune_paths
from mvge.runes.catalog import DEFAULT_MARKETPLACE_URL
from mvge.runes.loader import check_python_dep_installed
from mvge.runes.manifest import load_manifest
from mvge.runes.types import RuneManifest

console = get_console()


class DefaultCheckGroup(TyperGroup):
    """Group that defaults to the ``check`` subcommand when none is given."""

    def parse_args(self, ctx: _click.Context, args: list[str]) -> list[str]:
        if args and any(
            arg == "--profile" or arg.startswith("--profile=") for arg in args
        ):
            cmd_names = set(self.commands.keys())
            if not any(arg in cmd_names for arg in args):
                args = ["profile", *args]
        return super().parse_args(ctx, args)

    def invoke(self, ctx: _click.Context) -> Any:
        if not ctx._protected_args:
            ctx._protected_args = ["check"]
        return super().invoke(ctx)


setup_app = typer.Typer(
    name="setup", cls=DefaultCheckGroup, help="Install system dependencies for runes"
)

# Package manager mappings for common tools
PACKAGE_MAP: dict[str, dict[str, list[str]]] = {
    "ripgrep": {
        "windows": ["BurntSushi.ripgrep.MSVC", "RipGrep", "rg"],
        "darwin": ["ripgrep"],
        "linux": ["ripgrep"],
    },
    "git": {
        "windows": ["Git.Git"],
        "darwin": ["git"],
        "linux": ["git"],
    },
    "fd": {
        "windows": ["sharkdp.fd"],
        "darwin": ["fd"],
        "linux": ["fd-find"],
    },
    "bat": {
        "windows": ["sharkdp.bat"],
        "darwin": ["bat"],
        "linux": ["bat"],
    },
    "fzf": {
        "windows": ["junegunn.fzf"],
        "darwin": ["fzf"],
        "linux": ["fzf"],
    },
}

# Command-line tool names to check (may differ from package names)
TOOL_CHECK: dict[str, str] = {
    "ripgrep": "rg",
    "git": "git",
    "fd": "fd",
    "bat": "bat",
    "fzf": "fzf",
}


def get_platform() -> str:
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    elif system == "darwin":
        return "darwin"
    return "linux"


def get_package_manager_commands() -> list[list[str]]:
    """Return list of package manager command templates for current platform."""
    plat = get_platform()
    if plat == "windows":
        return [
            [
                "winget",
                "install",
                "--silent",
                "--accept-source-agreements",
                "--accept-package-agreements",
            ],
            ["choco", "install", "-y"],
            ["scoop", "install"],
        ]
    elif plat == "darwin":
        return [
            ["brew", "install"],
        ]
    return [
        ["apt", "install", "-y"],
        ["dnf", "install", "-y"],
        ["pacman", "-S", "--noconfirm"],
    ]


def check_tool_installed(tool: str) -> bool:
    """Check if a command-line tool is available on PATH."""
    check_name = TOOL_CHECK.get(tool, tool)
    return shutil.which(check_name) is not None


def check_python_dep(module_name: str) -> bool:
    """Check if a Python package is installed (PEP 508 aware)."""
    return check_python_dep_installed(module_name)


async def install_package(package: str, dry_run: bool = False) -> tuple[bool, str]:
    """Try to install a package using available package managers."""
    plat = get_platform()
    pkg_names = PACKAGE_MAP.get(package, {}).get(plat, [package])

    for pm_cmd in get_package_manager_commands():
        pm_name = pm_cmd[0]
        if not shutil.which(pm_name):
            continue

        for pkg in pkg_names:
            cmd = [*pm_cmd, pkg]
            console.print(f"[dim]Trying: {' '.join(cmd)}[/dim]")
            if dry_run:
                return True, f"Would run: {' '.join(cmd)}"
            try:
                result = await asyncio.to_thread(
                    subprocess.run,
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                if result.returncode == 0:
                    return True, f"Installed {package} via {pm_name}"
                console.print(
                    f"[yellow]{pm_name} failed for {pkg}: "
                    f"{result.stderr[:200]}[/yellow]"
                )
            except subprocess.TimeoutExpired:
                console.print(f"[yellow]{pm_name} timed out for {pkg}[/yellow]")
            except Exception as e:
                console.print(f"[yellow]{pm_name} error for {pkg}: {e}[/yellow]")

    return False, f"No working package manager found for {package} on {plat}"


def _has_build_config(rune_dir: Path) -> bool:
    """A rune ships a package only if it has a build config."""
    return (rune_dir / "pyproject.toml").exists() or (rune_dir / "setup.py").exists()


async def install_python_dep_by_name(
    dep: str, dry_run: bool = False
) -> tuple[bool, str]:
    """Install a single declared Python dependency by name via uv add."""
    if not shutil.which("uv"):
        return False, "uv not found on PATH"
    cmd = ["uv", "add", dep]
    console.print(f"[dim]Trying: {' '.join(cmd)}[/dim]")
    if dry_run:
        return True, f"Would run: {' '.join(cmd)}"
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode == 0:
            return True, f"Installed python dep {dep} via uv"
        return False, f"uv add {dep} failed: {result.stderr[:200]}"
    except subprocess.TimeoutExpired:
        return False, f"uv add {dep} timed out"
    except Exception as e:
        return False, f"uv add {dep} error: {e}"


async def _run_uv_editable(rune_dir: Path, dry_run: bool = False) -> tuple[bool, str]:
    """Editable-install a rune directory via uv add --editable."""
    cmd = ["uv", "add", "--editable", str(rune_dir)]
    console.print(f"[dim]Trying: {' '.join(cmd)}[/dim]")
    if dry_run:
        return True, f"Would run: {' '.join(cmd)}"
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode == 0:
            return True, f"Installed via uv add --editable {rune_dir.name}"
        return False, f"uv add failed: {result.stderr[:200]}"
    except subprocess.TimeoutExpired:
        return False, "uv add timed out"
    except Exception as e:
        return False, f"uv add error: {e}"


async def install_rune_python_deps(
    rune_dir: Path,
    manifest: RuneManifest | None = None,
    dry_run: bool = False,
) -> tuple[bool, str]:
    """Install a rune's Python deps.

    Declared ``python_deps`` are installed by name (``uv add <dep>``).
    When the rune directory ships a build config (``pyproject.toml`` /
    ``setup.py``) we fall back to an editable install of the directory,
    which also covers runes like heal-my-goap that ship no package.
    """
    if not shutil.which("uv"):
        return False, "uv not found on PATH"

    deps = list(manifest.python_deps) if manifest is not None else []
    if _has_build_config(rune_dir) or not deps:
        # Packaged rune (has build config) or no declared deps by name:
        # fall back to an editable install of the rune directory.
        return await _run_uv_editable(rune_dir, dry_run)

    results: list[tuple[bool, str]] = []
    for dep in deps:
        ok, msg = await install_python_dep_by_name(dep, dry_run=dry_run)
        results.append((ok, msg))
    failed = [r for r in results if not r[0]]
    if failed:
        return False, "; ".join(m for _, m in failed)
    return True, "; ".join(m for _, m in results)


def collect_rune_dirs(
    agent_name: str, extension_dir: str | None, project_dir: Path | None = None
) -> list[tuple[RuneManifest, Path]]:
    """Load enabled runes and return (manifest, rune_dir) pairs."""
    results: list[tuple[RuneManifest, Path]] = []
    seen_names: set[str] = set()
    for base in resolve_rune_paths(agent_name, extension_dir, project_dir=project_dir):
        if not base.exists():
            continue
        # Sort for deterministic duplicate resolution: iterdir() order is
        # filesystem-dependent, so without this the winning duplicate would
        # vary from machine to machine.
        for entry in sorted(base.iterdir()):
            if not entry.is_dir():
                continue
            manifest = load_manifest(entry)
            if manifest is None or not manifest.enabled:
                continue
            if manifest.name in seen_names:
                continue
            seen_names.add(manifest.name)
            results.append((manifest, entry))
    return results


@setup_app.command("check")
def setup_check(
    ctx: typer.Context,
    agent_name: str | None = typer.Option(
        None,
        "--agent-name",
        help="Agent name for agent-specific rune directory",
    ),
    extension_dir: str | None = typer.Option(
        None, "--extension-dir", "-e", help="Path to extension runes directory"
    ),
) -> None:
    """Check which system dependencies are missing."""
    # --agent-name may come from before the subcommand; resolve it
    # against the group context before anything uses it.
    agent_name = resolve_agent_name(ctx, agent_name)
    try:
        validate_agent_name(agent_name, allow_create=False)
    except ValueError as exc:
        console.print(format_error(exc))
        raise typer.Exit(1) from None

    runes = collect_rune_dirs(agent_name, extension_dir, project_dir=Path.cwd())

    if not runes:
        console.print("[yellow]No runes found[/yellow]")
        return

    all_system_deps: set[str] = set()
    all_python_deps: set[str] = set()
    for manifest, _ in runes:
        all_system_deps.update(manifest.system_deps)
        all_python_deps.update(manifest.python_deps)

    if not all_system_deps and not all_python_deps:
        console.print("[green]No dependencies declared by runes[/green]")
        return

    any_missing = False

    if all_system_deps:
        console.print("\n[bold]System Dependencies:[/bold]")
        for dep in sorted(all_system_deps):
            installed = check_tool_installed(dep)
            status = "[green]OK[/green]" if installed else "[red]MISSING[/red]"
            console.print(f"  {dep}: {status}")
            if not installed:
                any_missing = True

    if all_python_deps:
        console.print("\n[bold]Python Dependencies:[/bold]")
        for dep in sorted(all_python_deps):
            installed = check_python_dep(dep)
            status = "[green]OK[/green]" if installed else "[red]MISSING[/red]"
            console.print(f"  {dep}: {status}")
            if not installed:
                any_missing = True

    if any_missing:
        console.print(
            "\n[yellow]Run 'mvge setup install' "
            "to install missing dependencies[/yellow]"
        )
        raise typer.Exit(1)
    else:
        console.print("\n[green]All dependencies satisfied[/green]")


def install_missing_deps(
    agent_name: str = DEFAULT_AGENT_NAME,
    extension_dir: str | None = None,
    yes: bool = False,
    dry_run: bool = False,
) -> int:
    """Install missing rune dependencies.

    Shared core used by both the ``setup install`` command and the
    pre-flight startup auto-install path. Returns ``0`` on success, ``1`` on
    failure (no runes / nothing to install returns ``0``).
    """
    try:
        validate_agent_name(agent_name, allow_create=False)
    except ValueError as exc:
        console.print(format_error(exc))
        return 1

    runes = collect_rune_dirs(agent_name, extension_dir, project_dir=Path.cwd())

    if not runes:
        console.print("[yellow]No runes found[/yellow]")
        return 0

    all_system_deps: set[str] = set()
    all_python_deps: set[str] = set()
    for manifest, _ in runes:
        all_system_deps.update(manifest.system_deps)
        all_python_deps.update(manifest.python_deps)

    if not all_system_deps and not all_python_deps:
        console.print("[green]No dependencies declared by runes[/green]")
        return 0

    missing_system = sorted(d for d in all_system_deps if not check_tool_installed(d))
    missing_python = sorted(d for d in all_python_deps if not check_python_dep(d))
    runes_needing_python = [
        (m, d) for m, d in runes if any(dep in missing_python for dep in m.python_deps)
    ]

    if not missing_system and not missing_python:
        console.print("[green]All dependencies already installed[/green]")
        return 0

    if missing_system:
        console.print(
            f"\n[bold]Missing system dependencies:[/bold] {', '.join(missing_system)}"
        )
    if missing_python:
        console.print(
            f"[bold]Missing python dependencies:[/bold] {', '.join(missing_python)}"
        )

    if not yes and not dry_run:
        response = input("Install now? [y/N]: ").strip().lower()
        if response != "y":
            console.print("[yellow]Aborted[/yellow]")
            return 0

    results: list[tuple[str, bool, str]] = []
    for dep in missing_system:
        success, msg = asyncio.run(install_package(dep, dry_run=dry_run))
        results.append((dep, success, msg))
        if success:
            console.print(f"[green]OK {dep}: {msg}[/green]")
        else:
            console.print(f"[red]FAIL {dep}: {msg}[/red]")

    for manifest, rune_dir in runes_needing_python:
        success, msg = asyncio.run(
            install_rune_python_deps(rune_dir, manifest, dry_run=dry_run)
        )
        label = f"python deps ({manifest.name})"
        results.append((label, success, msg))
        if success:
            console.print(f"[green]OK {label}: {msg}[/green]")
        else:
            console.print(f"[red]FAIL {label}: {msg}[/red]")

    failed = [r for r in results if not r[1]]
    if failed:
        console.print(f"\n[red]Failed to install {len(failed)} dependency(s)[/red]")
        return 1
    console.print("\n[green]All dependencies installed successfully[/green]")
    return 0


@setup_app.command("install")
def setup_install(
    ctx: typer.Context,
    agent_name: str | None = typer.Option(
        None,
        "--agent-name",
        help="Agent name for agent-specific rune directory",
    ),
    extension_dir: str | None = typer.Option(
        None, "--extension-dir", "-e", help="Path to extension runes directory"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be installed without installing"
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help=(
            "Auto-confirm all installations (non-interactive). Without this flag "
            "the confirmation prompt reads piped stdin, so "
            "'echo y | mvge setup install' also works non-interactively."
        ),
    ),
) -> None:
    """Install missing dependencies for runes.

    Non-interactive use: pass ``--yes``/``-y`` to skip the confirmation prompt,
    or pipe stdin (``echo y | mvge setup install``) when prompted.
    """
    # --agent-name may come from before the subcommand; resolve it
    # against the group context before anything uses it.
    agent_name = resolve_agent_name(ctx, agent_name)
    code = install_missing_deps(
        agent_name=agent_name, extension_dir=extension_dir, yes=yes, dry_run=dry_run
    )
    if code != 0:
        raise typer.Exit(code)


@setup_app.command("profile")
def setup_profile(
    profile_name: str = typer.Argument(
        "coding",
        help="Name of the profile to install (minimal, coding, autonomous)",
    ),
    profile: str | None = typer.Option(
        None,
        "--profile",
        help="Alternative flag for profile name",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force reinstallation of already installed mvges and runes",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Auto-confirm python dependency installation",
    ),
    marketplace_url: str = typer.Option(
        DEFAULT_MARKETPLACE_URL,
        "--marketplace-url",
        help="Marketplace index URL",
    ),
) -> None:
    """Install all mvges and runes declared by a named profile."""
    from mvge.agent.installer import install_profile

    target_profile = profile or profile_name
    try:
        res = install_profile(
            target_profile,
            marketplace_url=marketplace_url,
            confirm_python_deps=yes,
            force=force,
        )
        console.print(
            f"[green]Successfully installed profile '{target_profile}':[/green]"
        )
        if res.installed_mvges:
            console.print(f"  Mvges: {', '.join(res.installed_mvges)}")
        if res.installed_runes:
            console.print(f"  Runes: {', '.join(res.installed_runes)}")
        if res.skipped_mvges:
            console.print(f"  Skipped existing mvges: {', '.join(res.skipped_mvges)}")
        if res.skipped_runes:
            console.print(f"  Skipped existing runes: {', '.join(res.skipped_runes)}")
    except Exception as exc:
        console.print(
            format_error(f"Failed to install profile '{target_profile}': {exc}")
        )
        raise typer.Exit(1) from exc
