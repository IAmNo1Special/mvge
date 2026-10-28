from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from pathlib import Path
from typing import Any, cast

import typer
import typer._click as _click
from typer._click.parser import _split_opt
from typer.core import TyperGroup

from mvge.agent.auth import (
    load_api_key_for_realm,
    save_api_key_to_auth,
)
from mvge.agent.config_manager import ConfigManager
from mvge.agent.environment import MvgeEnvironment
from mvge.agent.protocol import AgentFactory, MvgeAgent
from mvge.cli.agent_factory import create_agent, validate_api_key
from mvge.cli.approval_binding import (
    NO_SLOT_WARNING,
    approval_mode_notice,
    approval_presenter_bound,
    resolve_approval_mode,
)
from mvge.cli.approval_presenter import CliApprovalPresenter
from mvge.cli.approval_types import ApprovalMode
from mvge.cli.commands.build import build_app
from mvge.cli.commands.config import config_app
from mvge.cli.commands.info import info_app
from mvge.cli.commands.mvge import mvge_app
from mvge.cli.commands.repl import (
    _display_response,
    run_repl,
)
from mvge.cli.commands.rune import rune_app
from mvge.cli.commands.setup import DefaultCheckGroup, setup_app
from mvge.cli.commands.tome import tome_app
from mvge.cli.commands.tui import run_tui
from mvge.cli.console import (
    configure_streams,
    format_error,
    get_console,
    prompt_api_key,
)
from mvge.cli.dynamic_commands import (
    cli_project_dir,
    configured_rune_layers,
    discover_installed_rune_commands,
    load_rune_cli_command,
)
from mvge.cli.formatting import (
    format_partial_work,
    landed_work_of,
    transcript_length,
)
from mvge.cli.rpc import run_rpc
from mvge.core.constants import DEFAULT_AGENT_NAME
from mvge.provider import NoRealmRegisteredError, get_default_realm_registry
from mvge.provider.model_registry import (
    model_requires_credential,
    resolve_default_model,
    serving_realm_for_model,
)
from mvge.provider.realms import (
    REALM_DEFAULT_MODELS,
    api_key_env_for_realm,
    rune_for_realm,
)
from mvge.runes.installer import install_rune
from mvge.runes.trust import WorkspaceTrustStatus

console = get_console()

_create_agent = create_agent


def _default_spells_from_config(resolved: dict[str, Any]) -> str:
    val = resolved["spells_enabled"].value
    if isinstance(val, list):
        return ",".join(val)
    return str(val)


def resolve_cli_model(
    model: str | None,
    config_model: str | None,
    realm_preference: str | None,
) -> tuple[str | None, str]:
    """Resolve the model and Realm for the CLI key gate, Realm-first.

    An explicit ``--model`` wins, then a model set in agent config, then
    the stored Realm preference through the per-Realm default table.
    With none of those there is no model and no Realm to gate on: the
    caller must bootstrap, prompt for a Realm, or error naming the
    choice. Returning a guessed model here would be the global-default
    bug in a new place.
    """
    resolved = resolve_default_model(
        model=model or config_model, realm=realm_preference
    )
    if resolved is None:
        return None, ""
    return resolved, serving_realm_for_model(resolved)


def _load_config_preference(
    agent_name: str,
) -> tuple[str | None, str | None]:
    """Read the stored model and Realm preference for ``agent_name``.

    A read failure is a missing preference, not a fatal error: the
    caller falls back to the Realm prompt or the bootstrap path, and a
    config file that cannot be read must not become an OpenRouter
    assumption either.
    """
    try:
        resolved = ConfigManager(
            agent_name=agent_name, project_dir=cli_project_dir()
        ).load()
    except Exception:
        return None, None
    model_value = resolved.get("model")
    realm_value = resolved.get("realm")
    config_model = (
        str(model_value.value).strip()
        if model_value is not None and str(model_value.value).strip()
        else None
    )
    realm_preference = (
        str(realm_value.value).strip()
        if realm_value is not None and str(realm_value.value).strip()
        else None
    )
    return config_model, realm_preference


