from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mvge.core.event_bus import EventBus
from mvge.core.events import (
    MvgeEvent,
    MvgeEventType,
)


class TestEventBus:
    @pytest.fixture
    def bus(self) -> EventBus:
        return EventBus()

    def test_emit_calls_listener(self, bus: EventBus) -> None:
        handler = MagicMock()
        bus.on(MvgeEventType.MESSAGE_UPDATE, handler)
        bus.emit(MvgeEventType.MESSAGE_UPDATE, {"text": "hello"})
        handler.assert_called_once()
        event = handler.call_args[0][0]
        assert isinstance(event, MvgeEvent)
        assert event.type == MvgeEventType.MESSAGE_UPDATE
        assert event.data == {"text": "hello"}

    def test_emit_no_listeners(self, bus: EventBus) -> None:
        bus.emit(MvgeEventType.MESSAGE_UPDATE, {"text": "hello"})

    def test_emit_wrong_event_type(self, bus: EventBus) -> None:
        handler = MagicMock()
        bus.on(MvgeEventType.MESSAGE_UPDATE, handler)
        bus.emit(MvgeEventType.TURN_START, {})
        handler.assert_not_called()

    def test_multiple_listeners(self, bus: EventBus) -> None:
        h1 = MagicMock()
        h2 = MagicMock()
        bus.on(MvgeEventType.MESSAGE_END, h1)
        bus.on(MvgeEventType.MESSAGE_END, h2)
        bus.emit(MvgeEventType.MESSAGE_END, {"done": True})
        h1.assert_called_once()
        h2.assert_called_once()

    def test_listener_error_propagates(self, bus: EventBus) -> None:
        def bad(_event: MvgeEvent) -> None:
            raise ValueError("broken")

        bus.on(MvgeEventType.TURN_START, bad)
        with pytest.raises(ValueError, match="broken"):
            bus.emit(MvgeEventType.TURN_START, {})

    def test_unsubscribe(self, bus: EventBus) -> None:
        handler = MagicMock()
        unsubscribe = bus.on(MvgeEventType.MESSAGE_UPDATE, handler)
        unsubscribe()
        bus.emit(MvgeEventType.MESSAGE_UPDATE, {"text": "hello"})
        handler.assert_not_called()

    def test_unsubscribe_idempotent(self, bus: EventBus) -> None:
        handler = MagicMock()
        unsubscribe = bus.on(MvgeEventType.MESSAGE_UPDATE, handler)
        unsubscribe()
        unsubscribe()
        bus.emit(MvgeEventType.MESSAGE_UPDATE, {"text": "hello"})
        handler.assert_not_called()

    def test_on_returns_callable(self, bus: EventBus) -> None:
        result = bus.on(MvgeEventType.TURN_START, MagicMock())
        assert callable(result)

    def test_all_event_types(self, bus: EventBus) -> None:
        handler = MagicMock()
        for event_type in MvgeEventType:
            bus.on(event_type, handler)
            bus.emit(event_type, {"id": str(event_type)})
        assert handler.call_count == len(MvgeEventType)

    def test_subscribe_all_and_unsubscribe(self, bus: EventBus) -> None:
        handler = MagicMock()
        unsub = bus.subscribe(handler)
        bus.emit(MvgeEventType.TURN_START, {})
        handler.assert_called_once()
        unsub()
        bus.emit(MvgeEventType.TURN_START, {})
        assert handler.call_count == 1


class TestEventBusIntegration:
    @pytest.mark.asyncio
    async def test_loop_emits_to_event_bus(self) -> None:
        from mvge.agent.harness import MvgeHarness
        from mvge.agent.harness._state import MvgeState
        from mvge.core.channel import (
            Model,
            MvgeResponse,
            RealmResponse,
            StopReason,
        )
        from mvge.core.events import ContemplationLevel
        from mvge.core.invocations import SummonerRequest

        bus = EventBus()
        handler = MagicMock()
        bus.on(MvgeEventType.MESSAGE_UPDATE, handler)

        state = MvgeState(
            system_prompt="test",
            model={"id": "test-model", "name": "Test"},
            contemplation_level=ContemplationLevel.OFF,
            invocations=[SummonerRequest(role="user", content="hi")],
            event_bus=bus,
        )

        model = Model(
            id="test-model",
            name="Test",
            realm="test",
            base_url="",
            api_key="",
        )
        responses = [
            RealmResponse(
                model=model,
                invocation=MvgeResponse(
                    role="assistant",
                    content=[{"type": "text", "text": "Hel"}],
                    stop_reason=StopReason.PENDING,
                ),
            ),
            RealmResponse(
                model=model,
                invocation=MvgeResponse(
                    role="assistant",
                    content=[{"type": "text", "text": "lo"}],
                    stop_reason=StopReason.PENDING,
                ),
            ),
            RealmResponse(
                model=model,
                invocation=MvgeResponse(
                    role="assistant",
                    content=[{"type": "text", "text": "Hello"}],
                    stop_reason=StopReason.STOP,
                ),
            ),
        ]

        async def stream(invocations=None, signal=None):
            for r in responses:
                yield r

        harness = MvgeHarness(state=state)
        await harness.run(stream, {"id": "test-model"}, "none")

        assert handler.called
        assert state.events  # old path still works

    @pytest.mark.asyncio
    async def test_loop_no_event_bus_no_crash(self) -> None:
        from mvge.agent.harness import MvgeHarness
        from mvge.agent.harness._state import MvgeState
        from mvge.core.channel import (
            Model,
            MvgeResponse,
            RealmResponse,
            StopReason,
        )
        from mvge.core.events import ContemplationLevel
        from mvge.core.invocations import SummonerRequest

        state = MvgeState(
            system_prompt="test",
            model={"id": "test-model", "name": "Test"},
            contemplation_level=ContemplationLevel.OFF,
            invocations=[SummonerRequest(role="user", content="hi")],
        )

        model = Model(
            id="test-model",
            name="Test",
            realm="test",
            base_url="",
            api_key="",
        )
        responses = [
            RealmResponse(
                model=model,
                invocation=MvgeResponse(
                    role="assistant",
                    content=[{"type": "text", "text": "Hello"}],
                    stop_reason=StopReason.STOP,
                ),
            ),
        ]

        async def stream(invocations=None, signal=None):
            for r in responses:
                yield r

        harness = MvgeHarness(state=state)
        result = await harness.run(stream, {"id": "test-model"}, "none")
        assert result is not None
        assert state.events  # old path still works fine
