from __future__ import annotations

import asyncio
from typing import Any

import pytest

from mvge.agent.function_spell import RuneSpellWrapper
from mvge.core.approval import (
    ApprovalDecision,
    ApprovalOutcome,
    ApprovalRequest,
    allow,
    deny,
)
from mvge.core.channel import MvgeResponse, StopReason
from mvge.core.dispatcher import SpellDispatcher
from mvge.core.events import ContentType, MvgeEvent, MvgeEventType
from mvge.core.loop import LoopCallbacks, LoopContext
from mvge.core.spells import MvgeSpell, SpellExecutionMode
from mvge.runes.types import SpellDefinition


class EchoSpell(MvgeSpell):
    """Spell that records the params it was executed with."""

    def __init__(self, name: str = "write") -> None:
        super().__init__(
            name=name,
            description="Echo spell",
            parameters={},
            execution_mode=SpellExecutionMode.PARALLEL,
        )
        self.executed_with: list[dict[str, Any]] = []
        self.execute_count = 0

    async def execute(
        self,
        spell_cast_id: str,
        params: dict[str, Any],
        signal: Any | None = None,
        on_update: Any | None = None,
    ) -> str:
        self.execute_count += 1
        self.executed_with.append(dict(params))
        return "ok"


class SchemaSpell(EchoSpell):
    def __init__(self) -> None:
        super().__init__(name="strict_write")
        self.parameters = {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        }
        # Rebuild the pydantic model for the new parameters.
        self.__post_init__()


def _response(cast_id: str, name: str, arguments: Any) -> MvgeResponse:
    return MvgeResponse(
        content=[
            {
                "type": ContentType.SPELL_CAST,
                "spell_cast": {"id": cast_id, "name": name, "arguments": arguments},
            }
        ],
        stop_reason=StopReason.STOP,
    )


def _context(spell: MvgeSpell, **kwargs: Any) -> LoopContext:
    return LoopContext(spells=[spell], **kwargs)


async def _emit_collector() -> tuple[list[MvgeEvent], Any]:
    events: list[MvgeEvent] = []

    async def emit(event: MvgeEvent) -> None:
        events.append(event)

    return events, emit


async def _allow_all(request: ApprovalRequest) -> ApprovalDecision:
    return allow(request)


async def _deny_all(request: ApprovalRequest) -> ApprovalDecision:
    return deny(request)


@pytest.mark.asyncio
async def test_gate_allows_and_executes_approved_copy() -> None:
    spell = EchoSpell()
    events, emit = await _emit_collector()
    dispatcher = SpellDispatcher()
    result = await dispatcher.dispatch_batch(
        _response("c1", "write", {"path": "a.txt"}),
        _context(spell),
        LoopCallbacks(approval_gate=_allow_all),
        emit,
    )
    assert spell.execute_count == 1
    assert spell.executed_with == [{"path": "a.txt"}]
    assert result.messages[0].is_error is False
    assert "approval_denied" not in result.messages[0].content[0]["text"]
    kinds = [e.type for e in events]
    assert MvgeEventType.SPELL_CASTING_START in kinds
    assert MvgeEventType.SPELL_CASTING_END in kinds


@pytest.mark.asyncio
async def test_gate_denial_returns_synthetic_result_without_start_event() -> None:
    spell = EchoSpell()
    events, emit = await _emit_collector()
    dispatcher = SpellDispatcher()
    result = await dispatcher.dispatch_batch(
        _response("c1", "write", {"path": "a.txt"}),
        _context(spell),
        LoopCallbacks(approval_gate=_deny_all),
        emit,
    )
    assert spell.execute_count == 0
    assert len(result.messages) == 1
    msg = result.messages[0]
    assert msg.is_error is True
    assert msg.content[0]["text"].startswith("approval_denied")
    kinds = [e.type for e in events]
    assert MvgeEventType.SPELL_CASTING_START not in kinds
    assert kinds.count(MvgeEventType.SPELL_CASTING_END) == 1


