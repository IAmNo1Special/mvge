from __future__ import annotations

import dataclasses
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mvge.core.abort import AbortError, AbortSignal
from mvge.core.approval import ApprovalDecision, ApprovalRequest
from mvge.core.channel import Model, MvgeResponse, RealmResponse, StopReason
from mvge.core.dispatcher import SpellDispatcher
from mvge.core.errors import (
    AuthenticationError,
    MaxTurnsExceededError,
    RateLimitError,
    UpstreamTimeoutError,
)
from mvge.core.events import (
    ContemplationLevel,
    ContentType,
    MvgeEvent,
    MvgeEventType,
    PromptSource,
    QueueMode,
)
from mvge.core.invocations import MvgeInvocation, SummonerRequest
from mvge.core.spells import ExecutionBackend, MvgeSpell

EmitSink = Callable[[MvgeEvent], Awaitable[None]]
StreamFn = Callable[
    [list[MvgeInvocation], AbortSignal | None], AsyncIterator[RealmResponse]
]


@dataclass(frozen=True)
class LoopContext:
    """Immutable snapshot of everything the loop core reads.

    The core never sees the Rune runner, the Tome, or the event bus. Those are
    the wrapper's concern; the core reaches the outside world only through its
    emit sink and its callbacks.
    """

    system_prompt: str = ""
    prompt_source: PromptSource = PromptSource.BUILTIN
    invocations: list[MvgeInvocation] = field(default_factory=list)
    spells: list[MvgeSpell] = field(default_factory=list)
    contemplation_level: ContemplationLevel = ContemplationLevel.MEDIUM
    max_tokens: int | None = None
    temperature: float | None = None
    spell_timeout_ms: int = 30000
    contemplation_budget: int | None = None
    exclude_contemplation: bool = False
    max_turns: int = 50
    queue_mode: QueueMode = QueueMode.ONE_AT_A_TIME
    project_root: str = ""
    tome_id: str = ""
    agent_name: str = ""
    workspace_root: Path | None = None
    backend: ExecutionBackend | None = None
    sub_mvge_id: str | None = None
    parent_spell_cast_id: str | None = None
    _spell_index: dict[str, MvgeSpell] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_spell_index",
            {spell.name: spell for spell in self.spells},
        )

    def get_spell(self, name: str) -> MvgeSpell | None:
        """Lookup a spell by name in O(1) time."""
        return self._spell_index.get(name)


@dataclass
class LoopCallbacks:
    """Value-returning extension points for the loop core.

    Every callback is optional. Each one must not raise: return a safe fallback
    value instead. The wrapper honours this contract when it builds callbacks
    from a Rune runner.
    """

    transform_context: (
        Callable[[list[MvgeInvocation]], Awaitable[list[MvgeInvocation]]] | None
    ) = None
    before_realm_headers: (
        Callable[[dict[str, str]], Awaitable[dict[str, str]]] | None
    ) = None
    before_spell_cast: (
        Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]] | None
    ) = None
    approval_gate: Callable[[ApprovalRequest], Awaitable[ApprovalDecision]] | None = (
        None
    )
    after_spell_result: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = (
        None
    )
    get_steering_messages: Callable[[], Awaitable[list[MvgeInvocation]]] | None = None
    get_follow_up_messages: Callable[[], Awaitable[list[MvgeInvocation]]] | None = None
    after_invocation: (
        Callable[[list[MvgeInvocation]], Awaitable[list[MvgeInvocation] | None]] | None
    ) = None
    should_stop_after_turn: Callable[[], Awaitable[bool]] | None = None
    prepare_next_turn: Callable[[LoopContext], Awaitable[LoopContext]] | None = None
    before_provider_request: (
        Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]] | None
    ) = None


@dataclass
class _RunState:
    """Mutable bookkeeping threaded through a single run."""

    mana_used: int = 0
    last_stop_reason: StopReason | None = None


@dataclass
class _TurnOutcome:
    """What one turn produced."""

    produced: list[MvgeInvocation] = field(default_factory=list)
    stop_reason: StopReason | None = None
    cast_spells: bool = False


async def _drain(
    source: Callable[[], Awaitable[list[MvgeInvocation]]] | None,
) -> list[MvgeInvocation]:
    if source is None:
        return []
    return list(await source())


async def _resolve_model(callbacks: LoopCallbacks) -> dict[str, Any]:
    model: dict[str, Any] = {}
    headers: dict[str, str] = {}
    if callbacks.before_realm_headers is not None:
        headers = await callbacks.before_realm_headers(headers)
    if headers:
        model = {**model, "headers": headers}
    return model


