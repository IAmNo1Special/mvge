from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

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
from mvge.core.spells import MvgeSpell, SpellExecutionMode
from mvge.runes.rune_runner import RuneRunner
from mvge.runes.types import RuneLoad, RuneManifest, SigilHook


def _make_model() -> Model:
    return Model(
        id="test-model",
        name="Test Model",
        realm="test",
        base_url="http://localhost",
        api_key="test-key",
    )


@pytest.mark.asyncio
async def test_seeker_same_turn_search_activate_and_cast(tmp_path: Path) -> None:
    runner = RuneRunner()

    # Discoverable echo spell
    class EchoSpell(MvgeSpell):
        def __init__(self) -> None:
            super().__init__(
                name="echo_spell",
                description="Echo a message",
                parameters={
                    "type": "object",
                    "properties": {"msg": {"type": "string"}},
                    "required": ["msg"],
                },
                execution_mode=SpellExecutionMode.PARALLEL,
            )

        async def execute(
            self, spell_cast_id: str, params: dict[str, Any], **kwargs: Any
        ) -> dict[str, Any]:
            return {"echo": params.get("msg")}

    echo_spell = EchoSpell()

    # Define minimal gateway search & activate
    discovered: set[str] = set()

    class GatewaySearch(MvgeSpell):
        def __init__(self) -> None:
            super().__init__(
                name="search",
                description="Search tools",
                parameters={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
                execution_mode=SpellExecutionMode.PARALLEL,
            )

        async def execute(
            self, spell_cast_id: str, params: dict[str, Any], **kwargs: Any
        ) -> dict[str, Any]:
            discovered.add("echo_spell")
            return {"results": [{"name": "echo_spell", "kind": "spell"}]}

    class GatewayActivate(MvgeSpell):
        def __init__(self, api: Any) -> None:
            super().__init__(
                name="activate",
                description="Activate tools",
                parameters={
                    "type": "object",
                    "properties": {
                        "spells": {"type": "array", "items": {"type": "string"}},
                    },
                },
                execution_mode=SpellExecutionMode.PARALLEL,
            )
            self._api = api

        async def execute(
            self, spell_cast_id: str, params: dict[str, Any], **kwargs: Any
        ) -> dict[str, Any]:
            req = params.get("spells", [])
            for s in req:
                if s not in discovered:
                    return {
                        "status": "error",
                        "error": (
                            f"Capability '{s}' (kind=spell) has not been "
                            "returned by 'search' in this Tome; call 'search' first."
                        ),
                    }
            self._api.widen_global_allowlist(req)
            return {"status": "ok", "activated": req}

    gw_api = runner.create_api(rune_name="seeker")
    gw_search = GatewaySearch()
    gw_activate = GatewayActivate(gw_api)
    gw_api.register_spell(gw_search)
    gw_api.register_spell(gw_activate)

    await runner.load_rune_loads(
        [
            RuneLoad(
                manifest=RuneManifest(
                    name="seeker",
                    version="1.0.0",
                    description="Gateway",
                    spell_gateway=True,
                )
            )
        ]
    )

    runner.set_global_spell_allowlist(["search", "activate"])
    runner.set_discoverable_builtin_spells([echo_spell])

    state = MvgeState(
        system_prompt="Test prompt",
        spells=[gw_search, gw_activate, echo_spell],
        invocations=[SummonerRequest(role="user", content="Test prompt")],
        contemplation_level=ContemplationLevel.OFF,
        rune_runner=runner,
    )
    harness = MvgeHarness(
        state=state,
        discovered_spells=[echo_spell],
    )
    # Filter state.spells through allowlist
    harness.reload_spells()
    assert [s.name for s in harness.state.spells] == ["search", "activate"]

    step = 0

    async def multi_step_stream_fn(invocations: Any, signal: Any = None) -> Any:
        nonlocal step
        step += 1
        dummy_model = _make_model()

        if step == 1:
            # Step 1: Call search
            yield RealmResponse(
                model=dummy_model,
                invocation=MvgeResponse(
                    role="assistant",
                    content=[
                        {
                            "type": "spell_cast",
                            "spell_cast": {
                                "id": "call-search",
                                "name": "search",
                                "arguments": {"query": "echo"},
                            },
                        }
                    ],
                    stop_reason=StopReason.SPELL_USE,
                ),
            )
        elif step == 2:
            # Step 2: Call activate
            yield RealmResponse(
                model=dummy_model,
                invocation=MvgeResponse(
                    role="assistant",
                    content=[
                        {
                            "type": "spell_cast",
                            "spell_cast": {
                                "id": "call-act",
                                "name": "activate",
                                "arguments": {"spells": ["echo_spell"]},
                            },
                        }
                    ],
                    stop_reason=StopReason.SPELL_USE,
                ),
            )
        elif step == 3:
            # Step 3: Now cast echo_spell in the SAME turn!
            yield RealmResponse(
                model=dummy_model,
                invocation=MvgeResponse(
                    role="assistant",
                    content=[
                        {
                            "type": "spell_cast",
                            "spell_cast": {
                                "id": "call-echo",
                                "name": "echo_spell",
                                "arguments": {"msg": "hello from same turn"},
                            },
                        }
                    ],
                    stop_reason=StopReason.SPELL_USE,
                ),
            )
        else:
            # Done
            yield RealmResponse(
                model=dummy_model,
                invocation=MvgeResponse(
                    role="assistant",
                    content=[{"type": "text", "text": "Successfully executed echo"}],
                    stop_reason=StopReason.STOP,
                ),
            )

    response = await harness.run(
        stream_fn=multi_step_stream_fn,
        model={"id": "test-model"},
    )

    assert isinstance(response, MvgeResponse)
    assert response.content == [{"type": "text", "text": "Successfully executed echo"}]
    # Verify echo_spell was unlocked and present in state.spells
    assert "echo_spell" in [s.name for s in harness.state.spells]


@pytest.mark.asyncio
async def test_idle_ttl_eviction_after_8_turns() -> None:
    runner = RuneRunner()
    current_turn = 0
    last_active: dict[str, int] = {"echo_spell": 0}

    async def on_turn_end(data: dict[str, Any]) -> None:
        nonlocal current_turn
        current_turn += 1
        idle_ttl = 8
        allowlist = runner.get_global_spell_allowlist()
        if allowlist is None:
            return
        evictable = [
            s
            for s in allowlist
            if s != "search" and current_turn - last_active.get(s, 0) >= idle_ttl
        ]
        if evictable:
            runner.narrow_global_allowlist(evictable)

    runner.register_handler(SigilHook.TURN_END, on_turn_end)
    runner.set_global_spell_allowlist(["search", "echo_spell"])

    # Advance 7 turns -> still in allowlist
    for _ in range(7):
        await runner.emit_async(SigilHook.TURN_END, {})
    assert "echo_spell" in runner.get_global_spell_allowlist()

    # 8th turn -> evicted!
    await runner.emit_async(SigilHook.TURN_END, {})
    assert "echo_spell" not in runner.get_global_spell_allowlist()
    # Gateway search spell cannot be evicted
    assert "search" in runner.get_global_spell_allowlist()
