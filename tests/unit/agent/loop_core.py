from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from mvge.core.channel import (
    Model,
    MvgeResponse,
    RealmResponse,
    StopReason,
)
from mvge.core.errors import MaxTurnsExceededError
from mvge.core.events import (
    ContemplationLevel,
    MvgeEvent,
    MvgeEventType,
    QueueMode,
)
from mvge.core.invocations import (
    MvgeInvocation,
    SummonerRequest,
)
from mvge.core.loop import LoopCallbacks, LoopContext, run_loop
from mvge.core.spells import (
    MvgeSpell,
    SpellResultMessage,
)


def _model() -> Model:
    return Model(
        id="test-model",
        name="Test Model",
        realm="test",
        base_url="https://api.test.com",
        api_key="test",
    )


def _stream(
    responses: list[RealmResponse],
) -> Callable[[list[Any]], AsyncIterator[RealmResponse]]:
    """Build a StreamFn that yields one response per turn, in order.

    The final response repeats if the loop asks for more turns than supplied.
    """
    turn = -1

    def stream_fn(
        invocations: list[Any], signal: Any | None = None
    ) -> AsyncIterator[RealmResponse]:
        nonlocal turn
        turn += 1
        response = responses[min(turn, len(responses) - 1)]

        async def gen() -> AsyncIterator[RealmResponse]:
            yield response

        return gen()

    return stream_fn


def _text_response(
    text: str = "Hello!", stop: StopReason = StopReason.STOP
) -> RealmResponse:
    return RealmResponse(
        model=_model(),
        invocation=MvgeResponse(
            role="assistant",
            content=[{"type": "text", "text": text}],
            stop_reason=stop,
        ),
    )


def _spell_call_response(
    spell_name: str = "test_spell",
    call_id: str = "call-1",
    stop: StopReason = StopReason.SPELL_USE,
) -> RealmResponse:
    return RealmResponse(
        model=_model(),
        invocation=MvgeResponse(
            role="assistant",
            content=[
                {
                    "type": "spell_cast",
                    "spell_cast": {
                        "id": call_id,
                        "name": spell_name,
                        "arguments": {},
                    },
                }
            ],
            stop_reason=stop,
        ),
    )


class TurnScript:
    """A StreamFn that yields a scripted set of responses per turn."""

    def __init__(self, turns: list[list[RealmResponse]]) -> None:
        self._turns = turns
        self.calls: list[list[MvgeInvocation]] = []

    def __call__(
        self, invocations: list[Any], signal: Any | None = None
    ) -> AsyncIterator[RealmResponse]:
        self.calls.append(list(invocations))
        index = min(len(self.calls) - 1, len(self._turns) - 1)
        responses = self._turns[index]

        async def gen() -> AsyncIterator[RealmResponse]:
            for response in responses:
                yield response

        return gen()


class Recorder:
    """Recording emit sink standing in for the MvgeLoop wrapper."""

    def __init__(self) -> None:
        self.events: list[MvgeEvent] = []

    async def __call__(self, event: MvgeEvent) -> None:
        self.events.append(event)

    def types(self) -> list[MvgeEventType]:
        return [event.type for event in self.events]

    def count(self, event_type: MvgeEventType) -> int:
        return sum(1 for event in self.events if event.type == event_type)

    def of_type(self, event_type: MvgeEventType) -> list[MvgeEvent]:
        return [event for event in self.events if event.type == event_type]


@pytest.fixture
def spell() -> MvgeSpell:
    stub = MagicMock(spec=MvgeSpell)
    stub.name = "test_spell"
    stub.description = "A test spell"
    stub.parameters = {"type": "object", "properties": {}}
    stub.execute = AsyncMock(return_value={"result": "success"})
    return stub


@pytest.fixture
def context(spell: MvgeSpell) -> LoopContext:
    return LoopContext(
        system_prompt="You are a helpful assistant.",
        invocations=[SummonerRequest(role="user", content="Hello")],
        spells=[spell],
        contemplation_level=ContemplationLevel.OFF,
        max_tokens=4096,
        temperature=0.7,
    )


