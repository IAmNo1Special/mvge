from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mvge.core.events import MvgeEvent, MvgeEventType


class EventBus:
    def __init__(self) -> None:
        self._listeners: dict[MvgeEventType, list[Callable[[MvgeEvent], None]]] = {}
        self._all_listeners: list[Callable[[MvgeEvent], None]] = []

    def on(
        self,
        event_type: MvgeEventType,
        callback: Callable[[MvgeEvent], None],
    ) -> Callable[[], None]:
        if event_type not in self._listeners:
            self._listeners[event_type] = []
        self._listeners[event_type].append(callback)

        def unsubscribe() -> None:
            listeners = self._listeners.get(event_type, [])
            if callback in listeners:
                listeners.remove(callback)

        return unsubscribe

    def subscribe(self, callback: Callable[[MvgeEvent], None]) -> Callable[[], None]:
        """Subscribe to all events emitted on this bus."""
        self._all_listeners.append(callback)

        def unsubscribe() -> None:
            if callback in self._all_listeners:
                self._all_listeners.remove(callback)

        return unsubscribe

    def emit(self, event_type: MvgeEventType, data: dict[str, Any]) -> None:
        event = MvgeEvent(type=event_type, data=data)
        for listener in self._listeners.get(event_type, []):
            listener(event)
        for listener in self._all_listeners:
            listener(event)
