from __future__ import annotations

from typing import Any

import pytest

from mvge.core.approval import (
    ApprovalDecision,
    ApprovalRequest,
    allow,
)
from mvge.runes.rune_runner import RuneRunner
from mvge.runes.types import SigilHook, SpellDefinition


def _dummy_spell(name: str) -> SpellDefinition:
    return SpellDefinition(
        name=name,
        description=f"dummy spell {name}",
        parameters={},
        handler=lambda: "result",
    )


@pytest.mark.asyncio
async def test_disposable_scope_tracks_registrations() -> None:
    runner = RuneRunner()
    api = runner.create_api(rune_name="rune_alpha")

    disposed_flag = False

    def _cleanup() -> None:
        nonlocal disposed_flag
        disposed_flag = True

    api.on_dispose(_cleanup)
    api.register_spell(_dummy_spell("spell_a"))

    def _handler(data: Any) -> None:
        pass

    api.on(SigilHook.TURN_START, _handler)
    api.register_command("cmd_a", "desc", lambda: None)
    api.register_provider("prov_a", {"key": "val"})
    api.register_realm_factory("prefix_a", lambda: None)

    async def _gate(req: ApprovalRequest) -> ApprovalDecision:
        return allow(req)

    api.register_spell_gate(_gate)

    scope = runner.get_rune_scope("rune_alpha")
    assert scope is not None
    assert "spell_a" in scope.spell_names
    assert (SigilHook.TURN_START, _handler) in scope.sigil_handlers
    assert "cmd_a" in scope.commands
    assert "prov_a" in scope.providers
    assert "prefix_a" in scope.realm_factories
    assert scope.installed_spell_gate is True
    assert len(scope.cleanup_callbacks) == 1


@pytest.mark.asyncio
async def test_unregister_single_spell() -> None:
    runner = RuneRunner()
    api = runner.create_api(rune_name="rune_alpha")

    api.register_spell(_dummy_spell("spell_1"))
    api.register_spell(_dummy_spell("spell_2"))
    initial_version = runner.spell_version

    assert runner.get_spell("spell_1") is not None
    assert runner.get_spell("spell_2") is not None

    removed = api.unregister_spell("spell_1")
    assert removed is True
    assert runner.get_spell("spell_1") is None
    assert runner.get_spell("spell_2") is not None
    assert runner.spell_version > initial_version

    scope = runner.get_rune_scope("rune_alpha")
    assert scope is not None
    assert "spell_1" not in scope.spell_names
    assert "spell_2" in scope.spell_names


@pytest.mark.asyncio
async def test_unload_rune_unrolls_everything_and_preserves_other_runes() -> None:
    runner = RuneRunner()
    api1 = runner.create_api(rune_name="rune_one")
    api2 = runner.create_api(rune_name="rune_two")

    cleanup_1_called = False
    cleanup_2_called = False

    def _c1() -> None:
        nonlocal cleanup_1_called
        cleanup_1_called = True

    async def _c2() -> None:
        nonlocal cleanup_2_called
        cleanup_2_called = True

    api1.on_dispose(_c1)
    api2.on_dispose(_c2)

    api1.register_spell(_dummy_spell("spell_one"))
    api2.register_spell(_dummy_spell("spell_two"))

    def _sigil1(data: Any) -> None:
        pass

    def _sigil2(data: Any) -> None:
        pass

    api1.on(SigilHook.TURN_START, _sigil1)
    api2.on(SigilHook.TURN_START, _sigil2)

    api1.register_command("cmd_one", "desc 1", lambda: None)
    api2.register_command("cmd_two", "desc 2", lambda: None)

    api1.register_provider("prov_one", {"key": 1})
    api2.register_provider("prov_two", {"key": 2})

    api1.register_realm_factory("prefix_one", lambda: None)
    api2.register_realm_factory("prefix_two", lambda: None)

    version_before = runner.spell_version

    # Unload rune_one
    await runner.unload_rune("rune_one")

    assert cleanup_1_called is True
    assert cleanup_2_called is False
    assert runner.spell_version > version_before

    # rune_one resources removed
    assert runner.get_spell("spell_one") is None
    assert "cmd_one" not in [c.name for c in runner.get_commands()]
    assert "prov_one" not in runner.get_registered_providers()
    assert "prefix_one" not in runner.get_registered_realm_factories()
    assert _sigil1 not in runner._sigil_handlers.get(SigilHook.TURN_START, [])

    # rune_two resources preserved
    assert runner.get_spell("spell_two") is not None
    assert "cmd_two" in [c.name for c in runner.get_commands()]
    assert "prov_two" in runner.get_registered_providers()
    assert "prefix_two" in runner.get_registered_realm_factories()
    assert _sigil2 in runner._sigil_handlers.get(SigilHook.TURN_START, [])