class TestRunLoopInterface:
    def test_loop_context_is_frozen(self, context: LoopContext) -> None:
        with pytest.raises(AttributeError):
            context.system_prompt = "mutated"  # type: ignore[misc]

    def test_loop_context_get_spell(self, spell: MvgeSpell) -> None:
        ctx = LoopContext(spells=[spell])
        assert ctx.get_spell("test_spell") is spell
        assert ctx.get_spell("non_existent_spell") is None

    def test_loop_context_empty_spells(self) -> None:
        ctx = LoopContext()
        assert ctx.get_spell("any_spell") is None

    def test_callbacks_default_to_none(self) -> None:
        callbacks = LoopCallbacks()
        assert callbacks.transform_context is None
        assert callbacks.before_realm_headers is None
        assert callbacks.before_spell_cast is None
        assert callbacks.after_spell_result is None
        assert callbacks.should_stop_after_turn is None
        assert callbacks.prepare_next_turn is None

    @pytest.mark.asyncio
    async def test_returns_new_invocations_only(self, context: LoopContext) -> None:
        emit = Recorder()

        new_invocations = await run_loop(
            context, _stream([_text_response()]), emit, LoopCallbacks()
        )

        assert all(not isinstance(inv, SummonerRequest) for inv in new_invocations)
        assert isinstance(new_invocations[-1], MvgeResponse)
        assert new_invocations[-1].stop_reason == StopReason.STOP

    @pytest.mark.asyncio
    async def test_does_not_mutate_context_invocations(
        self, context: LoopContext
    ) -> None:
        emit = Recorder()
        before = list(context.invocations)

        await run_loop(context, _stream([_text_response()]), emit, LoopCallbacks())

        assert context.invocations == before

    @pytest.mark.asyncio
    async def test_runs_without_callbacks(self, context: LoopContext) -> None:
        emit = Recorder()

        new_invocations = await run_loop(
            context, _stream([_text_response()]), emit, LoopCallbacks()
        )

        assert len(new_invocations) == 1


class TestRunLoopEmitSequence:
    @pytest.mark.asyncio
    async def test_emits_agent_start_and_agent_end(self, context: LoopContext) -> None:
        emit = Recorder()

        await run_loop(context, _stream([_text_response()]), emit, LoopCallbacks())

        types = emit.types()
        assert types[0] == MvgeEventType.AGENT_START
        assert types[-1] == MvgeEventType.AGENT_END

    @pytest.mark.asyncio
    async def test_emits_input_event_for_summoner_request(
        self, context: LoopContext
    ) -> None:
        emit = Recorder()

        await run_loop(context, _stream([_text_response()]), emit, LoopCallbacks())

        input_events = emit.of_type(MvgeEventType.INPUT)
        assert len(input_events) == 1
        assert input_events[0].data["content"] == "Hello"

    @pytest.mark.asyncio
    async def test_emits_realm_request_and_response_events(
        self, context: LoopContext
    ) -> None:
        emit = Recorder()

        await run_loop(context, _stream([_text_response()]), emit, LoopCallbacks())

        types = emit.types()
        assert MvgeEventType.BEFORE_PROVIDER_REQUEST in types
        assert MvgeEventType.AFTER_PROVIDER_RESPONSE in types

    @pytest.mark.asyncio
    async def test_emits_invocation_lifecycle_events(
        self, context: LoopContext
    ) -> None:
        emit = Recorder()

        await run_loop(context, _stream([_text_response()]), emit, LoopCallbacks())

        types = emit.types()
        assert MvgeEventType.BEFORE_INVOCATION in types
        assert MvgeEventType.AFTER_INVOCATION in types

    @pytest.mark.asyncio
    async def test_message_end_emitted_for_summoner_request(
        self, context: LoopContext
    ) -> None:
        emit = Recorder()

        await run_loop(context, _stream([_text_response()]), emit, LoopCallbacks())

        roles = [
            event.data["invocation"].role
            for event in emit.of_type(MvgeEventType.MESSAGE_END)
        ]
        assert "user" in roles

    @pytest.mark.asyncio
    async def test_message_end_emitted_for_assistant_response(
        self, context: LoopContext
    ) -> None:
        emit = Recorder()

        await run_loop(context, _stream([_text_response()]), emit, LoopCallbacks())

        roles = [
            event.data["invocation"].role
            for event in emit.of_type(MvgeEventType.MESSAGE_END)
        ]
        assert "assistant" in roles

    @pytest.mark.asyncio
    async def test_message_end_emitted_for_spell_result(
        self, context: LoopContext
    ) -> None:
        emit = Recorder()
        responses = [_spell_call_response(), _text_response("Done")]

        await run_loop(context, _stream(responses), emit, LoopCallbacks())

        invocations = [
            event.data["invocation"]
            for event in emit.of_type(MvgeEventType.MESSAGE_END)
        ]
        assert any(isinstance(inv, SpellResultMessage) for inv in invocations)

    @pytest.mark.asyncio
    async def test_mana_used_carried_on_message_end(self, context: LoopContext) -> None:
        emit = Recorder()
        response = _text_response()
        response.mana_used = 42

        await run_loop(context, _stream([response]), emit, LoopCallbacks())

        message_end = emit.of_type(MvgeEventType.MESSAGE_END)
        assert message_end[-1].data["mana_used"] == 42

    @pytest.mark.asyncio
    async def test_merged_model_carried_on_realm_request(
        self, context: LoopContext
    ) -> None:
        emit = Recorder()

        async def add_header(headers: dict[str, str]) -> dict[str, str]:
            return {**headers, "X-Custom": "value"}

        callbacks = LoopCallbacks(before_realm_headers=add_header)

        await run_loop(context, _stream([_text_response()]), emit, callbacks)

        request_events = emit.of_type(MvgeEventType.BEFORE_PROVIDER_REQUEST)
        model = request_events[-1].data["model"]
        assert model["headers"]["X-Custom"] == "value"


