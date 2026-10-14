"""One owner for the GUI's active turn.

``AgentService`` used to keep the active turn's routing in four separate
fields (message, state, transcript, task) that ``run_prompt``'s ``finally``
cleared unconditionally. Stop-then-send faster than cancellation delivery
let the first task's ``finally`` clobber the second turn's routing, and the
events arriving afterwards were discarded silently.

``TurnOwner`` makes stop-then-send a state transition instead of a race:
every turn gets a monotonic id, and only the turn that still owns the
active slot may release it. Events that arrive with no active turn are
dropped with a log line, never silently.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from mvgeos_core.events import MvgeEvent

from mvgeos_gui.models import ChatMessage
from mvgeos_gui.transcript import InvocationTranscript

if TYPE_CHECKING:
    from mvgeos_gui.state import AppState

logger = logging.getLogger(__name__)


def transcript_for(message: ChatMessage) -> InvocationTranscript:
    """Return the transcript assembling ``message``, binding once.

    Start/end event pairs for one message must share a transcript, so the
    binding is cached on the message itself and dies with it. The active
    turn's transcript is this same object.
    """
    cached = getattr(message, "_turn_transcript", None)
    if cached is None:
        cached = InvocationTranscript.bind(message)
        # Cached on the message so start/end event pairs share a transcript;
        # dies with the message, so nothing leaks.
        message._turn_transcript = cached  # type: ignore[attr-defined]
    return cached


@dataclass
class Turn:
    """One model turn: its UI targets and its asyncio task."""

    id: int
    message: ChatMessage
    state: AppState
    task: asyncio.Task[Any] | None = None
    stopped: bool = False
    transcript: InvocationTranscript = field(init=False)

    def __post_init__(self) -> None:
        self.transcript = transcript_for(self.message)


TurnListener = Callable[[Turn, MvgeEvent], None]


class TurnOwner:
    """Exactly one owner of "which turn is active"."""

    def __init__(self) -> None:
        self._next_id = 0
        self._active: Turn | None = None
        self._listeners: list[TurnListener] = []

    @property
    def active(self) -> Turn | None:
        """The turn currently owning the slot, if any."""
        return self._active

    def start(self, message: ChatMessage, state: AppState) -> Turn:
        """Begin a new turn, stopping any turn still in flight.

        A start while a turn is active is a state transition, not a race:
        the previous turn is stopped first so its task can never outlive
        the slot handover.
        """
        if self._active is not None:
            logger.debug(
                "Starting turn while turn %d is still active; stopping it first",
                self._active.id,
            )
            self.stop()
        self._next_id += 1
        turn = Turn(id=self._next_id, message=message, state=state)
        self._active = turn
        return turn

    def stop(self) -> Turn | None:
        """Cancel the active turn's task and release the slot.

        Returns the stopped turn, or ``None`` when no turn was active.
        """
        turn = self._active
        self._active = None
        if turn is None:
            return None
        turn.stopped = True
        if turn.task is not None and not turn.task.done():
            turn.task.cancel()
        return turn

    def finish(self, turn: Turn) -> bool:
        """Release the active slot iff ``turn`` still owns it.

        A stale turn's ``finally`` must not clobber the turn that replaced
        it: the release is a compare-and-swap on turn identity. Returns
        ``True`` when the slot was released.
        """
        if self._active is turn:
            self._active = None
            return True
        logger.debug(
            "Turn %d finished after the slot moved on; keeping the current turn",
            turn.id,
        )
        return False

    def subscribe(self, listener: TurnListener) -> TurnListener:
        """Register a listener called as ``listener(turn, event)`` for routed events."""
        self._listeners.append(listener)
        return listener

    def route(self, event: MvgeEvent) -> bool:
        """Deliver an event to the active turn's listeners.

        An event arriving with no active turn is dropped with a log line,
        never silently. Returns ``True`` when the event was delivered.
        """
        turn = self._active
        if turn is None:
            logger.warning("Dropping %s event: no active turn", event.type)
            return False
        for listener in list(self._listeners):
            listener(turn, event)
        return True
