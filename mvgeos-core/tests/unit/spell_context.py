"""Unit tests for SpellContext, ExecutionBackend, and sync/async callable support."""

from __future__ import annotations

import asyncio
import functools
from pathlib import Path
from typing import Any

import pytest

from mvgeos_core.abort import AbortController
from mvgeos_core.spell_schema import (
    find_context_parameter,
    generate_spell_schema,
    inject_casting_rationale_schema,
    invoke_spell_callable,
    unwrap_callable,
)
from mvgeos_core.spells import (
    ExecResult,
    ExecutionBackend,
    LocalExecutionBackend,
    SpellContext,
)


def test_spell_context_attributes_and_immutability() -> None:
    ctx = SpellContext(
        spell_cast_id="cast-123",
        workspace_root=Path("/tmp/ws"),
        agent_name="coding_mvge",
        spell_action="Reading config file",
        spell_summary="Config read",
    )
    assert ctx.spell_cast_id == "cast-123"
    assert ctx.workspace_root == Path("/tmp/ws")
    assert ctx.agent_name == "coding_mvge"
    assert ctx.spell_action == "Reading config file"
    assert ctx.spell_summary == "Config read"
    assert ctx.backend is None
    assert ctx.signal is None
    assert ctx.sub_mvge_id is None

    with pytest.raises((AttributeError, TypeError)):
        ctx.spell_cast_id = "new-id"  # type: ignore[misc]


@pytest.mark.asyncio
async def test_local_execution_backend_basic(tmp_path: Path) -> None:
    backend: ExecutionBackend = LocalExecutionBackend()
    assert isinstance(backend, ExecutionBackend)

    # Test write_bytes and read_bytes
    test_file = tmp_path / "hello.txt"
    await backend.write_bytes(test_file, b"hello from execution backend")
    content = await backend.read_bytes(test_file)
    assert content == b"hello from execution backend"

    # Test exec_command
    res = await backend.exec_command("echo hello world", cwd=tmp_path)
    assert isinstance(res, ExecResult)
    assert res.exit_code == 0
    assert "hello world" in res.stdout
    assert not res.timed_out


@pytest.mark.asyncio
async def test_local_execution_backend_timeout(tmp_path: Path) -> None:
    backend = LocalExecutionBackend()
    res = await backend.exec_command("sleep 5", cwd=tmp_path, timeout_ms=100)
    assert res.timed_out
    assert res.exit_code != 0


@pytest.mark.asyncio
async def test_local_execution_backend_abort_signal(tmp_path: Path) -> None:
    backend = LocalExecutionBackend()
    controller = AbortController()

    async def abort_soon() -> None:
        await asyncio.sleep(0.05)
        controller.abort()

    task = asyncio.create_task(abort_soon())
    res = await backend.exec_command("sleep 5", cwd=tmp_path, signal=controller.signal)
    await task
    assert res.exit_code != 0


def test_unwrap_callable() -> None:
    def base_fn(x: int) -> int:
        return x * 2

    p1 = functools.partial(base_fn, 5)
    p2 = functools.partial(p1)

    assert unwrap_callable(base_fn) is base_fn
    assert unwrap_callable(p1) is base_fn
    assert unwrap_callable(p2) is base_fn

    class CallableObj:
        def __call__(self, a: str) -> str:
            return a.upper()

    obj = CallableObj()
    unwrapped = unwrap_callable(obj)
    assert callable(unwrapped)


def test_find_context_parameter() -> None:
    def fn_typed(a: str, ctx: SpellContext) -> str:
        return a

    def fn_typed_optional(a: str, ctx: SpellContext | None = None) -> str:
        return a

    def fn_named_ctx(a: str, ctx: Any = None) -> str:
        return a

    def fn_named_spell_context(a: str, spell_context: Any = None) -> str:
        return a

    def fn_no_ctx(a: str, b: int = 1) -> str:
        return a

    assert find_context_parameter(fn_typed) == "ctx"
    assert find_context_parameter(fn_typed_optional) == "ctx"
    assert find_context_parameter(fn_named_ctx) == "ctx"
    assert find_context_parameter(fn_named_spell_context) == "spell_context"
    assert find_context_parameter(fn_no_ctx) is None

    # Works with partial
    part = functools.partial(fn_typed, "hello")
    assert find_context_parameter(part) == "ctx"


def test_inject_casting_rationale_schema() -> None:
    schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to file"},
        },
        "required": ["path"],
    }
    injected = inject_casting_rationale_schema(schema)
    assert "spell_action" in injected["properties"]
    assert "spell_summary" in injected["properties"]
    assert "spell_action" in injected["required"]
    assert "spell_summary" in injected["required"]
    assert "path" in injected["properties"]


