from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mvge.agent.mvge import Mvge
from mvge.agent.sub_mvge import (
    SubMvgeManager,
    SubMvgeRecord,
    SubMvgeSpec,
    SubMvgeState,
)
from mvge.core.abort import AbortController
from mvge.core.approval import (
    ApprovalDecision,
    ApprovalReasonCode,
    ApprovalRequest,
    deny,
)
from mvge.core.channel import MvgeResponse
from mvge.core.errors import GatewayLoadError
from mvge.core.spells import MvgeSpell, SpellResult, SpellStatus
from mvge.runes.rune_runner import RuneRunner


class DummySpell(MvgeSpell):
    def __init__(self, name: str, read_only: bool = False) -> None:
        super().__init__(
            name=name,
            description=f"Dummy {name}",
            parameters={},
            read_only=read_only,
        )

    async def execute(self, **kwargs: object) -> SpellResult:
        return SpellResult(
            spell_name=self.name,
            status=SpellStatus.SUCCESS,
            content="ok",
        )


class MockChildMvgeContext:
    def __init__(
        self,
        default_response: str = "ok",
        custom_run: object = None,
        custom_init: object = None,
    ) -> None:
        self.default_response = default_response
        self.custom_run = custom_run
        self.custom_init = custom_init
        self._orig_init = Mvge.__init__
        self._orig_run = Mvge.run
        self._orig_init_async = Mvge.initialize

    def __enter__(self) -> None:
        default_text = self.default_response
        custom_r = self.custom_run
        custom_i = self.custom_init
        orig_init = self._orig_init

        def mocked_init(self: Mvge, *args: object, **kwargs: object) -> None:
            if custom_i is not None:
                custom_i(self, *args, **kwargs)
            else:
                orig_init(self, *args, **kwargs)

        async def mocked_init_async(self: Mvge) -> None:
            self._initialized = True
            if self._agent_tome is None:
                fake_tome = MagicMock()
                fake_tome.tome_id = f"tome-{self._sub_mvge_id or 'mock'}"
                fake_tome.path = self._tome_dir / f"{fake_tome.tome_id}.jsonl"
                self._agent_tome = fake_tome

        async def mocked_run(
            self: Mvge,
            prompt: str | list[dict[str, object]],
            *,
            signal: object = None,
        ) -> MvgeResponse:
            if custom_r is not None:
                params = inspect.signature(custom_r).parameters
                if "signal" in params or any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
                ):
                    return await custom_r(prompt, signal=signal)  # type: ignore[misc]
                return await custom_r(prompt)  # type: ignore[misc]
            return MvgeResponse(
                role="assistant",
                content=[{"type": "text", "text": default_text}],
            )

        Mvge.__init__ = mocked_init  # type: ignore[method-assign]
        Mvge.initialize = mocked_init_async  # type: ignore[method-assign]
        Mvge.run = mocked_run  # type: ignore[method-assign]

    def __exit__(self, *args: object) -> None:
        Mvge.__init__ = self._orig_init  # type: ignore[method-assign]
        Mvge.initialize = self._orig_init_async  # type: ignore[method-assign]
        Mvge.run = self._orig_run  # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_sub_mvge_spec_and_record_data_structures() -> None:
    spec = SubMvgeSpec(
        role="researcher",
        prompt="Analyze the repo",
        agent_name="coding_mvge",
        model="test-model",
        workspace="inherit",
        allowed_spells=frozenset({"read", "grep"}),
        wait=True,
    )
    assert spec.role == "researcher"
    assert spec.allowed_spells == frozenset({"read", "grep"})
    assert spec.wait is True

    record = SubMvgeRecord(
        sub_mvge_id="sub-123",
        role="researcher",
        agent_name="coding_mvge",
        model_id="test-model",
        tome_path=Path("/tmp/subsessions/tome-123.jsonl"),
        child_tome_id="tome-123",
        workspace_path=Path("/tmp/workspace"),
        state=SubMvgeState.RUNNING,
        state_detail="Executing turn",
    )
    assert record.sub_mvge_id == "sub-123"
    assert record.state == SubMvgeState.RUNNING
    assert record.mana_used == {}


