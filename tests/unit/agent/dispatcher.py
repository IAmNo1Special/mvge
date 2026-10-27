from __future__ import annotations

import asyncio
from typing import Any

import pytest

from mvge.agent.function_spell import FunctionSpell
from mvge.core.channel import (
    MvgeResponse,
    StopReason,
)
from mvge.core.dispatcher import BatchResult, SpellDispatcher
from mvge.core.events import (
    MvgeEvent,
    MvgeEventType,
)
from mvge.core.loop import LoopCallbacks, LoopContext
from mvge.core.spells import (
    MvgeSpell,
    SpellExecutionMode,
    SpellResult,
)


class SlowMockSpell(MvgeSpell):
    def __init__(self, name: str, delay_s: float, result_text: str) -> None:
        super().__init__(
            name=name,
            description=f"Mock spell {name}",
            parameters={},
            execution_mode=SpellExecutionMode.PARALLEL,
        )
        self.delay_s = delay_s
        self.result_text = result_text

    async def execute(
        self,
        spell_cast_id: str,
        params: dict[str, Any],
        signal: Any | None = None,
        on_update: Any | None = None,
    ) -> str:
        if self.delay_s > 0:
            await asyncio.sleep(self.delay_s)
        return self.result_text


class FailingSpell(MvgeSpell):
    def __init__(self, name: str) -> None:
        super().__init__(
            name=name,
            description=f"Failing spell {name}",
            parameters={},
            execution_mode=SpellExecutionMode.PARALLEL,
        )

    async def execute(
        self,
        spell_cast_id: str,
        params: dict[str, Any],
        signal: Any | None = None,
        on_update: Any | None = None,
    ) -> str:
        raise ValueError("Spell internal failure")


class TerminatingSpell(MvgeSpell):
    def __init__(self, name: str, terminate: bool) -> None:
        super().__init__(
            name=name,
            description=f"Terminating spell {name}",
            parameters={},
            execution_mode=SpellExecutionMode.PARALLEL,
        )
        self.terminate = terminate

    async def execute(
        self,
        spell_cast_id: str,
        params: dict[str, Any],
        signal: Any | None = None,
        on_update: Any | None = None,
    ) -> SpellResult:
        return SpellResult(
            spell_name=self.name,
            content=f"Result of {self.name}",
            terminate=self.terminate,
        )


class SequentialSpell(MvgeSpell):
    def __init__(self, name: str, result_text: str) -> None:
        super().__init__(
            name=name,
            description=f"Sequential spell {name}",
            parameters={},
            execution_mode=SpellExecutionMode.SEQUENTIAL,
        )
        self.result_text = result_text

    async def execute(
        self,
        spell_cast_id: str,
        params: dict[str, Any],
        signal: Any | None = None,
        on_update: Any | None = None,
    ) -> str:
        return self.result_text


@pytest.mark.asyncio
async def test_dispatch_parallel_execution() -> None:
    events: list[MvgeEvent] = []

    async def emit(event: MvgeEvent) -> None:
        events.append(event)

    spell_a = SlowMockSpell("spell_a", delay_s=0.05, result_text="output_a")
    spell_b = SlowMockSpell("spell_b", delay_s=0.05, result_text="output_b")

    dispatcher = SpellDispatcher()
    context = LoopContext(spells=[spell_a, spell_b])
    callbacks = LoopCallbacks()

    inv = MvgeResponse(
        stop_reason=StopReason.SPELL_USE,
        content=[
            {
                "type": "spell_cast",
                "spell_cast": {"id": "call_1", "name": "spell_a", "arguments": {}},
            },
            {
                "type": "spell_cast",
                "spell_cast": {"id": "call_2", "name": "spell_b", "arguments": {}},
            },
        ],
    )

    start_time = asyncio.get_running_loop().time()
    res: BatchResult = await dispatcher.dispatch_batch(
        inv=inv,
        context=context,
        callbacks=callbacks,
        emit=emit,
    )
    elapsed = asyncio.get_running_loop().time() - start_time

    # Both delay 0.05s, parallel execution finishes well under sequential time (~0.10s)
    assert elapsed < 0.2
    assert len(res.messages) == 2
    assert res.messages[0].spell_name == "spell_a"
    assert res.messages[1].spell_name == "spell_b"
    assert "output_a" in str(res.messages[0].content)
    assert "output_b" in str(res.messages[1].content)
    assert res.terminate is False