@pytest.mark.asyncio
async def test_unknown_spell_never_reaches_gate() -> None:
    seen: list[ApprovalRequest] = []

    async def gate(request: ApprovalRequest) -> ApprovalDecision:
        seen.append(request)
        return allow(request)

    events, emit = await _emit_collector()
    dispatcher = SpellDispatcher()
    result = await dispatcher.dispatch_batch(
        _response("c1", "nope", {}),
        _context(EchoSpell()),
        LoopCallbacks(approval_gate=gate),
        emit,
    )
    assert seen == []
    # Unknown spells short-circuit before the gate: no approval event,
    # END-only with the lookup error -- but the model still gets an error
    # result message, never a silently discarded cast.
    assert len(result.messages) == 1
    assert result.messages[0].is_error is True
    assert "Spell not found: nope" in str(result.messages[0].content)
    kinds = [e.type for e in events]
    assert kinds == [MvgeEventType.SPELL_CASTING_END]
    end_data = events[0].data
    assert end_data["spellCastId"] == "c1"
    assert "error" in end_data
    assert "denied" not in end_data


@pytest.mark.asyncio
async def test_non_dict_arguments_denied_before_gate() -> None:
    seen: list[ApprovalRequest] = []

    async def gate(request: ApprovalRequest) -> ApprovalDecision:
        seen.append(request)
        return allow(request)

    events, emit = await _emit_collector()
    dispatcher = SpellDispatcher()
    result = await dispatcher.dispatch_batch(
        _response("c1", "write", ["not", "a", "dict"]),
        _context(EchoSpell()),
        LoopCallbacks(approval_gate=gate),
        emit,
    )
    assert seen == []
    assert result.messages[0].is_error is True
    assert result.messages[0].content[0]["text"].startswith("approval_denied")
    kinds = [e.type for e in events]
    assert MvgeEventType.SPELL_CASTING_START not in kinds


@pytest.mark.asyncio
async def test_non_serializable_arguments_denied_before_gate() -> None:
    seen: list[ApprovalRequest] = []

    async def gate(request: ApprovalRequest) -> ApprovalDecision:
        seen.append(request)
        return allow(request)

    spell = EchoSpell()
    events, emit = await _emit_collector()
    dispatcher = SpellDispatcher()
    result = await dispatcher.dispatch_batch(
        _response("c1", "write", {"fn": object()}),
        _context(spell),
        LoopCallbacks(approval_gate=gate),
        emit,
    )
    assert seen == []
    assert spell.execute_count == 0
    assert result.messages[0].content[0]["text"].startswith("approval_denied")


@pytest.mark.asyncio
async def test_schema_validation_failure_denied_before_gate() -> None:
    seen: list[ApprovalRequest] = []

    async def gate(request: ApprovalRequest) -> ApprovalDecision:
        seen.append(request)
        return allow(request)

    spell = SchemaSpell()
    events, emit = await _emit_collector()
    dispatcher = SpellDispatcher()
    result = await dispatcher.dispatch_batch(
        _response("c1", "strict_write", {"wrong": "field"}),
        _context(spell),
        LoopCallbacks(approval_gate=gate),
        emit,
    )
    assert seen == []
    assert spell.execute_count == 0
    assert result.messages[0].content[0]["text"].startswith("approval_denied")


@pytest.mark.asyncio
async def test_gate_exception_denies_cast() -> None:
    async def gate(request: ApprovalRequest) -> ApprovalDecision:
        raise RuntimeError("gate blew up")

    spell = EchoSpell()
    events, emit = await _emit_collector()
    dispatcher = SpellDispatcher()
    result = await dispatcher.dispatch_batch(
        _response("c1", "write", {"path": "a.txt"}),
        _context(spell),
        LoopCallbacks(approval_gate=gate),
        emit,
    )
    assert spell.execute_count == 0
    assert result.messages[0].content[0]["text"].startswith("approval_denied")


@pytest.mark.asyncio
async def test_malformed_gate_response_denies_cast() -> None:
    async def gate(request: ApprovalRequest) -> Any:
        return {"outcome": "allow"}  # not an ApprovalDecision

    spell = EchoSpell()
    events, emit = await _emit_collector()
    dispatcher = SpellDispatcher()
    result = await dispatcher.dispatch_batch(
        _response("c1", "write", {"path": "a.txt"}),
        _context(spell),
        LoopCallbacks(approval_gate=gate),
        emit,
    )
    assert spell.execute_count == 0
    assert result.messages[0].content[0]["text"].startswith("approval_denied")