def _warn_untrusted_workspace(agent: MvgeAgent) -> None:
    """Warn the user when the workspace is untrusted.

    Pi-proven shape: trust is enforced at load and warned to the user,
    never steered into the model. Print mode lost that user-visible
    half when the steered turn was removed, so restore it here, after
    the run -- the status is only known once initialize has run.
    """
    status = getattr(agent, "workspace_trust_status", None)
    if status is not None and status != WorkspaceTrustStatus.TRUSTED:
        console.print(
            "[yellow]Warning: workspace is "
            f"{status.value}; project-level runes and spells were skipped."
            "[/yellow]"
        )


async def _run_print_mode(agent: MvgeAgent, prompts: list[str]) -> int:
    turn_start = 0
    try:
        for prompt in prompts:
            turn_start = transcript_length(agent)
            result = await agent.run(prompt)
            _display_response(result)
        _warn_untrusted_workspace(agent)
        return 0
    except Exception as exc:
        console.print(format_error(exc))
        # Spells write to disk as they go, so a failure here can follow a turn
        # that already completed the Summoner's work. Report it, or they retry
        # blind and duplicate work they already have.
        landed = landed_work_of(agent, since=turn_start)
        if landed is not None:
            for line in format_partial_work(landed):
                console.print(line)
        return 1


def _maybe_bootstrap_profile(
    bootstrap: bool | None = None,
    is_interactive: bool = False,
) -> bool:
    """Bootstrap the default 'coding' profile when no agents are installed.

    Interactive first run installs it automatically: the Y/n prompt was the
    step a fresh install could silently skip, leaving the empty default
    agent with no spells. ``--no-bootstrap`` (``bootstrap is False``) still
    opts out, and a non-terminal session never installs on its own.
    """
    from mvge.agent.agent_name import installed_agent_names
    from mvge.agent.installer import install_profile

    if installed_agent_names():
        return False

    if bootstrap is False:
        return False

    should_install = False
    if bootstrap is True:
        should_install = True
    elif is_interactive:
        # At a terminal means a person is present to see the install and
        # its failure; a piped session gets no silent network side effect.
        should_install = sys.stdin.isatty() and sys.stdout.isatty()

    if should_install:
        console.print("[dim]Bootstrapping 'coding' profile from marketplace...[/dim]")
        try:
            res = install_profile("coding")
            console.print(
                f"[green]Successfully bootstrapped 'coding' profile "
                f"({len(res.installed_mvges)} mvges, "
                f"{len(res.installed_runes)} runes installed). "
                "Starting session...[/green]"
            )
            return True
        except Exception as exc:
            console.print(format_error(f"Failed to bootstrap profile: {exc}"))
            console.print(
                "[yellow]No agent installed. Retry with 'mvge --bootstrap' "
                "or install the profile with 'mvge setup profile coding'.[/yellow]"
            )
            return False
    return False