@pytest.mark.asyncio
async def test_child_tome_persisted_in_subsessions_dir(tmp_path: Path) -> None:
    subsessions_dir = tmp_path / "sessions" / "subsessions"
    subsessions_dir.mkdir(parents=True, exist_ok=True)

    fake_harness = MagicMock()
    fake_harness.turn_in_flight = False
    fake_harness.state = MagicMock()
    fake_harness.state.mana_used = 42

    manager = SubMvgeManager(
        parent_workspace=tmp_path,
        parent_model_id="test-model",
        parent_agent_name="test_mvge",
        subsessions_dir=subsessions_dir,
    )

    with MockChildMvgeContext("Child response"):
        records = await manager.summon(
            [SubMvgeSpec(role="research", prompt="Check code", wait=True)]
        )

    assert len(records) == 1
    rec = records[0]
    assert rec.role == "research"
    assert rec.state == SubMvgeState.COMPLETED
    assert rec.summary == "Child response"
    assert rec.tome_path.parent == subsessions_dir
    assert rec.child_tome_id.startswith("tome-") or len(rec.child_tome_id) > 0


@pytest.mark.asyncio
async def test_post_rune_allowed_spells_enforcement(tmp_path: Path) -> None:
    subsessions_dir = tmp_path / "sessions" / "subsessions"
    subsessions_dir.mkdir(parents=True, exist_ok=True)

    manager = SubMvgeManager(
        parent_workspace=tmp_path,
        subsessions_dir=subsessions_dir,
    )

    spec = SubMvgeSpec(
        role="restricted_agent",
        prompt="Do read only",
        allowed_spells=frozenset({"read", "find"}),
        wait=False,
    )

    created_mvge: list[Mvge] = []
    original_init = Mvge.__init__

    def intercepting_init(self: Mvge, *args: object, **kwargs: object) -> None:
        created_mvge.append(self)
        original_init(self, *args, **kwargs)
        self._spells = [
            DummySpell("read"),
            DummySpell("find"),
            DummySpell("bash"),
            DummySpell("write"),
        ]

    with MockChildMvgeContext("Done", custom_init=intercepting_init):
        await manager.summon([spec])

    assert len(created_mvge) == 1
    child = created_mvge[0]
    assert child.allowed_spells == frozenset({"read", "find"})
    built_spells = child._build_spells()
    built_names = {s.name for s in built_spells}
    assert built_names == {"read", "find"}
    assert "bash" not in built_names
    assert "write" not in built_names


@pytest.mark.asyncio
async def test_parent_abort_stops_running_child_turn(tmp_path: Path) -> None:
    """Aborting the parent must stop a running child turn, not just flip
    a controller nobody reads. Observable outcome: the background child
    finishes promptly and its record lands in CANCELING, never COMPLETED.
    """
    subsessions_dir = tmp_path / "sessions" / "subsessions"
    subsessions_dir.mkdir(parents=True, exist_ok=True)

    parent_controller = AbortController()
    manager = SubMvgeManager(
        parent_workspace=tmp_path,
        subsessions_dir=subsessions_dir,
    )

    started = asyncio.Event()

    async def abortable_run(prompt: str, *, signal: object = None) -> MvgeResponse:
        started.set()
        if signal is not None:
            await signal.wait()  # type: ignore[attr-defined]
        else:
            await asyncio.sleep(10.0)
        return MvgeResponse(
            role="assistant",
            content=[{"type": "text", "text": "Done"}],
        )

    with MockChildMvgeContext(custom_run=abortable_run):
        records = await manager.summon(
            [SubMvgeSpec(role="worker", prompt="Sleep", wait=False)],
            parent_signal=parent_controller.signal,
        )
        assert len(records) == 1
        sub_id = records[0].sub_mvge_id
        await asyncio.wait_for(started.wait(), timeout=5.0)

        parent_controller.abort()

        async def _await_canceling() -> SubMvgeRecord:
            while True:
                rec = manager.list_active()[0]
                if rec.state == SubMvgeState.CANCELING:
                    return rec
                await asyncio.sleep(0.01)

        rec = await asyncio.wait_for(_await_canceling(), timeout=5.0)
        assert rec.sub_mvge_id == sub_id
        assert rec.state == SubMvgeState.CANCELING
        assert rec.summary != "Done"