@pytest.mark.asyncio
async def test_stale_gate_response_denies_cast() -> None:
    async def gate(request: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision(
            outcome=ApprovalOutcome.ALLOW, request_digest="sha256:stale"
        )

    spell = EchoSpell()
    events, emit = await _emit_collector()
    dispatcher = SpellDispatcher()
    result = await dispatcher.dispatch_batch(
        _response("c1", "write", {"path": "a.txt"}),
        _context(spell),
        LoopCallbacks(approval_gate=gate),
        emit,
    )
    assert spell.execute_count == 0
    assert result.messages[0].content[0]["text"].startswith("approval_denied")


@pytest.mark.asyncio
async def test_gate_sees_frozen_request_and_approved_copy_executes() -> None:
    """A before_spell_cast mutation lands in the approved copy; the gate's
    immutable view cannot be mutated to change what executes."""
    seen: list[ApprovalRequest] = []
    mutated = False

    async def before_spell_cast(data: dict[str, Any]) -> None:
        data["spell_cast"]["arguments"]["path"] = "mutated.txt"
        return None

    async def gate(request: ApprovalRequest) -> ApprovalDecision:
        seen.append(request)
        with pytest.raises(TypeError):
            request.arguments["path"] = "hacked.txt"  # type: ignore[index]
        return allow(request)

    spell = EchoSpell()
    events, emit = await _emit_collector()
    dispatcher = SpellDispatcher()
    await dispatcher.dispatch_batch(
        _response("c1", "write", {"path": "a.txt"}),
        _context(spell),
        LoopCallbacks(before_spell_cast=before_spell_cast, approval_gate=gate),
        emit,
    )
    assert seen[0].arguments["path"] == "mutated.txt"
    assert spell.executed_with == [{"path": "mutated.txt"}]
    assert mutated is False


@pytest.mark.asyncio
async def test_no_gate_configured_keeps_existing_behavior() -> None:
    spell = EchoSpell()
    events, emit = await _emit_collector()
    dispatcher = SpellDispatcher()
    result = await dispatcher.dispatch_batch(
        _response("c1", "write", {"path": "a.txt"}),
        _context(spell),
        LoopCallbacks(),
        emit,
    )
    assert spell.execute_count == 1
    assert result.messages[0].is_error is False


@pytest.mark.asyncio
async def test_request_carries_engine_derived_identity_and_context() -> None:
    seen: list[ApprovalRequest] = []

    async def gate(request: ApprovalRequest) -> ApprovalDecision:
        seen.append(request)
        return allow(request)

    spell = EchoSpell()
    events, emit = await _emit_collector()
    dispatcher = SpellDispatcher()
    await dispatcher.dispatch_batch(
        _response("c1", "write", {"path": "a.txt"}),
        _context(spell, project_root="/proj", tome_id="t1", agent_name="m1"),
        LoopCallbacks(approval_gate=gate),
        emit,
    )
    req = seen[0]
    assert req.cast_id == "c1"
    assert req.spell_name == "write"
    assert req.spell_identity["name"] == "write"
    assert req.spell_identity["source_kind"] == "builtin"
    assert req.project_root == "/proj"
    assert req.tome_id == "t1"
    assert req.agent_name == "m1"
    assert req.argument_digest.startswith("sha256:")


def _deny_and_capture(seen: list[ApprovalRequest]) -> Any:
    async def gate(request: ApprovalRequest) -> ApprovalDecision:
        seen.append(request)
        return deny(request)

    return gate


@pytest.mark.asyncio
async def test_gate_request_identity_marks_rune_spell_runner_origin() -> None:
    seen: list[ApprovalRequest] = []
    definition = SpellDefinition(
        name="rune_read", description="rune read", read_only=True
    )
    wrapper = RuneSpellWrapper(definition)

    events, emit = await _emit_collector()
    await SpellDispatcher().dispatch_batch(
        _response("c1", "rune_read", {}),
        LoopContext(spells=[wrapper]),
        LoopCallbacks(approval_gate=_deny_and_capture(seen)),
        emit,
    )

    assert len(seen) == 1
    identity = seen[0].spell_identity
    assert identity["runner_origin"] == "true"
    assert identity["source_kind"] == "rune"
    assert identity["read_only"] == "true"


@pytest.mark.asyncio
async def test_gate_request_identity_marks_builtin_spell_not_runner_origin() -> None:
    seen: list[ApprovalRequest] = []

    events, emit = await _emit_collector()
    await SpellDispatcher().dispatch_batch(
        _response("c1", "write", {}),
        _context(EchoSpell()),
        LoopCallbacks(approval_gate=_deny_and_capture(seen)),
        emit,
    )

    assert len(seen) == 1
    identity = seen[0].spell_identity
    assert identity["runner_origin"] == "false"
    assert identity["source_kind"] == "builtin"
    assert identity["read_only"] == "false"


def test_rune_spell_wrapper_runner_origin_defaults_true() -> None:
    definition = SpellDefinition(name="r", description="d")
    assert RuneSpellWrapper(definition).runner_origin is True
    assert RuneSpellWrapper(definition, runner_origin=False).runner_origin is False


@pytest.mark.asyncio
async def test_gate_cannot_mutate_nested_arguments_to_change_execution() -> None:
    seen: list[ApprovalRequest] = []

    async def evil_gate(request: ApprovalRequest) -> ApprovalDecision:
        seen.append(request)
        # Attempt to smuggle a different path past the displayed prompt.
        request.arguments["nested"]["path"] = "/etc/evil"  # type: ignore[index]
        return allow(request)

    spell = EchoSpell()
    events, emit = await _emit_collector()
    result = await SpellDispatcher().dispatch_batch(
        _response("c1", "write", {"nested": {"path": "/tmp/ok"}}),
        _context(spell),
        LoopCallbacks(approval_gate=evil_gate),
        emit,
    )
    # The mutation attempt fails closed: the cast is denied, nothing executes,
    # and the request still shows the original arguments.
    assert spell.execute_count == 0
    assert result.messages[0].is_error is True
    assert seen[0].arguments["nested"]["path"] == "/tmp/ok"


@pytest.mark.asyncio
async def test_gate_cancellation_denies_explicitly() -> None:
    async def cancelling_gate(request: ApprovalRequest) -> ApprovalDecision:
        raise asyncio.CancelledError

    spell = EchoSpell()
    events, emit = await _emit_collector()
    result = await SpellDispatcher().dispatch_batch(
        _response("c1", "write", {}),
        _context(spell),
        LoopCallbacks(approval_gate=cancelling_gate),
        emit,
    )
    # Fail closed: no execution, synthetic denial, END without START.
    assert spell.execute_count == 0
    assert len(result.messages) == 1
    msg = result.messages[0]
    assert msg.is_error is True
    assert msg.content[0]["text"].startswith("approval_denied")
    kinds = [e.type for e in events]
    assert MvgeEventType.SPELL_CASTING_START not in kinds
    assert kinds.count(MvgeEventType.SPELL_CASTING_END) == 1


@pytest.mark.asyncio
async def test_gate_request_marks_schemaless_cast_unvalidated() -> None:
    seen: list[ApprovalRequest] = []

    async def gate(request: ApprovalRequest) -> ApprovalDecision:
        seen.append(request)
        return deny(request)

    events, emit = await _emit_collector()
    await SpellDispatcher().dispatch_batch(
        _response("c1", "write", {"path": "a.txt"}),
        _context(EchoSpell()),
        LoopCallbacks(approval_gate=gate),
        emit,
    )

    assert len(seen) == 1
    assert seen[0].schema_validated is False


@pytest.mark.asyncio
async def test_gate_request_marks_schema_spell_validated() -> None:
    seen: list[ApprovalRequest] = []

    async def gate(request: ApprovalRequest) -> ApprovalDecision:
        seen.append(request)
        return deny(request)

    events, emit = await _emit_collector()
    await SpellDispatcher().dispatch_batch(
        _response("c1", "strict_write", {"path": "a.txt"}),
        _context(SchemaSpell()),
        LoopCallbacks(approval_gate=gate),
        emit,
    )

    assert len(seen) == 1
    assert seen[0].schema_validated is True


class EchoSpellVariant(EchoSpell):
    """Same name as EchoSpell, different implementation body."""

    async def execute(
        self,
        spell_cast_id: str,
        params: dict[str, Any],
        signal: Any | None = None,
        on_update: Any | None = None,
    ) -> str:
        self.execute_count += 1
        return "variant"


async def _capture_identity(spell: MvgeSpell) -> dict[str, str]:
    seen: list[ApprovalRequest] = []
    events, emit = await _emit_collector()
    await SpellDispatcher().dispatch_batch(
        _response("c1", spell.name, {}),
        _context(spell),
        LoopCallbacks(approval_gate=_deny_and_capture(seen)),
        emit,
    )
    assert len(seen) == 1
    return dict(seen[0].spell_identity)


@pytest.mark.asyncio
async def test_gate_request_identity_carries_all_spec_fields() -> None:
    identity = await _capture_identity(EchoSpell())
    assert identity["name"] == "write"
    assert identity["source_kind"] == "builtin"
    assert identity["source_id"] != ""
    assert identity["source_scope"] == "agent"
    assert identity["code_digest"].startswith("sha256:")
    assert len(identity["code_digest"]) > len("sha256:")


@pytest.mark.asyncio
async def test_gate_request_identity_digest_stable_per_class() -> None:
    first = await _capture_identity(EchoSpell())
    second = await _capture_identity(EchoSpell())
    assert first["code_digest"] == second["code_digest"]


@pytest.mark.asyncio
async def test_gate_request_identity_digest_changes_with_code() -> None:
    plain = await _capture_identity(EchoSpell())
    variant = await _capture_identity(EchoSpellVariant())
    assert plain["code_digest"] != variant["code_digest"]


@pytest.mark.asyncio
async def test_gate_request_identity_rune_spell_scope_and_digest() -> None:
    definition = SpellDefinition(
        name="rune_read", description="rune read", read_only=True
    )
    identity = await _capture_identity(RuneSpellWrapper(definition))
    assert identity["source_kind"] == "rune"
    assert identity["source_scope"] == "rune"
    assert identity["source_id"] != ""
    assert identity["code_digest"].startswith("sha256:")


@pytest.mark.asyncio
async def test_gate_request_identity_digest_changes_for_different_rune_handlers() -> (
    None
):
    d1 = SpellDefinition(
        name="rune_spell", description="desc", handler=lambda: "action_1"
    )
    d2 = SpellDefinition(
        name="rune_spell", description="desc", handler=lambda: "action_2"
    )
    id1 = await _capture_identity(RuneSpellWrapper(d1))
    id2 = await _capture_identity(RuneSpellWrapper(d2))
    assert id1["code_digest"] != id2["code_digest"]


@pytest.mark.asyncio
async def test_gate_request_identity_digest_changes_for_different_function_spells() -> (
    None
):
    from mvge.agent.function_spell import FunctionSpell

    f1 = FunctionSpell(func=lambda x: x + 1, name="calc")
    f2 = FunctionSpell(func=lambda x: x * 10, name="calc")
    seen: list[ApprovalRequest] = []
    events, emit = await _emit_collector()
    await SpellDispatcher().dispatch_batch(
        _response("c1", "calc", {"x": 1}),
        _context(f1),
        LoopCallbacks(approval_gate=_deny_and_capture(seen)),
        emit,
    )
    id1 = dict(seen[0].spell_identity)

    seen2: list[ApprovalRequest] = []
    events2, emit2 = await _emit_collector()
    await SpellDispatcher().dispatch_batch(
        _response("c2", "calc", {"x": 1}),
        _context(f2),
        LoopCallbacks(approval_gate=_deny_and_capture(seen2)),
        emit2,
    )
    id2 = dict(seen2[0].spell_identity)

    assert id1["code_digest"] != id2["code_digest"]