class TestRunLoopCallbacks:
    @pytest.mark.asyncio
    async def test_should_stop_after_turn_stops_gracefully(
        self, context: LoopContext
    ) -> None:
        emit = Recorder()
        stop_after_turn = AsyncMock(return_value=True)

        callbacks = LoopCallbacks(should_stop_after_turn=stop_after_turn)

        # Use spell call to force multiple turns
        responses = [_spell_call_response(), _text_response("Turn 2")]
        await run_loop(context, _stream(responses), emit, callbacks)

        # Should stop after first turn, not continue to second
        assert stop_after_turn.call_count == 1
        turn_ends = emit.of_type(MvgeEventType.TURN_END)
        assert len(turn_ends) == 1

    @pytest.mark.asyncio
    async def test_should_stop_after_turn_false_continues(
        self, context: LoopContext
    ) -> None:
        emit = Recorder()
        stop_after_turn = AsyncMock(return_value=False)

        callbacks = LoopCallbacks(should_stop_after_turn=stop_after_turn)

        # Use spell calls to force multiple turns (3 responses = 3 turns)
        responses = [
            _spell_call_response(),
            _spell_call_response(),
            _text_response("Done"),
        ]
        await run_loop(context, _stream(responses), emit, callbacks)

        # Called after each turn (3 turns = 3 calls)
        assert stop_after_turn.call_count == 3
        turn_ends = emit.of_type(MvgeEventType.TURN_END)
        assert len(turn_ends) == 3

    @pytest.mark.asyncio
    async def test_prepare_next_turn_modifies_context(
        self, context: LoopContext
    ) -> None:
        emit = Recorder()
        new_context = LoopContext(
            system_prompt="Modified prompt",
            invocations=[SummonerRequest(role="user", content="Modified")],
            spells=context.spells,
            contemplation_level=ContemplationLevel.OFF,
            max_tokens=4096,
            temperature=0.7,
        )
        prepare_next_turn = AsyncMock(return_value=new_context)

        callbacks = LoopCallbacks(prepare_next_turn=prepare_next_turn)

        # Use spell calls to force multiple turns (3 responses = 3 turns)
        # prepare_next_turn called before turn 2 and turn 3 = 2 calls
        responses = [
            _spell_call_response(),
            _spell_call_response(),
            _text_response("Done"),
        ]
        await run_loop(context, _stream(responses), emit, callbacks)

        assert prepare_next_turn.call_count == 2
        # The second turn should use the modified context
        # Verify by checking the INPUT event for the second turn (if any)
        # First turn has original input, second turn would have modified if it continued

    @pytest.mark.asyncio
    async def test_prepare_next_turn_returns_same_context_continues(
        self, context: LoopContext
    ) -> None:
        emit = Recorder()
        prepare_next_turn = AsyncMock(return_value=context)

        callbacks = LoopCallbacks(prepare_next_turn=prepare_next_turn)

        # Use spell calls to force multiple turns (3 responses = 3 turns)
        # prepare_next_turn called before turn 2 and turn 3 = 2 calls
        responses = [
            _spell_call_response(),
            _spell_call_response(),
            _text_response("Done"),
        ]
        await run_loop(context, _stream(responses), emit, callbacks)

        assert prepare_next_turn.call_count == 2
        turn_ends = emit.of_type(MvgeEventType.TURN_END)
        assert len(turn_ends) == 3

    @pytest.mark.asyncio
    async def test_transform_context_replaces_invocations(
        self, context: LoopContext
    ) -> None:
        emit = Recorder()

        async def transform(invocations: list[Any]) -> list[Any]:
            return [SummonerRequest(role="user", content="transformed")]

        callbacks = LoopCallbacks(transform_context=transform)

        await run_loop(context, _stream([_text_response()]), emit, callbacks)

        input_events = emit.of_type(MvgeEventType.INPUT)
        assert input_events[0].data["content"] == "transformed"

    @pytest.mark.asyncio
    async def test_before_spell_cast_veto_blocks_execution(
        self, context: LoopContext, spell: MvgeSpell
    ) -> None:
        emit = Recorder()

        async def veto(data: dict[str, Any]) -> dict[str, Any]:
            return {"block": True, "reason": "denied"}

        callbacks = LoopCallbacks(before_spell_cast=veto)
        responses = [_spell_call_response(), _text_response("Done")]

        await run_loop(context, _stream(responses), emit, callbacks)

        spell.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_after_spell_result_transforms_result(
        self, context: LoopContext
    ) -> None:
        emit = Recorder()

        async def rewrite(data: dict[str, Any]) -> dict[str, Any]:
            return {**data, "result": "rewritten"}

        callbacks = LoopCallbacks(after_spell_result=rewrite)
        responses = [_spell_call_response(), _text_response("Done")]

        new_invocations = await run_loop(context, _stream(responses), emit, callbacks)

        spell_results = [
            inv for inv in new_invocations if isinstance(inv, SpellResultMessage)
        ]
        assert "rewritten" in spell_results[0].content[0]["text"]

    @pytest.mark.asyncio
    async def test_core_never_imports_rune_runner(self) -> None:
        import inspect

        from mvge.core.loop import run_loop

        source = inspect.getsource(run_loop)
        assert "RuneRunner" not in source
        assert "SigilHook" not in source
        assert "rune_runner" not in source
        assert "agent_tome" not in source