def test_generate_spell_schema_excludes_context_and_injects_rationale() -> None:
    def my_spell(
        file_path: str,
        line_number: int = 1,
        ctx: SpellContext | None = None,
    ) -> str:
        """Read a file."""
        return file_path

    schema = generate_spell_schema(my_spell)
    props = schema.get("properties", {})
    assert "file_path" in props
    assert "line_number" in props
    # Context must NOT leak into model JSON schema
    assert "ctx" not in props
    assert "spell_context" not in props
    # Casting rationale fields MUST be injected
    assert "spell_action" in props
    assert "spell_summary" in props
    assert "spell_action" in schema.get("required", [])
    assert "spell_summary" in schema.get("required", [])


@pytest.mark.asyncio
async def test_invoke_spell_callable_sync_and_async() -> None:
    def sync_spell(x: int, y: int) -> int:
        return x + y

    async def async_spell(x: int, y: int) -> int:
        await asyncio.sleep(0.01)
        return x * y

    res_sync = await invoke_spell_callable(sync_spell, 3, 4)
    res_async = await invoke_spell_callable(async_spell, 3, 4)
    assert res_sync == 7
    assert res_async == 12


@pytest.mark.asyncio
async def test_spell_dispatcher_populates_spell_context_and_approval_request(
    tmp_path: Path,
) -> None:
    from mvgeos_core.approval import (
        ApprovalDecision,
        ApprovalOutcome,
        ApprovalReasonCode,
        ApprovalRequest,
    )
    from mvgeos_core.channel import MvgeResponse
    from mvgeos_core.dispatcher import SpellDispatcher
    from mvgeos_core.events import ContentType, MvgeEvent
    from mvgeos_core.loop import LoopCallbacks, LoopContext
    from mvgeos_core.spells import MvgeSpell, SpellResult

    captured_ctx: SpellContext | None = None
    captured_approval_req: ApprovalRequest | None = None

    class ContextCapturingSpell(MvgeSpell):
        async def execute(
            self,
            spell_cast_id: str,
            params: dict[str, Any],
            signal: Any = None,
            on_update: Any = None,
            ctx: SpellContext | None = None,
        ) -> SpellResult:
            nonlocal captured_ctx
            captured_ctx = ctx
            return SpellResult(
                spell_name=self.name,
                content=f"read {params.get('file_path')}",
            )

    spell = ContextCapturingSpell(
        name="read_file",
        description="Read file",
        parameters={
            "type": "object",
            "properties": {"file_path": {"type": "string"}},
            "required": ["file_path"],
        },
    )

    async def mock_approval_gate(req: ApprovalRequest) -> ApprovalDecision:
        nonlocal captured_approval_req
        captured_approval_req = req
        return ApprovalDecision(
            outcome=ApprovalOutcome.ALLOW,
            reason_code=ApprovalReasonCode.USER,
            request_digest=req.argument_digest,
        )

    backend = LocalExecutionBackend()
    context = LoopContext(
        spells=[spell],
        workspace_root=tmp_path,
        backend=backend,
        sub_mvge_id="sub-mvge-01",
        parent_spell_cast_id="parent-cast-99",
        agent_name="coding_mvge",
        tome_id="tome-42",
        project_root=str(tmp_path),
    )

    dispatcher = SpellDispatcher()
    inv = MvgeResponse(
        content=[
            {
                "type": ContentType.SPELL_CAST,
                "spell_cast": {
                    "id": "cast-1",
                    "name": "read_file",
                    "arguments": {
                        "file_path": "main.py",
                        "spell_action": "Inspecting main module",
                        "spell_summary": "Main inspection",
                    },
                },
            }
        ]
    )

    events: list[MvgeEvent] = []

    async def emit_sink(event: MvgeEvent) -> None:
        events.append(event)

    callbacks = LoopCallbacks(approval_gate=mock_approval_gate)
    res = await dispatcher.dispatch_batch(
        inv, context, callbacks=callbacks, emit=emit_sink
    )

    assert len(res.messages) == 1
    assert not res.messages[0].is_error
    assert captured_ctx is not None
    assert captured_ctx.spell_cast_id == "cast-1"
    assert captured_ctx.workspace_root == tmp_path
    assert captured_ctx.backend is backend
    assert captured_ctx.sub_mvge_id == "sub-mvge-01"
    assert captured_ctx.agent_name == "coding_mvge"
    assert captured_ctx.tome_id == "tome-42"
    assert captured_ctx.spell_action == "Inspecting main module"
    assert captured_ctx.spell_summary == "Main inspection"

    assert captured_approval_req is not None
    assert captured_approval_req.cast_id == "cast-1"
    assert captured_approval_req.spell_action == "Inspecting main module"
    assert captured_approval_req.spell_summary == "Main inspection"
    assert captured_approval_req.sub_mvge_id == "sub-mvge-01"
    assert captured_approval_req.parent_spell_cast_id == "parent-cast-99"
