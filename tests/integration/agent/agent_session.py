from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest

from mvge.agent.agent_session import MvgeTome
from mvge.core.errors import TomeResumeError
from mvge.runes.rune_runner import RuneRunner
from mvge.runes.types import SigilHook
from mvge.tome.handle import TomeHandle, TomeHandleFactory


@pytest.fixture
def tome_factory() -> TomeHandleFactory:
    with tempfile.TemporaryDirectory() as tmp:
        yield TomeHandleFactory(Path(tmp))


@pytest.fixture
def agent_tome(tome_factory: TomeHandleFactory) -> MvgeTome:
    write = tome_factory.create_tome("/test")
    read = tome_factory.open_read(write.tome_id)
    return MvgeTome(tome_factory, write, read)


@pytest.fixture
def runner() -> RuneRunner:
    return RuneRunner()


@pytest.mark.asyncio
async def test_start_emits_session_start(agent_tome: MvgeTome) -> None:
    runner = RuneRunner()
    agent_tome.bind_runner(runner)
    from unittest.mock import MagicMock

    handler = MagicMock()
    runner.register_handler(SigilHook.SESSION_START, handler)
    await agent_tome.start(reason="startup")
    handler.assert_called_once()
    call_data = handler.call_args[0][0]
    assert call_data.session_name == agent_tome.tome_id


@pytest.mark.asyncio
async def test_start_idempotent(agent_tome: MvgeTome) -> None:
    from unittest.mock import MagicMock

    runner = RuneRunner()
    agent_tome.bind_runner(runner)
    handler = MagicMock()
    runner.register_handler(SigilHook.SESSION_START, handler)
    await agent_tome.start(reason="startup")
    await agent_tome.start(reason="startup")
    handler.assert_called_once()


@pytest.mark.asyncio
async def test_shutdown_emits_session_shutdown(agent_tome: MvgeTome) -> None:
    from unittest.mock import MagicMock

    runner = RuneRunner()
    agent_tome.bind_runner(runner)
    handler = MagicMock()
    runner.register_handler(SigilHook.SESSION_SHUTDOWN, handler)
    await agent_tome.start(reason="startup")
    await agent_tome.shutdown(reason="quit")
    handler.assert_called_once()


@pytest.mark.asyncio
async def test_shutdown_with_target_file(agent_tome: MvgeTome) -> None:
    from unittest.mock import MagicMock

    runner = RuneRunner()
    agent_tome.bind_runner(runner)
    handler = MagicMock()
    runner.register_handler(SigilHook.SESSION_SHUTDOWN, handler)
    await agent_tome.start(reason="startup")
    await agent_tome.shutdown(reason="resume", target_session_file="/path/to/new")
    call_data = handler.call_args[0][0]
    assert call_data.session_name == agent_tome.tome_id


@pytest.mark.asyncio
async def test_shutdown_noop_if_not_started(agent_tome: MvgeTome) -> None:
    from unittest.mock import MagicMock

    runner = RuneRunner()
    agent_tome.bind_runner(runner)
    handler = MagicMock()
    runner.register_handler(SigilHook.SESSION_SHUTDOWN, handler)
    await agent_tome.shutdown(reason="quit")
    handler.assert_not_called()


@pytest.mark.asyncio
async def test_before_switch_cancellable(agent_tome: MvgeTome) -> None:
    runner = RuneRunner()
    agent_tome.bind_runner(runner)

    def canceller(data: dict) -> dict:
        return {"cancel": True}

    runner.register_handler(SigilHook.SESSION_BEFORE_SWITCH, canceller)
    result = await agent_tome.before_switch("/path/to/target")
    assert result is not None
    assert result["cancelled"]


@pytest.mark.asyncio
async def test_before_switch_not_cancelled(agent_tome: MvgeTome) -> None:
    runner = RuneRunner()
    agent_tome.bind_runner(runner)
    result = await agent_tome.before_switch("/path/to/target")
    assert result is not None
    assert not result["cancelled"]


@pytest.mark.asyncio
async def test_before_fork_cancellable(agent_tome: MvgeTome) -> None:
    runner = RuneRunner()
    agent_tome.bind_runner(runner)

    def canceller(data: dict) -> dict:
        return {"cancel": True}

    runner.register_handler(SigilHook.SESSION_BEFORE_FORK, canceller)
    result = await agent_tome.before_fork("entry-123")
    assert result is not None
    assert result["cancelled"]