@pytest.mark.asyncio
async def test_parent_abort_stops_waiting_child_turn(tmp_path: Path) -> None:
    """Same cascade for wait=True: the summon itself must return promptly
    with the child record in CANCELING instead of hanging on the child."""
    subsessions_dir = tmp_path / "sessions" / "subsessions"
    subsessions_dir.mkdir(parents=True, exist_ok=True)

    parent_controller = AbortController()
    manager = SubMvgeManager(
        parent_workspace=tmp_path,
        subsessions_dir=subsessions_dir,
    )

    started = asyncio.Event()

    async def abortable_run(prompt: str, *, signal: object = None) -> MvgeResponse:
        started.set()
        if signal is not None:
            await signal.wait()  # type: ignore[attr-defined]
        else:
            await asyncio.sleep(10.0)
        return MvgeResponse(
            role="assistant",
            content=[{"type": "text", "text": "Done"}],
        )

    with MockChildMvgeContext(custom_run=abortable_run):
        summon_task = asyncio.create_task(
            manager.summon(
                [SubMvgeSpec(role="worker", prompt="Sleep", wait=True)],
                parent_signal=parent_controller.signal,
            )
        )
        await asyncio.wait_for(started.wait(), timeout=5.0)
        parent_controller.abort()
        records = await asyncio.wait_for(summon_task, timeout=5.0)

    assert len(records) == 1
    assert records[0].state == SubMvgeState.CANCELING


@pytest.mark.asyncio
async def test_follow_up_after_abort_runs_again(tmp_path: Path) -> None:
    """A child cancelled by a parent abort is not poisoned forever: a
    later follow-up turn runs with a fresh, unaborted signal."""
    subsessions_dir = tmp_path / "sessions" / "subsessions"
    subsessions_dir.mkdir(parents=True, exist_ok=True)

    parent_controller = AbortController()
    manager = SubMvgeManager(
        parent_workspace=tmp_path,
        subsessions_dir=subsessions_dir,
    )

    started = asyncio.Event()

    async def abortable_run(prompt: str, *, signal: object = None) -> MvgeResponse:
        if prompt == "Sleep":
            started.set()
            if signal is not None:
                await signal.wait()  # type: ignore[attr-defined]
            else:
                await asyncio.sleep(10.0)
            return MvgeResponse(
                role="assistant",
                content=[{"type": "text", "text": "Done"}],
            )
        return MvgeResponse(
            role="assistant",
            content=[{"type": "text", "text": f"Follow-up: {prompt}"}],
        )

    with MockChildMvgeContext(custom_run=abortable_run):
        records = await manager.summon(
            [SubMvgeSpec(role="worker", prompt="Sleep", wait=False)],
            parent_signal=parent_controller.signal,
        )
        sub_id = records[0].sub_mvge_id
        await asyncio.wait_for(started.wait(), timeout=5.0)
        parent_controller.abort()

        async def _await_canceling() -> None:
            while manager.list_active()[0].state != SubMvgeState.CANCELING:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(_await_canceling(), timeout=5.0)

        rec = await manager.send_message(sub_id, "Try again")
        assert rec.state == SubMvgeState.COMPLETED
        assert rec.summary == "Follow-up: Try again"


@pytest.mark.asyncio
async def test_approval_presenter_lock_serialization() -> None:
    parent_runner = RuneRunner()
    lock_acquisitions: list[str] = []

    async def fake_presenter(req: ApprovalRequest) -> ApprovalDecision:
        lock_acquisitions.append(f"start:{req.sub_mvge_id}")
        await asyncio.sleep(0.05)
        lock_acquisitions.append(f"end:{req.sub_mvge_id}")
        return deny(req, ApprovalReasonCode.USER_DENIED)

    parent_runner.set_approval_presenter(fake_presenter)

    child_runner_1 = RuneRunner()
    child_runner_1.share_approval_presenter_from(parent_runner)

    child_runner_2 = RuneRunner()
    child_runner_2.share_approval_presenter_from(parent_runner)

    req1 = ApprovalRequest(
        cast_id="cast-1",
        spell_name="bash",
        arguments={"command": "rm -rf"},
        sub_mvge_id="child-1",
    )
    req2 = ApprovalRequest(
        cast_id="cast-2",
        spell_name="edit",
        arguments={"path": "critical.py"},
        sub_mvge_id="child-2",
    )

    await asyncio.gather(
        child_runner_1.request_approval(req1),
        child_runner_2.request_approval(req2),
    )

    # Check serialized mutex execution
    assert len(lock_acquisitions) == 4
    first_child = lock_acquisitions[0].split(":")[1]
    second_child = "child-2" if first_child == "child-1" else "child-1"
    assert lock_acquisitions == [
        f"start:{first_child}",
        f"end:{first_child}",
        f"start:{second_child}",
        f"end:{second_child}",
    ]


