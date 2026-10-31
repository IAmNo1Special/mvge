from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from mvge.agent.protocol import MvgeAgent
from mvge.core.events import QueueMode
from mvge.provider.model_registry import ModelRegistry

logger = logging.getLogger(__name__)

SLASH_COMMANDS: dict[str, str] = {
    "/help": "Show this help message",
    "/quit": "Exit the REPL",
    "/exit": "Exit the REPL",
    "/model": "Switch or list models: /model [id] or /model --free",
    "/models": "List available models: /models [--free]",
    "/mode": "Toggle queue mode (all/one-at-a-time): /mode or /m",
    "/new": "Start a new tome",
    "/tome": "Show current tome info",
    "/resume": "Resume a previous tome: /resume <path>",
    "/spells": "List or set enabled spells: /spells [comma-separated]",
    "/steer": "Steer agent mid-run: /steer <message>",
    "/followup": "Queue follow-up for post-run: /followup <message>",
    "/refresh-models": "Refresh model catalog from OpenRouter API",
    "/fork": "Fork current tome at entry or active leaf: /fork [entry_id]",
    "/leaves": "List active leaf entry IDs in the current tome",
    "/checkout": "Checkout an existing leaf entry: /checkout <leaf_id>",
    "/undo": "Undo the last summoner invocation",
    "/compact": "Trigger mana pool compaction on the current branch",
    "/skills": "List available skills and their locations",
    "/skill": "Activate a skill: /skill <name> [instructions]",
    "/contemplation": "Show or set contemplation level: /contemplation [level]",
    "/reload": "Rebuild runes, prompt, spells, skills, and harness mid-session",
}


class CommandAction(StrEnum):
    """Enumeration of structured actions produced by slash command dispatch."""

    EXIT = "exit"
    HELP = "help"
    TOME_INFO = "tome_info"
    MODELS_LISTED = "models_listed"
    MODEL_SWITCHED = "model_switched"
    CATALOG_REFRESHED = "catalog_refreshed"
    SPELLS_LISTED = "spells_listed"
    SPELLS_UPDATED = "spells_updated"
    QUEUE_MODE_CHANGED = "queue_mode_changed"
    STEERING_QUEUED = "steering_queued"
    FOLLOWUP_QUEUED = "followup_queued"
    SESSION_RESET = "session_reset"
    SESSION_RESUMED = "session_resumed"
    FORK_CREATED = "fork_created"
    LEAVES_LISTED = "leaves_listed"
    LEAF_CHECKED_OUT = "leaf_checked_out"
    RELOADED = "reloaded"
    RELOAD_QUEUED = "reload_queued"
    INVOCATION_UNDONE = "invocation_undone"
    MANA_COMPACTED = "mana_compacted"
    SKILLS_LISTED = "skills_listed"
    SKILL_ACTIVATED = "skill_activated"
    RUNE_COMMAND = "rune_command"
    CONTEMPLATION_CHANGED = "contemplation_changed"
    CONTEMPLATION_INFO = "contemplation_info"
    INFO = "info"
    ERROR = "error"


@dataclass(frozen=True)
class CommandOutcome:
    """The structured result of dispatching an interactive slash command."""

    command: str
    action: CommandAction
    data: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    should_exit: bool = False


