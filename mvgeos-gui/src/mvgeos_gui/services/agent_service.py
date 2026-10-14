"""Agent service layer connecting GUI to CodingMvge and MvgeHarness."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dotenv import load_dotenv
from mvgeos_agent import Mvge
from mvgeos_agent.agent_name import resolve_active_agent_name
from mvgeos_agent.auth import load_api_key_for_realm
from mvgeos_agent.commands import (
    CommandAction,
    CommandDispatcher,
    CommandOutcome,
)
from mvgeos_agent.environment import MvgeEnvironment
from mvgeos_agent.protocol import MvgeAgent
from mvgeos_core.errors import (
    AuthenticationError,
    RateLimitError,
    UpstreamTimeoutError,
)
from mvgeos_core.events import (
    MvgeEvent,
    MvgeEventType,
)
from mvgeos_provider import NoRealmRegisteredError
from mvgeos_provider.model_registry import ModelRegistry
from mvgeos_provider.realms import (
    DEFAULT_REALM,
    api_key_env_for_realm,
    rune_for_realm,
)

from mvgeos_gui.approval.presenter import bind_approval_presenter
from mvgeos_gui.context_usage import extract_token_usage
from mvgeos_gui.models import (
    Artifact,
    ArtifactType,
    BackgroundTask,
    ChatMessage,
    TaskStatus,
)
from mvgeos_gui.services.config_service import API_KEY_REALM, ConfigService
from mvgeos_gui.services.turn_owner import Turn, TurnOwner, transcript_for
from mvgeos_gui.transcript import InvocationTranscript

if TYPE_CHECKING:
    from mvgeos_gui.state import AppState

logger = logging.getLogger(__name__)


def _upstream_stall_message(detail: str) -> str:
    """Friendly transcript for an upstream mid-stream stall.

    Suggests retry or model switch only; never switches models automatically.
    """
    return (
        "**Upstream Provider Stalled**: The model host stopped responding "
        "mid-stream and the connection was closed.\n\n"
        f"- **Details**: {detail}\n"
        "- **Suggested actions**: Wait a few moments and try again, or switch "
        "to another model via the model selector below."
    )


def _load_saved_gui_api_key() -> str | None:
    """Read the API key persisted via the GUI settings dialog, if any."""
    with contextlib.suppress(Exception):
        saved = ConfigService().load_app_settings().api_key
        if saved and saved.strip():
            return saved.strip()
    return None


def resolve_api_key(
    explicit_key: str | None = None, realm: str | None = None
) -> str | None:
    """Resolve the API key for a realm.

    Precedence: explicit argument, the realm's own environment variable, then
    ``MVGEOS_API_KEY``, then -- for OpenRouter only -- the key saved through the
    GUI settings dialog (``gui.json``/keyring), then the ``mvgeos setup`` auth
    file for that realm.

    Every step is scoped to the Realm being asked about. Reading one Realm's
    credential and handing it to another authenticates at the wrong host, fails
    every call with a message that names the model rather than the credential,
    and gives the Summoner's key to a host that never asked for it.

    A Realm with no credential of its own resolves to ``None``. For OpenCode Zen
    that is the normal case, not a missing setup step: its free tier needs no
    key.
    """
    if explicit_key and explicit_key.strip():
        return explicit_key.strip()
    target = realm or DEFAULT_REALM
    env_key = os.environ.get(api_key_env_for_realm(target)) or os.environ.get(
        "MVGEOS_API_KEY"
    )
    if env_key and env_key.strip():
        return env_key.strip()
    # The settings dialog keeps a single credential and the storage names it
    # `openrouter_api_key`, so it is only ever an OpenRouter key. Reading it for
    # any other Realm sent the Summoner's OpenRouter credential to that Realm's
    # host -- which, with `opencode` as the default Realm, was the first-run path
    # for the GUI as well as the CLI and the library.
    if target == API_KEY_REALM:
        saved_key = _load_saved_gui_api_key()
        if saved_key:
            return saved_key
    with contextlib.suppress(Exception):
        auth_key = load_api_key_for_realm(target)
        if auth_key and auth_key.strip():
            return auth_key.strip()
    return None


class AgentService:
    """Service bridge managing agent runtime lifecycle and event sink for GUI."""

    def __init__(
        self,
        project_path: Path,
        api_key: str | None = None,
        agent_factory: Callable[..., Any] | None = None,
        model_registry: ModelRegistry | None = None,
    ) -> None:
        self._project_path = project_path
        env_path = (self._project_path / ".env").resolve()
        if env_path.is_file():
            load_dotenv(env_path)
        self._api_key = resolve_api_key(api_key, DEFAULT_REALM)
        self._agent_factory = agent_factory
        self._model_registry = model_registry or ModelRegistry()
        self._agent: MvgeAgent | None = None
        # Exactly one owner of "which turn is active". The turn's message,
        # state, transcript and task live on the Turn, never in separate
        # fields that a stale finally could clobber.
        self._turns = TurnOwner()
        self._turns.subscribe(
            lambda turn, event: self.handle_event(
                event, message=turn.message, state=turn.state
            )
        )
        self._start_time: float = 0.0
        self._pending_spell_starts: dict[str, float] = {}
        self._invoked_skill_names: set[str] = set()
        self._loaded_skill_names: set[str] = set()
        self._last_notify_time: float = 0.0
        self._notify_interval: float = 0.05

    def _notify_throttled(self, state: AppState) -> None:
        """Rate-limit state notifications during rapid token streaming."""
        now = time.monotonic()
        if now - self._last_notify_time >= self._notify_interval:
            self._last_notify_time = now
            state.notify_streaming()

    async def prewarm(self, state: AppState) -> None:
        """Pre-warm the agent and underlying provider client during startup."""
        effective_key = (
            self._api_key
            or os.environ.get("OPENROUTER_API_KEY")
            or os.environ.get("MVGEOS_API_KEY")
            or ""
        )
        if not effective_key and self._agent_factory is None:
            return
        if not self._api_key and effective_key:
            self._api_key = effective_key

        try:
            agent = self.get_or_create_agent(state)
            self._ensure_listeners(agent)
            provider_reg = getattr(agent, "_provider_registry", None)
            if provider_reg is not None and hasattr(provider_reg, "prewarm_client"):
                await provider_reg.prewarm_client()
            if hasattr(agent, "initialize"):
                await agent.initialize()
            logger.debug("AgentService pre-warm completed successfully")
        except Exception as e:
            logger.debug("AgentService pre-warm skipped or failed: %s", e)

    @property
    def project_path(self) -> Path:
        return self._project_path

    @property
    def is_running(self) -> bool:
        """Whether a turn currently owns the active slot."""
        return self._turns.active is not None

    def can_create_agent(self) -> bool:
        """Whether get_or_create_agent can run without raising for a key.

        Mirrors the raise condition in get_or_create_agent exactly: an
        existing agent, a custom factory, or a resolvable API key. When no
        key is cached yet, re-resolves from the environment and auth file
        (same sources as __init__) so a key added after this service was
        constructed is honored — matching prewarm's refresh pattern.
        """
        if self._agent is not None or self._agent_factory is not None:
            return True
        if not self._api_key:
            self._api_key = resolve_api_key(None, DEFAULT_REALM)
        return bool(self._api_key)

    def get_or_create_agent(self, state: AppState) -> MvgeAgent:
        """Instantiate or retrieve the bound MvgeAgent instance."""
        if self._agent is not None:
            return self._agent

        if self._agent_factory is not None:
            self._agent = self._agent_factory(
                project_path=self._project_path,
                api_key=self._api_key,
                state=state,
            )
        else:
            if not self._api_key:
                raise RuntimeError(
                    "Cannot instantiate Mvge without an API key. "
                    "Set OPENROUTER_API_KEY, pass --api-key, or configure "
                    "an agent_factory."
                )
            # Which Mvge the desktop embeds is the engine's resolution, made
            # once, not a name this service knows. The GUI admits no
            # --agent-name of its own, so it asks for the installed default
            # and gets whatever Mvge the resolver adopts.
            agent_name = resolve_active_agent_name()
            env = MvgeEnvironment.resolve(
                agent_name=agent_name,
                project_dir=self._project_path,
            )
            self._agent = Mvge(
                api_key=self._api_key,
                name=agent_name,
                tome_dir=state.tome_service.tome_dir,
                tome_resume=state.active_tome_id,
                tome_factory=state.tome_service.factory,
                environment=env,
            )

        # When the agent is created, seed active_skills from the runner's
        # loaded skill manifests so the inspector reflects available skills.
        self.populate_skills(self._agent, state)
        # Bind the Approval Rune GUI presenter to the engine runner's
        # presenter slot so spell casts can prompt the summoner. The
        # state's presenter is reused, so recreation after reset_agent()
        # rebinds cleanly; a missing engine slot fails closed (deny).
        bind_approval_presenter(state, self._agent)
        return self._agent

    def reset_agent(self) -> None:
        """Reset the cached agent instance so it will be recreated on next run."""
        self._agent = None

    def set_api_key(self, api_key: str | None) -> None:
        """Update the API key used for future agent instantiations.

        The cached agent (if any) was built with the previous key, so it is
        closed and dropped: the next turn constructs a fresh agent with the
        new key. Closing runs as a background task when an event loop is
        available; without one the reference is simply dropped.
        """
        key = api_key.strip() if api_key and api_key.strip() else None
        if key == self._api_key:
            return
        self._api_key = key
        self.close_agent_in_background()

    def detach_agent(self) -> Any | None:
        """Remove and return the cached agent without closing it."""
        agent, self._agent = self._agent, None
        return agent

    def close_agent_in_background(self) -> None:
        """Detach the cached agent and close it without blocking.

        The agent instance is captured now, so a new agent created before
        the background task runs is never closed by mistake. Uses the
        public agent close protocol, which stops the agent's rune
        watchers. Without a running event loop the reference is simply
        dropped.
        """
        agent = self.detach_agent()
        if agent is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(
                "No running event loop; dropped cached agent without closing it"
            )
            return
        loop.create_task(self._close_agent(agent))

    async def close_agent(self) -> None:
        """Close and drop the cached agent, stopping its rune watchers.

        Uses the public agent close protocol, which shuts down the rune
        lifecycle (all hot-reload watchers), aborts in-flight work, and
        closes the realm client and tome handle. The reference is dropped
        first so a concurrent send builds a fresh agent. Failures are
        logged, never raised: teardown must be fail-closed.
        """
        await self._close_agent(self.detach_agent())

    @staticmethod
    async def _close_agent(agent: Any | None) -> None:
        if agent is None:
            return
        close = getattr(agent, "close", None)
        if not callable(close):
            return
        try:
            result = close()
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.warning("Error closing cached agent", exc_info=True)

    async def _ensure_runes_loaded(self, agent: Any) -> None:
        """Load the agent's runes when its runner is not initialized yet.

        A freshly constructed agent (e.g. on a new page load) has no rune
        runner until ``load_runes()`` runs, so dynamic ``/<rune-command>``
        slash commands would not be recognized. Loading here is a local
        operation: it never starts a model turn.
        """
        runner = getattr(agent, "runner", None)
        if runner is None:
            runner = getattr(agent, "_runner", None)
        if runner is not None:
            return
        load_runes = getattr(agent, "load_runes", None)
        if not callable(load_runes):
            return
        try:
            result = load_runes()
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.debug("Lazy rune loading failed; continuing unloaded", exc_info=True)

    def _ensure_listeners(self, agent: Any) -> None:
        """Attach event listeners to the agent instance only once."""
        flag = getattr(agent, "_gui_listeners_bound", None)
        if flag is not None and not getattr(flag, "_mock_name", None) and flag:
            return
        if hasattr(agent, "on"):
            for event_type in MvgeEventType:
                agent.on(
                    event_type,
                    lambda e: self._turns.route(e),
                )
            agent._gui_listeners_bound = True

    def handle_event(
        self,
        event: MvgeEvent,
        message: ChatMessage | None = None,
        state: AppState | None = None,
    ) -> None:
        """Process an MvgeEvent and update reactive ChatMessage and AppState.

        Without explicit targets the event is resolved against the active
        turn. An event arriving with no active turn is dropped with a log
        line, never silently.
        """
        target_message = message
        target_state = state
        if target_message is None or target_state is None:
            active = self._turns.active
            if active is None:
                logger.warning("Dropping %s event: no active turn", event.type)
                return
            if target_message is None:
                target_message = active.message
            if target_state is None:
                target_state = active.state
        transcript = self._transcript_for(target_message)

        data = event.data

        if event.type == MvgeEventType.AGENT_START:
            if data.get("subagent"):
                if target_state is not None:
                    task_id = data.get("taskId") or data.get("parentTaskId", "")
                    name = data.get("name", "Sub-Agent")
                    self.register_subagent_task(
                        target_state,
                        task_id,
                        name,
                        parent_id=data.get("parentSpellCastId"),
                    )
            else:
                self._start_time = time.monotonic()
                target_message.is_streaming = True
                if target_state is not None:
                    target_state.is_channeling = True
                    target_state.set_mvge_status("channeling")
                    # Seed active_skills from the agent's loaded skill manifests.
                    if self._agent is not None:
                        self.populate_skills(self._agent, target_state)
                    target_state.notify()

        elif event.type == MvgeEventType.TURN_START:
            if data.get("subagent") and target_state is not None:
                task_id = data.get("parentTaskId") or data.get("taskId", "")
                progress = data.get("progress")
                if progress is not None:
                    self.update_subagent_task(
                        target_state, task_id, progress=float(progress)
                    )

        elif event.type == MvgeEventType.MESSAGE_UPDATE:
            transcript.apply_message_update(data)
            target_message.is_streaming = True
            if target_state is not None:
                self._notify_throttled(target_state)

        elif event.type == MvgeEventType.AFTER_PROVIDER_RESPONSE:
            mana = data.get("mana_used", 0)
            if isinstance(mana, int) and mana > 0:
                target_message.mana_used = mana
                if target_state is not None:
                    target_state.total_mana_used = mana
                    target_state.notify()
            usage = extract_token_usage(data.get("response"))
            if usage is not None and target_state is not None:
                target_state.record_context_usage(*usage)

        elif event.type == MvgeEventType.SPELL_CASTING_START:
            spell_id = data.get("spellCastId", "")
            spell_name = data.get("spellName", "tool")
            self._pending_spell_starts[spell_id] = time.monotonic()
            elapsed = (
                time.monotonic() - self._start_time if self._start_time > 0 else None
            )
            transcript.begin_spell(data, elapsed_seconds=elapsed)

            arguments = data.get("arguments") or {}
            path = arguments.get("path") if isinstance(arguments, dict) else None
            if spell_name in ("read", "grep", "find", "list") and str(path).endswith(
                "SKILL.md"
            ):
                # Reading a SKILL.md file marks that skill as invoked.
                self.mark_skill_invoked(str(path), state=target_state)

            if target_state is not None:
                target_state.set_mvge_status("working")
                target_state.notify()

            # Track long-running spells as background tasks in the inspector.
            if target_state is not None:
                self._track_spell_start(target_state, spell_id, str(spell_name), data)

        elif event.type == MvgeEventType.SPELL_CASTING_END:
            spell_id = data.get("spellCastId", "")
            start_ts = self._pending_spell_starts.pop(spell_id, time.monotonic())
            duration = max(0.0, time.monotonic() - start_ts)
            elapsed = (
                max(0.0, time.monotonic() - self._start_time)
                if self._start_time > 0
                else None
            )
            transcript.end_spell(
                data, duration_seconds=duration, elapsed_seconds=elapsed
            )
            if target_state is not None:
                target_state.notify()

            # Finalise the background task entry created on SPELL_CASTING_START.
            if target_state is not None:
                self._track_spell_end(
                    target_state,
                    spell_id,
                    data.get("result"),
                    data.get("error"),
                    duration,
                )

        elif event.type in (MvgeEventType.TURN_END, MvgeEventType.AGENT_END):
            if data.get("subagent"):
                if target_state is not None:
                    task_id = data.get("parentTaskId") or data.get("taskId", "")
                    if event.type == MvgeEventType.AGENT_END:
                        status = (
                            TaskStatus.ERROR
                            if data.get("error")
                            else TaskStatus.COMPLETE
                        )
                        self.update_subagent_task(
                            target_state,
                            task_id,
                            status=status,
                            progress=100.0,
                        )
                        task = target_state.get_background_task(task_id)
                        if task is not None:
                            task.result = str(data.get("result", ""))
                            if data.get("error"):
                                task.error = str(data.get("error"))
                    else:
                        progress = data.get("progress")
                        if progress is not None:
                            self.update_subagent_task(
                                target_state, task_id, progress=float(progress)
                            )
                    target_state.notify()
            else:
                self._pending_spell_starts.clear()
                elapsed = (
                    max(0.0, time.monotonic() - self._start_time)
                    if self._start_time > 0
                    else None
                )
                transcript.finish(elapsed_seconds=elapsed)
                if target_state is not None:
                    target_state.is_channeling = False
                    if event.type == MvgeEventType.AGENT_END:
                        target_state.set_mvge_status("idle")
                    target_state.notify()

        elif event.type == MvgeEventType.ARTIFACT_CREATED:
            artifact_data = data.get("artifact", {})
            artifact = Artifact(
                id=str(artifact_data.get("id", "")),
                title=str(artifact_data.get("title", "Untitled Artifact")),
                summary=str(artifact_data.get("summary", "")),
                content=str(artifact_data.get("content", "")),
                artifact_type=ArtifactType(
                    str(artifact_data.get("type", ArtifactType.OTHER))
                ),
                file_paths=[str(p) for p in artifact_data.get("file_paths", [])],
            )
            transcript.add_artifact(artifact)
            if target_state is not None:
                target_state.add_artifact(artifact)
            if target_state is not None:
                target_state.notify()

        elif event.type == MvgeEventType.PROVIDER_ERROR:
            detail = str(data.get("error_message", ""))
            target_message.is_error = True
            target_message.error_message = detail
            transcript.set_text(_upstream_stall_message(detail))
            if target_state is not None:
                target_state.notify()

    def _transcript_for(self, message: ChatMessage) -> InvocationTranscript:
        """Return the transcript assembling this message (bound once per message)."""
        return transcript_for(message)

    def _track_spell_start(
        self,
        state: AppState,
        spell_id: str,
        spell_name: str,
        data: dict[str, Any],
    ) -> None:
        """Register a long-running spell cast as a background task."""
        if not spell_id:
            return
        parent_id = data.get("parentSpellCastId") or data.get("parentTaskId")
        state.add_background_task(
            task_id=spell_id,
            name=spell_name,
            parent_id=parent_id,
        )

    def _track_spell_end(
        self,
        state: AppState,
        spell_id: str,
        result: Any,
        error: Any,
        duration: float,
    ) -> None:
        """Mark a background spell task complete or errored on SPELL_CASTING_END."""
        if not spell_id:
            return
        status = TaskStatus.ERROR if error else TaskStatus.COMPLETE
        state.update_background_task(
            spell_id,
            status=status,
            result=str(result or ""),
            error=str(error) if error else None,
        )
        # Cap progress at 100% on completion.
        task = state.get_background_task(spell_id)
        if task is not None and task.status in (
            TaskStatus.COMPLETE,
            TaskStatus.ERROR,
        ):
            task.progress = 100.0

    def register_subagent_task(
        self,
        state: AppState,
        task_id: str,
        name: str,
        *,
        parent_id: str | None = None,
    ) -> BackgroundTask | None:
        """Register a subagent background task in the inspector state."""
        if not task_id or state is None:
            return None
        return state.add_background_task(
            task_id=task_id, name=name, parent_id=parent_id
        )

    def update_subagent_task(
        self,
        state: AppState,
        task_id: str,
        *,
        status: TaskStatus | str | None = None,
        progress: float | None = None,
    ) -> BackgroundTask | None:
        """Update a subagent background task's status/progress."""
        return state.update_background_task(task_id, status=status, progress=progress)

    async def _is_dispatchable_command(self, stripped: str, state: AppState) -> bool:
        """Whether a '/'-prefixed line is a known slash command.

        Known static commands, catalog skills, and dynamic rune commands
        route to the dispatcher; anything else falls through to the model
        (harness skill activation, plain text). On agent-construction
        failure returns True so dispatch_slash_command renders the usual
        auth error instead of burning a model turn.
        """
        try:
            agent = self.get_or_create_agent(state)
        except Exception:
            return True
        # A fresh agent has no rune runner until its runes load; ensure it
        # here so dynamic /<rune-command> dispatches on first use.
        await self._ensure_runes_loaded(agent)
        dispatcher = CommandDispatcher(agent, self._model_registry)
        return await dispatcher.is_command(stripped)

    async def dispatch_slash_command(
        self, prompt: str, state: AppState, message: ChatMessage
    ) -> CommandOutcome | None:
        """Execute a harness slash command locally and synchronize state.

        Returns the structured outcome (``None`` when agent construction
        failed before dispatching).
        """
        turn = self._turns.start(message, state)
        transcript = turn.transcript

        try:
            agent = self.get_or_create_agent(state)
        except Exception as e:
            message.is_error = True
            message.error_message = str(e)
            self._cleanup_slash_turn(state, message, turn)
            return None

        # Same lazy guarantee as the dispatchability check: a fresh agent's
        # dynamic rune commands must be visible before dispatching.
        await self._ensure_runes_loaded(agent)
        dispatcher = CommandDispatcher(agent, self._model_registry)
        try:
            outcome = await dispatcher.dispatch(prompt)
        except Exception as exc:
            message.is_error = True
            message.error_message = str(exc)
            outcome = CommandOutcome(
                command=prompt,
                action=CommandAction.ERROR,
                message=f"Command error: {exc}",
            )

        if outcome.action == CommandAction.ERROR:
            message.is_error = True
            message.error_message = outcome.message
            # The error card renders error_message; keep the transcript body
            # empty so the same text is not shown twice.
            output_text = ""
        else:
            output_text = outcome.message
            if not output_text and outcome.should_exit:
                output_text = "*Session ended.*"

        transcript.set_text(output_text)

        # Synchronize reactive state via structured outcome action and data
        if outcome.action == CommandAction.MODEL_SWITCHED:
            state.switch_model(outcome.data.get("model_id", agent.model_id))

        elif outcome.action == CommandAction.SESSION_RESET:
            state.new_conversation()

        elif outcome.action == CommandAction.SESSION_RESUMED:
            tome_id = outcome.data.get("tome_id") or agent.tome_id
            if tome_id:
                state.switch_to_tome(tome_id)

        elif outcome.action == CommandAction.SPELLS_UPDATED:
            state.clear_skills()
            self.populate_skills(agent, state)
            state.notify()

        elif outcome.action == CommandAction.CATALOG_REFRESHED:
            state.notify()

        elif outcome.action == CommandAction.CONTEMPLATION_CHANGED:
            new_level = outcome.data.get("contemplation_level")
            if new_level and hasattr(state, "set_contemplation_level"):
                state.set_contemplation_level(new_level)

        self._cleanup_slash_turn(state, message, turn)
        return outcome

    def _cleanup_slash_turn(
        self, state: AppState, message: ChatMessage, turn: Turn
    ) -> None:
        message.is_streaming = False
        state.is_channeling = False
        state.set_mvge_status("idle")
        # Release the slot only if this turn still owns it.
        self._turns.finish(turn)
        state.notify()

    async def run_prompt(
        self, prompt: str | list[dict[str, Any]], state: AppState, message: ChatMessage
    ) -> None:
        """Run agent with prompt asynchronously while capturing all events."""
        if isinstance(prompt, str):
            stripped = prompt.strip()
            if stripped.startswith("/") and await self._is_dispatchable_command(
                stripped, state
            ):
                await self.dispatch_slash_command(prompt, state, message)
                return

        turn = self._turns.start(message, state)
        transcript = turn.transcript
        self._start_time = time.monotonic()
        self._pending_spell_starts.clear()
        self.reset_skill_tracking()
        state.is_channeling = True
        message.is_streaming = True

        if not self._api_key:
            message.is_error = True
            message.error_message = "API key required"
            transcript.set_text(
                "**Authentication Required**: No OpenRouter API key was found.\n\n"
                "Please set the `OPENROUTER_API_KEY` environment variable, "
                "run `mvgeos setup`, or pass `--api-key` when starting `mvgeos-gui`."
            )
            message.is_streaming = False
            state.is_channeling = False
            state.channeling_started_at = None
            state.set_mvge_status("idle")
            self._turns.finish(turn)
            state.notify()
            return

        try:
            agent = self.get_or_create_agent(state)
            self._ensure_listeners(agent)

            if state.selected_model and hasattr(agent, "switch_model"):
                switch_res = agent.switch_model(state.selected_model)
                if inspect.isawaitable(switch_res):
                    await switch_res
            if hasattr(agent, "set_contemplation_level") and getattr(
                state, "contemplation_level", None
            ):
                cont_res = agent.set_contemplation_level(state.contemplation_level)
                if inspect.isawaitable(cont_res):
                    await cont_res

            keepalive_stop = asyncio.Event()

            async def _keepalive() -> None:
                while not keepalive_stop.is_set():
                    await asyncio.sleep(5)
                    if not keepalive_stop.is_set():
                        state.notify()

            # The turn owns its task so cancel() reaches it without touching
            # any other turn's routing. The keepalive is created inside the
            # try so every exit path -- including cancellation before the
            # await -- runs the finally that stops it.
            keepalive_task: asyncio.Task[None] | None = None
            try:
                keepalive_task = asyncio.create_task(_keepalive())
                turn.task = asyncio.create_task(agent.run(prompt))
                await turn.task
            finally:
                keepalive_stop.set()
                if keepalive_task is not None:
                    keepalive_task.cancel()
        except asyncio.CancelledError:
            logger.info("Agent run cancelled by summoner")
            # The turn's own transcript: a newer turn may already own the
            # slot, and its transcript must not receive this turn's suffix.
            transcript.append_text("\n\n*(Cancelled by summoner)*")
        except AuthenticationError as exc:
            logger.exception("Authentication failed: %s", exc)
            message.is_error = True
            message.error_message = str(exc)
            transcript.set_text(
                "**Authentication Failed (HTTP 401)**: The OpenRouter API key "
                "is invalid or unauthorized.\n\n"
                "Please check your `OPENROUTER_API_KEY` environment variable "
                "or re-run `mvgeos setup`."
            )
        except RateLimitError as exc:
            logger.warning("Rate limit exceeded: %s", exc)
            message.is_error = True
            message.error_message = str(exc)

            if exc.limit_source == "openrouter_free_tier_daily":
                quota_str = (
                    f" ({exc.quota_limit}/{exc.quota_limit} requests used)"
                    if exc.quota_limit
                    else ""
                )
                reset_info = ""
                if exc.reset_at:
                    dt = datetime.fromtimestamp(exc.reset_at, UTC)
                    time_str = dt.strftime("%H:%M UTC")
                    reset_info = f"\n\n- **Quota Reset Time**: Resets at `{time_str}`."
                remedy = (
                    f"\n- **Remedy**: {exc.remedy_hint}"
                    if exc.remedy_hint
                    else (
                        "\n- **Suggested actions**: Add credits to your OpenRouter "
                        "account to unlock 1,000 requests/day, switch to a paid "
                        "model via the selector below, or wait for the daily reset."
                    )
                )
                transcript.set_text(
                    "**Daily Free Tier Quota Reached**: You have exhausted "
                    f"the daily request limit for free-tier models{quota_str}."
                    f"{reset_info}"
                    f"{remedy}"
                )
            elif (
                exc.limit_source == "upstream_rate_limit"
                or "provider returned error" in str(exc).lower()
            ):
                transcript.set_text(
                    "**Upstream Provider Overloaded (HTTP 429)**: The upstream model "
                    "host is temporarily unable to process requests.\n\n"
                    f"- **Details**: {exc}\n"
                    "- **Suggested actions**: Switch to another model via the selector "
                    "below, or wait a few moments and try again."
                )
            else:
                retry_hint = (
                    f"\n\n*Please wait {exc.retry_after:.0f}s before retrying.*"
                    if exc.retry_after
                    else ""
                )
                remedy = (
                    f"\n- **Remedy**: {exc.remedy_hint}\n" if exc.remedy_hint else ""
                )
                err_detail = (
                    f"\n- **Provider Message**: {exc}\n"
                    if str(exc) and str(exc).lower() != "rate limited"
                    else ""
                )
                transcript.set_text(
                    "**Rate Limit Exceeded (HTTP 429)**: The model provider is "
                    "temporarily rate-limiting requests.\n\n"
                    "- **Free tier models** (`:free`) frequently experience upstream "
                    "capacity limits and daily caps.\n"
                    f"{err_detail}"
                    f"{remedy}"
                    "- **Suggested actions**: Try switching to another model via the "
                    "model selector below, or wait a few moments and try again."
                    f"{retry_hint}"
                )
        except UpstreamTimeoutError as exc:
            logger.warning("Upstream provider stalled mid-stream: %s", exc)
            message.is_error = True
            message.error_message = str(exc)
            if not message.content:
                transcript.set_text(_upstream_stall_message(str(exc)))
        except NoRealmRegisteredError as exc:
            logger.warning("No realm registered: %s", exc)
            message.is_error = True
            message.error_message = str(exc)
            # The exception carries the Rune that provides the Realm. Reading it
            # from there replaces splitting the message on "install ", which
            # coupled the GUI to the wording of an error string.
            message.missing_rune = exc.rune_name or rune_for_realm(exc.realm)
            if not message.content:
                transcript.set_text(
                    f"**Missing Realm Extension**: The selected model requires the "
                    f"`{message.missing_rune}` extension to communicate with upstream "
                    f"providers.\n\n"
                    f"Install it from the Rune Marketplace or use the button below."
                )
        except Exception as exc:
            logger.exception("Error executing agent prompt: %s", exc)
            message.is_error = True
            message.error_message = str(exc)
            if not message.content:
                # Code-span the exception: raw error text is not markdown and
                # identifiers like OPENROUTER_API_KEY would otherwise be
                # mangled by emphasis parsing.
                transcript.set_text(f"Execution error: `{exc}`")
        finally:
            message.is_streaming = False
            state.is_channeling = False
            state.channeling_started_at = None
            state.set_mvge_status("idle")
            # Only the turn that still owns the slot releases it: a stale
            # turn's finally must not clobber the turn that replaced it.
            self._turns.finish(turn)
            # Adopt the engine-created tome so the session lifecycle commands
            # (rename/fork/export/compact) unlock for this conversation. Only
            # adopts when no tome is active; never steals an existing one.
            agent_tome_id = getattr(self._agent, "tome_id", None)
            if state.active_tome_id is None and isinstance(agent_tome_id, str):
                state.active_tome_id = agent_tome_id
                state.tome_title = state.tome_service.get_tome_title(agent_tome_id)
                state.load_tomes()
            state.notify()

    def cancel(self) -> None:
        """Cancel the active turn, if any.

        Delegates to the turn owner: the turn's task is cancelled and the
        slot released. A turn started afterwards is unaffected.
        """
        self._turns.stop()

    async def compact_active_tome(self, state: AppState) -> str:
        """Compact the active session's mana pool via the engine.

        Refuses rather than compacting the wrong Tome: the cached agent's
        tome must match the active tome id. Engine runtime errors become
        user-facing messages. Returns the outcome message.
        """
        tome_id = state.active_tome_id
        if tome_id is None:
            return "No active session to compact"
        if state.is_channeling:
            return "Cannot compact while channeling"
        try:
            agent = self.get_or_create_agent(state)
        except RuntimeError:
            return "Cannot compact: agent is not attached to the active session"
        if hasattr(agent, "initialize"):
            init_res = agent.initialize()
            if inspect.isawaitable(init_res):
                await init_res
        agent_tome = getattr(agent, "_agent_tome", None)
        if agent_tome is None or agent_tome.tome_id != tome_id:
            return "Cannot compact: agent is not attached to the active session"
        try:
            return await agent.compact()
        except RuntimeError as exc:
            return f"Compaction failed: {exc}"

    # --- Skill tracking helpers ---

    def _runner_skills(self, agent: Any) -> list[Any]:
        """Return the list of SkillManifest from the agent's RuneRunner."""
        runner = getattr(agent, "runner", None)
        if runner is None:
            return []
        getter = getattr(runner, "get_skills", None)
        if not callable(getter):
            return []
        try:
            return list(getter())
        except Exception:
            logger.debug("Failed to read skills from runner", exc_info=True)
            return []

    def populate_skills(self, agent: Any, state: AppState | None) -> None:
        """Seed state.active_skills from the agent's loaded skill manifests.

        Idempotent: skills already tracked are not duplicated. Skills that
        were previously invoked remain marked as invoked.
        """
        if state is None:
            return
        manifests = self._runner_skills(agent)
        for manifest in manifests:
            name = getattr(manifest, "name", "")
            if not name:
                continue
            if name in self._loaded_skill_names:
                continue
            self._loaded_skill_names.add(name)
            state.add_skill(manifest)
        if state.active_skills:
            state.notify()

    def mark_skill_invoked(self, path: str, state: AppState | None = None) -> None:
        """Record that a skill was invoked by reading one of its files."""
        if not path:
            return
        normalized = str(path).replace("\\", "/")
        active = self._turns.active
        target_state = state or (active.state if active is not None else None)
        if target_state is None:
            return
        for skill in target_state.active_skills:
            if not skill.path:
                continue
            skill_root = str(skill.path).replace("\\", "/").rstrip("/")
            skill_md = f"{skill_root}/SKILL.md"
            matched = normalized in (skill_root, skill_md) or normalized.startswith(
                skill_root + "/"
            )
            if matched and skill.name not in self._invoked_skill_names:
                self._invoked_skill_names.add(skill.name)
                skill.invoked = True
                target_state.notify()

    def reset_skill_tracking(self) -> None:
        """Clear per-session skill invocation tracking."""
        self._invoked_skill_names.clear()
        self._loaded_skill_names.clear()