@pytest.mark.asyncio
async def test_conversational_continuation_via_send_message(tmp_path: Path) -> None:
    subsessions_dir = tmp_path / "sessions" / "subsessions"
    subsessions_dir.mkdir(parents=True, exist_ok=True)

    manager = SubMvgeManager(
        parent_workspace=tmp_path,
        subsessions_dir=subsessions_dir,
    )

    turn_count = 0

    async def counting_run(prompt: str) -> MvgeResponse:
        nonlocal turn_count
        turn_count += 1
        response_text = f"Response turn {turn_count} to {prompt}"
        return MvgeResponse(
            role="assistant",
            content=[{"type": "text", "text": response_text}],
        )

    with MockChildMvgeContext(custom_run=counting_run):
        records = await manager.summon(
            [SubMvgeSpec(role="assistant", prompt="Turn 1", wait=True)]
        )
        sub_id = records[0].sub_mvge_id
        assert records[0].summary == "Response turn 1 to Turn 1"
        assert records[0].state == SubMvgeState.COMPLETED

        follow_up_rec = await manager.send_message(sub_id, "Turn 2")
        assert follow_up_rec.summary == "Response turn 2 to Turn 2"
        assert follow_up_rec.state == SubMvgeState.COMPLETED
        assert turn_count == 2


@pytest.mark.asyncio
async def test_reactive_wakeup_queueing(tmp_path: Path) -> None:
    subsessions_dir = tmp_path / "sessions" / "subsessions"
    subsessions_dir.mkdir(parents=True, exist_ok=True)

    fake_harness = MagicMock()
    fake_harness.turn_in_flight = False

    manager = SubMvgeManager(
        parent_workspace=tmp_path,
        parent_harness=fake_harness,
        subsessions_dir=subsessions_dir,
    )

    with MockChildMvgeContext("Child finished successfully"):
        records = await manager.summon(
            [SubMvgeSpec(role="background_worker", prompt="Compute", wait=False)]
        )
        sub_id = records[0].sub_mvge_id
        await asyncio.sleep(0.05)

    assert fake_harness.follow_up.called
    notification_arg = fake_harness.follow_up.call_args[0][0]
    assert f'<sub_mvge_notification id="{sub_id}"' in notification_arg
    assert 'role="background_worker"' in notification_arg
    assert 'status="completed"' in notification_arg
    assert "Child finished successfully" in notification_arg

    fake_harness.turn_in_flight = True
    with MockChildMvgeContext("Second child finished"):
        await manager.summon(
            [SubMvgeSpec(role="mid_turn_worker", prompt="Compute 2", wait=False)]
        )
        await asyncio.sleep(0.05)

    assert fake_harness.steer.called
    notification_arg_steer = fake_harness.steer.call_args[0][0]
    assert 'role="mid_turn_worker"' in notification_arg_steer


@pytest.mark.asyncio
async def test_sub_mvge_kill_and_kill_all(tmp_path: Path) -> None:
    subsessions_dir = tmp_path / "sessions"
    subsessions_dir.mkdir(parents=True, exist_ok=True)
    manager = SubMvgeManager(
        parent_workspace=tmp_path,
        subsessions_dir=subsessions_dir,
    )

    async def slow_run(*args: object, **kwargs: object) -> MvgeResponse:
        await asyncio.sleep(5.0)
        return MvgeResponse(role="assistant", content=[])

    with MockChildMvgeContext(custom_run=slow_run):
        records = await manager.summon(
            [SubMvgeSpec(role="to_kill", prompt="Long work", wait=False)]
        )
        sub_id = records[0].sub_mvge_id

        killed = await manager.kill([sub_id])
        assert len(killed) == 1
        assert killed[0].state == SubMvgeState.TERMINATED

        # kill unknown id
        killed_none = await manager.kill(["unknown_id"])
        assert killed_none == []

        # kill_all
        await manager.summon(
            [SubMvgeSpec(role="to_kill_all", prompt="More work", wait=False)]
        )
        killed_all = await manager.kill_all()
        assert len(killed_all) >= 1