@pytest.mark.asyncio
async def test_dispatch_preserves_request_order() -> None:
    events: list[MvgeEvent] = []

    async def emit(event: MvgeEvent) -> None:
        events.append(event)

    # spell_a takes longer than spell_b
    spell_a = SlowMockSpell("spell_a", delay_s=0.06, result_text="output_a")
    spell_b = SlowMockSpell("spell_b", delay_s=0.01, result_text="output_b")

    dispatcher = SpellDispatcher()
    context = LoopContext(spells=[spell_a, spell_b])

    inv = MvgeResponse(
        stop_reason=StopReason.SPELL_USE,
        content=[
            {
                "type": "spell_cast",
                "spell_cast": {"id": "call_1", "name": "spell_a", "arguments": {}},
            },
            {
                "type": "spell_cast",
                "spell_cast": {"id": "call_2", "name": "spell_b", "arguments": {}},
            },
        ],
    )

    res = await dispatcher.dispatch_batch(
        inv=inv,
        context=context,
        callbacks=LoopCallbacks(),
        emit=emit,
    )

    # Results must preserve request order [call_1, call_2]
    # even if spell_b finishes first
    assert res.messages[0].spell_cast_id == "call_1"
    assert res.messages[0].spell_name == "spell_a"
    assert res.messages[1].spell_cast_id == "call_2"
    assert res.messages[1].spell_name == "spell_b"


@pytest.mark.asyncio
async def test_dispatch_sequential_fallback() -> None:
    events: list[MvgeEvent] = []

    async def emit(event: MvgeEvent) -> None:
        events.append(event)

    spell_parallel = SlowMockSpell("spell_p", delay_s=0.03, result_text="output_p")
    spell_sequential = SequentialSpell("spell_s", result_text="output_s")

    dispatcher = SpellDispatcher()
    context = LoopContext(spells=[spell_parallel, spell_sequential])

    inv = MvgeResponse(
        stop_reason=StopReason.SPELL_USE,
        content=[
            {
                "type": "spell_cast",
                "spell_cast": {"id": "call_1", "name": "spell_p", "arguments": {}},
            },
            {
                "type": "spell_cast",
                "spell_cast": {"id": "call_2", "name": "spell_s", "arguments": {}},
            },
        ],
    )

    res = await dispatcher.dispatch_batch(
        inv=inv,
        context=context,
        callbacks=LoopCallbacks(),
        emit=emit,
    )

    assert len(res.messages) == 2
    assert res.messages[0].spell_name == "spell_p"
    assert res.messages[1].spell_name == "spell_s"


@pytest.mark.asyncio
async def test_dispatch_handles_isolated_failure() -> None:
    events: list[MvgeEvent] = []

    async def emit(event: MvgeEvent) -> None:
        events.append(event)

    spell_good = SlowMockSpell("spell_good", delay_s=0.01, result_text="good")
    spell_bad = FailingSpell("spell_bad")

    dispatcher = SpellDispatcher()
    context = LoopContext(spells=[spell_good, spell_bad])

    inv = MvgeResponse(
        stop_reason=StopReason.SPELL_USE,
        content=[
            {
                "type": "spell_cast",
                "spell_cast": {"id": "call_1", "name": "spell_good", "arguments": {}},
            },
            {
                "type": "spell_cast",
                "spell_cast": {"id": "call_2", "name": "spell_bad", "arguments": {}},
            },
        ],
    )

    res = await dispatcher.dispatch_batch(
        inv=inv,
        context=context,
        callbacks=LoopCallbacks(),
        emit=emit,
    )

    assert len(res.messages) == 2
    assert res.messages[0].is_error is False
    assert "good" in str(res.messages[0].content)
    assert res.messages[1].is_error is True
    assert "Spell internal failure" in str(res.messages[1].content)