async def _run_turn(
    context: LoopContext,
    stream_fn: StreamFn,
    emit: EmitSink,
    callbacks: LoopCallbacks,
    invocations: list[MvgeInvocation],
    state: _RunState,
    signal: AbortSignal | None = None,
) -> _TurnOutcome:
    outcome = _TurnOutcome()
    streamed_any_chunk = False

    synthetic_response = None
    if callbacks.before_provider_request is not None:
        model_data: Any = getattr(context, "model", None)
        model_dict: dict[str, Any] = {}
        if model_data is not None:
            if dataclasses.is_dataclass(model_data) and not isinstance(
                model_data, type
            ):
                model_dict = dataclasses.asdict(model_data)
            elif isinstance(model_data, dict):
                model_dict = model_data
        req_result = await callbacks.before_provider_request({"model": model_dict})
        if isinstance(req_result, dict) and "synthetic_response" in req_result:
            synthetic_response = req_result["synthetic_response"]

    if synthetic_response is not None:

        async def _synthetic_stream() -> AsyncIterator[RealmResponse]:
            if isinstance(synthetic_response, RealmResponse):
                yield synthetic_response
            elif isinstance(synthetic_response, MvgeResponse):
                stop_str = (
                    synthetic_response.stop_reason.value
                    if hasattr(synthetic_response.stop_reason, "value")
                    else str(synthetic_response.stop_reason or "stop")
                )
                yield RealmResponse(
                    model=Model(
                        id=synthetic_response.model or "synthetic",
                        name="synthetic",
                        realm=synthetic_response.realm or "synthetic",
                        base_url="",
                        api_key="",
                    ),
                    invocation=synthetic_response,
                    stop_reason=stop_str,
                )
            elif inspect.isasyncgen(synthetic_response):
                async for item in synthetic_response:
                    if isinstance(item, RealmResponse):
                        yield item
            elif isinstance(synthetic_response, Iterable):
                for item in synthetic_response:
                    if isinstance(item, RealmResponse):
                        yield item

        stream: AsyncIterator[RealmResponse] = _synthetic_stream()
    else:
        stream = stream_fn(list(invocations), signal)

    async for response in stream:
        if response.error_message:
            error_code = getattr(response, "error_code", None)
            if error_code == "rate_limited":
                raise RateLimitError(
                    response.error_message,
                    retry_after=getattr(response, "retry_after", None),
                    limit_source=getattr(response, "limit_source", None),
                    remedy_hint=getattr(response, "remedy_hint", None),
                    reset_at=getattr(response, "reset_at", None),
                    quota_limit=getattr(response, "quota_limit", None),
                    quota_remaining=getattr(response, "quota_remaining", None),
                )
            if error_code == "auth_failed":
                raise AuthenticationError(response.error_message)
            if error_code == "upstream_idle_timeout":
                await emit(
                    MvgeEvent(
                        type=MvgeEventType.PROVIDER_ERROR,
                        data={
                            "error_code": error_code,
                            "error_message": response.error_message,
                        },
                    )
                )
                raise UpstreamTimeoutError(
                    response.error_message or "Upstream idle timeout"
                )
            raise RuntimeError(response.error_message)

        state.mana_used += response.mana_used

        await emit(
            MvgeEvent(
                type=MvgeEventType.AFTER_PROVIDER_RESPONSE,
                data={"response": response, "mana_used": state.mana_used},
            )
        )

        if response.invocation is None:
            continue

        inv: MvgeResponse = response.invocation

        if inv.stop_reason == StopReason.PENDING:
            for item in inv.content or []:
                if item.get("type") == ContentType.TEXT:
                    streamed_any_chunk = True
                    await emit(
                        MvgeEvent(
                            type=MvgeEventType.MESSAGE_UPDATE,
                            data={"text": item.get("text", "")},
                        )
                    )
                elif item.get("type") == ContentType.CONTEMPLATION:
                    streamed_any_chunk = True
                    await emit(
                        MvgeEvent(
                            type=MvgeEventType.MESSAGE_UPDATE,
                            data={
                                "text": item.get("text", ""),
                                "kind": "contemplation",
                            },
                        )
                    )
            continue

        if not streamed_any_chunk:
            for item in inv.content or []:
                if item.get("type") == ContentType.TEXT:
                    await emit(
                        MvgeEvent(
                            type=MvgeEventType.MESSAGE_UPDATE,
                            data={"text": item.get("text", "")},
                        )
                    )
                elif item.get("type") == ContentType.CONTEMPLATION:
                    await emit(
                        MvgeEvent(
                            type=MvgeEventType.MESSAGE_UPDATE,
                            data={
                                "text": item.get("text", ""),
                                "kind": "contemplation",
                            },
                        )
                    )

        await emit(
            MvgeEvent(type=MvgeEventType.BEFORE_INVOCATION, data={"invocation": inv})
        )

        _dispatcher = SpellDispatcher()
        batch_result = await _dispatcher.dispatch_batch(
            inv=inv,
            context=context,
            callbacks=callbacks,
            emit=emit,
            signal=signal,
        )
        spell_results = batch_result.messages

        await emit(
            MvgeEvent(
                type=MvgeEventType.AFTER_INVOCATION,
                data={"invocation": inv, "stop_reason": inv.stop_reason},
            )
        )

        outcome.produced.append(inv)
        outcome.stop_reason = inv.stop_reason
        state.last_stop_reason = inv.stop_reason
        await emit(
            MvgeEvent(
                type=MvgeEventType.MESSAGE_END,
                data={"invocation": inv, "mana_used": state.mana_used},
            )
        )

        for spell_result in spell_results:
            outcome.produced.append(spell_result)
            await emit(
                MvgeEvent(
                    type=MvgeEventType.MESSAGE_END,
                    data={"invocation": spell_result, "mana_used": state.mana_used},
                )
            )

        outcome.cast_spells = (
            bool(spell_results)
            and inv.stop_reason == StopReason.SPELL_USE
            and not batch_result.terminate
        )
        return outcome

    return outcome