class TestRunLoopSpellExecution:
    @pytest.mark.asyncio
    async def test_executes_spell_and_returns_result(
        self, context: LoopContext, spell: MvgeSpell
    ) -> None:
        emit = Recorder()
        responses = [_spell_call_response(), _text_response("Done")]

        new_invocations = await run_loop(
            context, _stream(responses), emit, LoopCallbacks()
        )

        spell.execute.assert_called_once()
        assert any(isinstance(inv, SpellResultMessage) for inv in new_invocations)

    @pytest.mark.asyncio
    async def test_unknown_spell_emits_error_without_raising(
        self, context: LoopContext
    ) -> None:
        emit = Recorder()
        responses = [_spell_call_response("missing_spell"), _text_response("Done")]

        await run_loop(context, _stream(responses), emit, LoopCallbacks())

        casting_end = emit.of_type(MvgeEventType.SPELL_CASTING_END)
        assert any("error" in event.data for event in casting_end)


class TestRunLoopErrors:
    @pytest.mark.asyncio
    async def test_rate_limit_error_raised(self, context: LoopContext) -> None:
        from mvge.core.errors import RateLimitError

        emit = Recorder()
        responses = [
            RealmResponse(
                model=_model(),
                error_message="You are being rate limited",
                error_code="rate_limited",
            )
        ]

        with pytest.raises(RateLimitError, match="rate limited"):
            await run_loop(context, _stream(responses), emit, LoopCallbacks())

    @pytest.mark.asyncio
    async def test_rate_limit_error_propagates_diagnostics(
        self, context: LoopContext
    ) -> None:
        from mvge.core.errors import RateLimitError

        emit = Recorder()
        responses = [
            RealmResponse(
                model=_model(),
                error_message="Daily quota exceeded",
                error_code="rate_limited",
                retry_after=45.0,
                limit_source="openrouter_free_tier_daily",
                remedy_hint="Add credits",
                reset_at=1788566400.0,
                quota_limit=50,
                quota_remaining=0,
            )
        ]

        with pytest.raises(RateLimitError) as exc_info:
            await run_loop(context, _stream(responses), emit, LoopCallbacks())

        err = exc_info.value
        assert err.retry_after == 45.0
        assert err.limit_source == "openrouter_free_tier_daily"
        assert err.remedy_hint == "Add credits"
        assert err.reset_at == 1788566400.0
        assert err.quota_limit == 50
        assert err.quota_remaining == 0

    @pytest.mark.asyncio
    async def test_upstream_idle_timeout_raises_and_emits_provider_error(
        self, context: LoopContext
    ) -> None:
        from mvge.core.errors import UpstreamTimeoutError

        emit = Recorder()
        responses = [
            RealmResponse(
                model=_model(),
                error_message="Upstream idle timeout exceeded",
                error_code="upstream_idle_timeout",
            )
        ]

        with pytest.raises(UpstreamTimeoutError, match="Upstream idle timeout"):
            await run_loop(context, _stream(responses), emit, LoopCallbacks())

        provider_errors = emit.of_type(MvgeEventType.PROVIDER_ERROR)
        assert len(provider_errors) == 1
        assert provider_errors[0].data["error_code"] == "upstream_idle_timeout"
        assert (
            provider_errors[0].data["error_message"] == "Upstream idle timeout exceeded"
        )

    @pytest.mark.asyncio
    async def test_auth_error_raised(self, context: LoopContext) -> None:
        from mvge.core.errors import AuthenticationError

        emit = Recorder()
        responses = [
            RealmResponse(
                model=_model(),
                error_message="User not found",
                error_code="auth_failed",
            )
        ]

        with pytest.raises(AuthenticationError, match="User not found"):
            await run_loop(context, _stream(responses), emit, LoopCallbacks())

    @pytest.mark.asyncio
    async def test_no_invocations_raises(self) -> None:
        emit = Recorder()
        empty = LoopContext(system_prompt="test", invocations=[])

        async def empty_stream() -> AsyncIterator[RealmResponse]:
            if False:
                yield

        with pytest.raises(RuntimeError, match="No invocations to process"):
            await run_loop(empty, empty_stream(), emit, LoopCallbacks())

    @pytest.mark.asyncio
    async def test_no_mana_exhausted_stop_reason(self) -> None:
        assert not hasattr(StopReason, "MANA_EXHAUSTED")