@pytest.mark.asyncio
async def test_dispatch_truncated_length_stop() -> None:
    events: list[MvgeEvent] = []

    async def emit(event: MvgeEvent) -> None:
        events.append(event)

    spell = SlowMockSpell("spell_a", delay_s=0.01, result_text="good")
    dispatcher = SpellDispatcher()
    context = LoopContext(spells=[spell])

    inv = MvgeResponse(
        stop_reason=StopReason.LENGTH,
        content=[
            {
                "type": "spell_cast",
                "spell_cast": {"id": "call_1", "name": "spell_a", "arguments": {}},
            }
        ],
    )

    res = await dispatcher.dispatch_batch(
        inv=inv,
        context=context,
        callbacks=LoopCallbacks(),
        emit=emit,
    )

    assert len(res.messages) == 1
    assert res.messages[0].is_error is True
    assert "Mana limit" in str(res.messages[0].content) or "truncated" in str(
        res.messages[0].content
    )


@pytest.mark.asyncio
async def test_dispatch_batch_termination() -> None:
    events: list[MvgeEvent] = []

    async def emit(event: MvgeEvent) -> None:
        events.append(event)

    spell_t1 = TerminatingSpell("spell_t1", terminate=True)
    spell_t2 = TerminatingSpell("spell_t2", terminate=True)

    dispatcher = SpellDispatcher()
    context = LoopContext(spells=[spell_t1, spell_t2])

    inv = MvgeResponse(
        stop_reason=StopReason.SPELL_USE,
        content=[
            {
                "type": "spell_cast",
                "spell_cast": {"id": "call_1", "name": "spell_t1", "arguments": {}},
            },
            {
                "type": "spell_cast",
                "spell_cast": {"id": "call_2", "name": "spell_t2", "arguments": {}},
            },
        ],
    )

    res = await dispatcher.dispatch_batch(
        inv=inv,
        context=context,
        callbacks=LoopCallbacks(),
        emit=emit,
    )

    assert len(res.messages) == 2
    assert res.terminate is True


@pytest.mark.asyncio
async def test_dispatch_single_spell() -> None:
    events: list[MvgeEvent] = []

    async def emit(event: MvgeEvent) -> None:
        events.append(event)

    spell = SlowMockSpell("spell_single", delay_s=0, result_text="single_output")
    dispatcher = SpellDispatcher()
    context = LoopContext(spells=[spell])

    inv = MvgeResponse(
        stop_reason=StopReason.SPELL_USE,
        content=[
            {
                "type": "spell_cast",
                "spell_cast": {
                    "id": "call_single",
                    "name": "spell_single",
                    "arguments": {"key": "val"},
                },
            }
        ],
    )

    res = await dispatcher.dispatch_batch(
        inv=inv,
        context=context,
        callbacks=LoopCallbacks(),
        emit=emit,
    )

    assert len(res.messages) == 1
    assert res.messages[0].spell_cast_id == "call_single"
    assert res.messages[0].spell_name == "spell_single"
    assert "single_output" in str(res.messages[0].content)
    assert res.messages[0].is_error is False


@pytest.mark.asyncio
async def test_dispatch_missing_spell() -> None:
    events: list[MvgeEvent] = []

    async def emit(event: MvgeEvent) -> None:
        events.append(event)

    spell = SlowMockSpell("spell_existing", delay_s=0, result_text="existing")
    dispatcher = SpellDispatcher()
    context = LoopContext(spells=[spell])

    inv = MvgeResponse(
        stop_reason=StopReason.SPELL_USE,
        content=[
            {
                "type": "spell_cast",
                "spell_cast": {
                    "id": "call_missing",
                    "name": "spell_non_existent",
                    "arguments": {},
                },
            }
        ],
    )

    res = await dispatcher.dispatch_batch(
        inv=inv,
        context=context,
        callbacks=LoopCallbacks(),
        emit=emit,
    )

    # Missing spell returns no SpellResultMessage and emits error event
    assert len(res.messages) == 0
    end_events = [e for e in events if e.type == MvgeEventType.SPELL_CASTING_END]
    assert len(end_events) == 1
    assert end_events[0].data["spellCastId"] == "call_missing"
    assert "error" in end_events[0].data