async def _run_agent(
    incantation: str | None,
    model_id: str | None,
    api_key: str | None,
    temperature: float | None,
    max_tokens: int | None,
    contemplation_level: str | None,
    spells_enabled: list[str] | None,
    extension_dir: str | None,
    resume: str | None,
    provider_name: str | None,
    tome_dir: str | None,
    tui: bool,
    agent_name: str = DEFAULT_AGENT_NAME,
    prompts: list[str] | None = None,
    agent_factory: AgentFactory | None = None,
    approval_mode: str | None = None,
    realm_install_attempted: bool = False,
    mode: str = "auto",
    bootstrap: bool | None = False,
) -> int:
    prompts_out = ([incantation] if incantation else []) + (prompts or [])
    approval_mode_val: ApprovalMode = resolve_approval_mode(approval_mode)

    execution_mode = (
        "rpc"
        if mode == "rpc"
        else (
            "tui"
            if (mode == "tui" or tui)
            else ("text" if (mode == "text" or prompts_out) else "repl")
        )
    )
    is_interactive = not prompts_out and execution_mode not in ("rpc", "text")

    if (
        _maybe_bootstrap_profile(bootstrap=bootstrap, is_interactive=is_interactive)
        and agent_name == DEFAULT_AGENT_NAME
    ):
        from mvge.agent.agent_name import resolve_active_agent_name

        agent_name = resolve_active_agent_name(DEFAULT_AGENT_NAME)

    overrides: dict[str, Any] = {}
    if model_id is not None:
        overrides["model"] = model_id
    if temperature is not None:
        overrides["temperature"] = temperature
    if max_tokens is not None:
        overrides["max_tokens"] = max_tokens
    if contemplation_level is not None:
        overrides["contemplation_level"] = contemplation_level
    if spells_enabled is not None:
        overrides["spells_enabled"] = spells_enabled

    agent: MvgeAgent | None = None
    try:
        env = MvgeEnvironment.resolve(
            agent_name=agent_name,
            extension_dir=extension_dir,
            overrides=overrides,
            project_dir=cli_project_dir(),
        )
        resolved = env.config
        # Realm-first resolution: env.model_id is already the coerced
        # model (explicit model, then Realm preference, then empty).
        # Reading resolved["model"] here would KeyError now that the
        # config defaults carry no model, and falling back to a global
        # constant would reintroduce the bug this change removes.
        model_id = model_id or env.model_id
        if not model_id:
            # Bootstrap has already run above when it applies, so an
            # empty model here is a session with no Realm chosen, not a
            # session waiting on a key. Put the Realm choice here on a
            # terminal; otherwise name the choice in the error. Never
            # fill it with an OpenRouter model.
            if is_interactive:
                try:
                    choice = (
                        input(
                            "No model or Realm preference set. Choose a Realm "
                            f"({', '.join(sorted(REALM_DEFAULT_MODELS))}): "
                        )
                        .strip()
                        .lower()
                    )
                except (EOFError, KeyboardInterrupt):
                    choice = ""
                if choice in REALM_DEFAULT_MODELS:
                    model_id = REALM_DEFAULT_MODELS[choice]
                else:
                    console.print(
                        format_error(
                            "No model or Realm preference set. Set a model "
                            "with --model, or a Realm with `mvge config set "
                            "realm <realm>` (openrouter, opencode)."
                        )
                    )
                    return 1
            else:
                console.print(
                    format_error(
                        "No model or Realm preference set. Set a model "
                        "with --model, or a Realm with `mvge config set "
                        "realm <realm>` (openrouter, opencode)."
                    )
                )
                return 1
        if serving_realm_for_model(model_id) == "pollinations":
            # Explicit bootstrap-fallback notice, never a silent fallback.
            # Pollinations is one anonymous model on a legacy lane, about
            # one request per 15 seconds, and that lane can close. A
            # Summoner running on it without an OpenRouter key is told,
            # before heavy use, how to get the free key that replaces it:
            # sign up at openrouter.ai with email, GitHub, or Google (no
            # card), then set OPENROUTER_API_KEY or run with
            # --model openrouter/free. Free OpenRouter models allow 50
            # requests a day, 1,000 a day after $10 in credits.
            try:
                has_openrouter_key = bool(
                    os.environ.get("OPENROUTER_API_KEY")
                    or load_api_key_for_realm("openrouter")
                )
            except Exception:
                has_openrouter_key = False
            if not has_openrouter_key:
                console.print(
                    "[yellow]Running on Pollinations, the keyless bootstrap "
                    "fallback: one anonymous model, about one request per "
                    "15 seconds, on a legacy lane that can close. For heavy "
                    "use, get a free OpenRouter key: sign up at "
                    "openrouter.ai with email, GitHub, or Google (no card), "
                    "then set OPENROUTER_API_KEY or run with "
                    "--model openrouter/free. Free OpenRouter models allow "
                    "50 requests a day, 1,000 a day after $10 in "
                    "credits.[/yellow]"
                )
        if api_key is None:
            # Key after bootstrap, for the model that actually runs.
            # The callback gate runs before bootstrap and cannot know
            # this model yet; resolving the key here, per Realm, is what
            # lets a first run bootstrap before it is asked for a key.
            key_realm = serving_realm_for_model(model_id)
            api_key = os.environ.get(
                api_key_env_for_realm(key_realm)
            ) or load_api_key_for_realm(key_realm)
            if api_key is None and not model_requires_credential(model_id):
                api_key = ""
            if api_key is None:
                if is_interactive:
                    prompted_key = prompt_api_key(console, key_realm)
                    if prompted_key:
                        save_api_key_to_auth(prompted_key, key_realm)
                        api_key = prompted_key
                if api_key is None:
                    console.print(
                        format_error(
                            f"API key required. Set "
                            f"{api_key_env_for_realm(key_realm)}, "
                            "run 'mvge setup', or use --api-key"
                        )
                    )
                    return 1
        temperature = (
            temperature
            if temperature is not None
            else float(resolved["temperature"].value)
        )
        max_tokens = (
            max_tokens if max_tokens is not None else int(resolved["max_tokens"].value)
        )
        contemplation_level = contemplation_level or str(
            resolved["contemplation_level"].value
        )
        spells_joined = (
            ",".join(spells_enabled)
            if spells_enabled is not None
            else _default_spells_from_config(resolved)
        )

        reg = get_default_realm_registry()
        active_realm = None
        with contextlib.suppress(Exception):
            _, active_realm = reg.resolve(
                model_id, api_key=api_key, provider_name=provider_name
            )

        if active_realm is not None and active_realm.is_router:
            target_realm = getattr(active_realm, "realm_name", "openrouter")
            providers = reg.get_providers_for_realm(target_realm)
            if len(providers) > 1 and not provider_name and "/" not in model_id:
                if sys.stdin.isatty() and sys.stdout.isatty() and not prompts_out:
                    console.print(
                        f"[yellow]Realm '{target_realm}' is a router. "
                        f"Available providers: {', '.join(providers[:10])}...[/yellow]"
                    )
                    prompted = input("Select provider: ").strip()
                    if prompted:
                        provider_name = prompted
                    else:
                        console.print(
                            format_error(
                                "Provider selection required for router realm."
                            )
                        )
                        return 1
                else:
                    console.print(
                        format_error(
                            f"Provider selection required for router realm "
                            f"'{target_realm}'. Specify --provider or use "
                            "provider/model format."
                        )
                    )
                    return 1
        elif active_realm is not None and not active_realm.is_router:
            if not provider_name:
                provider_name = getattr(active_realm, "realm_name", None)

        if approval_mode is not None:
            notice = approval_mode_notice(approval_mode_val)
            if notice is not None:
                style = "yellow" if approval_mode_val == "allow-all" else "dim"
                console.print(f"[{style}]{notice}[/{style}]")
        if tui and not prompts_out and approval_mode_val == "prompt":
            console.print(
                "[dim]Note: approval prompts are unavailable in TUI mode; "
                "gated spell casts will be denied. Use the REPL for "
                "interactive approval, or --approval-mode allow-all/deny "
                "for this run.[/dim]"
            )
        if execution_mode == "rpc":
            validate_api_key(api_key, model_id=model_id)
            agent = await _create_agent(
                model=model_id,
                api_key=api_key,
                spells=spells_joined,
                extension_dir=extension_dir,
                tome_dir=tome_dir,
                resume=resume,
                provider=provider_name,
                temperature=temperature,
                max_tokens=max_tokens,
                contemplation=contemplation_level,
                agent_name=agent_name,
                agent_factory=agent_factory,
            )
            return await run_rpc(agent)

        if not prompts_out:
            if tui:
                await run_tui(
                    model=model_id,
                    api_key=api_key,
                    spells=spells_joined,
                    extension_dir=extension_dir,
                    resume=resume,
                    provider=provider_name,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    contemplation=contemplation_level,
                    tome_dir=tome_dir,
                    agent_name=agent_name,
                    agent_factory=agent_factory,
                    approval_mode=approval_mode,
                )
                return 0

            await run_repl(
                model=model_id,
                api_key=api_key,
                spells=spells_joined,
                extension_dir=extension_dir,
                resume=resume,
                provider=provider_name,
                temperature=temperature,
                max_tokens=max_tokens,
                contemplation=contemplation_level,
                tome_dir=tome_dir,
                agent_name=agent_name,
                agent_factory=agent_factory,
                approval_mode=approval_mode,
            )
            return 0

        # The model's Realm, not a bare key: this validator needs to know whether
        # the Realm being served requires a credential at all. Passing nothing
        # made a Realm whose free tier needs no key fail here with a demand for
        # a credential that could not change the outcome.
        validate_api_key(api_key, model_id=model_id)
        agent = await _create_agent(
            model=model_id,
            api_key=api_key,
            spells=spells_joined,
            extension_dir=extension_dir,
            tome_dir=tome_dir,
            resume=resume,
            provider=provider_name,
            temperature=temperature,
            max_tokens=max_tokens,
            contemplation=contemplation_level,
            agent_name=agent_name,
            agent_factory=agent_factory,
        )
        presenter = CliApprovalPresenter(mode=approval_mode_val)
        # getattr: a custom agent_factory may return an agent without the
        # engine runner accessor; that is a missing slot (warn, fail
        # closed), not a crash.
        runner = getattr(agent, "runner", None)
        with approval_presenter_bound(runner, presenter) as bound:
            if not bound:
                console.print(f"[yellow]{NO_SLOT_WARNING}[/yellow]")
            return await _run_print_mode(agent, prompts_out)
    except NoRealmRegisteredError as exc:
        is_interactive = not prompts_out and not tui
        # The exception carries the Rune that provides the model's Realm. Reading
        # it from here rather than hardcoding one is what lets a second Realm be
        # the default without this prompt offering the wrong install. When the
        # Realm could not be determined there is no install to offer, so the
        # error is reported as-is instead of a prompt naming nothing.
        missing_rune = exc.rune_name or rune_for_realm(exc.realm)
        # Is anyone there to be asked? That is a fact about the terminal, and it
        # is a different fact from whether this run's mode ever asks. A script, a
        # pipe and CI have no TTY, and there `input()` raises EOFError -- which
        # the old code caught and read as "no", so the one remedy available
        # turned itself off exactly when nobody was there to decline it. That is
        # what made the documented first run a dead end: the Realm is missing,
        # the Rune that provides it is named on screen, and the run still exits
        # 1.
        #
        # So the question is only put where there is somebody to answer it, and
        # the install runs on the prompt's default answer where there is not.
        # Which is the same act a Summoner typing "y" chose, and ADR-0015
        # already records the first run as depending on a marketplace fetch for
        # the Rune. Manifest-declared python dependencies stay a separate gate
        # and still fail closed without a terminal: fetching arbitrary packages
        # on the strength of a manifest is not the same act as fetching the Rune
        # that declares them.
        at_a_terminal = sys.stdin.isatty() and sys.stdout.isatty()
        may_ask = at_a_terminal and is_interactive
        answer = "y" if not at_a_terminal else "n"
        # One install, one retry. The retry is what makes the self-heal worth
        # having -- the factory only exists once the Rune has loaded -- but the
        # recursion that performs it is unbounded unless it is bounded here, and
        # an install that does not fix the problem (a Rune that loads and
        # registers nothing, a marketplace entry that names the wrong directory)
        # would otherwise reinstall on every frame until the stack ran out. A
        # second failure is reported rather than retried, which is also the
        # honest answer: the same Rune installed twice is the same Rune.
        can_install = missing_rune and not realm_install_attempted
        if may_ask and can_install:
            try:
                answer = (
                    input(
                        "No Realm extension installed. Would you like to install "
                        f"'{missing_rune}' from the marketplace now? [Y/n]: "
                    )
                    .strip()
                    .lower()
                )
            except (EOFError, KeyboardInterrupt):
                answer = "n"

        if can_install and answer in ("", "y", "yes"):
            try:
                install_rune(missing_rune)
                console.print(
                    f"[green]Successfully installed '{missing_rune}'. "
                    "Starting session...[/green]"
                )
                return await _run_agent(
                    incantation=incantation,
                    model_id=model_id,
                    api_key=api_key,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    contemplation_level=contemplation_level,
                    spells_enabled=spells_enabled,
                    extension_dir=extension_dir,
                    resume=resume,
                    provider_name=provider_name,
                    tome_dir=tome_dir,
                    tui=tui,
                    agent_name=agent_name,
                    prompts=prompts,
                    agent_factory=agent_factory,
                    approval_mode=approval_mode,
                    realm_install_attempted=True,
                    mode=mode,
                    bootstrap=bootstrap,
                )
            except Exception as install_exc:
                console.print(format_error(install_exc))
                return 1

        console.print(format_error(exc))
        return 1
    except Exception as exc:
        console.print(format_error(exc))
        return 1
    finally:
        if agent is not None:
            await agent.close()