@pytest.mark.asyncio
async def test_properties(agent_tome: MvgeTome) -> None:
    assert agent_tome.tome_id == agent_tome.metadata.id
    assert agent_tome.metadata.cwd == "/test"


@pytest.mark.asyncio
async def test_bind_runner(agent_tome: MvgeTome) -> None:
    runner = RuneRunner()
    agent_tome.bind_runner(runner)
    assert agent_tome._rune_runner is runner


@pytest.mark.asyncio
async def test_record_message_not_started(agent_tome: MvgeTome) -> None:
    result = agent_tome.record_message("user", "hello")
    assert result is None


@pytest.mark.asyncio
async def test_record_custom_not_started(agent_tome: MvgeTome) -> None:
    result = agent_tome.record_custom("test_type", {"key": "value"})
    assert result is None


@pytest.mark.asyncio
async def test_record_message_started(agent_tome: MvgeTome) -> None:
    runner = RuneRunner()
    agent_tome.bind_runner(runner)
    await agent_tome.start(reason="startup")

    result = agent_tome.record_message("user", "hello", model="test-model")
    assert result is not None
    assert result.type.value == "message"
    assert result.payload.get("role") == "user"
    assert result.payload.get("content") == "hello"


@pytest.mark.asyncio
async def test_record_custom_started(agent_tome: MvgeTome) -> None:
    runner = RuneRunner()
    agent_tome.bind_runner(runner)
    await agent_tome.start(reason="startup")

    result = agent_tome.record_custom("test_type", {"key": "value"})
    assert result is not None
    assert result.type.value == "custom"
    assert result.payload.get("type") == "test_type"


@pytest.mark.asyncio
async def test_safe_emit_no_runner(agent_tome: MvgeTome) -> None:
    # Should not raise when runner is None
    await agent_tome._safe_emit(SigilHook.SESSION_START, {"test": "data"})


@pytest.mark.asyncio
async def test_safe_emit_first_no_runner(agent_tome: MvgeTome) -> None:
    # Should return None when runner is None
    result = await agent_tome._safe_emit_first(
        SigilHook.SESSION_START, {"test": "data"}
    )
    assert result is None


def _record_shutdowns(rune_runner: RuneRunner) -> list[SigilHook]:
    seen: list[SigilHook] = []

    def recorder(data: dict) -> dict:
        seen.append(SigilHook.SESSION_SHUTDOWN)
        return {}

    rune_runner.register_handler(SigilHook.SESSION_SHUTDOWN, recorder)
    return seen


def _assert_no_shutdown(seen: list[SigilHook]) -> None:
    assert SigilHook.SESSION_SHUTDOWN not in seen


@pytest.mark.asyncio
async def test_factory_create_starts_session(
    tome_factory: TomeHandleFactory, runner: RuneRunner
) -> None:
    starts: list[Any] = []
    runner.register_handler(SigilHook.SESSION_START, lambda d: starts.append(d))

    tome = await MvgeTome.create(tome_factory, cwd="/custom/dir", runner=runner)

    assert isinstance(tome, MvgeTome)
    assert tome.metadata.cwd == "/custom/dir"
    assert len(starts) == 1
    assert starts[0].session_name == tome.tome_id


@pytest.mark.asyncio
async def test_factory_open_resumes_session(
    tome_factory: TomeHandleFactory, runner: RuneRunner, agent_tome: MvgeTome
) -> None:
    starts: list[Any] = []
    runner.register_handler(SigilHook.SESSION_START, lambda d: starts.append(d))

    tome = await MvgeTome.open(tome_factory, agent_tome.tome_id, runner=runner)

    assert isinstance(tome, MvgeTome)
    assert tome.tome_id == agent_tome.tome_id
    assert len(starts) == 1
    assert starts[0].session_name == agent_tome.tome_id


@pytest.mark.asyncio
async def test_factory_open_missing_raises(
    tome_factory: TomeHandleFactory, runner: RuneRunner
) -> None:
    with pytest.raises(TomeResumeError):
        await MvgeTome.open(tome_factory, "nonexistent", runner=runner)


@pytest.mark.asyncio
async def test_factory_open_or_create(
    tome_factory: TomeHandleFactory, runner: RuneRunner, agent_tome: MvgeTome
) -> None:
    tome1 = await MvgeTome.open_or_create(tome_factory, cwd="/proj", runner=runner)
    assert tome1.metadata.cwd == "/proj"

    tome2 = await MvgeTome.open_or_create(
        tome_factory, tome_resume=agent_tome.tome_id, runner=runner
    )
    assert tome2.tome_id == agent_tome.tome_id