class DetailedSpell(MvgeSpell):
    def __init__(self, name: str, content: str, details: dict) -> None:
        super().__init__(
            name=name,
            description=f"Detailed spell {name}",
            parameters={},
            execution_mode=SpellExecutionMode.PARALLEL,
        )
        self._content = content
        self._details = details

    async def execute(
        self,
        spell_cast_id: str,
        params: dict[str, Any],
        signal: Any | None = None,
        on_update: Any | None = None,
    ) -> SpellResult:
        return SpellResult(
            spell_name=self.name,
            content=self._content,
            details=self._details,
        )


@pytest.mark.asyncio
async def test_dispatch_backstop_caps_bypassing_spell() -> None:
    async def emit(event: MvgeEvent) -> None:
        pass

    spell = SlowMockSpell("spell_big", delay_s=0, result_text="x" * 200_000)
    dispatcher = SpellDispatcher()
    context = LoopContext(spells=[spell])

    inv = MvgeResponse(
        stop_reason=StopReason.SPELL_USE,
        content=[
            {
                "type": "spell_cast",
                "spell_cast": {"id": "call_1", "name": "spell_big", "arguments": {}},
            }
        ],
    )

    res = await dispatcher.dispatch_batch(
        inv=inv,
        context=context,
        callbacks=LoopCallbacks(),
        emit=emit,
    )

    assert len(res.messages) == 1
    text = res.messages[0].content[0]["text"]
    assert len(text.encode("utf-8")) <= 100_000 + 1024
    assert "Dispatcher backstop" in text
    assert res.messages[0].details is not None
    assert res.messages[0].details["truncation"]["backstop_applied"] is True
    assert res.messages[0].details["truncation"]["version"] == 1


@pytest.mark.asyncio
async def test_dispatch_propagates_spell_details() -> None:
    async def emit(event: MvgeEvent) -> None:
        pass

    spell = DetailedSpell(
        "spell_d",
        "small output",
        {"truncation": {"version": 1, "truncated": False}},
    )
    dispatcher = SpellDispatcher()
    context = LoopContext(spells=[spell])

    inv = MvgeResponse(
        stop_reason=StopReason.SPELL_USE,
        content=[
            {
                "type": "spell_cast",
                "spell_cast": {"id": "call_1", "name": "spell_d", "arguments": {}},
            }
        ],
    )

    res = await dispatcher.dispatch_batch(
        inv=inv,
        context=context,
        callbacks=LoopCallbacks(),
        emit=emit,
    )

    assert len(res.messages) == 1
    assert res.messages[0].content[0]["text"] == "small output"
    assert res.messages[0].details is not None
    assert res.messages[0].details["truncation"]["version"] == 1
    assert "backstop" not in res.messages[0].content[0]["text"]


@pytest.mark.asyncio
async def test_dispatch_backstop_preserves_spell_totals() -> None:
    async def emit(event: MvgeEvent) -> None:
        pass

    spell = DetailedSpell(
        "spell_huge",
        "y" * 200_000,
        {
            "truncation": {
                "version": 1,
                "truncated": False,
                "strategy": None,
                "total_lines": 1,
                "shown_start": None,
                "shown_end": None,
                "total_bytes": 200_000,
                "full_output_path": None,
                "backstop_applied": False,
            }
        },
    )
    dispatcher = SpellDispatcher()
    context = LoopContext(spells=[spell])

    inv = MvgeResponse(
        stop_reason=StopReason.SPELL_USE,
        content=[
            {
                "type": "spell_cast",
                "spell_cast": {"id": "call_1", "name": "spell_huge", "arguments": {}},
            }
        ],
    )

    res = await dispatcher.dispatch_batch(
        inv=inv,
        context=context,
        callbacks=LoopCallbacks(),
        emit=emit,
    )

    details = res.messages[0].details
    assert details is not None
    assert details["truncation"]["backstop_applied"] is True
    assert details["truncation"]["total_bytes"] == 200_000


