from __future__ import annotations

import asyncio
import io
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from mvgeos_core.channel import StopReason
from mvgeos_core.events import MvgeEvent, MvgeEventType
from mvgeos_core.invocations import MvgeResponse

from mvgeos_cli.approval_types import (
    ApprovalDecision,
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalScope,
)
from mvgeos_cli.rpc import RpcApprovalPresenter, RpcServer


@pytest.fixture
def mock_agent() -> MagicMock:
    agent = MagicMock()
    agent.model_id = "test-model"
    agent.contemplation_level = "medium"
    agent.tome_id = "tome-xyz"
    agent.session_id = "tome-xyz"
    agent.mana_used = 150
    agent.enabled_spells = ["read", "write"]
    agent.available_spells = ["read", "write", "bash"]
    agent.queue_mode.value = "one_at_a_time"

    agent.run = AsyncMock(
        return_value=MvgeResponse(
            role="assistant",
            content=[{"type": "text", "text": "Done"}],
            stop_reason=StopReason.STOP,
        )
    )
    agent.steer = MagicMock()
    agent.follow_up = MagicMock()
    agent.abort = MagicMock()
    agent.on = MagicMock(return_value=lambda: None)
    agent.runner = MagicMock()
    return agent


@pytest.mark.asyncio
async def test_rpc_prompt_and_streaming(mock_agent: MagicMock) -> None:
    stdin = io.StringIO(
        json.dumps({"command": "prompt", "id": "cmd-1", "content": "Hello"}) + "\n"
    )
    stdout = io.StringIO()

    server = RpcServer(mock_agent, stdin=stdin, stdout=stdout)
    await server.run()

    output = stdout.getvalue().strip().split("\n")
    frames = [json.loads(line) for line in output if line]

    # Preflight response
    preflight = next(
        f for f in frames if f.get("type") == "response" and f.get("id") == "cmd-1"
    )
    assert preflight["command"] == "prompt"
    assert preflight["success"] is True
    assert preflight["data"]["disposition"] == "started"

    mock_agent.run.assert_called_once_with("Hello")


@pytest.mark.asyncio
async def test_rpc_steer_and_follow_up(mock_agent: MagicMock) -> None:
    lines = [
        json.dumps({"command": "steer", "id": "cmd-2", "content": "steer msg"}),
        json.dumps({"command": "follow_up", "id": "cmd-3", "content": "followup msg"}),
    ]
    stdin = io.StringIO("\n".join(lines) + "\n")
    stdout = io.StringIO()

    server = RpcServer(mock_agent, stdin=stdin, stdout=stdout)
    await server.run()

    mock_agent.steer.assert_called_once_with("steer msg")
    mock_agent.follow_up.assert_called_once_with("followup msg")

    output = stdout.getvalue().strip().split("\n")
    frames = [json.loads(line) for line in output if line]
    resp_steer = next(f for f in frames if f.get("id") == "cmd-2")
    resp_follow = next(f for f in frames if f.get("id") == "cmd-3")
    assert resp_steer["success"] is True
    assert resp_follow["success"] is True


@pytest.mark.asyncio
async def test_rpc_abort(mock_agent: MagicMock) -> None:
    stdin = io.StringIO(json.dumps({"command": "abort", "id": "cmd-abort"}) + "\n")
    stdout = io.StringIO()

    server = RpcServer(mock_agent, stdin=stdin, stdout=stdout)
    await server.run()

    mock_agent.abort.assert_called_once()
    output = stdout.getvalue().strip().split("\n")
    frames = [json.loads(line) for line in output if line]
    resp = next(f for f in frames if f.get("id") == "cmd-abort")
    assert resp["success"] is True


@pytest.mark.asyncio
async def test_rpc_get_state(mock_agent: MagicMock) -> None:
    stdin = io.StringIO(json.dumps({"command": "get_state", "id": "cmd-state"}) + "\n")
    stdout = io.StringIO()

    server = RpcServer(mock_agent, stdin=stdin, stdout=stdout)
    await server.run()

    output = stdout.getvalue().strip().split("\n")
    frames = [json.loads(line) for line in output if line]
    resp = next(f for f in frames if f.get("id") == "cmd-state")
    assert resp["success"] is True
    assert resp["data"]["model"] == "test-model"
    assert resp["data"]["tome_id"] == "tome-xyz"
    assert resp["data"]["mana_used"] == 150
    assert "read" in resp["data"]["enabled_spells"]