@pytest.mark.asyncio
async def test_switch_cancelled(
    runner: RuneRunner, tome_factory: TomeHandleFactory, agent_tome: MvgeTome
) -> None:
    def canceller(data: dict) -> dict:
        return {"cancel": True}

    runner.register_handler(SigilHook.SESSION_BEFORE_SWITCH, canceller)
    agent_tome.bind_runner(runner)
    await agent_tome.start(reason="startup")
    seen = _record_shutdowns(runner)

    result = await agent_tome.switch(Path("/path/to/target.jsonl"))
    assert result is None
    _assert_no_shutdown(seen)


@pytest.mark.asyncio
async def test_switch_invalid_tome_id(
    runner: RuneRunner, tome_factory: TomeHandleFactory, agent_tome: MvgeTome
) -> None:
    agent_tome.bind_runner(runner)
    await agent_tome.start(reason="startup")
    seen = _record_shutdowns(runner)

    result = await agent_tome.switch(Path("/path/to/invalid.jsonl"))
    assert result is None
    _assert_no_shutdown(seen)


@pytest.mark.asyncio
async def test_switch_tome_not_found(
    runner: RuneRunner, tome_factory: TomeHandleFactory, agent_tome: MvgeTome
) -> None:
    agent_tome.bind_runner(runner)
    await agent_tome.start(reason="startup")
    seen = _record_shutdowns(runner)

    target_file = Path("/path/to") / f"{'a' * 32}.jsonl"
    result = await agent_tome.switch(target_file)
    assert result is None
    _assert_no_shutdown(seen)


@pytest.mark.asyncio
async def test_switch_success(
    tome_factory: TomeHandleFactory, runner: RuneRunner, agent_tome: MvgeTome
) -> None:
    agent_tome.bind_runner(runner)
    await agent_tome.start(reason="startup")

    target_write = tome_factory.create_tome("/test2")
    target_file = tome_factory.tome_file(target_write.tome_id)

    result = await agent_tome.switch(target_file)
    assert result is not None
    assert result.tome_id == target_write.tome_id


@pytest.mark.asyncio
async def test_fork_cancelled(
    tome_factory: TomeHandleFactory, runner: RuneRunner, agent_tome: MvgeTome
) -> None:
    def canceller(data: dict) -> dict:
        return {"cancel": True}

    runner.register_handler(SigilHook.SESSION_BEFORE_FORK, canceller)
    agent_tome.bind_runner(runner)
    await agent_tome.start(reason="startup")
    seen = _record_shutdowns(runner)

    agent_tome.record_message("user", "test")

    result = await agent_tome.fork()
    assert result is None
    _assert_no_shutdown(seen)


@pytest.mark.asyncio
async def test_fork_missing_backing_file_keeps_source_running(
    tome_factory: TomeHandleFactory, runner: RuneRunner
) -> None:
    ghost_id = "f" * 32
    write = TomeHandle(tome_factory.dir, ghost_id, "w")
    read = TomeHandle(tome_factory.dir, ghost_id, "r")
    source = MvgeTome(tome_factory, write, read, runner)
    await source.start(reason="startup")
    seen = _record_shutdowns(runner)

    result = await source.fork("entry-1")
    assert result is None
    _assert_no_shutdown(seen)


@pytest.mark.asyncio
async def test_fork_success(
    tome_factory: TomeHandleFactory, runner: RuneRunner, agent_tome: MvgeTome
) -> None:
    agent_tome.bind_runner(runner)
    await agent_tome.start(reason="startup")

    entry = agent_tome.record_message("user", "test")
    assert entry is not None

    result = await agent_tome.fork(entry.id)
    assert result is not None
    assert result.tome_id != agent_tome.tome_id

    branched_entries = tome_factory.get_entries(result.tome_id)
    assert any(e.id == entry.id for e in branched_entries)


@pytest.mark.asyncio
async def test_fork_invalid_entry_creates_branched(
    tome_factory: TomeHandleFactory, runner: RuneRunner, agent_tome: MvgeTome
) -> None:
    agent_tome.bind_runner(runner)
    await agent_tome.start(reason="startup")

    result = await agent_tome.fork("nonexistent-entry")
    assert result is not None
    assert result.tome_id != agent_tome.tome_id