@pytest.mark.asyncio
async def test_kill_sets_terminated_terminal_state(tmp_path: Path) -> None:
    subsessions_dir = tmp_path / "sessions"
    subsessions_dir.mkdir(parents=True, exist_ok=True)
    manager = SubMvgeManager(
        parent_workspace=tmp_path,
        subsessions_dir=subsessions_dir,
    )

    async def slow_run(*args: object, **kwargs: object) -> MvgeResponse:
        await asyncio.sleep(5.0)
        return MvgeResponse(role="assistant", content=[])

    with MockChildMvgeContext(custom_run=slow_run):
        records = await manager.summon(
            [SubMvgeSpec(role="to_kill", prompt="Long work", wait=False)]
        )
        sub_id = records[0].sub_mvge_id

        killed = await manager.kill([sub_id])
        assert len(killed) == 1
        assert killed[0].state == SubMvgeState.TERMINATED
        assert killed[0].state_detail == "Terminated"
        assert killed[0].error == "Terminated"

        listed = manager.list_active()
        assert len(listed) == 1
        assert listed[0].sub_mvge_id == sub_id
        assert listed[0].state == SubMvgeState.TERMINATED

        await asyncio.sleep(0.1)
        listed_again = manager.list_active()
        assert len(listed_again) == 1
        assert listed_again[0].state == SubMvgeState.TERMINATED
        assert listed_again[0].state_detail == "Terminated"


@pytest.mark.asyncio
async def test_kill_is_idempotent(tmp_path: Path) -> None:
    subsessions_dir = tmp_path / "sessions"
    subsessions_dir.mkdir(parents=True, exist_ok=True)
    manager = SubMvgeManager(
        parent_workspace=tmp_path,
        subsessions_dir=subsessions_dir,
    )

    async def slow_run(*args: object, **kwargs: object) -> MvgeResponse:
        await asyncio.sleep(5.0)
        return MvgeResponse(role="assistant", content=[])

    with MockChildMvgeContext(custom_run=slow_run):
        records = await manager.summon(
            [SubMvgeSpec(role="to_kill", prompt="Long work", wait=False)]
        )
        sub_id = records[0].sub_mvge_id

        first = await manager.kill([sub_id])
        second = await manager.kill([sub_id])
        assert len(second) == 1
        assert second[0].sub_mvge_id == first[0].sub_mvge_id
        assert second[0].state == SubMvgeState.TERMINATED

        listed = manager.list_active()
        assert len(listed) == 1
        assert listed[0].state == SubMvgeState.TERMINATED


@pytest.mark.asyncio
async def test_kill_all_terminates_all(tmp_path: Path) -> None:
    subsessions_dir = tmp_path / "sessions"
    subsessions_dir.mkdir(parents=True, exist_ok=True)
    manager = SubMvgeManager(
        parent_workspace=tmp_path,
        subsessions_dir=subsessions_dir,
    )

    async def slow_run(*args: object, **kwargs: object) -> MvgeResponse:
        await asyncio.sleep(5.0)
        return MvgeResponse(role="assistant", content=[])

    with MockChildMvgeContext(custom_run=slow_run):
        records = await manager.summon(
            [
                SubMvgeSpec(role="worker", prompt="Work 1", wait=False),
                SubMvgeSpec(role="worker", prompt="Work 2", wait=False),
            ]
        )
        assert len(records) == 2

        killed = await manager.kill_all()
        assert len(killed) == 2
        for rec in killed:
            assert rec.state == SubMvgeState.TERMINATED
            assert rec.state_detail == "Terminated"