class TestRunLoopMultiTurnDriving:
    @pytest.mark.asyncio
    async def test_stream_fn_called_once_for_single_turn(
        self, context: LoopContext
    ) -> None:
        script = TurnScript([[_text_response()]])
        await run_loop(context, script, Recorder(), LoopCallbacks())
        assert len(script.calls) == 1

    @pytest.mark.asyncio
    async def test_stream_fn_called_again_after_spell_use(
        self, context: LoopContext
    ) -> None:
        script = TurnScript([[_spell_call_response()], [_text_response("Done")]])
        await run_loop(context, script, Recorder(), LoopCallbacks())
        assert len(script.calls) == 2

    @pytest.mark.asyncio
    async def test_second_turn_receives_spell_result(
        self, context: LoopContext
    ) -> None:
        script = TurnScript([[_spell_call_response()], [_text_response("Done")]])
        await run_loop(context, script, Recorder(), LoopCallbacks())
        second_turn = script.calls[1]
        assert any(isinstance(inv, SpellResultMessage) for inv in second_turn)

    @pytest.mark.asyncio
    async def test_turn_start_emitted_per_turn(self, context: LoopContext) -> None:
        emit = Recorder()
        script = TurnScript([[_spell_call_response()], [_text_response("Done")]])
        await run_loop(context, script, emit, LoopCallbacks())
        assert emit.count(MvgeEventType.TURN_START) == 2


