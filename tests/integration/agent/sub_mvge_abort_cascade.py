"""Stage 1 harness-direct abort-cascade observation route (F3).

Drives the real parent harness, the real ``SubMvgeManager``, and a real
child ``Mvge`` in-process, with only the provider faked: the parent fake
emits one ``summon_mvge`` cast, the child fake emits one ``bash`` cast of
a real ``sleep && touch`` command. No model, no network, no PTY, no
spell-view involvement: the parent spell list is injected explicitly and
the child spell list is the workspace ``spells/`` discovery seam filtered
by the spec's explicit ``allowed_spells``.

The child's ``bash`` spell is a test-local definition written into the
workspace ``spells/`` directory at test time: the engine ships no bash
spell of its own (the production one arrives with the installed agent's
spells), so the test supplies the definition through the production
discovery seam. Execution itself is the production
``LocalExecutionBackend``: a real subprocess tree that abort must kill
for the marker file to stay absent.

Trigger shapes, kept separate because the separation is the point:

- ``signal``: ``parent.abort()`` -- the RPC shape.
- ``cancel``: cancelling the parent turn's asyncio task -- the REPL/TUI
  shape as it stood before the branch-2 fix.
- ``abort_then_cancel``: ``abort()`` then task cancellation -- the shape
  REPL/TUI produce after the branch-2 fix, and the only shape in which a
  wait:false background child can be stopped from the interrupt source.

Cancel-alone on a wait:false child leaves the child running to
completion: the background task is independent of the parent task, and
no signal fires. That orphan was observed pre-fix (child completed,
marker appeared) and is why REPL/TUI must abort first; it is recorded
in the implementation report, not asserted here as desired behavior.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from mvge.agent.mvge import Mvge
from mvge.agent.sub_mvge import (
    SubMvgeManager,
    SubMvgeRecord,
    SubMvgeSpec,
    SubMvgeState,
)
from mvge.core.abort import AbortError, AbortSignal
from mvge.core.channel import (
    ChannelConfig,
    Model,
    MvgeResponse,
    RealmResponse,
    StopReason,
)
from mvge.core.spells import (
    MvgeSpell,
    SpellContext,
    SpellResult,
    SpellStatus,
)
from mvge.provider.base import Realm
from mvge.provider.registry import RealmRegistry

PARENT_MODEL = "fake/parent"
CHILD_MODEL = "fake/child"
SLEEP_SECONDS = 20
WATCH_SECONDS = 60.0
SETTLE_SECONDS = 10.0

BASH_SPELLS_INIT = '''"""Test-local exec spell for the abort-cascade observation route.

