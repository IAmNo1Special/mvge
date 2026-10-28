from __future__ import annotations

import asyncio
import contextlib
import copy
import dataclasses
import inspect
import logging
import re
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

from mvge.agent.agent_session import MvgeTome
from mvge.agent.environment import MvgeEnvironment
from mvge.agent.function_spell import RuneSpellWrapper, SpellUnion, coerce_spell
from mvge.agent.harness._state import MvgeState
from mvge.agent.harness.capabilities import (
    AgentConfiguration,
    SessionLifecycle,
    TurnDriver,
)
from mvge.agent.harness.compaction.compaction import (
    DEFAULT_COMPACTION_SETTINGS,
    CompactionSettings,
    estimate_context_mana,
    should_compact,
)
from mvge.agent.harness.compaction.compaction_runner import CompactionRunner
from mvge.agent.harness.reload import (
    _RETIRED_SHUTDOWN_ATTEMPTS,
    _RETIRED_SHUTDOWN_BACKOFF_SECONDS,
    ReloadBuilder,
    ReloadCandidate,
    _restore_provider_registrations,
    _snapshot_provider_registrations,
)
from mvge.agent.protocol import ReloadResult
from mvge.agent.rune_lifecycle import RuneLifecycle
from mvge.agent.sigil_payloads import (
    payload_for,
    payload_for_event,
    prepare_next_turn_payload,
)
from mvge.core.abort import AbortController, AbortSignal
from mvge.core.approval import ApprovalDecision, ApprovalRequest
from mvge.core.channel import ChannelConfig, Model, MvgeResponse, RealmResponse
from mvge.core.events import (
    ContemplationLevel,
    ExecutionSnapshot,
    MvgeEvent,
    MvgeEventType,
    PromptSource,
    QueueMode,
)
from mvge.core.invocations import (
    MvgeInvocation,
    SummonerRequest,
)
from mvge.core.loop import EmitSink, LoopCallbacks, LoopContext, StreamFn, run_loop
from mvge.core.spells import MvgeSpell, SpellResultMessage
from mvge.provider.base import Realm
from mvge.provider.registry import RealmRegistry
from mvge.runes.rune_audit import (
    RuneAuditLog,
    default_rune_ops_dir,
    utcnow,
)
from mvge.runes.rune_runner import RuneRunner
from mvge.runes.types import (
    AfterSpellResultData,
    BeforeProviderHeadersData,
    ContextTransformData,
    Diagnostic,
    DiagnosticKind,
    InputData,
    PrepareNextTurnData,
    SigilHook,
    SkillDiagnostic,
)
from mvge.tome.handle import TomeHandleFactory

logger = logging.getLogger(__name__)

_EVENT_TO_SIGIL: dict[MvgeEventType, SigilHook] = {
    MvgeEventType.AGENT_START: SigilHook.AGENT_START,
    MvgeEventType.AGENT_END: SigilHook.AGENT_END,
    MvgeEventType.COMPACTION_START: SigilHook.COMPACTION_START,
    MvgeEventType.COMPACTION_END: SigilHook.COMPACTION_END,
    MvgeEventType.TURN_START: SigilHook.TURN_START,
    MvgeEventType.TURN_END: SigilHook.TURN_END,
    MvgeEventType.INPUT: SigilHook.INPUT,
    MvgeEventType.AFTER_PROVIDER_RESPONSE: SigilHook.AFTER_PROVIDER_RESPONSE,
    MvgeEventType.BEFORE_INVOCATION: SigilHook.BEFORE_INVOCATION,
    MvgeEventType.AFTER_INVOCATION: SigilHook.AFTER_INVOCATION,
}


SPELL_NAME_REGEX = re.compile(r"^[a-zA-Z0-9_-]+$")


def _validate_spell_name(name: Any) -> bool:
    """Validate spell name conforms to provider identifier standards."""
    return isinstance(name, str) and bool(SPELL_NAME_REGEX.match(name))


def _validate_spell_parameters(parameters: Any) -> bool:
    """Validate that parameters conform to a JSON Schema object dictionary."""
    if not isinstance(parameters, dict):
        return False
    if not parameters:
        return True

    schema_type = parameters.get("type")
    if schema_type is not None and schema_type != "object":
        return False

    properties = parameters.get("properties")
    if properties is not None:
        if not isinstance(properties, dict):
            return False
        for prop_name, prop_def in properties.items():
            if not isinstance(prop_name, str) or not isinstance(prop_def, dict):
                return False

    required = parameters.get("required")
    if required is not None:
        if not isinstance(required, (list, tuple, set)):
            return False
        if not all(isinstance(r, str) for r in required):
            return False

    return True


def _validate_spell_signature(spell: Any) -> bool:
    """Validate that spell.execute matches the MvgeSpell execution contract."""
    execute_fn = getattr(spell, "execute", None)
    if not callable(execute_fn):
        return False
    try:
        sig = inspect.signature(execute_fn)
    except (ValueError, TypeError):
        return False

    try:
        sig.bind("dummy_id", {}, signal=None, on_update=None)
        return True
    except TypeError:
        try:
            sig.bind("dummy_id", {}, signal=None)
            return True
        except TypeError:
            return False


def _transformed_input(result: Any, fallback: Any) -> Any:
    """Normalize an INPUT chain result back to content.

    Handlers may return the (mutated) InputData, the raw content, or None
    to keep the current content. Anything else keeps the fallback: a
    misbehaving rune must not corrupt the turn's input.
    """
    if result is None:
        return fallback
    if isinstance(result, InputData):
        return result.content
    if isinstance(result, (str, list)):
        return result
    return fallback


def _apply_prepare_next_turn(
    data: PrepareNextTurnData, context: LoopContext
) -> LoopContext:
    """Apply a rune-returned PrepareNextTurnData onto the loop context.

    Raises ValueError on uncoercible enum values; the caller treats that
    like any other misbehaving-rune result and keeps the context.
    """
    return dataclasses.replace(
        context,
        system_prompt=data.system_prompt,
        prompt_source=PromptSource(data.prompt_source),
        invocations=data.invocations,
        contemplation_level=ContemplationLevel(data.contemplation_level),
        max_tokens=data.max_tokens,
        temperature=data.temperature,
        spell_timeout_ms=data.spell_timeout_ms,
        contemplation_budget=data.contemplation_budget,
        exclude_contemplation=data.exclude_contemplation,
        max_turns=data.max_turns,
        queue_mode=QueueMode(data.queue_mode),
    )