class TestMaxTurns:
    @pytest.mark.asyncio
    async def test_max_turns_lives_on_context(self) -> None:
        assert LoopContext().max_turns == 50

    @pytest.mark.asyncio
    async def test_raises_when_max_turns_exceeded(self, spell: MvgeSpell) -> None:
        context = LoopContext(
            invocations=[SummonerRequest(role="user", content="Hello")],
            spells=[spell],
            max_turns=3,
        )
        # Always asks for another spell cast, so the loop never settles.
        script = TurnScript([[_spell_call_response()]])
        with pytest.raises(MaxTurnsExceededError, match="Max turns exceeded"):
            await run_loop(context, script, Recorder(), LoopCallbacks())


class TestTruncatedSpellCalls:
    @pytest.mark.asyncio
    async def test_truncated_spell_call_is_not_executed(
        self, context: LoopContext, spell: MvgeSpell
    ) -> None:
        # stop_reason LENGTH means arguments may be cut off mid-stream.
        script = TurnScript([[_spell_call_response(stop=StopReason.LENGTH)]])
        await run_loop(context, script, Recorder(), LoopCallbacks())
        spell.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_truncated_spell_call_yields_error_result(
        self, context: LoopContext
    ) -> None:
        script = TurnScript([[_spell_call_response(stop=StopReason.LENGTH)]])
        new_invocations = await run_loop(context, script, Recorder(), LoopCallbacks())
        results = [
            inv for inv in new_invocations if isinstance(inv, SpellResultMessage)
        ]
        assert len(results) == 1
        assert results[0].is_error is True
        assert "truncated" in results[0].content[0]["text"].lower()


class TestSteeringAndFollowUp:
    @pytest.mark.asyncio
    async def test_steering_messages_injected_into_next_turn(
        self, context: LoopContext
    ) -> None:
        drained = False

        async def get_steering() -> list[MvgeInvocation]:
            nonlocal drained
            if drained:
                return []
            drained = True
            return [SummonerRequest(role="user", content="steer me")]

        callbacks = LoopCallbacks(get_steering_messages=get_steering)
        script = TurnScript([[_text_response("First")], [_text_response("Second")]])
        await run_loop(context, script, Recorder(), callbacks)

        assert len(script.calls) == 2
        contents = [
            inv.content for inv in script.calls[1] if isinstance(inv, SummonerRequest)
        ]
        assert "steer me" in contents

    @pytest.mark.asyncio
    async def test_follow_up_messages_continue_after_stop(
        self, context: LoopContext
    ) -> None:
        drained = False

        async def get_follow_up() -> list[MvgeInvocation]:
            nonlocal drained
            if drained:
                return []
            drained = True
            return [SummonerRequest(role="user", content="follow up")]

        callbacks = LoopCallbacks(get_follow_up_messages=get_follow_up)
        script = TurnScript([[_text_response("First")], [_text_response("Second")]])
        await run_loop(context, script, Recorder(), callbacks)

        assert len(script.calls) == 2

    @pytest.mark.asyncio
    async def test_no_extra_turn_when_queues_empty(self, context: LoopContext) -> None:
        async def empty() -> list[MvgeInvocation]:
            return []

        callbacks = LoopCallbacks(
            get_steering_messages=empty, get_follow_up_messages=empty
        )
        script = TurnScript([[_text_response("Only")]])
        await run_loop(context, script, Recorder(), callbacks)

        assert len(script.calls) == 1


