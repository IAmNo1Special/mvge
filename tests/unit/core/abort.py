"""Unit tests for AbortSignal, AbortController, and AbortError."""

from __future__ import annotations

import asyncio

import pytest

from mvge.core.abort import AbortController, AbortError, AbortSignal


def test_abort_controller_initial_state() -> None:
    controller = AbortController()
    assert controller.signal.aborted is False
    controller.signal.raise_if_aborted()


def test_abort_controller_abort() -> None:
    controller = AbortController()
    called = []
    controller.signal.on_abort(lambda: called.append(True))
    assert not called

    controller.abort()
    assert controller.signal.aborted is True
    assert called == [True]

    with pytest.raises(AbortError, match="Operation aborted"):
        controller.signal.raise_if_aborted()

    # Calling on_abort on already aborted signal triggers immediately
    immediate = []
    controller.signal.on_abort(lambda: immediate.append(1))
    assert immediate == [1]

    # Redundant abort is a no-op
    controller.abort()
    assert controller.signal.aborted is True


def test_abort_signal_any_with_already_aborted() -> None:
    c1 = AbortController()
    c2 = AbortController()
    c2.abort()

    any_sig = AbortSignal.any([c1.signal, c2.signal])
    assert any_sig.aborted is True


def test_abort_signal_any_triggers_on_subsequent_abort() -> None:
    c1 = AbortController()
    c2 = AbortController()

    any_sig = AbortSignal.any([c1.signal, c2.signal])
    assert any_sig.aborted is False

    c1.abort()
    assert any_sig.aborted is True


@pytest.mark.asyncio
async def test_abort_signal_wait() -> None:
    controller = AbortController()

    # Wait after already aborted resolves immediately
    controller.abort()
    await controller.signal.wait()


@pytest.mark.asyncio
async def test_abort_signal_wait_async_trigger() -> None:
    controller = AbortController()

    async def _abort_soon() -> None:
        await asyncio.sleep(0.01)
        controller.abort()

    asyncio.create_task(_abort_soon())
    await controller.signal.wait()
    assert controller.signal.aborted is True


@pytest.mark.asyncio
async def test_abort_signal_wait_concurrent() -> None:
    controller = AbortController()

    async def _waiter() -> None:
        await controller.signal.wait()

    t1 = asyncio.create_task(_waiter())
    t2 = asyncio.create_task(_waiter())
    await asyncio.sleep(0.01)
    controller.abort()
    await asyncio.gather(t1, t2)
