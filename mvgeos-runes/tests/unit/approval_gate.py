from __future__ import annotations

import asyncio
from types import MappingProxyType
from typing import Any

import pytest
from mvgeos_core.approval import (
    ApprovalDecision,
    ApprovalOutcome,
    ApprovalRequest,
    allow,
    deny,
)

from mvgeos_runes.rune_runner import RuneRunner
from mvgeos_runes.types import RuneContext


def _request() -> ApprovalRequest:
    return ApprovalRequest(
        cast_id="call_1",
        spell_name="write",
        spell_identity=MappingProxyType({"name": "write", "source_kind": "builtin"}),
        arguments=MappingProxyType({"path": "a.txt"}),
        argument_digest="sha256:abc",
        project_root="/proj",
        tome_id="t1",
        agent_name="m1",
    )


def _runner_with_api(name: str = "approval") -> tuple[RuneRunner, Any]:
    runner = RuneRunner()
    api = runner.create_api(rune_name=name)
    return runner, api


@pytest.mark.asyncio
async def test_no_gates_allows() -> None:
    runner = RuneRunner()
    decision = await runner.evaluate_spell_gates(_request())
    assert decision.outcome is ApprovalOutcome.ALLOW
    assert decision.request_digest == "sha256:abc"


@pytest.mark.asyncio
async def test_register_spell_gate_via_api() -> None:
    runner, api = _runner_with_api()

    async def gate(request: ApprovalRequest) -> ApprovalDecision:
        return allow(request)

    api.register_spell_gate(gate)
    decision = await runner.evaluate_spell_gates(_request())
    assert decision.outcome is ApprovalOutcome.ALLOW


@pytest.mark.asyncio
async def test_sync_gate_handler_supported() -> None:
    runner, api = _runner_with_api()
    api.register_spell_gate(lambda request: allow(request))
    decision = await runner.evaluate_spell_gates(_request())
    assert decision.outcome is ApprovalOutcome.ALLOW


@pytest.mark.asyncio
async def test_and_semantics_any_deny_denies() -> None:
    runner, api = _runner_with_api()
    api.register_spell_gate(lambda request: allow(request))

    async def denier(request: ApprovalRequest) -> ApprovalDecision:
        return deny(request)

    api.register_spell_gate(denier)
    decision = await runner.evaluate_spell_gates(_request())
    assert decision.outcome is ApprovalOutcome.DENY


@pytest.mark.asyncio
async def test_allowing_gates_combine_to_allow() -> None:
    runner, api = _runner_with_api()
    api.register_spell_gate(lambda request: allow(request))
    api.register_spell_gate(lambda request: allow(request))
    decision = await runner.evaluate_spell_gates(_request())
    assert decision.outcome is ApprovalOutcome.ALLOW


@pytest.mark.asyncio
async def test_gate_exception_denies() -> None:
    runner, api = _runner_with_api()

    async def bad_gate(request: ApprovalRequest) -> ApprovalDecision:
        raise RuntimeError("boom")

    api.register_spell_gate(bad_gate)
    decision = await runner.evaluate_spell_gates(_request())
    assert decision.outcome is ApprovalOutcome.DENY
    assert decision.reason_code == "failure"


@pytest.mark.asyncio
async def test_gate_cancellation_denies() -> None:
    runner, api = _runner_with_api()

    async def cancelled_gate(request: ApprovalRequest) -> ApprovalDecision:
        raise asyncio.CancelledError

    api.register_spell_gate(cancelled_gate)
    decision = await runner.evaluate_spell_gates(_request())
    assert decision.outcome is ApprovalOutcome.DENY


@pytest.mark.asyncio
async def test_malformed_gate_response_denies() -> None:
    runner, api = _runner_with_api()

    async def malformed(request: ApprovalRequest) -> Any:
        return {"outcome": "allow"}

    api.register_spell_gate(malformed)
    decision = await runner.evaluate_spell_gates(_request())
    assert decision.outcome is ApprovalOutcome.DENY