class MvgeGroup(TyperGroup):
    """Rune-provided CLI commands, resolved the same way Rune loading does.

    Discovery goes through the one layer-stack resolver, so a command that
    mounts is a Rune that would load. The inputs come from the group's own
    parsed options via Click's public ``ctx.params``, which is what the Rune
    loading path already honours -- a second source of truth here is how the
    two came to disagree.
    """

    def _rune_discovery(self, ctx: _click.Context) -> dict[str, Any]:
        """The inputs Rune CLI command discovery resolves with.

        Taken from the group's own parsed options where Click has them, and
        from the same sources Rune loading reads otherwise: the agent name,
        the project the CLI is running in, ``--extension-dir``, and any
        configured ``rune_paths``. Discovery that resolved with fewer inputs
        than loading is exactly the divergence this ADR ends.
        """
        params = getattr(ctx, "params", None) or {}
        agent_name = params.get("agent_name") or DEFAULT_AGENT_NAME
        project_dir = params.get("project_dir") or cli_project_dir()
        return {
            "agent_name": agent_name,
            "project_dir": Path(project_dir),
            "extension_dir": params.get("extension_dir"),
            "extras": configured_rune_layers(agent_name, project_dir),
        }

    def get_command(self, ctx: _click.Context, cmd_name: str) -> _click.Command | None:
        cmd = super().get_command(ctx, cmd_name)
        if cmd is not None:
            return cmd
        res = load_rune_cli_command(cmd_name, **self._rune_discovery(ctx))
        if res is not None:
            return cast(_click.Command, res)
        return None

    def list_commands(self, ctx: _click.Context) -> list[str]:
        base_cmds = super().list_commands(ctx)
        rune_cmds = list(discover_installed_rune_commands(**self._rune_discovery(ctx)))
        return sorted(set(base_cmds + rune_cmds))

    def invoke(self, ctx: _click.Context) -> Any:
        if ctx._protected_args:
            args = [*ctx._protected_args, *ctx.args]
            first = args[0] if args else ""
            if not _split_opt(first)[0]:
                is_prompt = False
                cmd = self.get_command(ctx, first)
                if cmd is None:
                    is_prompt = True
                elif len(args) > 1 and not _split_opt(args[1])[0]:
                    subcommands = (
                        cmd.list_commands(ctx) if hasattr(cmd, "list_commands") else []
                    )
                    is_leaf = not bool(subcommands)
                    params = getattr(cmd, "params", [])
                    has_positional = any(
                        getattr(p, "param_type_name", None) == "argument"
                        for p in params
                    )
                    if is_leaf and not has_positional:
                        is_prompt = True

                if is_prompt:
                    ctx.meta["prompts"] = list(args)
                    ctx.args = []
                    ctx._protected_args = []
                    with ctx:
                        return _click.Command.invoke(self, ctx)
        return TyperGroup.invoke(self, ctx)


