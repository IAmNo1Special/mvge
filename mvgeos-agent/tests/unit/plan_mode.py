"""Plan mode: read-only spell gating at the engine level.

When plan mode is on, only spells marked ``read_only=True`` survive
``_build_spells()``. Unmarked spells are denied. Toggling mid-run never
rewrites an already-built LoopContext; the next build picks up the filter.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from mvgeos_core.loop import LoopContext
from mvgeos_core.spells import MvgeSpell
from mvgeos_runes.types import SpellDefinition

from mvgeos_agent import Mvge
from mvgeos_agent.function_spell import (
    FunctionSpell,
    PEP723ScriptSpell,
    RuneSpellWrapper,
    coerce_spell,
)
from mvgeos_agent.harness import MvgeHarness
from mvgeos_agent.harness._state import MvgeState


def read_files(path: str) -> str:
    """Read a file."""
    return path


def write_file(path: str, content: str) -> str:
    """Write a file."""
    return path


def _plan_agent() -> Mvge:
    return Mvge(
        api_key="test-key",
        spells=[
            FunctionSpell(func=read_files, name="read", read_only=True),
            FunctionSpell(func=write_file, name="write"),
        ],
    )


def test_read_only_defaults_to_false_everywhere() -> None:
    assert MvgeSpell(name="a", description="b", parameters={}).read_only is False
    assert SpellDefinition(name="a", description="b").read_only is False
    assert FunctionSpell(func=read_files).read_only is False
    assert (
        PEP723ScriptSpell(script_path=Path("x.py"), name="x", description="y").read_only
        is False
    )
    assert (
        RuneSpellWrapper(SpellDefinition(name="a", description="b")).read_only is False
    )


def test_function_spell_accepts_read_only_flag() -> None:
    spell = FunctionSpell(func=read_files, read_only=True)
    assert spell.read_only is True


def test_pep723_spell_accepts_read_only_flag() -> None:
    spell = PEP723ScriptSpell(
        script_path=Path("x.py"), name="x", description="y", read_only=True
    )
    assert spell.read_only is True


def test_rune_spell_wrapper_inherits_read_only_from_definition() -> None:
    marked = SpellDefinition(name="read", description="r", read_only=True)
    unmarked = SpellDefinition(name="write", description="w")
    assert RuneSpellWrapper(marked).read_only is True
    assert RuneSpellWrapper(unmarked).read_only is False


def test_coerce_spell_propagates_read_only_from_callable_attribute() -> None:
    def marked() -> str:
        """Marked spell."""
        return "x"

    marked.read_only = True  # type: ignore[attr-defined]
    assert coerce_spell(marked).read_only is True
    assert coerce_spell(read_files).read_only is False


def test_plan_mode_filters_to_read_only_spells() -> None:
    agent = _plan_agent()
    assert {s.name for s in agent._build_spells()} == {"read", "write"}
    agent.set_plan_mode(True)
    assert [s.name for s in agent._build_spells()] == ["read"]
    assert agent.enabled_spells == ["read"]
    assert agent.available_spells == ["read"]


def test_plan_mode_off_restores_full_spell_set() -> None:
    agent = _plan_agent()
    agent.set_plan_mode(True)
    agent.set_plan_mode(False)
    assert {s.name for s in agent._build_spells()} == {"read", "write"}
    assert agent.available_spells == ["read", "write"]


def test_unmarked_spells_all_denied_in_plan_mode() -> None:
    agent = Mvge(
        api_key="test-key",
        spells=[FunctionSpell(func=write_file, name="write")],
    )
    agent.set_plan_mode(True)
    assert agent._build_spells() == []
    assert agent.enabled_spells == []
    assert agent.available_spells == []


def test_zero_read_only_spells_emits_explicit_notice(
    caplog: pytest.LogCaptureFixture,
) -> None:
    agent = Mvge(
        api_key="test-key",
        spells=[FunctionSpell(func=write_file, name="write")],
    )
    with caplog.at_level(logging.WARNING, logger="mvgeos_agent.mvge"):
        agent.set_plan_mode(True)
    assert any(
        "plan mode" in record.message.lower() and "read-only" in record.message.lower()
        for record in caplog.records
    )


def test_mid_run_toggle_leaves_live_loop_context_untouched() -> None:
    agent = _plan_agent()
    context = LoopContext(spells=agent._build_spells())
    agent.set_plan_mode(True)
    # The in-flight context keeps its original spell set; the dispatcher
    # index is not surgically rewritten.
    assert context.get_spell("write") is not None
    assert {s.name for s in context.spells} == {"read", "write"}
    # The next build picks up the filter.
    assert [s.name for s in agent._build_spells()] == ["read"]


def _wired_agent() -> Mvge:
    """Agent with state and harness wired the way initialize() wires them."""
    agent = _plan_agent()
    agent._state = MvgeState(system_prompt="test", spells=agent._build_spells())
    agent._harness = MvgeHarness(
        state=agent._state,
        refresh_spells=agent._build_spells,
    )
    return agent