@pytest.mark.asyncio
async def test_stale_gate_response_denies() -> None:
    runner, api = _runner_with_api()

    async def stale(request: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision(
            outcome=ApprovalOutcome.ALLOW, request_digest="sha256:stale"
        )

    api.register_spell_gate(stale)
    decision = await runner.evaluate_spell_gates(_request())
    assert decision.outcome is ApprovalOutcome.DENY


@pytest.mark.asyncio
async def test_clear_rune_removes_its_gates() -> None:
    runner = RuneRunner()
    api = runner.create_api(rune_name="approval")
    api.register_spell_gate(lambda request: deny(request))
    runner.clear_rune("approval")
    decision = await runner.evaluate_spell_gates(_request())
    assert decision.outcome is ApprovalOutcome.ALLOW


@pytest.mark.asyncio
async def test_request_approval_without_presenter_denies() -> None:
    runner, api = _runner_with_api()
    decision = await api.request_approval(_request())
    assert decision.outcome is ApprovalOutcome.DENY


@pytest.mark.asyncio
async def test_request_approval_awaits_bound_presenter() -> None:

    runner, api = _runner_with_api()
    seen: list[ApprovalRequest] = []

    async def presenter(request: ApprovalRequest) -> ApprovalDecision:
        seen.append(request)
        return allow(request, scope="session")

    # The host keeps the runner context in sync with the request's context.
    runner.bind_context(RuneContext(session_id="t1", project_root="/proj"))
    runner.set_approval_presenter(presenter)
    decision = await api.request_approval(_request())
    assert decision.outcome is ApprovalOutcome.ALLOW
    assert decision.scope == "session"
    assert seen[0].cast_id == "call_1"


@pytest.mark.asyncio
async def test_request_approval_presenter_exception_denies() -> None:
    runner, api = _runner_with_api()

    async def bad_presenter(request: ApprovalRequest) -> ApprovalDecision:
        raise RuntimeError("ui blew up")

    runner.set_approval_presenter(bad_presenter)
    decision = await api.request_approval(_request())
    assert decision.outcome is ApprovalOutcome.DENY


@pytest.mark.asyncio
async def test_request_approval_unbound_while_waiting_denies() -> None:
    runner, api = _runner_with_api()
    release = asyncio.Event()

    async def slow_presenter(request: ApprovalRequest) -> ApprovalDecision:
        await release.wait()
        return allow(request)

    runner.set_approval_presenter(slow_presenter)
    task = asyncio.create_task(api.request_approval(_request()))
    await asyncio.sleep(0)
    runner.clear_approval_presenter()
    release.set()
    decision = await task
    assert decision.outcome is ApprovalOutcome.DENY


def test_presenter_binding_drives_has_ui() -> None:
    runner = RuneRunner()
    assert runner.context.has_ui is False

    async def presenter(request: ApprovalRequest) -> ApprovalDecision:
        return allow(request)

    runner.set_approval_presenter(presenter)
    assert runner.context.has_ui is True
    runner.clear_approval_presenter()
    assert runner.context.has_ui is False


def test_presenter_slot_not_exposed_through_rune_api() -> None:
    _, api = _runner_with_api()
    assert not hasattr(api, "set_approval_presenter")
    assert not hasattr(api, "clear_approval_presenter")
    assert not hasattr(api, "approval_presenter")


def test_rune_context_carries_project_root() -> None:

    ctx = RuneContext(project_root="/proj")
    assert ctx.project_root == "/proj"


@pytest.mark.asyncio
async def test_clear_presenter_resolves_pending_request_immediately() -> None:
    runner, api = _runner_with_api()
    release = asyncio.Event()

    async def slow_presenter(request: ApprovalRequest) -> ApprovalDecision:
        await release.wait()
        return allow(request)

    runner.set_approval_presenter(slow_presenter)
    task = asyncio.create_task(api.request_approval(_request()))
    await asyncio.sleep(0)
    # Unbind WITHOUT releasing the presenter: the pending request must
    # resolve as denied on its own, not via timeout cancellation.
    runner.clear_approval_presenter()
    await asyncio.sleep(0.1)
    assert task.done()
    decision = task.result()
    assert decision.outcome is ApprovalOutcome.DENY
    assert decision.reason_code.value == "failure"
    release.set()


@pytest.mark.asyncio
async def test_rebinding_presenter_invalidates_pending_request() -> None:
    runner, api = _runner_with_api()
    release = asyncio.Event()

    async def slow_presenter(request: ApprovalRequest) -> ApprovalDecision:
        await release.wait()
        return allow(request)

    async def new_presenter(request: ApprovalRequest) -> ApprovalDecision:
        return allow(request)

    runner.set_approval_presenter(slow_presenter)
    task = asyncio.create_task(api.request_approval(_request()))
    await asyncio.sleep(0)
    runner.set_approval_presenter(new_presenter)
    await asyncio.sleep(0.1)
    assert task.done()
    decision = task.result()
    assert decision.outcome is ApprovalOutcome.DENY
    release.set()


@pytest.mark.asyncio
async def test_request_started_after_rebind_uses_new_presenter() -> None:

    runner, api = _runner_with_api()

    async def old_presenter(request: ApprovalRequest) -> ApprovalDecision:
        return deny(request)

    async def new_presenter(request: ApprovalRequest) -> ApprovalDecision:
        return allow(request)

    runner.bind_context(RuneContext(session_id="t1", project_root="/proj"))
    runner.set_approval_presenter(old_presenter)
    runner.set_approval_presenter(new_presenter)
    decision = await api.request_approval(_request())
    assert decision.outcome is ApprovalOutcome.ALLOW


def test_bind_context_recomputes_has_ui_from_presenter_binding() -> None:

    async def presenter(request: ApprovalRequest) -> ApprovalDecision:
        return allow(request)

    runner = RuneRunner()
    runner.set_approval_presenter(presenter)
    # A rebind must not clobber the presenter-derived has_ui ...
    runner.bind_context(RuneContext(cwd="/x", has_ui=False))
    assert runner.context.has_ui is True
    assert runner.context.cwd == "/x"

    # ... nor fabricate one when no presenter is bound.
    runner.clear_approval_presenter()
    runner.bind_context(RuneContext(cwd="/y", has_ui=True))
    assert runner.context.has_ui is False


@pytest.mark.asyncio
async def test_request_approval_denies_when_tome_changed_mid_prompt() -> None:

    runner, api = _runner_with_api()
    release = asyncio.Event()

    async def slow_presenter(request: ApprovalRequest) -> ApprovalDecision:
        await release.wait()
        return allow(request)

    runner.bind_context(RuneContext(session_id="t1", project_root="/proj"))
    runner.set_approval_presenter(slow_presenter)
    task = asyncio.create_task(api.request_approval(_request()))
    await asyncio.sleep(0)
    # Tome switch while the prompt is open: the pending decision is stale.
    runner.bind_context(RuneContext(session_id="t2", project_root="/proj"))
    release.set()
    decision = await task
    assert decision.outcome is ApprovalOutcome.DENY
    assert decision.reason_code.value == "failure"


@pytest.mark.asyncio
async def test_request_approval_denies_when_project_changed_mid_prompt() -> None:

    runner, api = _runner_with_api()
    release = asyncio.Event()

    async def slow_presenter(request: ApprovalRequest) -> ApprovalDecision:
        await release.wait()
        return allow(request)

    runner.bind_context(RuneContext(session_id="t1", project_root="/proj"))
    runner.set_approval_presenter(slow_presenter)
    task = asyncio.create_task(api.request_approval(_request()))
    await asyncio.sleep(0)
    runner.bind_context(RuneContext(session_id="t1", project_root="/other"))
    release.set()
    decision = await task
    assert decision.outcome is ApprovalOutcome.DENY
    assert decision.reason_code.value == "failure"


@pytest.mark.asyncio
async def test_session_scoped_presenter_routing_and_isolation() -> None:
    runner, api = _runner_with_api()
    release_t1 = asyncio.Event()

    async def presenter_t1(request: ApprovalRequest) -> ApprovalDecision:
        await release_t1.wait()
        return allow(request)

    async def presenter_t2(request: ApprovalRequest) -> ApprovalDecision:
        return allow(request)

    runner.bind_context(RuneContext(session_id="t1", project_root="/proj"))
    runner.set_approval_presenter(presenter_t1, tome_id="t1")
    runner.set_approval_presenter(presenter_t2, tome_id="t2")

    req1 = _request()  # tome_id="t1"
    req2 = ApprovalRequest(
        cast_id="call_2",
        spell_name="write",
        spell_identity=MappingProxyType({"name": "write", "source_kind": "builtin"}),
        arguments=MappingProxyType({"path": "b.txt"}),
        argument_digest="sha256:def",
        project_root="/proj",
        tome_id="t2",
        agent_name="m1",
    )

    task1 = asyncio.create_task(api.request_approval(req1))
    await asyncio.sleep(0)

    # Rebinding presenter for t2 must NOT invalidate t1's pending request
    runner.set_approval_presenter(presenter_t2, tome_id="t2")
    await asyncio.sleep(0)
    assert not task1.done()

    # t2's request completes immediately using presenter_t2
    dec2 = await api.request_approval(req2)
    assert dec2.outcome is ApprovalOutcome.ALLOW

    # release t1
    release_t1.set()
    dec1 = await task1
    assert dec1.outcome is ApprovalOutcome.ALLOW