async def run_loop(
    context: LoopContext,
    stream_fn: StreamFn,
    emit: EmitSink,
    callbacks: LoopCallbacks,
    signal: AbortSignal | None = None,
) -> list[MvgeInvocation]:
    """Drive the Realm turn by turn, casting Spells as they are requested.

    The loop owns the turn cycle: it calls `stream_fn` once per turn with the
    running transcript, channels the responses, casts any Spells, and goes
    round again while Spell results, steering, or follow-ups remain.

    Returns the Invocations produced by this run. The caller reduces any state
    it keeps from the emitted events.
    """
    if not context.invocations:
        raise RuntimeError("No invocations to process")

    invocations = list(context.invocations)
    new_invocations: list[MvgeInvocation] = []
    state = _RunState()

    await emit(MvgeEvent(type=MvgeEventType.AGENT_START, data={}))

    if callbacks.transform_context is not None:
        invocations = await callbacks.transform_context(invocations)

    initial = next(
        (inv for inv in reversed(invocations) if isinstance(inv, SummonerRequest)),
        None,
    )
    if initial is not None:
        await emit(
            MvgeEvent(type=MvgeEventType.INPUT, data={"content": initial.content})
        )
        await emit(
            MvgeEvent(
                type=MvgeEventType.MESSAGE_END,
                data={"invocation": initial, "mana_used": state.mana_used},
            )
        )

    model = await _resolve_model(callbacks)
    pending: list[MvgeInvocation] = []
    turns = 0

    if signal is not None and signal.aborted:
        await emit(
            MvgeEvent(
                type=MvgeEventType.AGENT_END,
                data={"stop_reason": StopReason.ABORTED.value},
            )
        )
        return new_invocations

    while True:
        keep_going = True

        if signal is not None and signal.aborted:
            await emit(
                MvgeEvent(
                    type=MvgeEventType.AGENT_END,
                    data={"stop_reason": StopReason.ABORTED.value},
                )
            )
            return new_invocations

        while keep_going or pending:
            turns += 1
            if turns > context.max_turns:
                raise MaxTurnsExceededError(context.max_turns)

            if turns > 1 and callbacks.prepare_next_turn is not None:
                context = await callbacks.prepare_next_turn(context)

            for queued in pending:
                invocations.append(queued)
                new_invocations.append(queued)
                await emit(
                    MvgeEvent(
                        type=MvgeEventType.MESSAGE_END,
                        data={"invocation": queued, "mana_used": state.mana_used},
                    )
                )
            pending = []

            await emit(
                MvgeEvent(
                    type=MvgeEventType.BEFORE_PROVIDER_REQUEST, data={"model": model}
                )
            )
            await emit(MvgeEvent(type=MvgeEventType.TURN_START, data={"model": model}))

            try:
                turn = await _run_turn(
                    context, stream_fn, emit, callbacks, invocations, state, signal
                )
            except AbortError:
                await emit(
                    MvgeEvent(
                        type=MvgeEventType.AGENT_END,
                        data={"stop_reason": StopReason.ABORTED.value},
                    )
                )
                return new_invocations
            invocations.extend(turn.produced)
            new_invocations.extend(turn.produced)

            if turn.produced and callbacks.after_invocation is not None:
                replacement = await callbacks.after_invocation(list(invocations))
                if replacement is not None:
                    invocations = list(replacement)

            await emit(
                MvgeEvent(
                    type=MvgeEventType.TURN_END,
                    data={
                        "stop_reason": turn.stop_reason,
                        "mana_used": state.mana_used,
                    },
                )
            )

            if callbacks.should_stop_after_turn is not None:
                should_stop = await callbacks.should_stop_after_turn()
                if should_stop:
                    await emit(
                        MvgeEvent(
                            type=MvgeEventType.AGENT_END,
                            data={"stop_reason": state.last_stop_reason},
                        )
                    )
                    return new_invocations

            keep_going = turn.cast_spells
            if not keep_going:
                pending = await _drain(callbacks.get_steering_messages)

        follow_ups = await _drain(callbacks.get_follow_up_messages)
        if follow_ups:
            pending = follow_ups
            continue
        break

    await emit(
        MvgeEvent(
            type=MvgeEventType.AGENT_END,
            data={"stop_reason": state.last_stop_reason},
        )
    )
    return new_invocations


__all__ = [
    "EmitSink",
    "LoopCallbacks",
    "LoopContext",
    "StreamFn",
    "run_loop",
]
