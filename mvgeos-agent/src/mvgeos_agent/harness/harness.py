from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from typing import Any

from mvgeos_core.abort import AbortSignal
from mvgeos_core.approval import ApprovalDecision, ApprovalRequest
from mvgeos_core.channel import Model, MvgeResponse
from mvgeos_core.events import (
    ContemplationLevel,
    ExecutionSnapshot,
    MvgeEvent,
    MvgeEventType,
    PromptSource,
    QueueMode,
)
from mvgeos_core.invocations import (
    MvgeInvocation,
    SummonerRequest,
)
from mvgeos_core.loop import EmitSink, LoopCallbacks, LoopContext, StreamFn, run_loop
from mvgeos_core.spells import MvgeSpell, SpellResultMessage
from mvgeos_provider.base import Realm
from mvgeos_runes.types import (
    AfterSpellResultData,
    BeforeProviderHeadersData,
    ContextTransformData,
    InputData,
    PrepareNextTurnData,
    SigilHook,
)

from mvgeos_agent.agent_session import MvgeTome
from mvgeos_agent.function_spell import RuneSpellWrapper
from mvgeos_agent.harness.compaction.compaction import (
    DEFAULT_COMPACTION_SETTINGS,
    CompactionSettings,
    estimate_context_mana,
    should_compact,
)
from mvgeos_agent.harness.compaction.compaction_runner import CompactionRunner
from mvgeos_agent.sigil_payloads import (
    payload_for,
    payload_for_event,
    prepare_next_turn_payload,
)
from mvgeos_agent.types import MvgeState

logger = logging.getLogger(__name__)

_EVENT_TO_SIGIL: dict[MvgeEventType, SigilHook] = {
    MvgeEventType.AGENT_START: SigilHook.AGENT_START,
    MvgeEventType.AGENT_END: SigilHook.AGENT_END,
    MvgeEventType.COMPACTION_START: SigilHook.COMPACTION_START,
    MvgeEventType.COMPACTION_END: SigilHook.COMPACTION_END,
    MvgeEventType.TURN_START: SigilHook.TURN_START,
    MvgeEventType.TURN_END: SigilHook.TURN_END,
    MvgeEventType.INPUT: SigilHook.INPUT,
    MvgeEventType.BEFORE_PROVIDER_REQUEST: SigilHook.BEFORE_PROVIDER_REQUEST,
    MvgeEventType.AFTER_PROVIDER_RESPONSE: SigilHook.AFTER_PROVIDER_RESPONSE,
    MvgeEventType.BEFORE_INVOCATION: SigilHook.BEFORE_INVOCATION,
    MvgeEventType.AFTER_INVOCATION: SigilHook.AFTER_INVOCATION,
}


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


class MvgeHarness:
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
        refresh_spells: Callable[[], list[MvgeSpell]] | None = None,
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
        self._refresh_spells = refresh_spells

        self._compaction: CompactionRunner | None
        if compaction is not None:
            self._compaction = compaction
            if getattr(self._compaction, "_emit", None) is None:
                self._compaction._emit = self._emit
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
    def emit(self) -> EmitSink:
        """The sink that fans an event out to state, bus, Sigils, and Tome."""
        return self._emit

    def switch_tome(self, tome: MvgeTome) -> None:
        """Switch active Tome session reference and update CompactionRunner."""
        self._tome = tome
        self._state.agent_tome = tome
        if self._compaction is not None:
            self._compaction._tome = tome

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
            comp._model = model
            comp._realm = realm

        # Emit CONFIG_CHANGE for observers (tests, GUI, CLI)
        import asyncio

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

    def _resolve_spells(self) -> list[MvgeSpell]:
        """Resolve the spell list for the current turn.

        If a refresh_spells hook is provided (from Mvge._build_spells), use it
        for centralized validation, renaming, and filtering. Otherwise fall back
        to the legacy append-only merge (for backward compatibility).
        """
        if self._refresh_spells is not None:
            return self._refresh_spells()

        # Legacy append-only merge (no validation/rename/filter)
        spells = list(self._state.spells)
        runner = self._state.rune_runner
        if runner is None:
            return spells
        known = {spell.name for spell in spells}
        active = set(runner.get_active_spells())
        for rune_spell in runner.get_all_registered_spells():
            if rune_spell.name not in known and rune_spell.name in active:
                spells.append(RuneSpellWrapper(rune_spell, runner_origin=True))
        return spells

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
            return bool(result) if isinstance(result, bool) else False

        async def prepare_next_turn(context: LoopContext) -> LoopContext:
            try:
                result = await runner.emit_chain(
                    SigilHook.PREPARE_NEXT_TURN,
                    prepare_next_turn_payload(context),
                )
            except Exception:
                result = None
            if isinstance(result, PrepareNextTurnData):
                try:
                    current = _apply_prepare_next_turn(result, context)
                except (TypeError, ValueError, AttributeError):
                    current = context
            elif isinstance(result, LoopContext):
                current = result
            else:
                current = context
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