@pytest.mark.asyncio
async def test_rpc_approval_extension_ui_request_and_response() -> None:
    sent_frames: list[dict[str, object]] = []

    def mock_send(frame: dict[str, object]) -> None:
        sent_frames.append(frame)

    presenter = RpcApprovalPresenter(mock_send)
    req = ApprovalRequest(
        cast_id="cast-123",
        spell_name="bash",
        arguments={"command": "rm -rf /"},
    )

    # Launch request_approval in task
    task: asyncio.Task[ApprovalDecision] = asyncio.create_task(presenter(req))
    await asyncio.sleep(0.01)

    assert len(sent_frames) == 1
    ui_req = sent_frames[0]
    assert ui_req["type"] == "extension_ui_request"
    assert ui_req["spell_name"] == "bash"
    req_id = str(ui_req["request_id"])

    # Resolve with allow_session
    ok = presenter.resolve(req_id, "allow_session")
    assert ok is True

    decision = await task
    assert decision.outcome == ApprovalOutcome.ALLOW
    assert decision.scope == ApprovalScope.SESSION


@pytest.mark.asyncio
async def test_rpc_approval_deny_on_close() -> None:
    sent_frames: list[dict[str, object]] = []
    presenter = RpcApprovalPresenter(lambda f: sent_frames.append(f))
    req = ApprovalRequest(
        cast_id="cast-456",
        spell_name="write",
        arguments={},
    )

    task: asyncio.Task[ApprovalDecision] = asyncio.create_task(presenter(req))
    await asyncio.sleep(0.01)

    presenter.close()
    decision = await task
    assert decision.outcome == ApprovalOutcome.DENY


@pytest.mark.asyncio
async def test_rpc_o1_chunk_delta_event_forwarding(mock_agent: MagicMock) -> None:
    stdout = io.StringIO()
    server = RpcServer(mock_agent, stdin=io.StringIO(), stdout=stdout)

    # 1. MESSAGE_UPDATE with text chunk
    server._on_agent_event(
        MvgeEvent(type=MvgeEventType.MESSAGE_UPDATE, data={"text": "hello "})
    )
    # 2. MESSAGE_UPDATE with huge transcript to verify stripping
    server._on_agent_event(
        MvgeEvent(
            type=MvgeEventType.MESSAGE_UPDATE,
            data={
                "invocation": MvgeResponse(
                    role="assistant",
                    content=[{"type": "text", "text": "long transcript"}],
                ),
                "other": "meta",
            },
        )
    )

    output = stdout.getvalue().strip().split("\n")
    frames = [json.loads(line) for line in output if line]

    text_delta = next(f for f in frames if f.get("type") == "text_delta")
    assert text_delta["delta"] == "hello "

    msg_updates = [
        f
        for f in frames
        if f.get("type") == "event" and f.get("event") == "message_update"
    ]
    assert len(msg_updates) == 2
    # Ensure cumulative transcript was stripped according to O(1) bandwidth rule
    assert "invocation" not in msg_updates[1]["data"]
    assert msg_updates[1]["data"]["role"] == "assistant"
    assert msg_updates[1]["data"]["other"] == "meta"


@pytest.mark.asyncio
async def test_rpc_malformed_json_handling(mock_agent: MagicMock) -> None:
    stdin = io.StringIO("invalid json string\n")
    stdout = io.StringIO()

    server = RpcServer(mock_agent, stdin=stdin, stdout=stdout)
    await server.run()

    output = stdout.getvalue().strip().split("\n")
    frames = [json.loads(line) for line in output if line]
    err_frame = next(f for f in frames if f.get("type") == "error")
    assert "Malformed JSON" in str(err_frame.get("error"))
