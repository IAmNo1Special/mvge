from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from mvge.core.abort import AbortController, AbortError, AbortSignal
from mvge.core.channel import Model, MvgeResponse, RealmResponse, StopReason
from mvge.core.event_bus import EventBus
from mvge.core.events import MvgeEvent, MvgeEventType
from mvge.core.invocations import MvgeInvocation, SummonerRequest
from mvge.core.loop import LoopCallbacks, LoopContext, run_loop
from mvge.core.spells import MvgeSpell, SpellResult


def _test_model() -> Model:
    return Model(
        id="test-core-model",
        name="Test Core Model",
        realm="test",
        base_url="https://api.test.com",
        api_key="secret",
    )


class ScriptedStream:
    """Mock StreamFn yielding canned RealmResponse sequences per turn."""

    def __init__(self, turns: list[list[RealmResponse]]) -> None:
        self._turns = turns
        self.recorded_invocations: list[list[MvgeInvocation]] = []

    def __call__(
        self, invocations: list[MvgeInvocation], signal: AbortSignal | None = None
    ) -> AsyncIterator[RealmResponse]:
        self.recorded_invocations.append(list(invocations))
        turn_idx = min(len(self.recorded_invocations) - 1, len(self._turns) - 1)
        responses = self._turns[turn_idx]

        async def gen() -> AsyncIterator[RealmResponse]:
            for r in responses:
                if signal and signal.aborted:
                    raise AbortError("Aborted before yielding response")
                yield r

        return gen()


class EchoSpell(MvgeSpell):
    async def execute(
        self,
        spell_cast_id: str,
        params: dict[str, Any],
        signal: AbortSignal | None = None,
        on_update: Any | None = None,
    ) -> SpellResult:
        message = params.get("message", "")
        return SpellResult(spell_name=self.name, content=f"echo: {message}")


@pytest.mark.asyncio
async def test_loop_executes_spell_and_emits_events() -> None:
    """Integration test: multi-turn run_loop casting a spell via dispatcher."""
    events: list[MvgeEvent] = []

    async def emit_sink(event: MvgeEvent) -> None:
        events.append(event)

    echo_spell = EchoSpell(
        name="echo",
        description="Echoes back the message argument.",
        parameters={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
    )

    turn1_response = RealmResponse(
        model=_test_model(),
        invocation=MvgeResponse(
            role="assistant",
            content=[
                {
                    "type": "spell_cast",
                    "spell_cast": {
                        "id": "cast-1",
                        "name": "echo",
                        "arguments": {"message": "hello world"},
                    },
                }
            ],
            stop_reason=StopReason.SPELL_USE,
        ),
    )

    turn2_response = RealmResponse(
        model=_test_model(),
        invocation=MvgeResponse(
            role="assistant",
            content=[
                {
                    "type": "text",
                    "text": "Spell result received: echo: hello world",
                }
            ],
            stop_reason=StopReason.STOP,
        ),
    )

    stream_fn = ScriptedStream([[turn1_response], [turn2_response]])
    context = LoopContext(
        system_prompt="You are a helpful assistant.",
        invocations=[SummonerRequest(content="Please echo hello world")],
        spells=[echo_spell],
        max_turns=5,
    )
    callbacks = LoopCallbacks()

    result_invocations = await run_loop(context, stream_fn, emit_sink, callbacks)

    # 1. Assert result transcripts
    assert len(result_invocations) == 3
    # First invocation is turn 1 assistant spell cast
    assert result_invocations[0].role == "assistant"
    # Second invocation is spell result
    assert result_invocations[1].role == "spellResult"
    assert result_invocations[1].content[0]["text"] == "echo: hello world"
    # Third invocation is turn 2 assistant final message
    assert result_invocations[2].role == "assistant"
    assert "echo: hello world" in result_invocations[2].content[0]["text"]

    # 2. Assert events recorded by emit sink
    event_types = [e.type for e in events]
    assert MvgeEventType.TURN_START in event_types
    assert MvgeEventType.SPELL_CASTING_START in event_types
    assert MvgeEventType.SPELL_CASTING_END in event_types
    assert MvgeEventType.TURN_END in event_types


@pytest.mark.asyncio
async def test_loop_aborts_with_abort_signal() -> None:
    """Integration test: run_loop terminates promptly when AbortSignal is set."""
    controller = AbortController()
    events: list[MvgeEvent] = []

    async def emit_sink(event: MvgeEvent) -> None:
        events.append(event)

    def aborting_stream(
        invocations: list[MvgeInvocation], signal: AbortSignal | None = None
    ) -> AsyncIterator[RealmResponse]:
        controller.abort()

        async def gen() -> AsyncIterator[RealmResponse]:
            if signal and signal.aborted:
                raise AbortError("Operation aborted")
            yield RealmResponse(
                model=_test_model(),
                invocation=MvgeResponse(
                    role="assistant",
                    content=[{"type": "text", "text": "Should not complete"}],
                    stop_reason=StopReason.STOP,
                ),
            )

        return gen()

    context = LoopContext(
        invocations=[SummonerRequest(content="Hello")],
        max_turns=2,
    )

    await run_loop(
        context,
        aborting_stream,
        emit_sink,
        LoopCallbacks(),
        signal=controller.signal,
    )

    agent_ends = [e for e in events if e.type == MvgeEventType.AGENT_END]
    assert len(agent_ends) == 1
    assert agent_ends[0].data.get("stop_reason") == StopReason.ABORTED.value


@pytest.mark.asyncio
async def test_event_bus_receives_loop_sink_events() -> None:
    """Integration test: EventBus receives and routes events generated during a loop."""
    bus = EventBus()
    received: list[MvgeEvent] = []

    bus.subscribe(lambda event: received.append(event))

    async def emit_sink(event: MvgeEvent) -> None:
        bus.emit(event.type, event.data)

    response = RealmResponse(
        model=_test_model(),
        invocation=MvgeResponse(
            role="assistant",
            content=[{"type": "text", "text": "Done"}],
            stop_reason=StopReason.STOP,
        ),
    )
    stream_fn = ScriptedStream([[response]])
    context = LoopContext(
        invocations=[SummonerRequest(content="Quick test")],
        max_turns=2,
    )

    await run_loop(context, stream_fn, emit_sink, LoopCallbacks())

    assert len(received) > 0
    event_types = {e.type for e in received}
    assert MvgeEventType.TURN_START in event_types
    assert MvgeEventType.TURN_END in event_types
