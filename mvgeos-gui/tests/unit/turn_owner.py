"""Unit tests for TurnOwner: one owner of the GUI's active turn."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import pytest
from mvgeos_core.events import MvgeEvent, MvgeEventType

from mvgeos_gui.models import ChatMessage
from mvgeos_gui.services.turn_owner import TurnOwner
from mvgeos_gui.state import AppState


@pytest.fixture
def app_state(tmp_path: Path) -> AppState:
    return AppState(project_path=tmp_path)


def _message_event(text: str) -> MvgeEvent:
    return MvgeEvent(type=MvgeEventType.MESSAGE_UPDATE, data={"text": text})


def test_start_stop_active(app_state: AppState) -> None:
    """start() claims the slot; stop() releases it and returns the turn."""
    owner = TurnOwner()
    assert owner.active is None

    message = ChatMessage(role="assistant")
    turn = owner.start(message, app_state)

    assert owner.active is turn
    assert turn.message is message
    assert turn.state is app_state
    assert turn.transcript is not None

    stopped = owner.stop()
    assert stopped is turn
    assert turn.stopped is True
    assert owner.active is None
    assert owner.stop() is None


def test_start_assigns_monotonic_ids(app_state: AppState) -> None:
    """Turns are distinguishable by id across stop/start cycles."""
    owner = TurnOwner()
    first = owner.start(ChatMessage(role="assistant"), app_state)
    owner.stop()
    second = owner.start(ChatMessage(role="assistant"), app_state)
    assert second.id != first.id
    assert second.id > first.id


def test_start_stops_previous_turn(app_state: AppState) -> None:
    """A start while a turn is active is a state transition, not a race."""
    owner = TurnOwner()
    first = owner.start(ChatMessage(role="assistant"), app_state)
    second = owner.start(ChatMessage(role="assistant"), app_state)

    assert first.stopped is True
    assert owner.active is second


@pytest.mark.asyncio
async def test_stop_cancels_running_task(app_state: AppState) -> None:
    """stop() cancels the turn's task so cancellation reaches the runner."""
    owner = TurnOwner()
    turn = owner.start(ChatMessage(role="assistant"), app_state)
    turn.task = asyncio.create_task(asyncio.sleep(60))

    owner.stop()

    # Give the event loop a chance to deliver the cancellation.
    await asyncio.sleep(0)
    assert turn.task.cancelled()


def test_finish_releases_only_owning_turn(app_state: AppState) -> None:
    """A stale turn's finally must not clobber the turn that replaced it."""
    owner = TurnOwner()
    first = owner.start(ChatMessage(role="assistant"), app_state)
    second = owner.start(ChatMessage(role="assistant"), app_state)

    assert owner.finish(first) is False
    assert owner.active is second

    assert owner.finish(second) is True
    assert owner.active is None


def test_route_delivers_to_active_turn(app_state: AppState) -> None:
    """Subscribed listeners receive (turn, event) for the active turn."""
    owner = TurnOwner()
    received: list[tuple[int, MvgeEvent]] = []
    owner.subscribe(lambda turn, event: received.append((turn.id, event)))

    turn = owner.start(ChatMessage(role="assistant"), app_state)
    event = _message_event("hello")

    assert owner.route(event) is True
    assert received == [(turn.id, event)]


def test_route_drops_with_log_when_no_active_turn(
    app_state: AppState, caplog: pytest.LogCaptureFixture
) -> None:
    """An event with no active turn is dropped with a log line, never silently."""
    owner = TurnOwner()
    received: list[tuple[int, MvgeEvent]] = []
    owner.subscribe(lambda turn, event: received.append((turn.id, event)))

    with caplog.at_level(logging.WARNING):
        assert owner.route(_message_event("orphan")) is False

    assert received == []
    assert any("no active turn" in record.message for record in caplog.records), (
        "expected a log line for the dropped event"
    )


def test_start_stop_start_has_no_cross_turn_contamination(
    app_state: AppState, caplog: pytest.LogCaptureFixture
) -> None:
    """The acceptance test: stop-then-send never routes one turn's events to another."""
    owner = TurnOwner()
    delivered: list[tuple[int, Any, Any]] = []
    owner.subscribe(
        lambda turn, event: delivered.append((turn.id, turn.message, turn.state))
    )

    first_message = ChatMessage(role="assistant")
    first = owner.start(first_message, app_state)
    owner.route(_message_event("first turn streaming"))
    assert [turn_id for turn_id, _, _ in delivered] == [first.id]

    # Stop, then send before any cancellation could be "delivered".
    owner.stop()
    with caplog.at_level(logging.WARNING):
        assert owner.route(_message_event("stale event")) is False
    # The stale event reached no listener and was logged, not swallowed.
    assert len(delivered) == 1
    assert any("no active turn" in r.message for r in caplog.records)

    second_message = ChatMessage(role="assistant")
    second = owner.start(second_message, app_state)
    owner.route(_message_event("second turn streaming"))

    assert [turn_id for turn_id, _, _ in delivered] == [first.id, second.id]
    # The second turn's events land on the second turn's targets.
    _, routed_message, routed_state = delivered[1]
    assert routed_message is second_message
    assert routed_state is app_state

    # The first turn finishing late (its `finally` running after the handover)
    # must not release the second turn's slot.
    assert owner.finish(first) is False
    assert owner.active is second
    assert owner.route(_message_event("still second")) is True
    assert delivered[-1][0] == second.id