@pytest.mark.asyncio
async def test_message_to_terminated_child_is_rejected(tmp_path: Path) -> None:
    subsessions_dir = tmp_path / "sessions"
    subsessions_dir.mkdir(parents=True, exist_ok=True)
    manager = SubMvgeManager(
        parent_workspace=tmp_path,
        subsessions_dir=subsessions_dir,
    )

    async def slow_run(*args: object, **kwargs: object) -> MvgeResponse:
        await asyncio.sleep(5.0)
        return MvgeResponse(role="assistant", content=[])

    with MockChildMvgeContext(custom_run=slow_run):
        records = await manager.summon(
            [SubMvgeSpec(role="to_kill", prompt="Long work", wait=False)]
        )
        sub_id = records[0].sub_mvge_id
        await manager.kill([sub_id])
        summary_before = manager.list_active()[0].summary

        with pytest.raises(RuntimeError, match="terminated"):
            await manager.send_message(sub_id, "Are you still there?")

        listed = manager.list_active()
        assert len(listed) == 1
        assert listed[0].state == SubMvgeState.TERMINATED
        assert listed[0].summary == summary_before


@pytest.mark.asyncio
async def test_parent_abort_still_canceling_not_terminated(tmp_path: Path) -> None:
    subsessions_dir = tmp_path / "sessions" / "subsessions"
    subsessions_dir.mkdir(parents=True, exist_ok=True)

    parent_controller = AbortController()
    manager = SubMvgeManager(
        parent_workspace=tmp_path,
        subsessions_dir=subsessions_dir,
    )

    started = asyncio.Event()

    async def abortable_run(prompt: str, *, signal: object = None) -> MvgeResponse:
        started.set()
        if signal is not None:
            await signal.wait()  # type: ignore[attr-defined]
        else:
            await asyncio.sleep(10.0)
        return MvgeResponse(
            role="assistant",
            content=[{"type": "text", "text": "Done"}],
        )

    with MockChildMvgeContext(custom_run=abortable_run):
        records = await manager.summon(
            [SubMvgeSpec(role="worker", prompt="Sleep", wait=False)],
            parent_signal=parent_controller.signal,
        )
        sub_id = records[0].sub_mvge_id
        await asyncio.wait_for(started.wait(), timeout=5.0)

        parent_controller.abort()

        async def _await_canceling() -> SubMvgeRecord:
            while True:
                rec = manager.list_active()[0]
                if rec.state == SubMvgeState.CANCELING:
                    return rec
                await asyncio.sleep(0.01)

        rec = await asyncio.wait_for(_await_canceling(), timeout=5.0)
        assert rec.sub_mvge_id == sub_id
        assert rec.state == SubMvgeState.CANCELING
        assert rec.state_detail == "Cancelled"
        assert rec.state != SubMvgeState.TERMINATED


@pytest.mark.asyncio
async def test_sub_mvge_send_message_error(tmp_path: Path) -> None:
    subsessions_dir = tmp_path / "sessions"
    subsessions_dir.mkdir(parents=True, exist_ok=True)
    manager = SubMvgeManager(
        parent_workspace=tmp_path,
        subsessions_dir=subsessions_dir,
    )

    async def failing_run(*args: object, **kwargs: object) -> MvgeResponse:
        raise RuntimeError("Crash in child")

    with MockChildMvgeContext(custom_run=failing_run):
        records = await manager.summon(
            [SubMvgeSpec(role="failing", prompt="Fail", wait=True)]
        )
        sub_id = records[0].sub_mvge_id
        assert records[0].state == SubMvgeState.ERRORED

        rec = await manager.send_message(sub_id, "Retry")
        assert rec.state == SubMvgeState.ERRORED
        assert "Crash in child" in str(rec.error)


@pytest.mark.asyncio
async def test_sub_mvge_workspace_worktree(tmp_path: Path) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    subsessions_dir = tmp_path / "sessions"
    subsessions_dir.mkdir(parents=True, exist_ok=True)

    manager = SubMvgeManager(
        parent_workspace=tmp_path,
        subsessions_dir=subsessions_dir,
    )

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        ws = await manager._resolve_workspace("sub-1", "worktree")
        assert "worktrees" in ws.parts
        assert ws.name == "sub-1"

        manager._records["sub-1"] = SubMvgeRecord(
            sub_mvge_id="sub-1",
            role="test",
            agent_name="test_mvge",
            model_id="test_model",
            tome_path=tmp_path / "tome.jsonl",
            child_tome_id="sub-1",
            workspace_path=ws,
            state=SubMvgeState.IDLE,
        )
        ws.mkdir(parents=True, exist_ok=True)
        killed = await manager.kill(["sub-1"])
        assert len(killed) == 1


