from __future__ import annotations

from collections.abc import Callable

from rich.console import Console

from mvge.agent.commands import (
    SLASH_COMMANDS,
    CommandAction,
    CommandOutcome,
)
from mvge.agent.commands import (
    CommandDispatcher as AgentCommandDispatcher,
)
from mvge.agent.protocol import MvgeAgent
from mvge.cli.formatting import format_error
from mvge.provider.model_registry import ModelRegistry

_console = Console()


class CliCommandDispatcher:
    """CLI presentation adapter executing slash commands and formatting output."""

    def __init__(
        self,
        agent: MvgeAgent,
        registry: ModelRegistry | None = None,
        out: Callable[[str], None] = _console.print,
    ) -> None:
        self._inner = AgentCommandDispatcher(agent, registry)
        self._out = out

    @property
    def agent(self) -> MvgeAgent:
        return self._inner.agent

    @agent.setter
    def agent(self, value: MvgeAgent) -> None:
        self._inner.agent = value

    @property
    def registry(self) -> ModelRegistry | None:
        return self._inner.registry

    @registry.setter
    def registry(self, value: ModelRegistry | None) -> None:
        self._inner.registry = value

    async def dispatch(
        self, command: str, out: Callable[[str], None] | None = None
    ) -> bool:
        """Execute a slash command and render Rich output to console.

        Returns:
            bool: True if the interactive session should exit, False otherwise.
        """
        output = out if out is not None else self._out
        parts = command.strip().split(maxsplit=1)
        if not parts:
            return False

        cmd = parts[0]
        args = parts[1] if len(parts) > 1 else ""

        # Pre-execution informational messages matching CLI UX
        if cmd == "/refresh-models":
            output("[yellow]Fetching latest models from OpenRouter...[/yellow]")
        elif cmd == "/new":
            output("[yellow]Starting a new session...[/yellow]")
        elif cmd == "/resume" and args:
            output(f"[yellow]Resuming tome {args}...[/yellow]")

        outcome = await self._inner.dispatch(command)
        self._render_outcome(outcome, output)
        return outcome.should_exit

    def _render_outcome(
        self, outcome: CommandOutcome, output: Callable[[str], None]
    ) -> None:
        if outcome.action == CommandAction.EXIT:
            return

        if outcome.action == CommandAction.HELP:
            output("[bold]Available commands:[/bold]")
            for name, desc in outcome.data.get("commands", {}).items():
                output(f"  [cyan]{name:<15}[/cyan] {desc}")
            return

        if outcome.action == CommandAction.TOME_INFO:
            tid = outcome.data.get("tome_id", "none")
            output(f"[dim]Tome ID: {tid}[/dim]")
            output(f"[dim]Model: {outcome.data.get('model_id')}[/dim]")
            spells = outcome.data.get("enabled_spells", [])
            output(f"[bold]Enabled spells:[/bold] {', '.join(spells) or 'none'}")
            providers = outcome.data.get("providers", [])
            output(f"[dim]Providers: {', '.join(providers) or 'none'}[/dim]")
            return

        if outcome.action == CommandAction.MODELS_LISTED:
            output(f"[bold]Current model:[/bold] {outcome.data.get('current_model')}")
            output("[bold]Available models:[/bold]")
            for m in outcome.data.get("models", []):
                is_free = getattr(m, "is_free", getattr(m, "free", False))
                tag = " [green](free)[/green]" if is_free else ""
                output(f"  {m.id}{tag}")
            output("[dim]Try /refresh-models to fetch the latest catalog[/dim]")
            return

        if outcome.action == CommandAction.MODEL_SWITCHED:
            output(f"[green]Model switched: {outcome.data.get('model_id')}[/green]")
            levels = outcome.data.get("supported_contemplation_levels", [])
            if levels:
                output(
                    f"[dim]Supported contemplation levels: {', '.join(levels)}[/dim]"
                )
            return

        if outcome.action in (
            CommandAction.CONTEMPLATION_CHANGED,
            CommandAction.CONTEMPLATION_INFO,
        ):
            output(f"[dim]{outcome.message}[/dim]")
            return

        if outcome.action == CommandAction.CATALOG_REFRESHED:
            count = outcome.data.get("new_models_count", 0)
            output(f"[green]Models refreshed ({count} new models).[/green]")
            return

        if outcome.action == CommandAction.SPELLS_UPDATED:
            spells = outcome.data.get("enabled_spells", [])
            output(f"[green]Spells set to: {', '.join(spells)}[/green]")
            return

        if outcome.action == CommandAction.SPELLS_LISTED:
            enabled = outcome.data.get("enabled_spells", [])
            available = outcome.data.get("available_spells", [])
            if enabled:
                output(f"[bold]Enabled spells:[/bold] {', '.join(enabled)}")
            other = [s for s in available if s not in enabled]
            if other:
                output(f"[dim]Available inactive spells:[/dim] {', '.join(other)}")
            if not enabled and not other:
                output("[dim]No spells available[/dim]")
            return

        if outcome.action == CommandAction.SKILLS_LISTED:
            skills = outcome.data.get("skills", [])
            if not skills:
                output("[dim]No skills registered[/dim]")
                return
            output(f"[bold]Available skills ({len(skills)}):[/bold]")
            for s in skills:
                name = s.get("name", "")
                scope = s.get("scope", "unknown")
                desc = s.get("description", "")
                output(f"  {name:<20} ({scope}) - {desc}")
            return

        if outcome.action == CommandAction.SKILL_ACTIVATED:
            output(f"[green]{outcome.message}[/green]")
            return

        if outcome.action == CommandAction.QUEUE_MODE_CHANGED:
            output(f"[dim]Queue mode: {outcome.data.get('queue_mode')}[/dim]")
            return

        if outcome.action == CommandAction.STEERING_QUEUED:
            output(f"[dim]Steering queued: {outcome.data.get('message')}[/dim]")
            return

        if outcome.action == CommandAction.FOLLOWUP_QUEUED:
            output(f"[dim]Follow-up queued: {outcome.data.get('message')}[/dim]")
            return

        if outcome.action == CommandAction.INFO:
            output(f"[dim]Queue mode: {outcome.data.get('queue_mode')}[/dim]")
            return

        if outcome.action == CommandAction.SESSION_RESET:
            output(f"[green]New tome: {outcome.data.get('tome_id')}[/green]")
            return

        if outcome.action == CommandAction.SESSION_RESUMED:
            output(f"[green]Resumed tome: {outcome.data.get('tome_id')}[/green]")
            return

        if outcome.action == CommandAction.FORK_CREATED:
            output(f"[green]{outcome.message}[/green]")
            return

        if outcome.action == CommandAction.LEAVES_LISTED:
            leaves = outcome.data.get("leaves", [])
            if not leaves:
                output("[dim]No leaves available[/dim]")
                return
            output(f"[bold]Tome leaves ({len(leaves)}):[/bold]")
            for leaf in leaves:
                output(f"  {leaf}")
            return

        if outcome.action == CommandAction.LEAF_CHECKED_OUT:
            output(f"[green]{outcome.message}[/green]")
            return

        if outcome.action == CommandAction.INVOCATION_UNDONE:
            output(f"[green]{outcome.message}[/green]")
            return

        if outcome.action == CommandAction.MANA_COMPACTED:
            output(f"[green]{outcome.message}[/green]")
            return

        if outcome.action == CommandAction.RELOADED:
            output(f"[green]{outcome.message}[/green]")
            return

        if outcome.action == CommandAction.RELOAD_QUEUED:
            output(f"[dim]{outcome.message}[/dim]")
            return

        if outcome.action == CommandAction.RUNE_COMMAND:
            # The message is the rune command's own output, not a status
            # line, so it prints verbatim rather than dimmed or green.
            output(outcome.message)
            return

        if outcome.action == CommandAction.ERROR:
            msg_lines = outcome.message.split("\n", 1)
            output(format_error(msg_lines[0]))
            if len(msg_lines) > 1:
                output(f"[dim]{msg_lines[1]}[/dim]")
            return


__all__ = [
    "SLASH_COMMANDS",
    "CliCommandDispatcher",
]