class CommandDispatcher:
    """Deep module for executing interactive slash commands against MvgeAgent."""

    def __init__(
        self,
        agent: MvgeAgent,
        registry: ModelRegistry | None = None,
    ) -> None:
        self._agent = agent
        self._registry = registry or getattr(agent, "model_registry", None)

    @property
    def agent(self) -> MvgeAgent:
        return self._agent

    @agent.setter
    def agent(self, value: MvgeAgent) -> None:
        self._agent = value

    @property
    def registry(self) -> ModelRegistry | None:
        return self._registry

    @registry.setter
    def registry(self, value: ModelRegistry | None) -> None:
        self._registry = value

    async def dispatch(self, command: str) -> CommandOutcome:
        """Execute a slash command and return a structured outcome.

        Args:
            command: The command line string (e.g. '/model google/gemini-2.5-flash').

        Returns:
            CommandOutcome: The structured action, payload data, and display message.
        """
        parts = command.strip().split(maxsplit=1)
        if not parts:
            return CommandOutcome(
                command="",
                action=CommandAction.INFO,
                message="Type /help for available commands",
            )

        cmd = parts[0]
        args = parts[1] if len(parts) > 1 else ""

        if cmd in ("/quit", "/exit"):
            return CommandOutcome(
                command=cmd,
                action=CommandAction.EXIT,
                should_exit=True,
                message="Session ended.",
            )

        if cmd == "/help":
            lines = ["Available commands:"]
            for name, desc in SLASH_COMMANDS.items():
                lines.append(f"  {name:<16} {desc}")
            skills = await self._get_skills_catalog_safe()
            if skills:
                lines.append("\nAvailable skill commands:")
                for s in skills:
                    lines.append(f"  /{s['name']:<15} Run skill {s['name']}")
            rune_cmds = self._get_rune_commands_safe()
            if rune_cmds:
                lines.append("\nAvailable extension commands:")
                for rc in rune_cmds:
                    rc_name = getattr(rc, "name", "")
                    rc_desc = (
                        getattr(rc, "description", "")
                        or f"Run extension command {rc_name}"
                    )
                    lines.append(f"  /{rc_name:<15} {rc_desc}")
            return CommandOutcome(
                command=cmd,
                action=CommandAction.HELP,
                data={"commands": SLASH_COMMANDS},
                message="\n".join(lines),
            )

        if cmd == "/tome":
            tid = self._agent.tome_id or "none"
            model = self._agent.model_id
            spells = self._agent.enabled_spells
            providers = self._agent.registered_providers
            lines = [
                f"Tome ID: {tid}",
                f"Model: {model}",
                f"Enabled spells: {', '.join(spells) or 'none'}",
                f"Providers: {', '.join(providers) or 'none'}",
            ]
            return CommandOutcome(
                command=cmd,
                action=CommandAction.TOME_INFO,
                data={
                    "tome_id": tid,
                    "model_id": model,
                    "enabled_spells": spells,
                    "providers": providers,
                },
                message="\n".join(lines),
            )

        if cmd in ("/model", "/models"):
            stripped = args.strip()
            if not stripped or stripped in ("--free", "-f"):
                if self._registry is None:
                    return CommandOutcome(
                        command=cmd,
                        action=CommandAction.ERROR,
                        data={"error": "Model registry unavailable"},
                        message="Model registry unavailable",
                    )
                all_models = self._registry.list_all()
                is_free_only = stripped in ("--free", "-f")
                if is_free_only:
                    all_models = [
                        m
                        for m in all_models
                        if getattr(m, "is_free", getattr(m, "free", False))
                    ]
                lines = [
                    f"Current model: {self._agent.model_id}",
                    "Available models:",
                ]
                for m in all_models:
                    is_free = getattr(m, "is_free", getattr(m, "free", False))
                    tag = " (free)" if is_free else ""
                    lines.append(f"  {m.id}{tag}")
                lines.append("Try /refresh-models to fetch the latest catalog")
                return CommandOutcome(
                    command=cmd,
                    action=CommandAction.MODELS_LISTED,
                    data={
                        "models": all_models,
                        "is_free_only": is_free_only,
                        "current_model": self._agent.model_id,
                    },
                    message="\n".join(lines),
                )

            candidate = stripped
            if self._registry is not None and self._registry.get(candidate) is None:
                return CommandOutcome(
                    command=cmd,
                    action=CommandAction.ERROR,
                    data={
                        "error": f"Unknown model: {candidate}",
                        "candidate": candidate,
                    },
                    message=(
                        f"Unknown model: {candidate}\n"
                        "Try /refresh-models to fetch the latest catalog"
                    ),
                )

            try:
                await self._agent.switch_model(candidate)
            except Exception as e:
                return CommandOutcome(
                    command=cmd,
                    action=CommandAction.ERROR,
                    data={"error": str(e), "candidate": candidate},
                    message=f"Failed to switch model: {e}",
                )

            getter = getattr(self._registry, "get_supported_contemplation_levels", None)
            supported_levels = getter(candidate) if getter is not None else []
            msg = f"Model switched: {candidate}"
            if supported_levels:
                msg += (
                    f"\nSupported contemplation levels: {', '.join(supported_levels)}"
                )
            return CommandOutcome(
                command=cmd,
                action=CommandAction.MODEL_SWITCHED,
                data={
                    "model_id": candidate,
                    "supported_contemplation_levels": supported_levels,
                },
                message=msg,
            )

        if cmd in ("/contemplation", "/thinking"):
            getter = getattr(self._registry, "get_supported_contemplation_levels", None)
            levels = getter(self._agent.model_id) if getter is not None else []
            current_level = getattr(self._agent, "contemplation_level", "medium")
            if not args.strip():
                lines = [f"Current contemplation level: {current_level}"]
                if levels:
                    lines.append(f"Supported contemplation levels: {', '.join(levels)}")
                else:
                    lines.append("Current model does not declare contemplation levels.")
                return CommandOutcome(
                    command=cmd,
                    action=CommandAction.CONTEMPLATION_INFO,
                    data={
                        "contemplation_level": current_level,
                        "supported_contemplation_levels": levels,
                    },
                    message="\n".join(lines),
                )
            new_level = args.strip().lower()
            if hasattr(self._agent, "set_contemplation_level"):
                await self._agent.set_contemplation_level(new_level)
            msg = f"Contemplation level set to: {new_level}"
            if levels and new_level not in levels:
                msg += f" (Note: model specifies supported levels: {', '.join(levels)})"
            return CommandOutcome(
                command=cmd,
                action=CommandAction.CONTEMPLATION_CHANGED,
                data={
                    "contemplation_level": new_level,
                    "supported_contemplation_levels": levels,
                },
                message=msg,
            )

        if cmd == "/refresh-models":
            if self._registry is None:
                return CommandOutcome(
                    command=cmd,
                    action=CommandAction.ERROR,
                    data={"error": "Model registry unavailable"},
                    message="Model registry unavailable",
                )
            try:
                count = await self._registry.refresh(force_refresh=True)
            except Exception as exc:
                return CommandOutcome(
                    command=cmd,
                    action=CommandAction.ERROR,
                    data={"error": str(exc)},
                    message=f"Failed to refresh models: {exc}",
                )
            return CommandOutcome(
                command=cmd,
                action=CommandAction.CATALOG_REFRESHED,
                data={"new_models_count": count},
                message=f"Models refreshed ({count} new models).",
            )

        if cmd == "/spells":
            if args:
                new_spells = [s.strip() for s in args.split(",") if s.strip()]
                self._agent.set_enabled_spells(new_spells)
                return CommandOutcome(
                    command=cmd,
                    action=CommandAction.SPELLS_UPDATED,
                    data={"enabled_spells": new_spells},
                    message=f"Spells set to: {', '.join(new_spells)}",
                )

            enabled = self._agent.enabled_spells
            available = self._agent.available_spells
            lines = []
            if enabled:
                lines.append(f"Enabled spells: {', '.join(enabled)}")
            other = [s for s in available if s not in enabled]
            if other:
                lines.append(f"Available inactive spells: {', '.join(other)}")
            if not enabled and not other:
                lines.append("No spells available")
            return CommandOutcome(
                command=cmd,
                action=CommandAction.SPELLS_LISTED,
                data={"enabled_spells": enabled, "available_spells": available},
                message="\n".join(lines),
            )

        if cmd in ("/mode", "/m"):
            current_mode = self._agent.queue_mode
            new_mode = (
                QueueMode.ALL
                if current_mode == QueueMode.ONE_AT_A_TIME
                else QueueMode.ONE_AT_A_TIME
            )
            self._agent.queue_mode = new_mode
            return CommandOutcome(
                command=cmd,
                action=CommandAction.QUEUE_MODE_CHANGED,
                data={"queue_mode": new_mode},
                message=f"Queue mode: {new_mode}",
            )

        if cmd in ("/steer", "/s"):
            if args:
                self._agent.steer(args.strip())
                return CommandOutcome(
                    command=cmd,
                    action=CommandAction.STEERING_QUEUED,
                    data={"message": args.strip()},
                    message=f"Steering queued: {args.strip()}",
                )
            return CommandOutcome(
                command=cmd,
                action=CommandAction.ERROR,
                data={"error": "Usage: /steer <message>"},
                message="Usage: /steer <message>",
            )

        if cmd in ("/followup", "/f", "/follow"):
            if args:
                self._agent.follow_up(args.strip())
                return CommandOutcome(
                    command=cmd,
                    action=CommandAction.FOLLOWUP_QUEUED,
                    data={"message": args.strip()},
                    message=f"Follow-up queued: {args.strip()}",
                )
            return CommandOutcome(
                command=cmd,
                action=CommandAction.INFO,
                data={"queue_mode": self._agent.queue_mode},
                message=f"Queue mode: {self._agent.queue_mode}",
            )

        if cmd == "/new":
            try:
                await self._agent.reset_session(resume_tome_id=None)
            except Exception as e:
                return CommandOutcome(
                    command=cmd,
                    action=CommandAction.ERROR,
                    data={"error": str(e)},
                    message=f"Failed to start new tome: {e}",
                )
            return CommandOutcome(
                command=cmd,
                action=CommandAction.SESSION_RESET,
                data={"tome_id": self._agent.tome_id},
                message=f"New tome: {self._agent.tome_id}",
            )

        if cmd == "/resume":
            if args:
                target_path = args.strip()
                try:
                    await self._agent.reset_session(resume_tome_id=target_path)
                except Exception as e:
                    return CommandOutcome(
                        command=cmd,
                        action=CommandAction.ERROR,
                        data={"error": str(e), "path": target_path},
                        message=f"Failed to resume tome: {e}",
                    )
                return CommandOutcome(
                    command=cmd,
                    action=CommandAction.SESSION_RESUMED,
                    data={"tome_id": self._agent.tome_id, "path": target_path},
                    message=f"Resumed tome: {self._agent.tome_id}",
                )
            return CommandOutcome(
                command=cmd,
                action=CommandAction.ERROR,
                data={"error": "Usage: /resume <path-to-tome.jsonl>"},
                message="Usage: /resume <path-to-tome.jsonl>",
            )

        if cmd == "/fork":
            entry_id = args.strip() if args else None
            try:
                new_tome_id = await self._agent.fork_tome(entry_id)
            except Exception as e:
                return CommandOutcome(
                    command=cmd,
                    action=CommandAction.ERROR,
                    data={"error": str(e)},
                    message=f"Failed to fork tome: {e}",
                )
            return CommandOutcome(
                command=cmd,
                action=CommandAction.FORK_CREATED,
                data={"tome_id": new_tome_id, "entry_id": entry_id},
                message=f"Forked tome: {new_tome_id}",
            )

        if cmd == "/leaves":
            try:
                leaves = await self._agent.list_leaves()
            except Exception as e:
                return CommandOutcome(
                    command=cmd,
                    action=CommandAction.ERROR,
                    data={"error": str(e)},
                    message=f"Failed to list leaves: {e}",
                )
            lines = [f"Tome leaves ({len(leaves)}):"]
            for leaf in leaves:
                lines.append(f"  {leaf}")
            return CommandOutcome(
                command=cmd,
                action=CommandAction.LEAVES_LISTED,
                data={"leaves": leaves},
                message="\n".join(lines),
            )

        if cmd == "/checkout":
            if not args.strip():
                return CommandOutcome(
                    command=cmd,
                    action=CommandAction.ERROR,
                    data={"error": "Usage: /checkout <leaf_id>"},
                    message="Usage: /checkout <leaf_id>",
                )
            leaf_id = args.strip()
            try:
                await self._agent.checkout_leaf(leaf_id)
            except Exception as e:
                return CommandOutcome(
                    command=cmd,
                    action=CommandAction.ERROR,
                    data={"error": str(e), "leaf_id": leaf_id},
                    message=f"Failed to checkout leaf {leaf_id}: {e}",
                )
            return CommandOutcome(
                command=cmd,
                action=CommandAction.LEAF_CHECKED_OUT,
                data={"leaf_id": leaf_id},
                message=f"Checked out leaf: {leaf_id}",
            )

        if cmd == "/undo":
            try:
                undone_target_id = await self._agent.undo()
            except Exception as e:
                return CommandOutcome(
                    command=cmd,
                    action=CommandAction.ERROR,
                    data={"error": str(e)},
                    message=f"Failed to undo: {e}",
                )
            return CommandOutcome(
                command=cmd,
                action=CommandAction.INVOCATION_UNDONE,
                data={"target_id": undone_target_id},
                message=f"Undone to invocation: {undone_target_id}",
            )

        if cmd == "/compact":
            try:
                result_msg = await self._agent.compact()
            except Exception as e:
                return CommandOutcome(
                    command=cmd,
                    action=CommandAction.ERROR,
                    data={"error": str(e)},
                    message=f"Failed to compact: {e}",
                )
            return CommandOutcome(
                command=cmd,
                action=CommandAction.MANA_COMPACTED,
                data={"result": result_msg},
                message=result_msg,
            )

        if cmd == "/skills":
            skills = await self._get_skills_catalog_safe()
            if not skills:
                return CommandOutcome(
                    command=cmd,
                    action=CommandAction.SKILLS_LISTED,
                    data={"skills": []},
                    message="No skills registered",
                )
            lines = [f"Available skills ({len(skills)}):"]
            for s in skills:
                lines.append(f"  {s['name']:<20} ({s['scope']}) - {s['description']}")
            return CommandOutcome(
                command=cmd,
                action=CommandAction.SKILLS_LISTED,
                data={"skills": skills},
                message="\n".join(lines),
            )

        if cmd == "/skill":
            if not args.strip():
                return CommandOutcome(
                    command=cmd,
                    action=CommandAction.ERROR,
                    data={"error": "Usage: /skill <skill_name> [instructions]"},
                    message="Usage: /skill <skill_name> [instructions]",
                )
            skill_parts = args.strip().split(maxsplit=1)
            target_skill = skill_parts[0]
            extra_args = skill_parts[1] if len(skill_parts) > 1 else ""
            return await self._dispatch_skill_activation(cmd, target_skill, extra_args)

        if cmd == "/reload":
            # Engine-owned: this branch sits ahead of the dynamic skill and
            # rune command fallbacks, so no Rune command named "reload" can
            # masquerade as the engine reload.
            reload_fn = getattr(self._agent, "reload", None)
            if reload_fn is None or not callable(reload_fn):
                return CommandOutcome(
                    command=cmd,
                    action=CommandAction.ERROR,
                    data={"error": "Agent does not support reload"},
                    message="This agent does not support /reload.",
                )
            result = await reload_fn()
            ok = getattr(result, "ok", False)
            queued = getattr(result, "queued", False)
            # A queued reload has not executed: report it as queued, never
            # as completed. RELOADED means the new state is live.
            if ok and queued:
                action = CommandAction.RELOAD_QUEUED
            else:
                action = CommandAction.RELOADED if ok else CommandAction.ERROR
            return CommandOutcome(
                command=cmd,
                action=action,
                data={
                    "ok": ok,
                    "queued": queued,
                    "prompt_changed": getattr(result, "prompt_changed", False),
                    "spells_changed": getattr(result, "spells_changed", False),
                },
                message=getattr(result, "message", "Reload complete."),
            )

        # Dynamic /<skill_name>
        potential_skill_name = cmd.lstrip("/")
        catalog = await self._get_skills_catalog_safe()
        known_skills = {s["name"]: s for s in catalog if "name" in s}
        if potential_skill_name in known_skills:
            return await self._dispatch_skill_activation(
                cmd, potential_skill_name, args.strip()
            )

        # Dynamic extension commands
        potential_rune_cmd = cmd.lstrip("/")
        for rcmd in self._get_rune_commands_safe():
            if getattr(rcmd, "name", "") == potential_rune_cmd:
                return await self._dispatch_rune_command(
                    command.strip(), rcmd, args.strip()
                )

        return CommandOutcome(
            command=cmd,
            action=CommandAction.ERROR,
            data={"error": f"Unknown command: {cmd}"},
            message=f"Unknown command: {cmd}\nType /help for available commands",
        )

    async def is_command(self, text: str) -> bool:
        """Whether ``dispatch()`` would handle this line as a command.

        Lets hosts (the GUI chat) route only known commands to the
        dispatcher while unknown ``/``-prefixed input still reaches the
        model (harness skill activation, plain text). Mirrors
        ``dispatch()``'s decision structure without executing anything:
        static commands (plus their short aliases), the ``/skill``
        activator, catalog skills, and dynamic rune commands.
        """
        parts = text.strip().split(maxsplit=1)
        if not parts:
            return False
        cmd = parts[0]
        if cmd in SLASH_COMMANDS:
            return True
        if cmd in ("/m", "/s", "/f", "/follow", "/thinking"):
            return True
        name = cmd.lstrip("/")
        catalog = await self._get_skills_catalog_safe()
        if any(s.get("name") == name for s in catalog):
            return True
        return any(
            getattr(rcmd, "name", "") == name for rcmd in self._get_rune_commands_safe()
        )

    async def _dispatch_rune_command(
        self, cmd: str, rcmd: Any, extra_args: str
    ) -> CommandOutcome:
        handler = getattr(rcmd, "handler", None)
        cmd_name = getattr(rcmd, "name", "")
        if handler is None:
            return CommandOutcome(
                command=cmd,
                action=CommandAction.RUNE_COMMAND,
                message=f"Command /{cmd_name} has no handler.",
                data={"command": cmd_name},
            )

        try:
            try:
                sig = inspect.signature(handler)
                accepts_args = len(sig.parameters) > 0
            except (ValueError, TypeError):
                accepts_args = True

            if inspect.iscoroutinefunction(handler):
                res = await handler(extra_args) if accepts_args else await handler()
            else:
                res = (
                    await asyncio.to_thread(handler, extra_args)
                    if accepts_args
                    else await asyncio.to_thread(handler)
                )

            if isinstance(res, CommandOutcome):
                return res

            msg = str(res) if res is not None else f"Executed {cmd}."
            return CommandOutcome(
                command=cmd,
                action=CommandAction.RUNE_COMMAND,
                data={"command": cmd_name, "result": res},
                message=msg,
            )
        except Exception as e:
            return CommandOutcome(
                command=cmd,
                action=CommandAction.ERROR,
                data={"error": str(e), "command": cmd_name},
                message=f"Failed to execute {cmd}: {e}",
            )

    def _get_rune_commands_safe(self) -> list[Any]:
        if hasattr(self._agent, "get_registered_commands"):
            try:
                res = self._agent.get_registered_commands()
                if isinstance(res, list):
                    return res
            except Exception:
                pass
        runner = getattr(self._agent, "_runner", None)
        if runner is not None and hasattr(runner, "get_commands"):
            try:
                cmd_list = runner.get_commands()
                if isinstance(cmd_list, list):
                    return list(cmd_list)
            except Exception:
                pass
        return []

    async def _dispatch_skill_activation(
        self, cmd: str, skill_name: str, extra_args: str
    ) -> CommandOutcome:
        try:
            if hasattr(self._agent, "activate_skill"):
                content = await self._agent.activate_skill(skill_name)
            else:
                content = f"Skill '{skill_name}' activated."

            message = f"Skill '{skill_name}' activated."
            steer_payload = content
            if extra_args:
                steer_payload = f"{content}\n\nSummoner instruction: {extra_args}"
                message += f" Queued instruction: {extra_args}"

            if hasattr(self._agent, "steer"):
                self._agent.steer(steer_payload)

            return CommandOutcome(
                command=cmd,
                action=CommandAction.SKILL_ACTIVATED,
                data={"skill": skill_name, "content": content, "args": extra_args},
                message=message,
            )
        except Exception as e:
            return CommandOutcome(
                command=cmd,
                action=CommandAction.ERROR,
                data={"error": str(e), "skill": skill_name},
                message=f"Failed to activate skill '{skill_name}': {e}",
            )

    async def _get_skills_catalog_safe(self) -> list[dict[str, Any]]:
        if not hasattr(self._agent, "get_skills_catalog"):
            return []
        try:
            res: Any = self._agent.get_skills_catalog()
            if inspect.isawaitable(res):
                res = await res
            if isinstance(res, list):
                return [s for s in res if isinstance(s, dict)]
        except Exception:
            pass
        return []


__all__ = [
    "SLASH_COMMANDS",
    "CommandAction",
    "CommandDispatcher",
    "CommandOutcome",
]