The definition is test-local; execution is the production
LocalExecutionBackend, so abort must kill a real subprocess tree for
the marker file to stay absent.
"""

from __future__ import annotations

from typing import Any

from mvge.core.spells import (
    LocalExecutionBackend,
    MvgeSpell,
    SpellResult,
    SpellStatus,
)


class BashSpell(MvgeSpell):
    def __init__(self) -> None:
        super().__init__(
            name="bash",
            description="Run a shell command.",
            parameters={
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        )

    async def execute(
        self,
        spell_cast_id: str,
        params: dict[str, Any],
        signal: Any = None,
        on_update: Any = None,
        ctx: Any = None,
    ) -> SpellResult:
        backend = LocalExecutionBackend()
        result = await backend.exec_command(str(params["command"]), signal=signal)
        return SpellResult(
            spell_name="bash",
            status=SpellStatus.SUCCESS,
            content=result.stdout + result.stderr,
        )


bash = BashSpell()

__all__ = ["bash"]
'''


def _text_response(text: str) -> RealmResponse:
    return RealmResponse(
        model=Model(
            id="fake/x",
            name="fake",
            realm="fake",
            base_url="",
            api_key="test-key",
        ),
        invocation=MvgeResponse(
            role="assistant",
            content=[{"type": "text", "text": text}],
            stop_reason=StopReason.STOP,
        ),
    )


def _cast_response(name: str, args: dict[str, Any]) -> RealmResponse:
    return RealmResponse(
        model=Model(
            id="fake/x",
            name="fake",
            realm="fake",
            base_url="",
            api_key="test-key",
        ),
        invocation=MvgeResponse(
            role="assistant",
            content=[
                {
                    "type": "spell_cast",
                    "spell_cast": {"id": "call_1", "name": name, "arguments": args},
                }
            ],
            stop_reason=StopReason.SPELL_USE,
        ),
    )


class _FakeRealm(Realm):
    """Deterministic scripted provider for parent and child turns.

    The parent script: one ``summon_mvge`` cast, then a probe cast gated
    on a release event (so a wait:false parent turn stays in flight
    while the child sleeps), then a final text response. The child
    script: one ``bash`` cast of the sleep command, then a final text
    response; a follow-up turn (transcript already holds a spell
    result) answers with a distinct follow-up text.
    """

    def __init__(
        self,
        *,
        release: asyncio.Event,
        probes: list[str],
        marker: Path,
        started: Path,
        wait: bool,
    ) -> None:
        self._release = release
        self._probes = probes
        self._marker = marker
        self._started = started
        self._wait = wait
        self._parent_calls = 0

    async def stream(
        self,
        model: Model,
        invocations: list[Any],
        config: ChannelConfig,
        signal: AbortSignal | None = None,
    ) -> AsyncIterator[RealmResponse]:
        if model.id == CHILD_MODEL:
            has_result = any(
                getattr(inv, "role", None) == "spellResult" for inv in invocations
            )
            if has_result:
                last_user = ""
                for inv in reversed(invocations):
                    if getattr(inv, "role", None) == "user":
                        last_user = str(getattr(inv, "content", ""))
                        break
                if "Try again" in last_user:
                    yield _text_response("Follow-up complete.")
                else:
                    yield _text_response("Child done.")
            else:
                self._started.write_text("started")
                yield _cast_response(
                    "bash",
                    {"command": f"sleep {SLEEP_SECONDS} && touch {self._marker}"},
                )
            return
        self._parent_calls += 1
        if self._parent_calls == 1:
            yield _cast_response(
                "summon_mvge",
                {
                    "subagents": [
                        {
                            "role": "worker",
                            "prompt": "Run the sleep command.",
                            "model": CHILD_MODEL,
                            "allowed_spells": ["bash"],
                        }
                    ],
                    "wait": self._wait,
                },
            )
            return
        if self._parent_calls == 2:
            if signal is not None and signal.aborted:
                raise AbortError("Operation aborted")
            release_task = asyncio.create_task(self._release.wait())
            abort_task = (
                asyncio.create_task(signal.wait()) if signal is not None else None
            )
            waiters = [release_task] + ([abort_task] if abort_task else [])
            try:
                await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in waiters:
                    if not task.done():
                        task.cancel()
            if signal is not None and signal.aborted:
                raise AbortError("Operation aborted")
            yield _cast_response("probe", {})
            return
        yield _text_response("Parent done.")

    async def complete(
        self,
        model: Model,
        messages: list[dict[str, Any]],
        config: ChannelConfig,
        signal: AbortSignal | None = None,
    ) -> RealmResponse:
        return _text_response("complete")


class _SummonSpell(MvgeSpell):
    """Test-local ``summon_mvge`` spell: the bridge's wiring, no more.

    Forwards the dispatcher's ``ctx.signal`` as the manager's
    ``parent_signal`` exactly as the submvge-bridge rune's
    ``handle_summon`` does, and opts out of the dispatcher wall clock
    (``timeout_ms = 0``) exactly as that rune does for wait:true.
    """

    def __init__(self, manager: SubMvgeManager) -> None:
        super().__init__(
            name="summon_mvge",
            description="Summon a child sub-Mvge.",
            parameters={
                "type": "object",
                "properties": {
                    "subagents": {"type": "array"},
                    "wait": {"type": "boolean"},
                },
                "required": ["subagents"],
            },
            timeout_ms=0,
        )
        self._manager = manager

    async def execute(
        self,
        spell_cast_id: str,
        params: dict[str, Any],
        signal: AbortSignal | None = None,
        on_update: Any = None,
        ctx: SpellContext | None = None,
    ) -> dict[str, Any]:
        specs: list[SubMvgeSpec] = []
        for sub in params.get("subagents", []):
            allowed = sub.get("allowed_spells")
            specs.append(
                SubMvgeSpec(
                    role=str(sub.get("role", "worker")),
                    prompt=str(sub.get("prompt", "")),
                    model=str(sub.get("model", "inherit")),
                    allowed_spells=frozenset(allowed) if allowed else None,
                    wait=bool(params.get("wait", True)),
                )
            )
        parent_signal = getattr(ctx, "signal", None) if ctx is not None else None
        records = await self._manager.summon(
            specs,
            parent_signal=parent_signal,
            parent_spell_cast_id=spell_cast_id,
        )
        return {
            "success": True,
            "subagents": [
                {"sub_mvge_id": rec.sub_mvge_id, "status": rec.state.value}
                for rec in records
            ],
        }


class _ProbeSpell(MvgeSpell):
    """Counts any post-summon cast: a resumed parent turn would cast it."""

    def __init__(self, probes: list[str]) -> None:
        super().__init__(
            name="probe",
            description="Probe for post-interrupt parent activity.",
            parameters={"type": "object", "properties": {}},
        )
        self._probes = probes

    async def execute(
        self,
        spell_cast_id: str,
        params: dict[str, Any],
        signal: AbortSignal | None = None,
        on_update: Any = None,
        ctx: SpellContext | None = None,
    ) -> SpellResult:
        self._probes.append(spell_cast_id)
        return SpellResult(
            spell_name="probe", status=SpellStatus.SUCCESS, content="probed"
        )


@dataclass
class _Case:
    workspace: Path
    marker: Path
    started: Path
    manager: SubMvgeManager
    parent: Mvge
    turn: asyncio.Task[Any]
    sub_id: str
    tome_path: Path
    probes: list[str]
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def teardown(self) -> None:
        if not self.turn.done():
            self.turn.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.turn
        with contextlib.suppress(Exception):
            await self.manager.kill_all()
        with contextlib.suppress(Exception):
            await self.parent.close()


async def _start_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, wait: bool
) -> _Case:
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(tmp_path / "home"))
    monkeypatch.setenv("MVGE_API_KEY", "test-key")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    workspace = tmp_path / "workspace"
    spells_dir = workspace / "spells"
    spells_dir.mkdir(parents=True, exist_ok=True)
    (spells_dir / "__init__.py").write_text(BASH_SPELLS_INIT)
    sessions = tmp_path / "sessions"
    subsessions = sessions / "subsessions"
    subsessions.mkdir(parents=True, exist_ok=True)
    marker = workspace / "marker.txt"
    started = workspace / "child_started.txt"
    release = asyncio.Event()
    probes: list[str] = []
    registry = RealmRegistry()
    fake = _FakeRealm(
        release=release,
        probes=probes,
        marker=marker,
        started=started,
        wait=wait,
    )

    def _factory(api_key: str = "", base_url: str = "", **kwargs: Any) -> Realm:
        return fake

    registry.register_realm_factory("fake", _factory)
    manager = SubMvgeManager(
        parent_workspace=workspace,
        parent_agent_name="cascade_parent",
        parent_model_id=PARENT_MODEL,
        shared_realm_registry=registry,
        subsessions_dir=subsessions,
    )
    parent = Mvge(
        name="cascade_parent",
        caller_dir=workspace,
        tome_dir=sessions,
        model_id=PARENT_MODEL,
        spells=[_SummonSpell(manager), _ProbeSpell(probes)],
        api_key="test-key",
        shared_realm_registry=registry,
    )
    await parent.initialize()
    turn = asyncio.create_task(parent.run("Summon a worker."))
    sub_id = ""
    tome_path = Path()
    for _ in range(300):
        if started.exists() and manager.list_active():
            record = manager.list_active()[0]
            if record.state == SubMvgeState.RUNNING:
                sub_id = record.sub_mvge_id
                tome_path = record.tome_path
                break
        await asyncio.sleep(0.05)
    assert sub_id, "child never started"
    for _ in range(200):
        if tome_path.exists() and tome_path.stat().st_size > 0:
            break
        await asyncio.sleep(0.05)
    assert tome_path.exists(), "child tome never materialized before the trigger"
    assert tome_path.stat().st_size > 0, "child tome empty before the trigger"
    return _Case(
        workspace=workspace,
        marker=marker,
        started=started,
        manager=manager,
        parent=parent,
        turn=turn,
        sub_id=sub_id,
        tome_path=tome_path,
        probes=probes,
        release=release,
    )


def _record(case: _Case) -> SubMvgeRecord:
    for record in case.manager.list_active():
        if record.sub_mvge_id == case.sub_id:
            return record
    raise AssertionError(f"no record for {case.sub_id}")


def _tome_size(case: _Case) -> int:
    if case.tome_path.exists():
        return case.tome_path.stat().st_size
    return 0


async def _await_state(
    case: _Case, state: SubMvgeState, timeout: float = SETTLE_SECONDS
) -> SubMvgeRecord:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        record = _record(case)
        if record.state == state:
            return record
        if loop.time() >= deadline:
            raise AssertionError(
                f"child record stayed {record.state.value}, "
                f"expected {state.value} within {timeout}s"
            )
        await asyncio.sleep(0.05)


async def _await_parent_end(case: _Case, timeout: float) -> str:
    try:
        await asyncio.wait_for(case.turn, timeout=timeout)
        return "returned"
    except asyncio.CancelledError:
        return "cancelled"
    except AbortError:
        return "aborted"


async def _assert_cascade(case: _Case, *, trigger: str) -> None:
    """Shared P1-P3 verdict for one trigger shape: CANCELING within 10s,
    parent turn ended within 10s, no post-interrupt cast, marker absent
    and child tome frozen between trigger+10s and trigger+60s."""
    loop = asyncio.get_running_loop()
    started_at = loop.time()
    if trigger == "signal":
        case.parent.abort()
    elif trigger == "cancel":
        case.turn.cancel()
    elif trigger == "abort_then_cancel":
        case.parent.abort()
        case.turn.cancel()
    else:  # pragma: no cover - trigger names are test-controlled
        raise ValueError(f"unknown trigger {trigger}")
    record = await _await_state(case, SubMvgeState.CANCELING)
    assert record.state == SubMvgeState.CANCELING
    remaining = SETTLE_SECONDS - (loop.time() - started_at)
    outcome = await _await_parent_end(case, timeout=max(0.0, remaining))
    assert outcome in ("returned", "cancelled", "aborted")
    assert case.probes == [], "parent turn cast after the interrupt"
    while loop.time() < started_at + SETTLE_SECONDS:
        await asyncio.sleep(0.2)
    size_at_10s = _tome_size(case)
    assert size_at_10s > 0, "child tome empty at trigger+10s"
    while loop.time() < started_at + WATCH_SECONDS:
        await asyncio.sleep(0.5)
    size_at_60s = _tome_size(case)
    assert size_at_10s == size_at_60s, (
        f"child tome grew after the interrupt: {size_at_10s} -> {size_at_60s}"
    )
    assert _record(case).state == SubMvgeState.CANCELING, (
        "child record left CANCELING after the interrupt"
    )
    assert not case.marker.exists(), "marker appeared: child sleep ran to completion"


@pytest.mark.asyncio
async def test_signal_abort_wait_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = await _start_case(tmp_path, monkeypatch, wait=True)
    try:
        await _assert_cascade(case, trigger="signal")
    finally:
        await case.teardown()


@pytest.mark.asyncio
async def test_signal_abort_wait_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = await _start_case(tmp_path, monkeypatch, wait=False)
    try:
        await _assert_cascade(case, trigger="signal")
    finally:
        await case.teardown()


@pytest.mark.asyncio
async def test_task_cancel_wait_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = await _start_case(tmp_path, monkeypatch, wait=True)
    try:
        await _assert_cascade(case, trigger="cancel")
    finally:
        await case.teardown()


@pytest.mark.asyncio
async def test_ctrl_c_shape_abort_then_cancel_wait_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The production Ctrl-C shape for a wait:false child: abort first,
    then cancel the turn task, as REPL/TUI now do. This replaces the
    plan's cancel-alone wait:false variant, which failed pre-fix with
    the orphan (child stayed running, then completed with the marker
    present): cancel-alone is not a production interrupt path once the
    handlers abort first, so the verdict test exercises the shape the
    handlers actually produce."""
    case = await _start_case(tmp_path, monkeypatch, wait=False)
    try:
        await _assert_cascade(case, trigger="abort_then_cancel")
    finally:
        await case.teardown()


@pytest.mark.asyncio
async def test_follow_up_after_abort_runs_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = await _start_case(tmp_path, monkeypatch, wait=False)
    try:
        case.parent.abort()
        await _await_state(case, SubMvgeState.CANCELING)
        await _await_parent_end(case, timeout=SETTLE_SECONDS)
        record = await case.manager.send_message(case.sub_id, "Try again")
        assert record.state == SubMvgeState.COMPLETED
        assert record.summary == "Follow-up complete."
        assert not case.marker.exists()
    finally:
        await case.teardown()


@pytest.mark.asyncio
async def test_kill_still_terminates_running_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = await _start_case(tmp_path, monkeypatch, wait=False)
    try:
        records = await case.manager.kill([case.sub_id])
        assert len(records) == 1
        assert records[0].state == SubMvgeState.TERMINATED
        await asyncio.sleep(3.0)
        assert not case.marker.exists()
    finally:
        await case.teardown()