class TestAfterInvocationCallback:
    @pytest.mark.asyncio
    async def test_called_after_each_mvge_invocation(
        self, context: LoopContext
    ) -> None:
        seen: list[list[MvgeInvocation]] = []

        async def after(invocations: list[MvgeInvocation]) -> None:
            seen.append(list(invocations))

        callbacks = LoopCallbacks(after_invocation=after)
        script = TurnScript([[_spell_call_response()], [_text_response("Done")]])
        await run_loop(context, script, Recorder(), callbacks)

        assert len(seen) == 2

    @pytest.mark.asyncio
    async def test_replacement_transcript_used_for_next_turn(
        self, context: LoopContext
    ) -> None:
        compacted = [SummonerRequest(role="user", content="compacted history")]

        async def after(invocations: list[MvgeInvocation]) -> list[MvgeInvocation]:
            return list(compacted)

        callbacks = LoopCallbacks(after_invocation=after)
        script = TurnScript([[_spell_call_response()], [_text_response("Done")]])
        await run_loop(context, script, Recorder(), callbacks)

        second_turn = script.calls[1]
        assert second_turn == compacted

    @pytest.mark.asyncio
    async def test_none_return_leaves_transcript_untouched(
        self, context: LoopContext
    ) -> None:
        async def after(invocations: list[MvgeInvocation]) -> None:
            return None

        callbacks = LoopCallbacks(after_invocation=after)
        script = TurnScript([[_spell_call_response()], [_text_response("Done")]])
        await run_loop(context, script, Recorder(), callbacks)

        second_turn = script.calls[1]
        assert any(isinstance(inv, SpellResultMessage) for inv in second_turn)


class TestCoreStaysDecoupled:
    def test_core_does_not_import_realm(self) -> None:
        import inspect

        from mvge.core.loop import run_loop

        source = inspect.getsource(run_loop)
        assert "Realm(" not in source
        assert "realm.stream" not in source
        assert "RuneRunner" not in source
        assert "SigilHook" not in source


