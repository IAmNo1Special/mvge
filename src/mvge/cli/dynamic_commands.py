from __future__ import annotations

import asyncio
import importlib.util
import inspect
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import click
import typer

from mvge.agent.config_manager import ConfigManager
from mvge.agent.mvge import discover_agent_rune_extras
from mvge.core.constants import DEFAULT_AGENT_NAME
from mvge.core.layers import ResolvedLayer, Scope, resolve_rune_layers
from mvge.runes.loader import _inject_rune_paths
from mvge.runes.manifest import load_manifest
from mvge.runes.rune_api import RuneAPI
from mvge.runes.rune_runner import RuneRunner
from mvge.runes.types import RuneManifest

#: Environment variable naming one extra Rune directory for CLI command
#: discovery. Resolves last, like every other "extra directory" in the
#: engine: it is the caller's most specific declaration, so it is the last
#: resort rather than a layer that outranks the stack.
EXTENSION_DIR_ENV = "MVGE_EXTENSION_DIR"


def get_extension_dirs(
    agent_name: str = DEFAULT_AGENT_NAME,
    *,
    project_dir: Path | None = None,
    global_dir: Path | None = None,
    extension_dir: str | None = None,
    extras: Sequence[ResolvedLayer] = (),
) -> list[Path]:
    """Rune directories to scan for CLI commands, in precedence order.

    The same resolver Rune loading uses, asked the same question with the
    same inputs -- that equality is the point, and it is what makes a Rune
    installable and then discoverable. A directory that Rune loading would
    not read is not scanned here either.

    Args:
        agent_name: Names the agent layer.
        project_dir: Anchors the project layer. ``None`` omits it entirely.
        global_dir: Overrides the global layer for this call.
        extension_dir: An extra directory, or ``$MVGE_EXTENSION_DIR`` when
            not given.
        extras: Further caller-declared layers -- configured ``rune_paths``
            arrives this way, so a directory named in config is discovered
            exactly as Rune loading discovers it.

    Non-existent layers are dropped: CLI command discovery lists what is
    installed, so it has nothing to say about a layer that is not there. The
    resolver itself does no filtering -- installation needs the whole stack.
    """
    declared_extras: list[ResolvedLayer] = list(extras)
    declared = extension_dir or os.environ.get(EXTENSION_DIR_ENV)
    if declared:
        declared_extras.append(
            ResolvedLayer(Scope.PROJECT, Path(declared).expanduser())
        )

    layers = resolve_rune_layers(
        agent_name,
        project_dir=project_dir,
        global_dir=global_dir,
        extras=declared_extras,
    )
    return [layer.path for layer in layers if layer.path.expanduser().is_dir()]


def cli_project_dir() -> Path:
    """The project the CLI is operating on.

    The CLI runs *inside* a project directory, so that directory is its
    anchor -- a deliberate choice, not an ambient leak. Rune loading already
    resolves with it (``main.py`` passes ``Path.cwd()`` into
    ``MvgeEnvironment.resolve``), and CLI discovery has to resolve with the
    same anchor or the two disagree about which layer is the project layer.

    A library embedder has no such anchor, which is why the resolver takes
    ``project_dir=None`` and omits the layer entirely rather than guessing.
    """
    return Path.cwd()


def configured_rune_layers(
    agent_name: str = DEFAULT_AGENT_NAME,
    project_dir: Path | str | None = None,
) -> list[ResolvedLayer]:
    """Layers declared by a configured ``rune_paths``, as the loader sees them.

    Read through the same coercion the loader uses, so a directory named in
    config is discovered here under the same scope it loads under. A config
    that names one directory extends the layering rather than replacing it, so
    this returns extras and never a replacement stack.

    The agent's own shipped Rune directories are folded in too, from the same
    function ``Mvge`` calls. Omitting them made a Rune that loads in a session
    undiscoverable from the CLI, which is the same divergence this function
    exists to remove.
    """
    resolved = ConfigManager(
        agent_name=agent_name,
        project_dir=Path(project_dir) if project_dir is not None else Path.cwd(),
    ).load()
    raw = resolved.get("rune_paths")
    declared = raw.value if raw is not None else None
    return [
        *discover_agent_rune_extras(agent_name),
        *(
            ResolvedLayer(Scope.PROJECT, Path(str(p)).expanduser())
            for p in (declared or [])
        ),
    ]


def discover_installed_rune_commands(
    agent_name: str = DEFAULT_AGENT_NAME,
    *,
    project_dir: Path | None = None,
    global_dir: Path | None = None,
    extension_dir: str | None = None,
    extras: Sequence[ResolvedLayer] = (),
) -> dict[str, tuple[Path, RuneManifest]]:
    """Scan extension directories and map command names to their parent rune."""
    cmd_map: dict[str, tuple[Path, RuneManifest]] = {}

    dirs = get_extension_dirs(
        agent_name,
        project_dir=project_dir,
        global_dir=global_dir,
        extension_dir=extension_dir,
        extras=extras,
    )
    for ext_dir in dirs:
        try:
            for child in ext_dir.iterdir():
                if not child.is_dir():
                    continue
                manifest = load_manifest(child)
                if manifest is None or not manifest.enabled:
                    continue
                for cmd in manifest.commands:
                    if cmd not in cmd_map:
                        cmd_map[cmd] = (child, manifest)
        except (OSError, PermissionError):
            continue

    return cmd_map


