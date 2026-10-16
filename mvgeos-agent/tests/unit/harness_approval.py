from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from unittest.mock import MagicMock

import pytest
from mvgeos_core.approval import ApprovalOutcome, ApprovalRequest, deny
from mvgeos_runes.rune_runner import RuneRunner

from mvgeos_agent.harness import MvgeHarness
from mvgeos_agent.rune_lifecycle import RuneLifecycle
from mvgeos_agent.harness._state import MvgeState


def _request() -> ApprovalRequest:
    return ApprovalRequest(
        cast_id="call_1",
        spell_name="write",
        spell_identity=MappingProxyType({"name": "write", "source_kind": "builtin"}),
        arguments=MappingProxyType({"path": "a.txt"}),
        argument_digest="sha256:abc",
    )


def _harness_with_runner(runner: RuneRunner | None) -> MvgeHarness:
    mock_state = MagicMock(spec=MvgeState)
    mock_state.rune_runner = runner
    return MvgeHarness(state=mock_state, tome=MagicMock())


@pytest.mark.asyncio
async def test_build_callbacks_wires_approval_gate_to_runner() -> None:
    runner = RuneRunner()
    api = runner.create_api(rune_name="approval")
    api.register_spell_gate(lambda request: deny(request))

    harness = _harness_with_runner(runner)
    callbacks = harness._build_callbacks()

    assert callbacks.approval_gate is not None
    decision = await callbacks.approval_gate(_request())
    assert decision.outcome is ApprovalOutcome.DENY


@pytest.mark.asyncio
async def test_build_callbacks_without_runner_has_no_gate() -> None:
    harness = _harness_with_runner(None)
    callbacks = harness._build_callbacks()
    assert callbacks.approval_gate is None


@pytest.mark.asyncio
async def test_lifecycle_binds_canonical_project_root() -> None:
    lifecycle = RuneLifecycle(agent_name="coder", cwd="/tmp")
    runner = await lifecycle.load()
    assert runner.context.project_root == str(Path("/tmp").resolve())
    assert runner.context.agent_name == "coder"


@pytest.mark.asyncio
async def test_rebind_runner_context_updates_project_root() -> None:
    from mvgeos_agent.mvge import Mvge

    mvge = Mvge.__new__(Mvge)
    runner = RuneRunner()
    mvge._runner = runner
    tome = MagicMock()
    tome.tome_id = "tome-1"
    mvge._agent_tome = tome
    mvge._tome_dir = Path("/tmp/tomes")
    mvge._model_id = "model-1"
    config_manager = MagicMock()
    config_manager.project_dir = Path("/tmp/proj")
    mvge._config_manager = config_manager

    mvge._rebind_runner_context()

    assert runner.context.project_root == str(Path("/tmp/proj").resolve())
    assert runner.context.session_id == "tome-1"