def _commit_candidate_providers(
    registry: RealmRegistry,
    live_runner: RuneRunner | None,
    candidate_runner: RuneRunner,
) -> None:
    """Replace rune-owned provider registrations with the candidate's exact state.

    ``RealmRegistry.register_provider()`` merges configs (``dict.update``),
    so a successful reload must *replace* — not merge — the entries the
    candidate build touched, and drop entries whose rune is gone.
    Ownership is determined from the runners: names registered on the
    live or candidate runner are rune-owned; anything else in the
    registry is unrelated non-rune state and is left alone.
    """
    candidate_providers = candidate_runner.get_registered_providers()
    candidate_factories = candidate_runner.get_registered_realm_factories()
    live_provider_names = (
        set(live_runner.get_registered_providers())
        if live_runner is not None
        else set()
    )
    live_factory_prefixes = (
        set(live_runner.get_registered_realm_factories())
        if live_runner is not None
        else set()
    )
    for name, config in candidate_providers.items():
        # Replace, not merge: keys the rune dropped must not survive.
        registry.unregister_provider(name)
        registry.register_provider(name, dict(config))
    for name in live_provider_names - set(candidate_providers):
        # The providing rune is gone; its registration must not survive.
        registry.unregister_provider(name)
    for prefix, factory in candidate_factories.items():
        # Realm factories already replace on register; re-register to
        # make the candidate's factory the live one.
        registry.register_realm_factory(prefix, factory)
    for prefix in live_factory_prefixes - set(candidate_factories):
        registry.unregister_realm_factory(prefix)