def load_rune_cli_command(
    cmd_name: str,
    agent_name: str = DEFAULT_AGENT_NAME,
    *,
    project_dir: Path | None = None,
    global_dir: Path | None = None,
    extension_dir: str | None = None,
    extras: Sequence[ResolvedLayer] = (),
) -> click.Command | None:
    """Dynamically mount a Click command from an installed rune."""
    cmd_map = discover_installed_rune_commands(
        agent_name,
        project_dir=project_dir,
        global_dir=global_dir,
        extension_dir=extension_dir,
        extras=extras,
    )
    if cmd_name not in cmd_map:
        return None

    rune_dir, manifest = cmd_map[cmd_name]
    entry = rune_dir / (manifest.entry_point or "rune.py")
    _inject_rune_paths(rune_dir, entry)

    # Strategy 1: Check cli.py
    cli_py = rune_dir / "cli.py"
    if cli_py.is_file():
        spec = importlib.util.spec_from_file_location(
            f"mvge_rune_cli_{manifest.name}", cli_py
        )
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(mod)
                for attr_name in (
                    "app",
                    "cli",
                    cmd_name,
                    cmd_name.replace("-", "_"),
                    f"{cmd_name}_app",
                    f"{cmd_name}_cli",
                    f"{cmd_name.replace('-', '_')}_app",
                    f"{cmd_name.replace('-', '_')}_cli",
                    "main",
                ):
                    if hasattr(mod, attr_name):
                        target = getattr(mod, attr_name)
                        if isinstance(target, typer.Typer):
                            if (
                                len(target.registered_commands) == 1
                                and not target.registered_groups
                                and not target.registered_callback
                            ):

                                @target.callback()
                                def _default_group_callback() -> None:
                                    pass

                            cmd_obj = typer.main.get_command(target)
                            cmd_obj.name = cmd_name
                            return cast(click.Command, cmd_obj)
                        if isinstance(target, click.Command):
                            target.name = cmd_name
                            return target
            except Exception:
                pass

    # Strategy 2: Check rune.py or rune_factory
    rune_py = rune_dir / (manifest.entry_point or "rune.py")
    if rune_py.is_file():
        spec = importlib.util.spec_from_file_location(
            f"mvge_rune_{manifest.name}", rune_py
        )
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(mod)
                # Check for click/typer command in rune.py
                for attr_name in (
                    "app",
                    "cli",
                    cmd_name,
                    cmd_name.replace("-", "_"),
                    f"{cmd_name}_app",
                    f"{cmd_name}_cli",
                    f"{cmd_name.replace('-', '_')}_app",
                    f"{cmd_name.replace('-', '_')}_cli",
                    "main",
                ):
                    if hasattr(mod, attr_name):
                        target = getattr(mod, attr_name)
                        if isinstance(target, typer.Typer):
                            if (
                                len(target.registered_commands) == 1
                                and not target.registered_groups
                                and not target.registered_callback
                            ):

                                @target.callback()
                                def _default_group_callback_rune() -> None:
                                    pass

                            cmd_obj = typer.main.get_command(target)
                            cmd_obj.name = cmd_name
                            return cast(click.Command, cmd_obj)
                        if isinstance(target, click.Command):
                            target.name = cmd_name
                            return target

                # Check rune_factory registering commands
                if hasattr(mod, "rune_factory"):
                    runner = RuneRunner()
                    api = RuneAPI(runner, rune_name=manifest.name)
                    factory_res = mod.rune_factory(api)
                    if inspect.iscoroutine(factory_res):
                        asyncio.run(factory_res)

                    for rc in runner.get_commands():
                        if rc.name == cmd_name:

                            @click.command(
                                name=cmd_name,
                                help=rc.description
                                or f"Execute {cmd_name} extension command",
                            )
                            @click.argument(
                                "extra_args", nargs=-1, type=click.UNPROCESSED
                            )
                            @click.pass_context
                            def _dynamic_cmd(
                                ctx: click.Context,
                                extra_args: tuple[str, ...],
                                _handler: Any = rc.handler,
                            ) -> None:
                                args_str = " ".join(extra_args)
                                if _handler is None:
                                    return
                                try:
                                    sig = inspect.signature(_handler)
                                    accepts = len(sig.parameters) > 0
                                except (ValueError, TypeError):
                                    accepts = True

                                if inspect.iscoroutinefunction(_handler):
                                    res = asyncio.run(
                                        _handler(args_str) if accepts else _handler()
                                    )
                                else:
                                    res = _handler(args_str) if accepts else _handler()
                                if res is not None:
                                    click.echo(str(res))

                            return _dynamic_cmd
            except Exception:
                pass

    return None
