from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Sequence


class AbortError(Exception):
    """Raised when an operation is cancelled via AbortSignal."""


class AbortSignal:
    """Cancellation signal, mirroring the web AbortSignal API (Pi-compatible).

    A signal is created with ``aborted`` False. Calling ``controller.abort()``
    flips the flag and fires any registered callbacks. Consumers should check
    ``aborted`` or call ``raise_if_aborted()`` at safe checkpoints.
    """

    def __init__(self, controller: AbortController) -> None:
        self._controller = controller
        self._aborted = False
        self._callbacks: list[Callable[[], None]] = []
        self._wait_future: asyncio.Future[None] | None = None

    @classmethod
    def any(cls, signals: Sequence[AbortSignal]) -> AbortSignal:
        """Create an AbortSignal that aborts when any of the input signals aborts."""
        controller = AbortController()
        if any(s.aborted for s in signals):
            controller.abort()
            return controller.signal
        for s in signals:
            s.on_abort(controller.abort)
        return controller.signal

    @property
    def aborted(self) -> bool:
        return self._aborted

    def on_abort(self, callback: Callable[[], None]) -> None:
        if self._aborted:
            callback()
        else:
            self._callbacks.append(callback)

    def raise_if_aborted(self) -> None:
        if self._aborted:
            raise AbortError("Operation aborted")

    async def wait(self) -> None:
        """Block until the signal is aborted or the await is cancelled.

        Multiple concurrent calls to wait() will all resolve when the signal is aborted.
        """
        if self._aborted:
            return
        if self._wait_future is not None and not self._wait_future.done():
            await self._wait_future
            return
        self._wait_future = asyncio.get_event_loop().create_future()

        def _set_result() -> None:
            if self._wait_future is not None and not self._wait_future.done():
                self._wait_future.set_result(None)

        self._callbacks.append(_set_result)
        try:
            await self._wait_future
        finally:
            if _set_result in self._callbacks:
                self._callbacks.remove(_set_result)


class AbortController:
    """Controller that owns an :class:`AbortSignal` and can abort it.

    Mirrors the web ``AbortController`` API and Pi's cancellation model.
    """

    def __init__(self) -> None:
        self._signal = AbortSignal(self)

    @property
    def signal(self) -> AbortSignal:
        return self._signal

    def abort(self) -> None:
        if self._signal._aborted:
            return
        self._signal._aborted = True
        for callback in list(self._signal._callbacks):
            with contextlib.suppress(Exception):
                callback()
        self._signal._callbacks.clear()
        wait_future = self._signal._wait_future
        if wait_future is not None and not wait_future.done():
            wait_future.set_result(None)


__all__ = [
    "AbortController",
    "AbortError",
    "AbortSignal",
]
