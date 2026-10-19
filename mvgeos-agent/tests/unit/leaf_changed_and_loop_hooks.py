from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from mvgeos_core.channel import MvgeResponse, StopReason
from mvgeos_core.invocations import SummonerRequest
from mvgeos_core.loop import LoopCallbacks, LoopContext, run_loop
from mvgeos_runes.types import (
    SessionLeafChangedData,
    SigilHook,
)

from mvgeos_agent.harness._state import MvgeState
from mvgeos_agent.harness.harness import MvgeHarness


@pytest.mark.asyncio
async def test_session_leaf_changed_emitted_on_checkout_leaf() -> None:
    runner = MagicMock()
    runner.emit_async = AsyncMock()

    tome = MagicMock()
    tome.tome_id = "test-tome-123"
    tome.active_leaf_id = "leaf-old"
    tome.reconstruct_invocations.return_value = []

    state = MvgeState()
    runner.context.project_root = "/workspace/test"
    state.rune_runner = runner

    harness = MvgeHarness(state=state, tome=tome)

    await harness.checkout_leaf("leaf-new")

    tome.set_leaf.assert_called_once_with("leaf-new")
    runner.emit_async.assert_awaited_once()
    hook, payload = runner.emit_async.await_args[0]
    assert hook == SigilHook.SESSION_LEAF_CHANGED
    assert isinstance(payload, SessionLeafChangedData)
    assert payload.tome_id == "test-tome-123"
    assert payload.old_leaf_id == "leaf-old"
    assert payload.new_leaf_id == "leaf-new"
    assert payload.reason == "checkout"
    assert payload.workspace_root == "/workspace/test"


@pytest.mark.asyncio
async def test_session_leaf_changed_emitted_on_undo() -> None:
    runner = MagicMock()
    runner.emit_async = AsyncMock()

    tome = MagicMock()
    tome.tome_id = "test-tome-456"
    tome.active_leaf_id = "leaf-cur"
    tome.reconstruct_invocations.return_value = []

    parent_entry = MagicMock()
    parent_entry.id = "leaf-parent"

    tome_factory = MagicMock()
    tome_factory.get_parent_summoner_entry.return_value = parent_entry

    state = MvgeState()
    runner.context.project_root = "/workspace/test"
    state.rune_runner = runner

    harness = MvgeHarness(state=state, tome=tome)
    harness._tome_factory = tome_factory

    reverted_id = await harness.undo()
    assert reverted_id == "leaf-parent"
    tome.set_leaf.assert_called_once_with("leaf-parent")

    runner.emit_async.assert_awaited_once()
    hook, payload = runner.emit_async.await_args[0]
    assert hook == SigilHook.SESSION_LEAF_CHANGED
    assert isinstance(payload, SessionLeafChangedData)
    assert payload.tome_id == "test-tome-456"
    assert payload.old_leaf_id == "leaf-cur"
    assert payload.new_leaf_id == "leaf-parent"
    assert payload.reason == "undo"
    assert payload.workspace_root == "/workspace/test"


@pytest.mark.asyncio
async def test_session_leaf_changed_emitted_on_fork_tome() -> None:
    runner = MagicMock()
    runner.emit_async = AsyncMock()

    tome = MagicMock()
    tome.tome_id = "test-tome-old"
    tome.active_leaf_id = "leaf-old"

    new_tome = MagicMock()
    new_tome.tome_id = "test-tome-forked"
    new_tome.active_leaf_id = "leaf-forked"
    new_tome.reconstruct_invocations.return_value = []

    tome.fork = AsyncMock(return_value=new_tome)

    state = MvgeState()
    runner.context.project_root = "/workspace/test"
    state.rune_runner = runner

    harness = MvgeHarness(state=state, tome=tome)

    forked_id = await harness.fork_tome()
    assert forked_id == "test-tome-forked"

    runner.emit_async.assert_awaited_once()
    hook, payload = runner.emit_async.await_args[0]
    assert hook == SigilHook.SESSION_LEAF_CHANGED
    assert isinstance(payload, SessionLeafChangedData)
    assert payload.tome_id == "test-tome-forked"
    assert payload.old_leaf_id == "leaf-old"
    assert payload.new_leaf_id == "leaf-forked"
    assert payload.reason == "fork"
    assert payload.workspace_root == "/workspace/test"


@pytest.mark.asyncio
async def test_should_stop_after_turn_accepts_dict_with_stop_true() -> None:
    runner = MagicMock()
    runner.emit_first = AsyncMock(return_value={"stop": True, "reason": "budget"})

    state = MvgeState()
    state.rune_runner = runner
    harness = MvgeHarness(state=state)

    callbacks = harness._build_callbacks()
    assert callbacks.should_stop_after_turn is not None
    should_stop = await callbacks.should_stop_after_turn()
    assert should_stop is True


@pytest.mark.asyncio
async def test_run_loop_synthetic_response_short_circuit() -> None:
    stream_fn = MagicMock()

    synthetic_resp = MvgeResponse(
        content=[{"type": "text", "text": "Intercepted synthetic answer"}],
        stop_reason=StopReason.STOP,
    )

    before_req = AsyncMock(return_value={"synthetic_response": synthetic_resp})
    callbacks = LoopCallbacks(before_provider_request=before_req)

    context = LoopContext(
        invocations=[SummonerRequest(content="hello")],
        spells=[],
    )

    emitted_events = []

    async def emit(event: object) -> None:
        emitted_events.append(event)

    results = await run_loop(
        context=context,
        stream_fn=stream_fn,
        emit=emit,
        callbacks=callbacks,
    )

    # stream_fn must not be called; synthetic response short-circuits it
    stream_fn.assert_not_called()
    assert len(results) == 1
    assert results[0] == synthetic_resp