def test_extract_invocation_text_fallbacks() -> None:
    from mvge.agent.sub_mvge import _extract_invocation_text

    class InvWithText:
        text = "direct text"

    assert _extract_invocation_text(InvWithText()) == "direct text"

    class InvPlain:
        def __str__(self) -> str:
            return "plain string"

    assert _extract_invocation_text(InvPlain()) == "plain string"


def _d1_gateway_rune_py() -> str:
    names = (
        "search",
        "activate",
        "execute",
        "unload",
        "tool_search",
        "skill_search",
        "skill_execute",
        "mcp_search",
    )
    lines = [
        "from mvge.runes.types import SpellDefinition",
        "",
        "",
        "def rune_factory(api):",
    ]
    for name in names:
        lines.append(
            f"    api.register_spell(SpellDefinition(name={name!r}, description=''))"
        )
    return "\n".join(lines) + "\n"


def _d1_write_rune(
    runes_dir: Path, name: str, body: str, gateway: bool, missing_dep: bool
) -> None:
    rune_dir = runes_dir / name
    rune_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "name": name,
        "version": "1.0.0",
        "description": "D1 probe rune",
        "entry_point": "rune.py",
        "spell_gateway": gateway,
    }
    if missing_dep:
        manifest["python_deps"] = ["definitely_missing_package_d1_xyz"]
    (rune_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (rune_dir / "rune.py").write_text(body, encoding="utf-8")


def _d1_workspace(tmp_path: Path, *, gateway_missing_dep: bool = False) -> Path:
    """A workspace a summoned child discovers spells and runes from."""
    ws = tmp_path / "ws"
    spells_dir = ws / "spells"
    spells_dir.mkdir(parents=True, exist_ok=True)
    (spells_dir / "bash.py").write_text(
        'def bash() -> str:\n    """Run a bash command."""\n    return "ok"\n',
        encoding="utf-8",
    )
    runes_dir = ws / "runes"
    _d1_write_rune(
        runes_dir,
        "seeker",
        _d1_gateway_rune_py(),
        gateway=True,
        missing_dep=gateway_missing_dep,
    )
    _d1_write_rune(
        runes_dir,
        "bridge",
        "from mvge.runes.types import SpellDefinition\n"
        "\n"
        "\n"
        "def rune_factory(api):\n"
        "    api.register_spell(\n"
        "        SpellDefinition(name='manage_mvges', description='')\n"
        "    )\n",
        gateway=False,
        missing_dep=False,
    )
    return ws


def _d1_manager(
    tmp_path: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> SubMvgeManager:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(tmp_path / "global"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    config_dir = tmp_path / "global" / "agents" / "d1-child"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "SYSTEM.md").write_text("# D1 child\n", encoding="utf-8")
    from mvge.provider.base import Realm
    from mvge.provider.realms import realm_for_model_id
    from mvge.provider.registry import RealmRegistry

    registry = RealmRegistry()
    registry.register_realm_factory(
        realm_for_model_id("openrouter/free"), lambda **kwargs: MagicMock(spec=Realm)
    )
    subsessions_dir = tmp_path / "subsessions"
    subsessions_dir.mkdir(parents=True, exist_ok=True)
    return SubMvgeManager(
        parent_workspace=workspace,
        parent_model_id="openrouter/free",
        parent_agent_name="d1-child",
        shared_realm_registry=registry,
        subsessions_dir=subsessions_dir,
    )


async def _d1_drain(manager: SubMvgeManager) -> None:
    """Let wait=False child turns finish (they error against the stub
    realm) so no background task outlives the test."""
    for _ in range(1000):
        tasks = list(manager._tasks.values())
        if all(t.done() for t in tasks):
            return
        await asyncio.sleep(0)
    await manager.kill_all()


@pytest.mark.asyncio
async def test_summon_record_carries_effective_spells(
    tmp_path: Path,
) -> None:
    """D1 test 2: the summon result reports the loop dispatch set, so an
    allowed_spells=[] child is observably spell-less, not mysteriously
    broken. No tome-header field is asserted: route (b) writes none."""
    subsessions_dir = tmp_path / "sessions" / "subsessions"
    subsessions_dir.mkdir(parents=True, exist_ok=True)
    manager = SubMvgeManager(
        parent_workspace=tmp_path,
        subsessions_dir=subsessions_dir,
    )

    original_init = Mvge.__init__

    def intercepting_init(self: Mvge, *args: object, **kwargs: object) -> None:
        original_init(self, *args, **kwargs)
        self._spells = [DummySpell("bash"), DummySpell("read")]

    with MockChildMvgeContext("Done", custom_init=intercepting_init):
        records = await manager.summon(
            [
                SubMvgeSpec(
                    role="worker",
                    prompt="Do nothing",
                    allowed_spells=frozenset(),
                    wait=True,
                ),
                SubMvgeSpec(
                    role="worker",
                    prompt="Do bash",
                    allowed_spells=frozenset({"bash"}),
                    wait=True,
                ),
            ]
        )

    assert records[0].effective_spells == []
    assert records[1].effective_spells == ["bash"]
    record = SubMvgeRecord(
        sub_mvge_id="sub-123",
        role="worker",
        agent_name="coding_mvge",
        model_id="test-model",
        tome_path=Path("/tmp/subsessions/tome-123.jsonl"),
        child_tome_id="tome-123",
        workspace_path=Path("/tmp/workspace"),
        state=SubMvgeState.RUNNING,
    )
    assert record.effective_spells == []


@pytest.mark.asyncio
async def test_repeated_spawns_have_identical_dispatch_sets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D1 test 7: 10 spawns per spec, counted from the summon result's
    effective_spells only, never from a tome header."""
    workspace = _d1_workspace(tmp_path)
    manager = _d1_manager(tmp_path, workspace, monkeypatch)
    try:
        gateway_names = sorted(
            [
                "search",
                "activate",
                "execute",
                "unload",
                "tool_search",
                "skill_search",
                "skill_execute",
                "mcp_search",
            ]
        )
        cases: list[tuple[frozenset[str] | None, list[str]]] = [
            (None, gateway_names),
            (frozenset({"bash"}), ["bash"]),
            (frozenset(), []),
        ]
        for allowed, expected in cases:
            seen: list[list[str]] = []
            for _ in range(10):
                records = await manager.summon(
                    [
                        SubMvgeSpec(
                            role="worker",
                            prompt="hi",
                            allowed_spells=allowed,
                            wait=False,
                        )
                    ]
                )
                seen.append(sorted(records[0].effective_spells))
            assert len(seen) == 10
            assert all(dispatch == expected for dispatch in seen), (
                f"allowed={allowed}: dispatch sets varied or mismatched: {seen}"
            )
    finally:
        await _d1_drain(manager)
        await manager.kill_all()


@pytest.mark.asyncio
async def test_repeated_spawn_with_missing_gateway_dep_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D1 test 7, gateway-failure variant: a ["bash"] spawn under a
    gateway rune with a MISSING_DEP diagnostic fails loudly and leaves
    no record, instead of silently producing a different dispatch set."""
    workspace = _d1_workspace(tmp_path, gateway_missing_dep=True)
    manager = _d1_manager(tmp_path, workspace, monkeypatch)
    with pytest.raises(GatewayLoadError) as exc_info:
        await manager.summon(
            [
                SubMvgeSpec(
                    role="worker",
                    prompt="hi",
                    allowed_spells=frozenset({"bash"}),
                    wait=False,
                )
            ]
        )
    assert "seeker" in str(exc_info.value)
    assert manager.list_active() == []


@pytest.mark.asyncio
async def test_summoned_child_reload_keeps_allowed_drops_ephemeral(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D1 tests 6/6c at the summon level: reloading a summoned ["bash"]
    child keeps bash and drops a seeker-discovered ephemeral widening."""
    workspace = _d1_workspace(tmp_path)
    manager = _d1_manager(tmp_path, workspace, monkeypatch)
    try:
        records = await manager.summon(
            [
                SubMvgeSpec(
                    role="worker",
                    prompt="hi",
                    allowed_spells=frozenset({"bash"}),
                    wait=False,
                )
            ]
        )
        assert records[0].effective_spells == ["bash"]
        child = manager._instances[records[0].sub_mvge_id]
        assert child.runner is not None
        child.runner.widen_global_allowlist(["manage_mvges"])
        result = await child.reload()
        assert result.ok
        assert [s.name for s in child._build_spells()] == ["bash"]
    finally:
        await _d1_drain(manager)
        await manager.kill_all()