app = typer.Typer(
    name="mvge",
    help="mvge - a Python-based AI coding agent",
    cls=MvgeGroup,
)


@app.callback(invoke_without_command=True, cls=MvgeGroup)
def _repl_callback(
    ctx: typer.Context,
    incantation: str | None = typer.Option(
        None, "--incantation", help="Prompt to send (if omitted, starts REPL)"
    ),
    model: str | None = typer.Option(None, "--model", "-m", help="Model to use"),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        help="OpenRouter API key (or set OPENROUTER_API_KEY env)",
    ),
    temperature: float | None = typer.Option(
        None, "--temperature", "-t", help="Sampling temperature"
    ),
    max_tokens: int | None = typer.Option(None, "--max-tokens", help="Maximum tokens"),
    contemplation: str | None = typer.Option(
        None, "--contemplation", "-c", help="Contemplation level"
    ),
    spells: str | None = typer.Option(
        None,
        "--spells",
        "-s",
        help="Comma-separated list of enabled spells",
    ),
    extension_dir: str | None = typer.Option(
        None,
        "--extension-dir",
        "-e",
        help="Path to extension runes directory",
    ),
    resume: str | None = typer.Option(
        None,
        "--resume",
        "-r",
        help="Path to a session JSONL file to resume",
    ),
    provider: str | None = typer.Option(
        None,
        "--provider",
        "-p",
        help="Provider name (from extension registration)",
    ),
    tome_dir: str | None = typer.Option(
        None,
        "--tome-dir",
        help="Custom tome storage directory",
    ),
    tui: bool = typer.Option(
        False,
        "--tui",
        help="Use the full-screen TUI instead of the streaming REPL",
    ),
    agent_name: str = typer.Option(
        DEFAULT_AGENT_NAME,
        "--agent-name",
        help="Agent name for agent-specific rune directory",
    ),
    approval_mode: str | None = typer.Option(
        None,
        "--approval-mode",
        help=(
            "One-run approval behavior for gated spell casts: "
            "deny, prompt, or allow-all. Never persisted; "
            "environment variables never grant approval."
        ),
    ),
    mode: str = typer.Option(
        "auto",
        "--mode",
        help=(
            "Execution mode: 'auto' (text or tui based on --tui), "
            "'text', 'tui', or 'rpc' (headless stdio JSONL RPC)"
        ),
    ),
    bootstrap: bool | None = typer.Option(
        None,
        "--bootstrap/--no-bootstrap",
        help=(
            "Bootstrap the default 'coding' profile when no agents are "
            "installed (automatic on interactive first run; "
            "--no-bootstrap skips it)"
        ),
    ),
) -> None:
    """Launch the Mvge interactive REPL, or run a single prompt."""
    if ctx.invoked_subcommand is not None:
        return

    if approval_mode is not None:
        try:
            resolve_approval_mode(approval_mode)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc

    # Which variable holds the credential depends on the model's Realm, so the
    # model has to be known first. With no --model the Realm is the default
    # model's. The callback-resolved model IS passed onwards to _run_agent
    # (model or key_model): key_model already follows the config layer's
    # precedence (explicit model, then config model, then Realm
    # preference), so it cannot disagree with what _run_agent resolves --
    # except for a Realm the Summoner just chose at the prompt, which is
    # exactly the choice that must reach the session.
    #
    # The Realm that serves the model, not the slug's prefix. Those differ for a
    # routed slug: `nvidia/nemotron-3-ultra-550b-a55b:free` is served by
    # openrouter, so reading the prefix asked for NVIDIA_API_KEY and for
    # ~/.agents/auth/nvidia.json while the credential `mvge export` writes sits
    # in openrouter.json doing nothing.
    # Realm-first model resolution (ADR-0028). An explicit --model wins,
    # then a model set in agent config, then the stored Realm preference
    # through the per-Realm default table. With none of those there is no
    # model and no Realm to gate on, and this callback must not invent
    # one: that was the global-default bug, an OpenRouter key prompt for
    # every Summoner. With --no-bootstrap the choice is put here, as a
    # Realm prompt on a terminal or a Realm-choice error otherwise.
    # Otherwise the gate is deferred to _run_agent, which bootstraps
    # first and then resolves the key for the model that actually runs.
    config_model, realm_preference = _load_config_preference(agent_name)
    key_model, key_realm = resolve_cli_model(model, config_model, realm_preference)
    # Bootstrap can only run for an interactive session with no prompt:
    # _maybe_bootstrap_profile runs inside _run_agent only in that case,
    # so a prompt run with no model and no Realm has no bootstrap to
    # defer to, and deferring it would hand a mocked or keyless run a
    # session it never chose. Prompt or error here instead.
    prompts_out = cast("list[str] | None", ctx.meta.get("prompts"))
    may_bootstrap = (
        bootstrap is not False
        and incantation is None
        and not prompts_out
        and mode not in ("text", "rpc")
    )
    if key_model is None and not may_bootstrap:
        if sys.stdin.isatty() and sys.stdout.isatty():
            try:
                choice = (
                    input(
                        "No model or Realm preference set. Choose a Realm "
                        f"({', '.join(sorted(REALM_DEFAULT_MODELS))}): "
                    )
                    .strip()
                    .lower()
                )
            except (EOFError, KeyboardInterrupt):
                choice = ""
            if choice in REALM_DEFAULT_MODELS:
                key_model = REALM_DEFAULT_MODELS[choice]
                key_realm = choice
        if key_model is None:
            console.print(
                format_error(
                    "No model or Realm preference set. Set a model with "
                    "--model, or a Realm with `mvge config set realm "
                    "<realm>` (openrouter, opencode), then run again."
                )
            )
            raise typer.Exit(1)
    if key_model is not None and api_key is None:
        # Only the Realm's own variable, then its own credential file. The
        # unconditional OpenRouter fallback this replaced sent an OpenRouter key
        # to whichever Realm the model named, which since `opencode` became the
        # default was the common case rather than an edge: the key went to
        # opencode.ai, authenticating at the wrong host and exposing the
        # Summoner's credential to a host that never asked for it.
        api_key = os.environ.get(api_key_env_for_realm(key_realm))
    if api_key is None and key_realm:
        # Gated on a resolved Realm, not just on a missing key. On the
        # deferred/bootstrap path key_realm is "" and load_api_key_for_realm
        # maps an empty Realm to the OpenRouter file, so an ungated lookup
        # here handed _run_agent an OpenRouter key it never asked for --
        # and _run_agent then skipped its own per-Realm resolution, so a
        # model bootstrap resolved to opencode ran with the OpenRouter key
        # (validate_api_key only checks the sk-or- format). Deferred runs
        # must reach _run_agent with api_key=None.
        api_key = load_api_key_for_realm(key_realm)
    if api_key is None and not model_requires_credential(key_model):
        # A model this engine will serve without one. Only reached once every
        # lookup above came up empty, so a key the Summoner *did* set is still
        # forwarded -- the exemption removes the requirement, not the key.
        # Asking for a credential here is a dead end, since there is none to
        # set, and the shipped default is exempt, so the default could not
        # otherwise start on the machine it ships for.
        #
        # Asked per model, not per Realm: a Realm whose free tier is anonymous
        # still serves keyed paid models, so demanding a key for those would be
        # as wrong as demanding one for the free tier.
        api_key = ""
    if key_model is not None and api_key is None:
        if sys.stdin.isatty() and sys.stdout.isatty():
            prompted_key = prompt_api_key(console, key_realm)
            if prompted_key:
                saved_to = save_api_key_to_auth(prompted_key, key_realm)
                console.print(f"[green]Saved API key to {saved_to}[/green]")
                api_key = prompted_key
        if key_model is not None and api_key is None:
            console.print(
                format_error(
                    f"API key required. Set {api_key_env_for_realm(key_realm)}, "
                    "run 'mvge setup', or use --api-key"
                )
            )
            raise typer.Exit(1)

    spells_list = (
        [s.strip() for s in spells.split(",") if s.strip()] if spells else None
    )

    prompts = cast("list[str] | None", ctx.meta.get("prompts"))

    code = asyncio.run(
        _run_agent(
            incantation=incantation,
            # The callback-resolved model, not just --model: key_model
            # carries a config model, a Realm preference, or the Realm
            # the Summoner just chose at the prompt. Passing only --model
            # would discard that choice, so _run_agent would prompt a
            # second time (REPL) or error (prompt run) after the key for
            # the chosen Realm had already been gated.
            model_id=model or key_model,
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            contemplation_level=contemplation,
            spells_enabled=spells_list,
            extension_dir=extension_dir,
            resume=resume,
            provider_name=provider,
            tome_dir=tome_dir,
            tui=tui,
            agent_name=agent_name,
            prompts=prompts,
            approval_mode=approval_mode,
            mode=mode,
            bootstrap=bootstrap,
        )
    )
    if code:
        raise typer.Exit(code)


app.add_typer(build_app, name="build")
app.add_typer(config_app, name="config")
app.add_typer(info_app, name="info")
app.add_typer(tome_app, name="tome")
app.add_typer(rune_app, name="rune")
app.add_typer(mvge_app, name="mvge")
app.add_typer(setup_app, name="setup", cls=DefaultCheckGroup)


def main() -> None:
    configure_streams()
    app()


if __name__ == "__main__":
    main()