@pytest.mark.asyncio
async def test_mvge_tome_create_initializes_valid_tome() -> None:
    with tempfile.TemporaryDirectory() as direct_dir:
        factory = TomeHandleFactory(Path(direct_dir))
        tome = await MvgeTome.create(factory)

        assert tome.metadata.cwd == str(Path.cwd())
        assert tome.metadata.parent_tome_id is None
        assert tome.record_custom("probe", {}) is not None


@pytest.mark.asyncio
async def test_session_resume_with_matching_configuration_persisted_to_disk(
    tome_factory: TomeHandleFactory, runner: RuneRunner
) -> None:
    tome1 = await MvgeTome.create(
        tome_factory,
        cwd="/workspace",
        runner=runner,
        model="model-v1",
        contemplation_level="medium",
        spells=["read", "write"],
    )
    tome1.record_message("user", "first question")
    await tome1.shutdown(reason="quit")

    tome2 = await MvgeTome.open(
        tome_factory,
        tome1.tome_id,
        runner=runner,
        expected_model="model-v1",
        expected_contemplation="medium",
        expected_spells=["read", "write", "bash"],
        strict=True,
    )

    assert tome2.compatibility_report is not None
    assert tome2.compatibility_report.compatible
    assert tome2.tome_id == tome1.tome_id


@pytest.mark.asyncio
async def test_session_resume_strict_raises_tome_incompatible_on_mismatched_model(
    tome_factory: TomeHandleFactory, runner: RuneRunner
) -> None:
    from mvge.core.errors import TomeIncompatibleError

    tome1 = await MvgeTome.create(
        tome_factory,
        cwd="/workspace",
        runner=runner,
        model="claude-3-opus",
        contemplation_level="high",
        spells=["read", "write"],
    )
    await tome1.shutdown(reason="quit")

    with pytest.raises(TomeIncompatibleError) as exc_info:
        await MvgeTome.open(
            tome_factory,
            tome1.tome_id,
            runner=runner,
            expected_model="gpt-4o",
            expected_contemplation="high",
            expected_spells=["read", "write"],
            strict=True,
        )

    assert exc_info.value.tome_id == tome1.tome_id
    assert exc_info.value.model_mismatch == ("claude-3-opus", "gpt-4o")


@pytest.mark.asyncio
async def test_session_resume_force_fork_preserves_history_with_updated_metadata(
    tome_factory: TomeHandleFactory, runner: RuneRunner
) -> None:
    tome1 = await MvgeTome.create(
        tome_factory,
        cwd="/workspace",
        runner=runner,
        model="old-model",
        contemplation_level="low",
        spells=["deprecated_spell"],
    )
    e1 = tome1.record_message("user", "historic prompt")
    assert e1 is not None
    await tome1.shutdown(reason="quit")

    forked_tome = await MvgeTome.open(
        tome_factory,
        tome1.tome_id,
        runner=runner,
        expected_model="new-model",
        expected_contemplation="high",
        expected_spells=["new_spell"],
        force_fork=True,
    )

    assert forked_tome.tome_id != tome1.tome_id
    assert forked_tome.metadata.parent_tome_id == tome1.tome_id
    assert forked_tome.metadata.model == "new-model"
    assert forked_tome.metadata.contemplation_level == "high"
    assert forked_tome.metadata.spells == ["new_spell"]

    entries = tome_factory.get_entries(forked_tome.tome_id)
    assert any(e.id == e1.id for e in entries)


@pytest.mark.asyncio
async def test_session_resume_unconstrained_legacy_session_resumes_cleanly(
    tome_factory: TomeHandleFactory, runner: RuneRunner
) -> None:
    # Unconstrained sessions resume cleanly without errors or warnings
    legacy_write = tome_factory.create_tome("/workspace")
    legacy_meta = tome_factory.open_tome(legacy_write.tome_id)
    assert legacy_meta is not None
    assert legacy_meta.model is None
    assert legacy_meta.spells == []

    tome = await MvgeTome.open(
        tome_factory,
        legacy_write.tome_id,
        runner=runner,
        expected_model="any-model",
        expected_spells=["any_spell"],
        strict=True,
    )

    assert tome.compatibility_report is not None
    assert tome.compatibility_report.compatible
    assert len(tome.compatibility_report.diagnostics) == 0
