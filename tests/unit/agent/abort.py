from __future__ import annotations

import asyncio

import pytest

from mvge.core.abort import (
    AbortController,
    AbortError,
)


class TestAbortSignal:
    def test_signal_is_not_aborted_initially(self) -> None:
        controller = AbortController()
        signal = controller.signal
        assert signal.aborted is False

    def test_abort_sets_aborted_flag(self) -> None:
        controller = AbortController()
        signal = controller.signal
        controller.abort()
        assert signal.aborted is True

    def test_abort_is_idempotent(self) -> None:
        controller = AbortController()
        controller.abort()
        controller.abort()
        assert controller.signal.aborted is True

    def test_signal_does_not_raise_when_not_aborted(self) -> None:
        controller = AbortController()
        controller.signal.raise_if_aborted()

    def test_signal_raises_if_aborted(self) -> None:
        controller = AbortController()
        controller.abort()
        with pytest.raises(AbortError):
            controller.signal.raise_if_aborted()


class TestAbortController:
    def test_signal_property_returns_aborted_signal(self) -> None:
        controller = AbortController()
        controller.abort()
        assert controller.signal.aborted is True

    def test_signal_events_fire(self) -> None:
        controller = AbortController()
        events: list[bool] = []

        def callback() -> None:
            events.append(True)

        controller.signal.on_abort(callback)
        controller.abort()
        assert events == [True]

    def test_on_abort_callback_already_aborted(self) -> None:
        controller = AbortController()
        controller.abort()
        events: list[bool] = []

        def callback() -> None:
            events.append(True)

        controller.signal.on_abort(callback)
        assert events == [True]

    def test_on_abort_multiple_callbacks(self) -> None:
        controller = AbortController()
        received: list[int] = []

        def cb1() -> None:
            received.append(1)

        def cb2() -> None:
            received.append(2)

        controller.signal.on_abort(cb1)
        controller.signal.on_abort(cb2)
        controller.abort()
        assert sorted(received) == [1, 2]

    def test_on_abort_does_not_fire_when_not_aborted(self) -> None:
        controller = AbortController()
        events: list[bool] = []

        def callback() -> None:
            events.append(True)

        controller.signal.on_abort(callback)
        assert events == []


class TestAbortWait:
    @pytest.mark.asyncio
    async def test_wait_returns_immediately_if_aborted(self) -> None:
        controller = AbortController()
        controller.abort()
        await controller.signal.wait()

    @pytest.mark.asyncio
    async def test_wait_blocks_until_aborted(self) -> None:
        controller = AbortController()

        async def abort_after_delay() -> None:
            await asyncio.sleep(0.05)
            controller.abort()

        asyncio.create_task(abort_after_delay())
        start = asyncio.get_running_loop().time()
        await controller.signal.wait()
        elapsed = asyncio.get_running_loop().time() - start
        assert 0.03 <= elapsed <= 0.15

    @pytest.mark.asyncio
    async def test_wait_blocks_until_timeout(self) -> None:
        controller = AbortController()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(controller.signal.wait(), timeout=0.01)