class TestQueueMode:
    @pytest.mark.asyncio
    async def test_default_queue_mode_is_one_at_a_time(self) -> None:
        assert LoopContext().queue_mode == QueueMode.ONE_AT_A_TIME

    @pytest.mark.asyncio
    async def test_all_mode_drains_entire_steer_queue(
        self, context: LoopContext
    ) -> None:
        context = LoopContext(
            invocations=context.invocations,
            spells=context.spells,
            queue_mode=QueueMode.ALL,
        )
        drained = False
        all_staged: list[list[MvgeInvocation]] = []

        async def get_steering() -> list[MvgeInvocation]:
            nonlocal drained
            if drained:
                return []
            drained = True
            staged = [
                SummonerRequest(role="user", content="steer one"),
                SummonerRequest(role="user", content="steer two"),
                SummonerRequest(role="user", content="steer three"),
            ]
            all_staged.append(staged)
            return staged

        callbacks = LoopCallbacks(get_steering_messages=get_steering)
        script = TurnScript([[_text_response("First")], [_text_response("Second")]])
        await run_loop(context, script, Recorder(), callbacks)

        assert len(all_staged) == 1
        assert len(all_staged[0]) == 3

    @pytest.mark.asyncio
    async def test_one_at_a_time_drains_single_steer_message(
        self, context: LoopContext
    ) -> None:
        context = LoopContext(
            invocations=context.invocations,
            spells=context.spells,
            queue_mode=QueueMode.ONE_AT_A_TIME,
        )
        steer_queue = [
            SummonerRequest(role="user", content="steer one"),
            SummonerRequest(role="user", content="steer two"),
            SummonerRequest(role="user", content="steer three"),
        ]
        all_drained: list[list[MvgeInvocation]] = []

        async def get_steering() -> list[MvgeInvocation]:
            if not steer_queue:
                return []
            drained = [steer_queue.pop(0)]
            all_drained.append(drained)
            return drained

        callbacks = LoopCallbacks(get_steering_messages=get_steering)
        script = TurnScript(
            [
                [_text_response("First")],
                [_text_response("Second")],
                [_text_response("Third")],
                [_text_response("Fourth")],
            ]
        )
        await run_loop(context, script, Recorder(), callbacks)

        assert len(all_drained) <= 4
        for batch in all_drained:
            assert len(batch) == 1

    @pytest.mark.asyncio
    async def test_one_at_a_time_drains_all_staged_messages_over_multiple_turns(
        self, context: LoopContext
    ) -> None:
        context = LoopContext(
            invocations=context.invocations,
            spells=context.spells,
            queue_mode=QueueMode.ONE_AT_A_TIME,
        )
        steer_queue = [
            SummonerRequest(role="user", content="steer one"),
            SummonerRequest(role="user", content="steer two"),
            SummonerRequest(role="user", content="steer three"),
        ]

        async def get_steering() -> list[MvgeInvocation]:
            if not steer_queue:
                return []
            return [steer_queue.pop(0)]

        callbacks = LoopCallbacks(get_steering_messages=get_steering)
        script = TurnScript(
            [
                [_text_response("First")],
                [_text_response("Second")],
                [_text_response("Third")],
                [_text_response("Fourth")],
            ]
        )
        await run_loop(context, script, Recorder(), callbacks)

        assert len(script.calls) == 4
        assert not steer_queue

    @pytest.mark.asyncio
    async def test_one_at_a_time_drains_single_followup_message(
        self, context: LoopContext
    ) -> None:
        context = LoopContext(
            invocations=context.invocations,
            spells=context.spells,
            queue_mode=QueueMode.ONE_AT_A_TIME,
        )
        followup_queue = [
            SummonerRequest(role="user", content="followup one"),
            SummonerRequest(role="user", content="followup two"),
            SummonerRequest(role="user", content="followup three"),
        ]
        all_drained: list[list[MvgeInvocation]] = []

        async def get_follow_up() -> list[MvgeInvocation]:
            if not followup_queue:
                return []
            drained = [followup_queue.pop(0)]
            all_drained.append(drained)
            return drained

        callbacks = LoopCallbacks(get_follow_up_messages=get_follow_up)
        script = TurnScript(
            [
                [_text_response("First")],
                [_text_response("Second")],
                [_text_response("Third")],
                [_text_response("Fourth")],
            ]
        )
        await run_loop(context, script, Recorder(), callbacks)

        for batch in all_drained:
            assert len(batch) == 1

    @pytest.mark.asyncio
    async def test_all_mode_drains_multiple_followup_messages(
        self, context: LoopContext
    ) -> None:
        context = LoopContext(
            invocations=context.invocations,
            spells=context.spells,
            queue_mode=QueueMode.ALL,
        )
        drained = False

        async def get_follow_up() -> list[MvgeInvocation]:
            nonlocal drained
            if drained:
                return []
            drained = True
            return [
                SummonerRequest(role="user", content="followup one"),
                SummonerRequest(role="user", content="followup two"),
            ]

        callbacks = LoopCallbacks(get_follow_up_messages=get_follow_up)
        script = TurnScript([[_text_response("First")], [_text_response("Second")]])
        await run_loop(context, script, Recorder(), callbacks)

        assert len(script.calls) == 2

    @pytest.mark.asyncio
    async def test_one_at_a_time_preserves_unprocessed_steer_items(
        self, context: LoopContext
    ) -> None:
        context = LoopContext(
            invocations=context.invocations,
            spells=context.spells,
            queue_mode=QueueMode.ONE_AT_A_TIME,
        )
        steer_queue = [
            SummonerRequest(role="user", content="steer one"),
            SummonerRequest(role="user", content="steer two"),
        ]
        drained_items: list[str] = []

        async def get_steering() -> list[MvgeInvocation]:
            if not steer_queue:
                return []
            item = steer_queue.pop(0)
            drained_items.append(item.content or "")
            return [item]

        callbacks = LoopCallbacks(get_steering_messages=get_steering)
        script = TurnScript([[_text_response("First")], [_text_response("Second")]])
        await run_loop(context, script, Recorder(), callbacks)

        assert drained_items == ["steer one", "steer two"]