@pytest.mark.asyncio
async def test_dispatch_backstop_garbage_totals_fallback() -> None:
    async def emit(event: MvgeEvent) -> None:
        pass

    spell = DetailedSpell(
        "spell_g",
        "z" * 200_000,
        {"truncation": {"version": 1, "total_lines": "lots"}},
    )
    dispatcher = SpellDispatcher()
    context = LoopContext(spells=[spell])

    inv = MvgeResponse(
        stop_reason=StopReason.SPELL_USE,
        content=[
            {
                "type": "spell_cast",
                "spell_cast": {"id": "call_1", "name": "spell_g", "arguments": {}},
            }
        ],
    )

    res = await dispatcher.dispatch_batch(
        inv=inv,
        context=context,
        callbacks=LoopCallbacks(),
        emit=emit,
    )

    details = res.messages[0].details
    assert details is not None
    assert details["truncation"]["backstop_applied"] is True


@pytest.mark.asyncio
async def test_dispatch_chained_non_dict_ignored() -> None:
    async def emit(event: MvgeEvent) -> None:
        pass

    async def after_spell_result(_data: dict[str, Any]) -> Any:
        return ["not", "a", "dict"]

    spell = SlowMockSpell("spell_a", delay_s=0, result_text="kept")
    dispatcher = SpellDispatcher()
    context = LoopContext(spells=[spell])

    inv = MvgeResponse(
        stop_reason=StopReason.SPELL_USE,
        content=[
            {
                "type": "spell_cast",
                "spell_cast": {"id": "call_1", "name": "spell_a", "arguments": {}},
            }
        ],
    )

    res = await dispatcher.dispatch_batch(
        inv=inv,
        context=context,
        callbacks=LoopCallbacks(after_spell_result=after_spell_result),
        emit=emit,
    )

    assert len(res.messages) == 1
    assert res.messages[0].content[0]["text"] == "kept"


def _detailed_fn() -> SpellResult:
    """Return details through coercion."""
    return SpellResult(
        spell_name="detailed_fn",
        content="coerced ok",
        details={"truncation": {"version": 1, "truncated": False}},
    )


@pytest.mark.asyncio
async def test_dispatch_propagates_coerced_function_details() -> None:
    async def emit(event: MvgeEvent) -> None:
        pass

    dispatcher = SpellDispatcher()
    context = LoopContext(spells=[FunctionSpell(_detailed_fn)])

    inv = MvgeResponse(
        stop_reason=StopReason.SPELL_USE,
        content=[
            {
                "type": "spell_cast",
                "spell_cast": {"id": "call_1", "name": "_detailed_fn", "arguments": {}},
            }
        ],
    )

    res = await dispatcher.dispatch_batch(
        inv=inv,
        context=context,
        callbacks=LoopCallbacks(),
        emit=emit,
    )

    assert len(res.messages) == 1
    assert res.messages[0].content[0]["text"] == "coerced ok"
    assert res.messages[0].details is not None
    assert res.messages[0].details["truncation"]["version"] == 1


@pytest.mark.asyncio
async def test_dispatch_aborted_signals() -> None:
    from mvge.core.abort import AbortController, AbortError

    dispatcher = SpellDispatcher()
    context = LoopContext(spells=[SlowMockSpell("slow", 0.01, "done")])
    callbacks = LoopCallbacks()

    async def emit(event: MvgeEvent) -> None:
        pass

    controller = AbortController()
    controller.abort()

    casts = [{"id": "c1", "name": "slow", "arguments": {}}]

    with pytest.raises(AbortError, match="Operation aborted"):
        await dispatcher._dispatch_sequential(
            casts, context, callbacks, emit, signal=controller.signal
        )

    with pytest.raises(AbortError, match="Operation aborted"):
        await dispatcher._dispatch_parallel(
            casts, context, callbacks, emit, signal=controller.signal
        )

    class AbortingSpell(MvgeSpell):
        def __init__(self) -> None:
            super().__init__(
                name="ab",
                description="ab",
                parameters={},
                execution_mode=SpellExecutionMode.PARALLEL,
            )

        async def execute(self, *args: Any, **kwargs: Any) -> Any:
            raise AbortError("aborted inside spell")

    context_ab = LoopContext(spells=[AbortingSpell()])
    with pytest.raises(AbortError, match="aborted inside spell"):
        await dispatcher._dispatch_parallel(
            [{"id": "c2", "name": "ab", "arguments": {}}],
            context_ab,
            callbacks,
            emit,
        )