class MvgeHarness(TurnDriver, SessionLifecycle, AgentConfiguration):
    """Session-aware operational owner of the agent loop.

    Drives run_loop, reduces events into MvgeState, manages Sigil hooks,
    and orchestrates Tome recording and compaction behind a single deep interface.
    Mirrors Pi's AgentHarness architecture.
    """

    def __init__(
        self,
        *,
        state: MvgeState,
        tome: MvgeTome | None = None,
        realm: Realm | None = None,
        model: Model | None = None,
        compaction: CompactionRunner | None = None,
        compaction_settings: CompactionSettings = DEFAULT_COMPACTION_SETTINGS,
        callbacks: LoopCallbacks | None = None,
        discovered_spells: Sequence[SpellUnion] | None = None,
        plan_mode: bool = False,
        enabled_spells_filter: set[str] | None = None,
        allowed_spells: frozenset[str] | Sequence[str] | None = None,
        rune_lifecycle: RuneLifecycle | None = None,
        provider_registry: RealmRegistry | None = None,
        environment: MvgeEnvironment | None = None,
        tome_factory: TomeHandleFactory | None = None,
        tome_dir: Path | None = None,
        model_id: str | None = None,
        api_key: str | None = None,
        provider_name: str | None = None,
        reload_builder: ReloadBuilder | None = None,
    ) -> None:
        self._state = state
        self._tome: MvgeTome | None = tome or getattr(self._state, "agent_tome", None)
        self._realm = realm
        self._model = model
        self._configured_model = model.id if model is not None else None
        self._configured_realm = realm.__class__.__name__ if realm is not None else None
        self._captured_model: str | None = None
        self._captured_realm: str | None = None
        self._compaction_settings = compaction_settings
        self._callbacks = callbacks
        self._discovered_spells: Sequence[SpellUnion] = (
            list(discovered_spells) if discovered_spells is not None else []
        )
        self._plan_mode = plan_mode
        self._enabled_spells_filter = (
            set(enabled_spells_filter) if enabled_spells_filter is not None else None
        )
        self._allowed_spells: frozenset[str] | None = (
            frozenset(allowed_spells) if allowed_spells is not None else None
        )
        # Last RuneRunner.spell_version the spell cache was built from.
        # Initialized to the live version so pre-populated state.spells
        # (construction-time build) are the cache until the version moves.
        runner_for_version = state.rune_runner
        self._spell_version_seen: int = (
            runner_for_version.spell_version if runner_for_version is not None else 0
        )
        # TurnDriver-owned: exactly one turn at a time.
        self._abort_controller: AbortController | None = None
        self._turn_in_flight = False
        # Turn-boundary reload: a reload requested mid-turn queues here.
        self._reload_pending = False
        # SessionLifecycle-owned: session-scoped collaborators.
        self._rune_lifecycle = rune_lifecycle
        self._provider_registry = provider_registry
        self._environment = environment
        self._tome_factory = tome_factory
        self._tome_dir = tome_dir
        self._model_id = model_id
        self._api_key = api_key
        self._provider_name = provider_name
        # SessionLifecycle-owned: reload transaction.
        self._reload_builder = reload_builder
        self._reload_lock = asyncio.Lock()
        self._reload_diagnostics: list[Diagnostic | SkillDiagnostic] = []
        self._gateway_engaged_rune: str | None = None

        self._compaction: CompactionRunner | None
        if compaction is not None:
            self._compaction = compaction
            if getattr(self._compaction, "_emit", None) is None:
                self._compaction.rebind(emit=self._emit)
        elif self._realm is not None and self._model is not None:
            self._compaction = CompactionRunner(
                realm=self._realm,
                model=self._model,
                emit=self._emit,
                settings=self._compaction_settings,
                tome=self._tome,
            )
        else:
            self._compaction = None

    @property
    def state(self) -> MvgeState:
        return self._state

    @property
    def tome(self) -> MvgeTome | None:
        return self._tome

    @property
    def compaction(self) -> CompactionRunner | None:
        return self._compaction

    @property
    def allowed_spells(self) -> frozenset[str] | None:
        """The restricted subset of spell names this harness permits, if any."""
        return self._allowed_spells

    @property
    def emit(self) -> EmitSink:
        """The sink that fans an event out to state, bus, Sigils, and Tome."""
        return self._emit

    async def switch_tome(self, tome: MvgeTome) -> None:
        """Switch active Tome session reference and update CompactionRunner."""
        self._tome = tome
        self._state.agent_tome = tome
        if self._compaction is not None:
            self._compaction.rebind(tome=tome)

    def _workspace_root(self) -> str | None:
        runner_ctx = getattr(self._state.rune_runner, "context", None)
        root = getattr(runner_ctx, "project_root", None) or getattr(
            runner_ctx, "cwd", None
        )
        if root:
            return str(root)
        config_manager = self._environment.config_manager if self._environment else None
        configured = getattr(config_manager, "project_dir", None)
        if configured:
            return str(Path(configured).resolve())
        return None

    async def fork_tome(self, entry_id: str | None = None) -> str:
        """Branch the current Tome and switch to the new branch."""
        if self._tome is None:
            raise RuntimeError("Agent not initialized")
        old_leaf_id = getattr(self._tome, "active_leaf_id", None)
        new_tome = await self._tome.fork(entry_id)
        if new_tome is None:
            raise RuntimeError("Failed to fork tome")
        await self.switch_tome(new_tome)
        self._state.invocations = new_tome.reconstruct_invocations()
        self._rebind_runner_context()
        if self._state.rune_runner is not None:
            new_leaf_id = getattr(new_tome, "active_leaf_id", None)
            await self._state.rune_runner.emit_async(
                SigilHook.SESSION_LEAF_CHANGED,
                payload_for(
                    SigilHook.SESSION_LEAF_CHANGED,
                    tome_id=new_tome.tome_id,
                    old_leaf_id=old_leaf_id,
                    new_leaf_id=new_leaf_id,
                    reason="fork",
                    workspace_root=self._workspace_root(),
                ),
            )
        return new_tome.tome_id

    async def checkout_leaf(self, leaf_id: str) -> None:
        """Switch the active position in the Tome to a specific leaf."""
        if self._tome is None:
            raise RuntimeError("Agent not initialized")
        if self._state.is_streaming:
            raise RuntimeError("Cannot checkout leaf while invocation is streaming")
        old_leaf_id = getattr(self._tome, "active_leaf_id", None)
        self._tome.set_leaf(leaf_id)
        self._state.invocations = self._tome.reconstruct_invocations()
        if self._state.rune_runner is not None:
            await self._state.rune_runner.emit_async(
                SigilHook.SESSION_LEAF_CHANGED,
                payload_for(
                    SigilHook.SESSION_LEAF_CHANGED,
                    tome_id=self._tome.tome_id,
                    old_leaf_id=old_leaf_id,
                    new_leaf_id=leaf_id,
                    reason="checkout",
                    workspace_root=self._workspace_root(),
                ),
            )

    async def undo(self) -> str | None:
        """Revert the most recent summoner invocation."""
        if self._tome is None:
            raise RuntimeError("Agent not initialized")
        if self._state.is_streaming:
            raise RuntimeError("Cannot undo while invocation is streaming")
        if self._tome_factory is None:
            raise RuntimeError("Agent not initialized")
        target = self._tome_factory.get_parent_summoner_entry(
            self._tome.tome_id, self._tome.active_leaf_id
        )
        if target is None:
            raise ValueError("Cannot undo: at root invocation")
        old_leaf_id = getattr(self._tome, "active_leaf_id", None)
        self._tome.set_leaf(target.id)
        self._state.invocations = self._tome.reconstruct_invocations()
        if self._state.rune_runner is not None:
            await self._state.rune_runner.emit_async(
                SigilHook.SESSION_LEAF_CHANGED,
                payload_for(
                    SigilHook.SESSION_LEAF_CHANGED,
                    tome_id=self._tome.tome_id,
                    old_leaf_id=old_leaf_id,
                    new_leaf_id=target.id,
                    reason="undo",
                    workspace_root=self._workspace_root(),
                ),
            )
        return target.id

    async def compact(self) -> str:
        """Trigger mana pool compaction on the current Tome branch."""
        if self._tome is None or self._compaction is None:
            raise RuntimeError("Agent not initialized")
        if self._state.is_streaming:
            raise RuntimeError("Cannot compact while invocation is streaming")
        if not self._state.invocations:
            return "No invocations to compact"
        replacement = await self._compaction.force_compact(self._state.invocations)
        if replacement is not None:
            self._state.invocations = replacement
            return "Compaction completed"
        return "Nothing to compact or compaction skipped"

    async def close(self) -> None:
        """Teardown the session and release resources."""
        if self._abort_controller is not None:
            self._abort_controller.abort()

        if self._rune_lifecycle is not None:
            await self._rune_lifecycle.shutdown()
            self._rune_lifecycle = None
            self._state.rune_runner = None

        if self._realm is not None:
            close_fn = getattr(self._realm, "close", None)
            if callable(close_fn):
                res = close_fn()
                if inspect.isawaitable(res):
                    await res
            self._realm = None

        if self._tome is not None:
            await self._tome.shutdown()

    async def reload(self) -> ReloadResult:
        """Rebuild the runtime mid-session: runes, prompt, spells.

        The sole engine-owned reload mechanism. A reload requested
        mid-turn queues until the turn completes; the in-flight turn
        keeps its LoopContext. Concurrent calls serialize on a lock.
        """
        if self._turn_in_flight:
            self._reload_pending = True
            return ReloadResult(
                ok=True,
                queued=True,
                message=(
                    "Reload queued: a turn is in flight; it applies at the "
                    "next turn boundary."
                ),
            )
        if self._reload_builder is None:
            return ReloadResult(
                ok=False, message="Agent not initialized; nothing to reload."
            )
        async with self._reload_lock:
            return await self._reload_locked()

    async def _reload_locked(self) -> ReloadResult:
        """Execute one reload; the caller holds ``self._reload_lock``."""
        assert self._reload_builder is not None
        assert self._provider_registry is not None
        # Provider registrations are the one shared-mutable step of the
        # build: snapshot first so any build failure rolls them back.
        provider_snapshot = _snapshot_provider_registrations(self._provider_registry)
        try:
            candidate = await self._reload_builder()
        except Exception as exc:
            _restore_provider_registrations(self._provider_registry, provider_snapshot)
            diagnostic = f"Reload failed: {exc}"
            logger.exception("Reload failed; last-known-good state preserved")
            self._reload_diagnostics = [
                Diagnostic(
                    kind=DiagnosticKind.LOAD_FAILURE,
                    rune_name="engine",
                    message=diagnostic,
                )
            ]
            audit_error = self._audit_reload(
                ok=False, code="reload_failed", message=diagnostic
            )
            message = diagnostic + "; last-known-good state preserved."
            if audit_error is not None:
                message += f" ({audit_error})"
            return ReloadResult(ok=False, message=message)
        old_lifecycle = self._apply_reload_candidate(candidate)
        # Watcher handover: the candidate lifecycle (already swapped in)
        # starts its watchers. The retired lifecycle's watchers stop
        # afterwards, with bounded retry.
        watcher_error: str | None = None
        if self._rune_lifecycle is not None:
            try:
                await self._rune_lifecycle.start()
            except Exception as exc:
                watcher_error = f"watcher re-sync failed: {exc}"
                logger.exception(
                    "Reload applied but watcher re-sync failed; "
                    "file-triggered reloads are paused until the next "
                    "successful reload"
                )
        retired_shutdown_error: str | None = None
        if old_lifecycle is not None:
            for attempt in range(_RETIRED_SHUTDOWN_ATTEMPTS):
                try:
                    await old_lifecycle.shutdown()
                    retired_shutdown_error = None
                    break
                except Exception as exc:
                    retired_shutdown_error = f"retired watcher shutdown failed: {exc}"
                    if attempt == _RETIRED_SHUTDOWN_ATTEMPTS - 1:
                        logger.exception(
                            "Retired rune watchers did not shut down "
                            "cleanly after reload"
                        )
                    else:
                        await asyncio.sleep(_RETIRED_SHUTDOWN_BACKOFF_SECONDS)
        if watcher_error is not None:
            # The swap succeeded — the new state IS live — but the
            # post-swap watcher handover failed. Per the §4.6 audit
            # pattern the result is a loud failure that states plainly
            # what is live and what is not.
            message = f"{candidate.summary}; reload IS live but {watcher_error}."
            audit_error = self._audit_reload(
                ok=False,
                code="watcher_sync_failed",
                message=message,
                extra={
                    "prompt_changed": candidate.prompt_changed,
                    "spells_changed": candidate.spells_changed,
                    "rune_diagnostics": len(candidate.rune_diagnostics),
                    "rehydrate_errors": candidate.rehydrate_errors,
                    "spell_file_warnings": candidate.spell_file_warnings,
                },
            )
            if audit_error is not None:
                message += f" ({audit_error})"
            self._reload_diagnostics = [
                Diagnostic(
                    kind=DiagnosticKind.LOAD_FAILURE,
                    rune_name="engine",
                    message=message,
                )
            ]
            return ReloadResult(ok=False, message=message)
        audit_error = self._audit_reload(
            ok=True,
            code="reload_ok",
            message=candidate.summary,
            extra={
                "prompt_changed": candidate.prompt_changed,
                "spells_changed": candidate.spells_changed,
                "rune_diagnostics": len(candidate.rune_diagnostics),
                "rehydrate_errors": candidate.rehydrate_errors,
                "spell_file_warnings": candidate.spell_file_warnings,
                # None unless the retired lifecycle's watchers failed to
                # stop; the new state is still live in that case.
                "retired_shutdown_error": retired_shutdown_error,
            },
        )
        if audit_error is not None:
            # §4.6, normative: an audit append failure turns the result
            # into audit_failed (ok=False), never a silent ok. The
            # mutation succeeded — the message states plainly that the
            # reload IS live but unrecorded.
            message = (
                f"audit_failed: {audit_error}; {candidate.summary} — "
                "the reload IS live but unrecorded."
            )
            self._reload_diagnostics = [
                Diagnostic(
                    kind=DiagnosticKind.LOAD_FAILURE,
                    rune_name="engine",
                    message=message,
                )
            ]
            return ReloadResult(
                ok=False,
                message=message,
                prompt_changed=candidate.prompt_changed,
                spells_changed=candidate.spells_changed,
            )
        self._reload_diagnostics = [
            *candidate.rune_diagnostics,
            *(
                Diagnostic(
                    kind=DiagnosticKind.LOAD_FAILURE,
                    rune_name="engine",
                    message=warning,
                )
                for warning in candidate.spell_file_warnings
            ),
            *(
                Diagnostic(
                    kind=DiagnosticKind.LOAD_FAILURE,
                    rune_name="engine",
                    message=error,
                )
                for error in candidate.rehydrate_errors
            ),
            *(
                [
                    Diagnostic(
                        kind=DiagnosticKind.LOAD_FAILURE,
                        rune_name="engine",
                        message=retired_shutdown_error,
                    )
                ]
                if retired_shutdown_error is not None
                else []
            ),
        ]
        return ReloadResult(
            ok=True,
            message=candidate.summary,
            prompt_changed=candidate.prompt_changed,
            spells_changed=candidate.spells_changed,
        )

    def _apply_reload_candidate(
        self, candidate: ReloadCandidate
    ) -> RuneLifecycle | None:
        """Swap in validated reload state.

        Synchronous so the swap cannot interleave with another task.
        Returns the retired lifecycle so the caller can hand its
        watchers over to the candidate's after the swap. The live
        harness is NOT rebuilt: it reads prompt/spells/runner from
        state per turn, so reference updates are the swap.
        """
        old_lifecycle = self._rune_lifecycle
        self._environment = candidate.environment
        self._discovered_spells = list(candidate.discovered_spells)
        self._state.system_prompt = candidate.prompt
        self._state.spells = candidate.built_spells
        self._state._spell_index = {s.name: s for s in candidate.built_spells}
        # Reset the version guard so the next turn sees the fresh cache.
        runner = candidate.runner
        self._spell_version_seen = runner.spell_version if runner is not None else 0
        if candidate.lifecycle is not None and runner is not None:
            # A genuine candidate: swap the runner with its wiring and
            # retire the old lifecycle.
            assert self._provider_registry is not None
            _commit_candidate_providers(
                self._provider_registry,
                self._state.rune_runner,
                runner,
            )
            self._state.rune_runner = runner
            event_bus = self._state.event_bus
            if event_bus is not None:
                runner.on_event(
                    "mvge_event", lambda ev: event_bus.emit(ev.type, ev.data)
                )
            self._gateway_engaged_rune = candidate.gateway_rune
            self._rune_lifecycle = candidate.lifecycle
            self._rebind_runner_context()
        return old_lifecycle

    def _audit_reload(
        self,
        *,
        ok: bool,
        code: str,
        message: str,
        extra: dict[str, Any] | None = None,
    ) -> str | None:
        """Append one reload record to the user-scope audit log."""
        record: dict[str, Any] = dict(extra) if extra else {}
        record.update(
            {
                "timestamp": utcnow(),
                "rune": "engine",
                "op": "reload",
                "outcome": "ok" if ok else "failed",
                "code": code,
                "target": None,
                "message": message,
            }
        )
        try:
            RuneAuditLog(default_rune_ops_dir()).append_event(record)
        except Exception as exc:
            logger.exception("Reload audit append failed")
            return f"audit append failed: {type(exc).__name__}: {exc}"
        return None

    def _rebind_runner_context(self) -> None:
        """Synchronize active session/tome context into the bound RuneRunner."""
        runner = self._state.rune_runner
        if runner is not None and self._tome is not None:
            project_root = getattr(runner.context, "project_root", "")
            config_manager = (
                self._environment.config_manager if self._environment else None
            )
            configured = getattr(config_manager, "project_dir", None)
            if configured is not None:
                project_root = str(Path(configured).resolve())
            if dataclasses.is_dataclass(runner.context) and not isinstance(
                runner.context, type
            ):
                new_ctx = dataclasses.replace(
                    runner.context,
                    project_root=project_root,
                    session_id=self._tome.tome_id,
                    tome_dir=str(self._tome_dir) if self._tome_dir else "",
                    model_id=self._model_id or "",
                )
                runner.bind_context(new_ctx)

    def set_model_and_realm(self, model: Model, realm: Realm) -> None:
        """Update model and realm references on harness and compaction runner.

        Emits CONFIG_CHANGE event for observers (GUI, CLI, tests).
        Mirrors Pi's config_update event on AgentLane.setModel().
        """
        self._model = model
        self._realm = realm
        self._configured_model = model.id
        self._configured_realm = realm.__class__.__name__

        comp: CompactionRunner | None = self._compaction
        if comp is None:
            self._compaction = CompactionRunner(
                realm=self._realm,
                model=self._model,
                emit=self._emit,
                settings=self._compaction_settings,
                tome=self._tome,
            )
        else:
            comp.rebind(model=model, realm=realm)

        # Emit CONFIG_CHANGE for observers (tests, GUI, CLI)
        asyncio.create_task(
            self._emit(
                MvgeEvent(
                    type=MvgeEventType.CONFIG_CHANGE,
                    data={"model": model.id, "realm": realm.__class__.__name__},
                )
            )
        )

    @property
    def snapshot(self) -> ExecutionSnapshot:
        """Read-only observability snapshot (mirrors Pi's LaneExecutionInfo)."""
        return ExecutionSnapshot(
            configured_model=self._configured_model,
            captured_model=self._captured_model,
            configured_realm=self._configured_realm,
            captured_realm=self._captured_realm,
            contemplation_budget=self._state.contemplation_budget
            if isinstance(self._state.contemplation_budget, int)
            else None,
            active_spell_count=len(self._state.spells),
        )

    def set_contemplation_level(self, level: ContemplationLevel | str) -> None:
        """Set the contemplation level for subsequent turns."""
        self._state.contemplation_level = level
        lvl_val = level.value if isinstance(level, ContemplationLevel) else str(level)
        if self._tome is not None:
            asyncio.create_task(
                self._tome.record_custom_async(
                    "contemplation_switch", {"level": lvl_val}
                )
            )

    def ensure_system_prompt(self, prompt: str) -> None:
        """Set the system prompt if none is set (construction-time)."""
        if not self._state.system_prompt:
            self._state.system_prompt = prompt

    async def switch_model(self, model_id: str) -> None:
        """Switch the model in-flight."""
        if self._model is not None and self._model.id == model_id:
            return

        self._model_id = model_id
        if self._provider_registry is not None:
            new_model, new_realm = self._provider_registry.resolve(
                model_id, self._api_key or "", self._provider_name
            )
            self._model = new_model
            self._realm = new_realm
            self._state.model = dataclasses.asdict(new_model)
            self.set_model_and_realm(new_model, new_realm)
            if self._tome is not None:
                await self._tome.record_custom_async(
                    "model_switch", {"model": new_model.id}
                )

    def set_plan_mode(self, enabled: bool) -> None:
        """Enable or disable plan mode (read-only spells only).

        Refreshes the spell cache immediately; the next turn's
        LoopContext carries the new spell set. A turn already in
        flight keeps the LoopContext it was built with.
        """
        self._plan_mode = enabled
        self.reload_spells()
        if enabled and not self._state.spells:
            logger.warning(
                "Plan mode enabled but no spells are marked read-only; "
                "the agent has no tools available in this mode.",
            )

    def set_enabled_spells(self, spell_names: Sequence[str] | None) -> None:
        """Filter which spells are enabled for execution."""
        self._enabled_spells_filter = (
            set(spell_names) if spell_names is not None else None
        )
        self.reload_spells()

    @property
    def queue_mode(self) -> QueueMode:
        return self._state.queue_mode

    @queue_mode.setter
    def queue_mode(self, mode: QueueMode | str) -> None:
        if isinstance(mode, str):
            mode = QueueMode(mode)
        self._state.queue_mode = mode

    @property
    def driver(self) -> TurnDriver:
        """The TurnDriver capability (this harness)."""
        return self

    @property
    def lifecycle(self) -> SessionLifecycle:
        """The SessionLifecycle capability (this harness)."""
        return self

    @property
    def configuration(self) -> AgentConfiguration:
        """The AgentConfiguration capability (this harness)."""
        return self

    def steer(self, text: str) -> None:
        """Enqueue steering input; drained at the next turn boundary."""
        self._state.steer_queue.append(SummonerRequest(role="user", content=text))

    def follow_up(self, text: str) -> None:
        """Enqueue a follow-up; drained at the next turn boundary."""
        self._state.followup_queue.append(SummonerRequest(role="user", content=text))

    def abort(self) -> None:
        """Abort the in-flight turn, if any. Idempotent."""
        if self._abort_controller is not None:
            self._abort_controller.abort()

    @property
    def turn_in_flight(self) -> bool:
        return self._turn_in_flight

    @property
    def reload_pending(self) -> bool:
        """Whether a reload is queued for the next turn boundary."""
        return self._reload_pending

    @property
    def reload_diagnostics(self) -> list[Diagnostic | SkillDiagnostic]:
        """Diagnostics from the last reload execution."""
        return list(self._reload_diagnostics)

    def _make_stream_fn(self) -> StreamFn:
        """Build the provider closure for a turn from captured state.

        The driver owns the Realm: no stream_fn crosses the seam.
        """
        state = self._state
        model = self._model
        realm = self._realm
        assert model is not None
        assert realm is not None
        spells = [
            {
                "type": "function",
                "function": {
                    "name": spell.name,
                    "description": spell.description,
                    "parameters": spell.parameters,
                },
            }
            for spell in state.spells
            if spell.parameters
        ]

        def stream_fn(
            invocations: list[MvgeInvocation],
            signal: AbortSignal | None = None,
        ) -> AsyncIterator[RealmResponse]:
            return realm.stream(
                model=model,
                invocations=invocations,
                config=ChannelConfig(
                    model=model,
                    # The tome id is the stable per-conversation session
                    # id. Realms that route per conversation (opencode
                    # sends x-opencode-session) read it from meta_data;
                    # without it they fall back to a per-instance value
                    # that does not survive a realm rebuild across turns.
                    meta_data=(
                        {"session_id": self._tome.tome_id}
                        if self._tome is not None
                        else {}
                    ),
                    temperature=(
                        state.temperature if state.temperature is not None else 0.7
                    ),
                    max_tokens=(
                        state.max_tokens if state.max_tokens is not None else 4096
                    ),
                    contemplation_level=(
                        state.contemplation_level.value
                        if isinstance(state.contemplation_level, ContemplationLevel)
                        else str(state.contemplation_level)
                    ),
                    contemplation_budget=state.contemplation_budget,
                    exclude_contemplation=state.exclude_contemplation,
                    spells=spells,
                    system_prompt=state.system_prompt,
                ),
                signal=signal,
            )

        return stream_fn

    async def run_turn(self, invocation: SummonerRequest) -> MvgeInvocation:
        """Run one turn for the given Summoner invocation.

        TurnDriver entry point: appends the invocation, builds the
        provider closure from the captured Realm, and drives the turn.
        A reload queued mid-turn applies at the turn boundary.
        """
        self._state.invocations.append(invocation)
        if self._abort_controller is not None:
            self._abort_controller.abort()
        self._abort_controller = AbortController()
        signal = self._abort_controller.signal

        self._turn_in_flight = True
        try:
            return await self.run(
                self._make_stream_fn(),
                model=dataclasses.asdict(self._model) if self._model else {},
                contemplation_level=(
                    self._state.contemplation_level.value
                    if isinstance(self._state.contemplation_level, ContemplationLevel)
                    else str(self._state.contemplation_level)
                ),
                signal=signal,
            )
        finally:
            self._turn_in_flight = False
            if self._reload_pending:
                try:
                    result = await self.reload()
                except Exception:
                    logger.exception("Queued reload failed at turn boundary")
                    # Flag stays True: the request survives for retry at the
                    # next boundary.
                else:
                    if result.ok:
                        self._reload_pending = False
                    # If not ok, the flag stays True so the next turn
                    # boundary retries the request instead of dropping it.

    def _build_spells_from(
        self,
        spells: Sequence[SpellUnion],
        runner: RuneRunner | None = None,
    ) -> list[MvgeSpell]:
        """Build executable spells from an explicit spell list.

        Single source of validation, collision renaming, and filtering.
        ``runner`` selects which runner's registered spells are merged:
        the live runner by default, a candidate runner during reload.
        """
        built: list[MvgeSpell] = []
        for s in spells:
            spell = coerce_spell(s)
            if not _validate_spell_name(spell.name):
                logger.warning(
                    "Spell '%s' has an invalid name (must match ^[a-zA-Z0-9_-]+$) "
                    "and will be skipped.",
                    spell.name,
                )
                continue
            built.append(spell)

        seen_names: set[str] = {s.name for s in built}

        active_runner = runner if runner is not None else self._state.rune_runner
        if active_runner is not None:
            active_runner.set_discoverable_builtin_spells([copy.copy(s) for s in built])
            active = set(active_runner.get_active_spells())
            for rs in active_runner.get_all_registered_spells():
                if rs.name not in active:
                    continue

                if not _validate_spell_name(rs.name):
                    logger.warning(
                        "Rune spell '%s' has an invalid name "
                        "(must match ^[a-zA-Z0-9_-]+$) and will be skipped.",
                        rs.name,
                    )
                    continue

                if not _validate_spell_parameters(getattr(rs, "parameters", None)):
                    logger.warning(
                        "Rune spell '%s' has an invalid parameter schema "
                        "and will be skipped.",
                        rs.name,
                    )
                    continue

                if not _validate_spell_signature(rs):
                    logger.warning(
                        "Rune spell '%s' execution signature does not conform "
                        "to the execution contract and will be skipped.",
                        rs.name,
                    )
                    continue

                spell_to_add: MvgeSpell = RuneSpellWrapper(rs, runner_origin=True)
                spell_name = rs.name
                if spell_name in seen_names:
                    source_rune = getattr(rs, "source_rune", None) or "rune"
                    prefixed_name = f"{source_rune}_{spell_name}"
                    logger.warning(
                        "Rune spell '%s' collides with existing spell; "
                        "renaming to '%s'.",
                        spell_name,
                        prefixed_name,
                    )
                    if not _validate_spell_name(prefixed_name):
                        logger.warning(
                            "Prefixed rune spell '%s' has an invalid name "
                            "and will be skipped.",
                            prefixed_name,
                        )
                        continue
                    if prefixed_name in seen_names:
                        logger.warning(
                            "Prefixed rune spell '%s' still collides with an "
                            "existing spell and will be skipped.",
                            prefixed_name,
                        )
                        continue
                    spell_to_add = copy.copy(spell_to_add)
                    spell_to_add.name = prefixed_name
                    spell_name = prefixed_name

                seen_names.add(spell_name)
                built.append(spell_to_add)

        if self._enabled_spells_filter is not None:
            built = [s for s in built if s.name in self._enabled_spells_filter]

        if active_runner is not None:
            global_allowlist = active_runner.get_global_spell_allowlist()
            if global_allowlist is not None:
                allowlist_set = set(global_allowlist)
                built = [s for s in built if s.name in allowlist_set]

        if self._plan_mode:
            built = [s for s in built if s.read_only]

        if self._allowed_spells is not None:
            built = [s for s in built if s.name in self._allowed_spells]

        return built

    def _build_spells(self) -> list[MvgeSpell]:
        """Rebuild the executable spell list from discovered spells."""
        return self._build_spells_from(self._discovered_spells)

    def reload_spells(self) -> None:
        """Re-run validated spell resolution and refresh the spell cache.

        Called after Rune watcher reload, manual load_runes(), or any
        event that may have changed the rune-registered spell set.
        Refreshes ``state.spells`` and the internal spell index, and
        records the runner's spell version so the per-turn read can
        skip the rebuild when nothing changed.
        """
        self._state.spells = self._build_spells()
        self._state._spell_index = {s.name: s for s in self._state.spells}
        runner = self._state.rune_runner
        self._spell_version_seen = runner.spell_version if runner is not None else 0

    def _resolve_spells(self) -> list[MvgeSpell]:
        """Resolve the spell list for the current turn.

        Consults ``RuneRunner.spell_version``: an unchanged version is a
        cache hit (no ``inspect.signature``, no ``RuneSpellWrapper``
        churn); a changed version rebuilds the cache. The legacy
        append-only merge is deleted — removals now take effect.
        """
        runner = self._state.rune_runner
        version = runner.spell_version if runner is not None else 0
        if self._spell_version_seen != version:
            self.reload_spells()
        return self._state.spells

    async def _drain_steer_queue(self) -> list[MvgeInvocation]:
        if not self._state.steer_queue:
            return []
        if self._state.queue_mode == QueueMode.ONE_AT_A_TIME:
            return [self._state.steer_queue.pop(0)]
        queued: list[MvgeInvocation] = list(self._state.steer_queue)
        self._state.steer_queue.clear()
        return queued

    async def _drain_followup_queue(self) -> list[MvgeInvocation]:
        if not self._state.followup_queue:
            return []
        if self._state.queue_mode == QueueMode.ONE_AT_A_TIME:
            return [self._state.followup_queue.pop(0)]
        queued: list[MvgeInvocation] = list(self._state.followup_queue)
        self._state.followup_queue.clear()
        return queued

    def _rune_context_value(self, field_name: str) -> str:
        """Read a string field from the bound runner's RuneContext.

        Returns "" when no runner is bound, keeping the loop core runnable
        without the rune system.
        """
        runner = self._state.rune_runner
        if runner is None:
            return ""
        value = getattr(runner.context, field_name, "")
        return value if isinstance(value, str) else ""

    def _build_callbacks(self, signal: AbortSignal | None = None) -> LoopCallbacks:
        runner = self._state.rune_runner

        async def after_invocation(
            invocations: list[MvgeInvocation],
        ) -> list[MvgeInvocation] | None:
            if self._compaction is not None:
                replacement = await self._compaction.maybe_compact(
                    list(invocations), signal
                )
                if replacement is not None:
                    invocations = list(replacement)
                    self._state.invocations = list(replacement)
            if (
                self._callbacks is not None
                and self._callbacks.after_invocation is not None
            ):
                replacement = await self._callbacks.after_invocation(list(invocations))
                if replacement is not None:
                    invocations = list(replacement)
                    self._state.invocations = list(replacement)
            return invocations

        if runner is None:
            return LoopCallbacks(
                get_steering_messages=(
                    self._callbacks.get_steering_messages
                    if self._callbacks and self._callbacks.get_steering_messages
                    else self._drain_steer_queue
                ),
                get_follow_up_messages=(
                    self._callbacks.get_follow_up_messages
                    if self._callbacks and self._callbacks.get_follow_up_messages
                    else self._drain_followup_queue
                ),
                after_invocation=after_invocation,
                transform_context=(
                    self._callbacks.transform_context if self._callbacks else None
                ),
                before_realm_headers=(
                    self._callbacks.before_realm_headers if self._callbacks else None
                ),
                before_spell_cast=(
                    self._callbacks.before_spell_cast if self._callbacks else None
                ),
                after_spell_result=(
                    self._callbacks.after_spell_result if self._callbacks else None
                ),
                should_stop_after_turn=(
                    self._callbacks.should_stop_after_turn if self._callbacks else None
                ),
                prepare_next_turn=(
                    self._callbacks.prepare_next_turn if self._callbacks else None
                ),
                before_provider_request=(
                    self._callbacks.before_provider_request if self._callbacks else None
                ),
            )

        async def transform_context(
            invocations: list[MvgeInvocation],
        ) -> list[MvgeInvocation]:
            payload = payload_for(
                SigilHook.CONTEXT_TRANSFORM, invocations=list(invocations)
            )
            # The chain carries the raw list: shipped runes take and return
            # the list itself. A rune returning the documented dataclass is
            # accepted below instead of crashing at list(result).
            try:
                result = await runner.emit_chain(
                    SigilHook.CONTEXT_TRANSFORM, payload.invocations
                )
            except Exception:
                result = payload.invocations
            if isinstance(result, ContextTransformData):
                current = result.invocations
            elif isinstance(result, list):
                current = result
            else:
                current = payload.invocations
            if self._callbacks is not None and self._callbacks.transform_context:
                current = await self._callbacks.transform_context(current)
            return current

        async def before_realm_headers(headers: dict[str, str]) -> dict[str, str]:
            try:
                result = await runner.emit_chain(
                    SigilHook.BEFORE_PROVIDER_HEADERS,
                    payload_for(SigilHook.BEFORE_PROVIDER_HEADERS, headers=headers),
                )
            except Exception:
                result = None
            if isinstance(result, BeforeProviderHeadersData):
                incoming: dict[str, str] = result.headers
            elif isinstance(result, dict):
                incoming = result
            else:
                incoming = {}
            current = {**headers, **incoming}
            if self._callbacks is not None and self._callbacks.before_realm_headers:
                current = await self._callbacks.before_realm_headers(current)
            return current

        async def before_spell_cast(data: dict[str, Any]) -> dict[str, Any] | None:
            if self._callbacks is not None and self._callbacks.before_spell_cast:
                blocked = await self._callbacks.before_spell_cast(data)
                if blocked:
                    return blocked
            try:
                return await runner.emit_block(
                    SigilHook.BEFORE_SPELL_CAST,
                    payload_for(
                        SigilHook.BEFORE_SPELL_CAST,
                        spell_cast=data["spell_cast"],
                        spell_name=data["spell_name"],
                    ),
                )
            except Exception:
                return None

        async def after_spell_result(data: dict[str, Any]) -> dict[str, Any]:
            try:
                result = await runner.emit_chain(
                    SigilHook.AFTER_SPELL_RESULT,
                    payload_for(
                        SigilHook.AFTER_SPELL_RESULT,
                        spell_name=data["spell_name"],
                        spell_cast_id=data["spell_cast_id"],
                        result=data["result"],
                    ),
                )
            except Exception:
                result = None
            if isinstance(result, AfterSpellResultData):
                current = {
                    "spell_name": result.spell_name,
                    "spell_cast_id": result.spell_cast_id,
                    "result": result.result,
                }
            elif isinstance(result, dict):
                current = result
            else:
                current = data
            if self._callbacks is not None and self._callbacks.after_spell_result:
                current = await self._callbacks.after_spell_result(current)
            return current

        async def should_stop_after_turn() -> bool:
            if (
                self._callbacks is not None
                and self._callbacks.should_stop_after_turn
                and await self._callbacks.should_stop_after_turn()
            ):
                return True
            try:
                result = await runner.emit_first(
                    SigilHook.SHOULD_STOP_AFTER_TURN,
                    payload_for(SigilHook.SHOULD_STOP_AFTER_TURN),
                )
            except Exception:
                return False
            if isinstance(result, bool):
                return result
            if isinstance(result, dict):
                return bool(result.get("stop", False))
            return bool(getattr(result, "stop", False))

        async def before_provider_request(
            data: dict[str, Any],
        ) -> dict[str, Any] | None:
            if self._callbacks is not None and self._callbacks.before_provider_request:
                cb_res = await self._callbacks.before_provider_request(data)
                if cb_res is not None:
                    return cb_res
            model_data = data.get("model") or {}
            result: Any = await runner.emit_first(
                SigilHook.BEFORE_PROVIDER_REQUEST,
                payload_for(SigilHook.BEFORE_PROVIDER_REQUEST, model=model_data),
            )
            if isinstance(result, dict):
                return result
            if result is not None and hasattr(result, "synthetic_response"):
                return {"synthetic_response": result.synthetic_response}
            return None

        async def prepare_next_turn(context: LoopContext) -> LoopContext:
            current = context
            if runner is not None and self._spell_version_seen != runner.spell_version:
                self.reload_spells()
                current = dataclasses.replace(current, spells=self._state.spells)
            try:
                result = await runner.emit_chain(
                    SigilHook.PREPARE_NEXT_TURN,
                    prepare_next_turn_payload(current),
                )
            except Exception:
                result = None
            if isinstance(result, PrepareNextTurnData):
                with contextlib.suppress(TypeError, ValueError, AttributeError):
                    current = _apply_prepare_next_turn(result, current)
            elif isinstance(result, LoopContext):
                current = result
            if runner is not None and self._spell_version_seen != runner.spell_version:
                self.reload_spells()
                current = dataclasses.replace(current, spells=self._state.spells)
            if self._callbacks is not None and self._callbacks.prepare_next_turn:
                current = await self._callbacks.prepare_next_turn(current)
            return current

        async def approval_gate(request: ApprovalRequest) -> ApprovalDecision:
            return await runner.evaluate_spell_gates(request)

        return LoopCallbacks(
            transform_context=transform_context,
            before_realm_headers=before_realm_headers,
            before_spell_cast=before_spell_cast,
            after_spell_result=after_spell_result,
            approval_gate=approval_gate,
            get_steering_messages=(
                self._callbacks.get_steering_messages
                if self._callbacks and self._callbacks.get_steering_messages
                else self._drain_steer_queue
            ),
            get_follow_up_messages=(
                self._callbacks.get_follow_up_messages
                if self._callbacks and self._callbacks.get_follow_up_messages
                else self._drain_followup_queue
            ),
            after_invocation=after_invocation,
            should_stop_after_turn=should_stop_after_turn,
            prepare_next_turn=prepare_next_turn,
            before_provider_request=before_provider_request,
        )

    async def _emit(self, event: MvgeEvent) -> None:
        runner = self._state.rune_runner
        hook = _EVENT_TO_SIGIL.get(event.type)
        is_input = event.type == MvgeEventType.INPUT
        if is_input and runner is not None and hook is not None:
            # INPUT is a transform hook: runes observe and may rewrite the
            # Summoner input before it is consumed into state. The chained
            # content is what gets reduced, recorded and broadcast.
            chained = await runner.emit_chain(hook, self._sigil_payload(event))
            event.data["content"] = _transformed_input(
                chained, event.data.get("content")
            )
            hook = None  # already fanned out through the chain
        self._reduce(event)
        self._record_event(event)

        bus = self._state.event_bus
        if bus is not None:
            bus.emit(event.type, event.data)

        if runner is not None and hook is not None:
            await runner.emit_async(hook, self._sigil_payload(event))

        if event.type == MvgeEventType.MESSAGE_END:
            await self._record_invocation(event)

    def _reduce(self, event: MvgeEvent) -> None:
        mana_used = event.data.get("mana_used")
        if isinstance(mana_used, int):
            self._state.mana_used = mana_used

        if event.type == MvgeEventType.BEFORE_PROVIDER_REQUEST:
            model = event.data.get("model")
            if isinstance(model, dict) and model.get("headers"):
                merged = {
                    **(self._state.model or {}).get("headers", {}),
                    **model["headers"],
                }
                self._state.model = {**(self._state.model or {}), "headers": merged}

        if event.type == MvgeEventType.INPUT:
            transformed = event.data.get("content")
            for inv in reversed(self._state.invocations):
                if isinstance(inv, SummonerRequest):
                    inv.content = transformed
                    break

        if event.type == MvgeEventType.MESSAGE_END:
            invocation = event.data.get("invocation")
            if invocation is not None and not isinstance(invocation, SummonerRequest):
                self._state.invocations.append(invocation)

    def _record_event(self, event: MvgeEvent) -> None:
        if len(self._state.events) >= self._state.max_events:
            self._state.events = self._state.events[-self._state.max_events // 2 :]
        self._state.events.append(event)

    def _sigil_payload(self, event: MvgeEvent) -> Any:
        hook = _EVENT_TO_SIGIL.get(event.type)
        if hook is None:
            return event.data
        return payload_for_event(hook, event)

    async def _record_invocation(self, event: MvgeEvent) -> None:
        session: MvgeTome | None = self._tome or self._state.agent_tome
        if session is None:
            return
        invocation = event.data.get("invocation")
        if invocation is None:
            return

        parent_id = await session.active_leaf_id_async()
        model = self._state.model or {}
        if isinstance(invocation, SummonerRequest):
            await session.record_message_async(
                role="user",
                content=invocation.content,
                parent_id=parent_id,
            )
        elif isinstance(invocation, MvgeResponse):
            await session.record_message_async(
                role="assistant",
                content=invocation.content,
                parent_id=parent_id,
                model=model.get("id", ""),
                provider=model.get("id", "").split("/")[0],
            )
        elif isinstance(invocation, SpellResultMessage):
            await session.record_message_async(
                role="spellResult",
                content=invocation.content,
                parent_id=parent_id,
            )

    async def run(
        self,
        stream_fn: StreamFn,
        model: dict[str, Any],
        contemplation_level: str = "medium",
        signal: AbortSignal | None = None,
        prompt: str | None = None,
    ) -> MvgeInvocation:
        """Run the harness turn by turn, driving run_loop directly."""
        if prompt is not None:
            self._state.invocations.append(SummonerRequest(role="user", content=prompt))

        await self._maybe_precompact(signal)

        self._state.is_streaming = True
        self._state.model = model
        self._state.contemplation_level = ContemplationLevel(contemplation_level)

        # Capture configured model/realm for this turn (Pi's capturedModel semantics)
        self._captured_model = self._configured_model
        self._captured_realm = self._configured_realm
        self._captured_realm = self._configured_realm

        context = LoopContext(
            system_prompt=self._state.system_prompt,
            prompt_source=self._state.prompt_source,
            invocations=list(self._state.invocations),
            spells=self._resolve_spells(),
            contemplation_level=self._state.contemplation_level,
            max_tokens=self._state.max_tokens,
            temperature=self._state.temperature,
            spell_timeout_ms=self._state.spell_timeout_ms,
            contemplation_budget=self._state.contemplation_budget,
            exclude_contemplation=self._state.exclude_contemplation,
            max_turns=self._state.max_turns,
            queue_mode=self._state.queue_mode,
            project_root=self._rune_context_value("project_root"),
            tome_id=self._rune_context_value("session_id"),
            agent_name=self._rune_context_value("agent_name"),
        )

        try:
            new_invocations = await run_loop(
                context,
                stream_fn,
                self._emit,
                self._build_callbacks(signal=signal),
                signal,
            )
        finally:
            self._state.is_streaming = False

        if new_invocations:
            return new_invocations[-1]
        if self._state.invocations:
            return self._state.invocations[-1]
        raise RuntimeError("No invocations to return")

    async def _maybe_precompact(self, signal: AbortSignal | None = None) -> None:
        """Force-compact before the first send when the Mana Pool is crowded.

        Post-turn compaction cannot rescue an already-oversized transcript:
        the next provider send fails before after_invocation ever runs. This
        pre-send attempt handles gradual accumulation. A single already-huge
        spell result may survive (it sits in the retained tail); that session
        needs manual rescue (compact / undo / new tome) and future casts are
        bounded by the dispatcher truncation backstop.
        """
        if signal is not None and signal.aborted:
            return
        if self._compaction is None or self._model is None:
            return
        try:
            context_window = getattr(self._model, "context_window", 0) or 0
            if not context_window:
                return
            settings = getattr(
                self._compaction, "_settings", DEFAULT_COMPACTION_SETTINGS
            )
            estimated = estimate_context_mana(list(self._state.invocations)).mana
            if not should_compact(estimated, context_window, settings):
                return
            replacement = await self._compaction.force_compact(
                list(self._state.invocations), signal
            )
            if replacement is not None:
                self._state.invocations = list(replacement)
        except Exception:
            logger.exception("Pre-send compaction attempt failed")


__all__ = ["MvgeHarness"]
