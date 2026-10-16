import asyncio
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from mvgeos_core.channel import (
    MvgeResponse,
    StopReason,
)
from mvgeos_core.errors import (
    TomeIncompatibleError,
    TomeResumeError,
)
from mvgeos_core.events import ContentType
from mvgeos_core.invocations import SummonerRequest
from mvgeos_core.spells import SpellResultMessage
from mvgeos_runes.rune_runner import RuneRunner
from mvgeos_runes.types import (
    Diagnostic,
    DiagnosticKind,
    SessionStartData,
    SigilHook,
)
from mvgeos_tome.handle import TomeHandleFactory
from mvgeos_tome.types import (
    TomeEntry,
    TomeEntryType,
    TomeMetadata,
    TomeVersionError,
)

from mvgeos_agent.agent_session import MvgeTome, _serialise_invocation
from mvgeos_agent.compatibility import SessionCompatibilityReport


def _temp_tome(tmp: str) -> tuple[MvgeTome, TomeHandleFactory]:
    factory = TomeHandleFactory(Path(tmp))
    write = factory.create_tome("/tmp")
    read = factory.open_read(write.tome_id)
    tome = MvgeTome(factory, write, read)
    tome._started = True
    return tome, factory


def _metadata(tome_id: str = "a" * 32, cwd: str = "/test") -> TomeMetadata:
    return TomeMetadata(id=tome_id, created_at="now", cwd=cwd)


def _mock_runner() -> MagicMock:
    runner = MagicMock(spec=RuneRunner)
    runner.emit_async = AsyncMock()
    runner.emit_first = AsyncMock(return_value=None)
    return runner


def _mock_handles(tome_id: str = "a" * 32) -> tuple[MagicMock, MagicMock]:
    write = MagicMock()
    write.tome_id = tome_id
    read = MagicMock()
    return write, read


def _started_source(factory: MagicMock, runner: MagicMock) -> MvgeTome:
    write, read = _mock_handles()
    read.get_metadata.return_value = _metadata(cwd="/test")
    source = MvgeTome(factory, write, read, runner)
    return source


async def _emitted(runner: MagicMock, hook: SigilHook) -> list[Any]:
    return [c.args[1] for c in runner.emit_async.await_args_list if c.args[0] == hook]


def _track(runner: MagicMock) -> list[SigilHook]:
    order: list[SigilHook] = []

    async def _record(hook: SigilHook, data: dict) -> None:
        order.append(hook)

    runner.emit_first = AsyncMock(side_effect=_record)
    runner.emit_async = AsyncMock(side_effect=_record)
    return order


async def _assert_source_still_running(runner: MagicMock, source: MvgeTome) -> None:
    shutdowns = await _emitted(runner, SigilHook.SESSION_SHUTDOWN)
    assert shutdowns == []
    assert source.record_message("user", "hi") is not None


class TestFactories:
    @pytest.mark.asyncio
    async def test_create_creates_new_tome(self) -> None:
        factory = MagicMock()
        write, _read = _mock_handles(_metadata().id)
        factory.create_tome.return_value = write
        factory.open_read.return_value = _read
        runner = _mock_runner()

        tome = await MvgeTome.create(factory, cwd="/proj", runner=runner)

        factory.create_tome.assert_called_once_with(
            "/proj", model=None, contemplation_level=None, spells=None
        )
        assert isinstance(tome, MvgeTome)
        assert tome.tome_id == _metadata().id
        starts = await _emitted(runner, SigilHook.SESSION_START)
        assert starts == [SessionStartData(session_name=_metadata().id)]

    @pytest.mark.asyncio
    async def test_create_passes_session_config(self) -> None:
        factory = MagicMock()
        write, read = _mock_handles(_metadata().id)
        factory.create_tome.return_value = write
        factory.open_read.return_value = read

        await MvgeTome.create(
            factory,
            cwd="/proj",
            model="test-model",
            contemplation_level="high",
            spells=["spell1"],
        )

        factory.create_tome.assert_called_once_with(
            "/proj",
            model="test-model",
            contemplation_level="high",
            spells=["spell1"],
        )

    @pytest.mark.asyncio
    async def test_create_default_cwd_is_process_cwd(self) -> None:
        factory = MagicMock()
        write, read = _mock_handles(_metadata().id)
        factory.create_tome.return_value = write
        factory.open_read.return_value = read

        await MvgeTome.create(factory)

        factory.create_tome.assert_called_once_with(
            str(Path.cwd()), model=None, contemplation_level=None, spells=None
        )

    @pytest.mark.asyncio
    async def test_open_resumes_existing_tome(self) -> None:
        resumed = _metadata("b" * 32)
        factory = MagicMock()
        write, read = _mock_handles(resumed.id)
        read.get_metadata.return_value = resumed
        read.get_entries.return_value = []
        factory.open_read.return_value = read
        factory.open_write.return_value = write
        runner = _mock_runner()

        tome = await MvgeTome.open(factory, "b" * 32, runner=runner)

        factory.open_read.assert_called_once_with("b" * 32)
        factory.create_tome.assert_not_called()
        assert tome.metadata is resumed
        starts = await _emitted(runner, SigilHook.SESSION_START)
        assert len(starts) == 1
        assert starts[0].session_name == resumed.id

    @pytest.mark.asyncio
    async def test_open_repairs_torn_tail_before_resume(self) -> None:
        resumed = _metadata("b" * 32)
        factory = MagicMock()
        write, read = _mock_handles(resumed.id)
        read.get_metadata.return_value = resumed
        read.get_entries.return_value = []
        factory.open_read.return_value = read
        factory.open_write.return_value = write
        runner = _mock_runner()

        await MvgeTome.open(factory, "b" * 32, runner=runner)

        write.repair_torn_tail.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_open_missing_target_raises(self) -> None:
        factory = MagicMock()
        factory.open_read.side_effect = FileNotFoundError("missing-tome")

        with pytest.raises(TomeResumeError, match="missing-tome"):
            await MvgeTome.open(factory, "missing-tome")
        factory.create_tome.assert_not_called()

    @pytest.mark.asyncio
    async def test_open_unreadable_header_raises(self) -> None:
        factory = MagicMock()
        read = MagicMock()
        read.get_metadata.return_value = None
        factory.open_read.return_value = read

        with pytest.raises(TomeResumeError, match="ghost-tome"):
            await MvgeTome.open(factory, "ghost-tome")
        factory.create_tome.assert_not_called()

    @pytest.mark.asyncio
    async def test_open_with_explicit_entries_skips_read(self) -> None:
        resumed = _metadata("b" * 32)
        factory = MagicMock()
        write, read = _mock_handles(resumed.id)
        read.get_metadata.return_value = resumed
        factory.open_read.return_value = read
        factory.open_write.return_value = write
        given: list = []

        tome = await MvgeTome.open(factory, "b" * 32, entries=given)

        read.get_entries.assert_not_called()
        assert tome.metadata is resumed

    @pytest.mark.asyncio
    async def test_open_or_create_without_resume(self) -> None:
        factory = MagicMock()
        write, read = _mock_handles(_metadata().id)
        factory.create_tome.return_value = write
        factory.open_read.return_value = read
        runner = _mock_runner()

        tome = await MvgeTome.open_or_create(factory, cwd="/proj", runner=runner)

        factory.create_tome.assert_called_once_with(
            "/proj", model=None, contemplation_level=None, spells=None
        )
        assert tome.tome_id == _metadata().id

    @pytest.mark.asyncio
    async def test_open_or_create_with_resume(self) -> None:
        resumed = _metadata("b" * 32)
        factory = MagicMock()
        write, read = _mock_handles(resumed.id)
        read.get_metadata.return_value = resumed
        read.get_entries.return_value = []
        factory.open_read.return_value = read
        factory.open_write.return_value = write
        runner = _mock_runner()

        tome = await MvgeTome.open_or_create(
            factory, tome_resume="b" * 32, runner=runner
        )

        factory.open_read.assert_called_with("b" * 32)
        assert tome.metadata is resumed


class TestFork:
    @pytest.mark.asyncio
    async def test_success_starts_branched_tome(self) -> None:
        source_meta = _metadata(cwd="/test")
        branched_id = "c" * 32
        factory = MagicMock()
        forked_write, forked_read = _mock_handles(branched_id)
        forked_read.get_metadata.return_value = _metadata(branched_id, cwd="/test")
        factory.create_branched_tome.return_value = forked_write
        factory.open_read.return_value = forked_read
        runner = _mock_runner()
        source = _started_source(factory, runner)
        await source.start(reason="startup")

        result = await source.fork("entry-1")

        factory.create_branched_tome.assert_called_once_with(
            parent_tome_id=source_meta.id,
            cwd="/test",
            fork_from_leaf_id="entry-1",
        )
        assert result is not None
        assert result.tome_id == branched_id
        forks = await _emitted(runner, SigilHook.SESSION_SHUTDOWN)
        assert forks[-1].session_name == source_meta.id
        starts = await _emitted(runner, SigilHook.SESSION_START)
        assert starts[-1].session_name == branched_id

    @pytest.mark.asyncio
    async def test_success_hook_order(self) -> None:
        factory = MagicMock()
        forked_write, forked_read = _mock_handles("c" * 32)
        factory.create_branched_tome.return_value = forked_write
        factory.open_read.return_value = forked_read
        runner = _mock_runner()
        source = _started_source(factory, runner)
        await source.start(reason="startup")
        order = _track(runner)

        result = await source.fork("entry-1")

        assert result is not None
        assert order == [
            SigilHook.SESSION_BEFORE_FORK,
            SigilHook.SESSION_SHUTDOWN,
            SigilHook.SESSION_START,
        ]

    @pytest.mark.asyncio
    async def test_cancelled_by_sigil_returns_none(self) -> None:
        factory = MagicMock()
        runner = _mock_runner()
        runner.emit_first.return_value = {"cancel": True}
        source = _started_source(factory, runner)
        await source.start(reason="startup")

        result = await source.fork("entry-1")

        assert result is None
        factory.create_branched_tome.assert_not_called()

    @pytest.mark.asyncio
    async def test_factory_error_returns_none(self) -> None:
        factory = MagicMock()
        factory.create_branched_tome.side_effect = ValueError("boom")
        runner = _mock_runner()
        source = _started_source(factory, runner)
        await source.start(reason="startup")

        result = await source.fork("entry-1")

        assert result is None
        await _assert_source_still_running(runner, source)


class TestSwitch:
    @pytest.mark.asyncio
    async def test_success_resumes_target_tome(self) -> None:
        target_meta = _metadata("d" * 32)
        factory = MagicMock()
        target_write, target_read = _mock_handles(target_meta.id)
        target_read.get_metadata.return_value = target_meta
        factory.open_read.return_value = target_read
        factory.open_write.return_value = target_write
        runner = _mock_runner()
        source = _started_source(factory, runner)
        await source.start(reason="startup")
        target_file = Path("/tomes") / f"{'d' * 32}.jsonl"

        result = await source.switch(target_file)

        factory.open_read.assert_called_once_with("d" * 32)
        assert result is not None
        assert result.tome_id == target_meta.id
        shutdowns = await _emitted(runner, SigilHook.SESSION_SHUTDOWN)
        assert shutdowns[-1].session_name == source.tome_id
        starts = await _emitted(runner, SigilHook.SESSION_START)
        assert starts[-1].session_name == target_meta.id

    @pytest.mark.asyncio
    async def test_success_hook_order(self) -> None:
        target_meta = _metadata("d" * 32)
        factory = MagicMock()
        target_write, target_read = _mock_handles(target_meta.id)
        target_read.get_metadata.return_value = target_meta
        factory.open_read.return_value = target_read
        factory.open_write.return_value = target_write
        runner = _mock_runner()
        source = _started_source(factory, runner)
        await source.start(reason="startup")
        order = _track(runner)
        target_file = Path("/tomes") / f"{'d' * 32}.jsonl"

        result = await source.switch(target_file)

        assert result is not None
        assert order == [
            SigilHook.SESSION_BEFORE_SWITCH,
            SigilHook.SESSION_SHUTDOWN,
            SigilHook.SESSION_START,
        ]

    @pytest.mark.asyncio
    async def test_cancelled_by_sigil_returns_none(self) -> None:
        factory = MagicMock()
        runner = _mock_runner()
        runner.emit_first.return_value = {"cancel": True}
        source = _started_source(factory, runner)
        await source.start(reason="startup")

        result = await source.switch(Path("/t/x.jsonl"))

        assert result is None
        factory.open_read.assert_not_called()

    @pytest.mark.asyncio
    async def test_unparseable_target_file_returns_none(self) -> None:
        factory = MagicMock()
        runner = _mock_runner()
        source = _started_source(factory, runner)
        await source.start(reason="startup")

        result = await source.switch(Path("/t/not-a-tome.jsonl"))

        assert result is None
        factory.open_read.assert_not_called()
        await _assert_source_still_running(runner, source)

    @pytest.mark.asyncio
    async def test_unknown_target_tome_returns_none(self) -> None:
        factory = MagicMock()
        read = MagicMock()
        read.get_metadata.return_value = None
        factory.open_read.return_value = read
        runner = _mock_runner()
        source = _started_source(factory, runner)
        await source.start(reason="startup")

        result = await source.switch(Path("/t") / f"{'e' * 32}.jsonl")

        assert result is None
        await _assert_source_still_running(runner, source)

    @pytest.mark.asyncio
    async def test_open_target_error_returns_none(self) -> None:
        factory = MagicMock()
        factory.open_read.side_effect = ValueError("boom")
        runner = _mock_runner()
        source = _started_source(factory, runner)
        await source.start(reason="startup")

        result = await source.switch(Path("/t") / f"{'e' * 32}.jsonl")

        assert result is None
        await _assert_source_still_running(runner, source)


class TestCompatibility:
    def _mock_open(self, meta: TomeMetadata, entries: list | None = None) -> MagicMock:
        factory = MagicMock()
        write, read = _mock_handles(meta.id)
        read.get_metadata.return_value = meta
        read.get_entries.return_value = entries or []
        factory.open_read.return_value = read
        factory.open_write.return_value = write
        return factory

    @pytest.mark.asyncio
    async def test_open_strict_raises_when_incompatible(self) -> None:
        meta = TomeMetadata(
            id="a" * 32,
            created_at="now",
            cwd="/test",
            model="old-model",
            spells=["spell-1"],
        )
        factory = self._mock_open(meta)

        with pytest.raises(TomeIncompatibleError) as exc_info:
            await MvgeTome.open(
                factory,
                "a" * 32,
                expected_model="new-model",
                expected_spells=["spell-2"],
                strict=True,
            )

        assert exc_info.value.tome_id == "a" * 32
        assert exc_info.value.model_mismatch == ("old-model", "new-model")
        assert "spell-1" in exc_info.value.missing_spells

    @pytest.mark.asyncio
    async def test_open_non_strict_warns_and_populates_report(self) -> None:
        meta = TomeMetadata(
            id="a" * 32,
            created_at="now",
            cwd="/test",
            model="old-model",
            spells=["spell-1"],
        )
        factory = self._mock_open(meta)

        tome = await MvgeTome.open(
            factory,
            "a" * 32,
            expected_model="new-model",
            expected_spells=["spell-2"],
            strict=False,
        )

        assert tome.compatibility_report is not None
        assert not tome.compatibility_report.compatible
        assert tome.compatibility_report.model_mismatch == ("old-model", "new-model")
        assert "spell-1" in tome.compatibility_report.missing_spells

    @pytest.mark.asyncio
    async def test_open_force_fork_creates_branched_tome(self) -> None:
        meta = TomeMetadata(
            id="a" * 32,
            created_at="now",
            cwd="/test",
            model="old-model",
            spells=["spell-1"],
            active_leaf_id="leaf-1",
        )
        factory = self._mock_open(meta)
        forked_write, forked_read = _mock_handles("f" * 32)
        forked_read.get_metadata.return_value = TomeMetadata(
            id="f" * 32,
            created_at="now",
            cwd="/test",
            parent_tome_id="a" * 32,
            model="new-model",
            spells=["spell-2"],
        )
        factory.get_leaf_id.return_value = "leaf-1"
        factory.create_branched_tome.return_value = forked_write

        real_open_read = factory.open_read

        def _open_read(tome_id: str):
            if tome_id == "f" * 32:
                return forked_read
            return real_open_read.return_value

        factory.open_read.side_effect = _open_read

        tome = await MvgeTome.open(
            factory,
            "a" * 32,
            expected_model="new-model",
            expected_spells=["spell-2"],
            force_fork=True,
        )

        factory.create_branched_tome.assert_called_once_with(
            parent_tome_id="a" * 32,
            cwd="/test",
            fork_from_leaf_id="leaf-1",
            model="new-model",
            contemplation_level=None,
            spells=["spell-2"],
        )
        assert tome.tome_id == "f" * 32

    @pytest.mark.asyncio
    async def test_open_compatible_has_clean_report(self) -> None:
        meta = TomeMetadata(
            id="a" * 32,
            created_at="now",
            cwd="/test",
            model="same-model",
            spells=["spell-1"],
        )
        factory = self._mock_open(meta)

        tome = await MvgeTome.open(
            factory,
            "a" * 32,
            expected_model="same-model",
            expected_spells=["spell-1", "spell-2"],
            strict=True,
        )

        assert tome.compatibility_report is not None
        assert tome.compatibility_report.compatible
        assert len(tome.compatibility_report.diagnostics) == 0


class TestMigrationAndReconstruction:
    @pytest.mark.asyncio
    async def test_version_property_reflects_metadata(self) -> None:
        meta = TomeMetadata(id="a" * 32, created_at="now", cwd="/test", version=1)
        factory = MagicMock()
        write, read = _mock_handles(meta.id)
        read.get_metadata.return_value = meta
        tome = MvgeTome(factory, write, read)
        assert tome.version == 1

    @pytest.mark.asyncio
    async def test_open_future_version_raises_tome_resume_error(self) -> None:
        factory = MagicMock()
        factory.open_read.side_effect = TomeVersionError(4, "Unsupported version")

        with pytest.raises(TomeResumeError) as exc_info:
            await MvgeTome.open(factory, "future-tome")

        assert "future-tome" in str(exc_info.value)
        assert isinstance(exc_info.value.__cause__, TomeVersionError)

    @pytest.mark.asyncio
    async def test_reconstruct_invocations_various_entry_types(self) -> None:
        entries = [
            TomeEntry(
                id="e1",
                parent_id=None,
                type=TomeEntryType.MESSAGE,
                timestamp=1000.0,
                payload={"role": "user", "content": "hello agent"},
            ),
            TomeEntry(
                id="e2",
                parent_id="e1",
                type=TomeEntryType.MESSAGE,
                timestamp=1001.0,
                payload={
                    "role": "assistant",
                    "content": [{"type": "text", "text": "calling tool"}],
                },
            ),
            TomeEntry(
                id="e3",
                parent_id="e2",
                type=TomeEntryType.MESSAGE,
                timestamp=1002.0,
                payload={
                    "role": "spellResult",
                    "content": [{"type": "text", "text": "tool output"}],
                    "spell_name": "bash",
                    "spell_cast_id": "c1",
                },
            ),
            TomeEntry(
                id="e4",
                parent_id="e3",
                type=TomeEntryType.MESSAGE,
                timestamp=1003.0,
                payload={"role": "assistant", "content": "plain text response"},
            ),
        ]
        factory = MagicMock()
        factory.get_entries_for_context.return_value = entries
        factory.get_leaf_id.return_value = "e4"
        write, _read = _mock_handles()

        tome = MvgeTome(factory, write, _read)
        invocations = tome.reconstruct_invocations()

        assert len(invocations) == 4
        assert isinstance(invocations[0], SummonerRequest)
        assert invocations[0].content == "hello agent"

        assert isinstance(invocations[1], MvgeResponse)
        assert invocations[1].content == [{"type": "text", "text": "calling tool"}]

        assert isinstance(invocations[2], SpellResultMessage)
        assert invocations[2].role == "spellResult"
        assert invocations[2].spell_name == "bash"
        assert invocations[2].spell_cast_id == "c1"

        assert isinstance(invocations[3], MvgeResponse)
        assert invocations[3].content == [
            {"type": ContentType.TEXT, "text": "plain text response"}
        ]

    @pytest.mark.asyncio
    async def test_reconstruct_invocations_with_compaction(self) -> None:
        entries = [
            TomeEntry(
                id="c1",
                parent_id=None,
                type=TomeEntryType.COMPACTION,
                timestamp=1000.0,
                payload={
                    "summary": "Compacted conversation",
                    "manaBefore": 5000,
                    "retainedTail": [{"role": "user", "content": "tail question"}],
                },
            ),
        ]
        factory = MagicMock()
        factory.get_entries_for_context.return_value = entries
        factory.get_leaf_id.return_value = "c1"
        write, read = _mock_handles()

        tome = MvgeTome(factory, write, read)
        invocations = tome.reconstruct_invocations()

        assert len(invocations) == 2
        assert isinstance(invocations[0], SummonerRequest)
        assert "Compacted conversation" in invocations[0].content
        assert isinstance(invocations[1], SummonerRequest)
        assert invocations[1].content == "tail question"


class TestSetLeaf:
    def test_set_leaf_points_and_syncs_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tome, factory = _temp_tome(tmp)

            e1 = tome.record_message(role="user", content="msg 1")
            e2 = tome.record_message(role="assistant", content="msg 2")
            assert e1 is not None
            assert e2 is not None
            assert tome.active_leaf_id == e2.id

            leaf = tome.set_leaf(e1.id)

            assert leaf.payload == {"targetId": e1.id}
            assert tome.active_leaf_id == e1.id
            assert tome.metadata.active_leaf_id == e1.id


class TestMvgeTomeParentId:
    def test_record_message_defaults_parent_id_to_active_leaf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tome, _factory = _temp_tome(tmp)

            assert tome.active_leaf_id is None

            # First message has parent_id = None
            e1 = tome.record_message(role="user", content="msg 1")
            assert e1 is not None
            assert e1.parent_id is None
            assert tome.active_leaf_id == e1.id

            # Second message automatically receives e1.id as parent_id
            e2 = tome.record_message(role="assistant", content="msg 2")
            assert e2 is not None
            assert e2.parent_id == e1.id
            assert tome.active_leaf_id == e2.id

            # Third message automatically receives e2.id as parent_id
            e3 = tome.record_message(role="user", content="msg 3")
            assert e3 is not None
            assert e3.parent_id == e2.id
            assert tome.active_leaf_id == e3.id


class TestLeafAdvances:
    def test_leaf_advances_on_recorded_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tome, factory = _temp_tome(tmp)

            entry = tome.record_message(role="user", content="hello")

            assert entry is not None
            assert factory.get_leaf_id(tome.tome_id) == entry.id

    def test_leaf_tracks_the_most_recent_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tome, factory = _temp_tome(tmp)

            tome.record_message(role="user", content="one")
            second = tome.record_message(role="assistant", content="two")

            assert second is not None
            assert factory.get_leaf_id(tome.tome_id) == second.id

    def test_metadata_active_leaf_id_is_populated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tome, _ = _temp_tome(tmp)

            entry = tome.record_message(role="user", content="hello")

            assert entry is not None
            assert tome.metadata.active_leaf_id == entry.id

    def test_no_leaf_recorded_when_tome_not_started(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            factory = TomeHandleFactory(Path(tmp))
            write = factory.create_tome("/tmp")
            read = factory.open_read(write.tome_id)
            tome = MvgeTome(factory, write, read)

            tome.record_message(role="user", content="dropped")

            assert factory.get_leaf_id(tome.tome_id) is None

    def test_leaf_survives_reload_from_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tome, _ = _temp_tome(tmp)
            entry = tome.record_message(role="user", content="hello")
            assert entry is not None

            reopened = TomeHandleFactory(Path(tmp))

            assert reopened.get_leaf_id(tome.tome_id) == entry.id


class TestRecordCompaction:
    def test_appends_compaction_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tome, factory = _temp_tome(tmp)

            tome.record_compaction(
                summary="## Goal\nShip it.",
                mana_before=5000,
                retained_tail=[SummonerRequest(role="user", content="keep me")],
            )

            entries = factory.get_entries(tome.tome_id, TomeEntryType.COMPACTION)
            assert len(entries) == 1

    def test_payload_carries_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tome, factory = _temp_tome(tmp)

            tome.record_compaction(
                summary="## Goal\nShip it.",
                mana_before=5000,
                retained_tail=[SummonerRequest(role="user", content="keep me")],
            )

            entry = factory.get_entries(tome.tome_id, TomeEntryType.COMPACTION)[0]
            assert entry.payload["summary"] == "## Goal\nShip it."
            assert entry.payload["manaBefore"] == 5000
            assert entry.payload["retainedTail"]

    def test_retained_tail_is_serialisable(self) -> None:
        import json

        with tempfile.TemporaryDirectory() as tmp:
            tome, factory = _temp_tome(tmp)

            tome.record_compaction(
                summary="s",
                mana_before=1,
                retained_tail=[SummonerRequest(role="user", content="keep me")],
            )

            entry = factory.get_entries(tome.tome_id, TomeEntryType.COMPACTION)[0]
            assert json.loads(json.dumps(entry.payload))

    def test_dropped_when_tome_not_started(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            factory = TomeHandleFactory(Path(tmp))
            write = factory.create_tome("/tmp")
            read = factory.open_read(write.tome_id)
            tome = MvgeTome(factory, write, read)

            result = tome.record_compaction(
                summary="s", mana_before=1, retained_tail=[]
            )

            assert result is None
            assert factory.get_entries(tome.tome_id, TomeEntryType.COMPACTION) == []

    def test_records_first_kept_entry_id_when_given(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tome, factory = _temp_tome(tmp)

            tome.record_compaction(
                summary="s",
                mana_before=1,
                retained_tail=[],
                first_kept_entry_id="abc123",
            )

            entry = factory.get_entries(tome.tome_id, TomeEntryType.COMPACTION)[0]
            assert entry.payload["firstKeptEntryId"] == "abc123"

    def test_serialises_mvge_response_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tome, factory = _temp_tome(tmp)

            tome.record_compaction(
                summary="s",
                mana_before=1,
                retained_tail=[
                    MvgeResponse(
                        role="assistant",
                        content=[{"type": "text", "text": "hi"}],
                        stop_reason=StopReason.STOP,
                    )
                ],
            )

            entry = factory.get_entries(tome.tome_id, TomeEntryType.COMPACTION)[0]
            tail = entry.payload["retainedTail"]
            assert tail[0]["role"] == "assistant"


class TestTomeAsync:
    @pytest.mark.asyncio
    async def test_record_message_async_defaults_parent_to_active_leaf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tome, _ = _temp_tome(tmp)

            e1 = await tome.record_message_async(role="user", content="msg 1")
            assert e1 is not None
            assert e1.parent_id is None
            assert await tome.active_leaf_id_async() == e1.id

            e2 = await tome.record_message_async(role="assistant", content="msg 2")
            assert e2 is not None
            assert e2.parent_id == e1.id
            assert await tome.active_leaf_id_async() == e2.id

    @pytest.mark.asyncio
    async def test_record_compaction_and_custom_async_persist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tome, factory = _temp_tome(tmp)

            comp = await tome.record_compaction_async(
                summary="s", mana_before=10, retained_tail=[]
            )
            assert comp is not None
            custom = await tome.record_custom_async("note", {"k": "v"})
            assert custom is not None

            entries = factory.get_entries(tome.tome_id)
            types = {e.type for e in entries}
            assert TomeEntryType.COMPACTION in types
            assert TomeEntryType.CUSTOM in types

    @pytest.mark.asyncio
    async def test_record_compaction_async_keeps_first_kept_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tome, factory = _temp_tome(tmp)
            comp = await tome.record_compaction_async(
                summary="s",
                mana_before=0,
                retained_tail=[],
                first_kept_entry_id="keep-1",
            )
            assert comp is not None
            entry = factory.get_entry(tome.tome_id, comp.id)
            assert entry is not None
            assert entry.payload["firstKeptEntryId"] == "keep-1"

    @pytest.mark.asyncio
    async def test_advance_leaf_failure_is_swallowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tome, _ = _temp_tome(tmp)
            entry = await tome.record_message_async(role="user", content="x")
            assert entry is not None
            # Force the leaf-advance to fail; recording must not raise.
            tome._write_handle.append_leaf = MagicMock(side_effect=RuntimeError("boom"))
            tome._advance_leaf(entry)

    @pytest.mark.asyncio
    async def test_recording_async_dropped_when_not_started(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tome, _factory = _temp_tome(tmp)
            tome._started = False

            assert await tome.record_message_async(role="user", content="x") is None
            assert (
                await tome.record_compaction_async(
                    summary="s", mana_before=0, retained_tail=[]
                )
                is None
            )
            assert await tome.record_custom_async("note") is None
            assert await tome.record_tome_info_async({"name": "x"}) is None

    @pytest.mark.asyncio
    async def test_concurrent_async_recording_no_loss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tome, factory = _temp_tome(tmp)

            async def record(i: int) -> None:
                await tome.record_message_async(role="user", content=f"msg {i}")

            await asyncio.gather(*(record(i) for i in range(30)))

            entries = factory.get_entries(tome.tome_id)
            assert len([e for e in entries if e.payload.get("role") == "user"]) == 30


class TestAgentSessionCoverageExtensions:
    """Tests targeting uncovered branches and edge cases in agent_session."""

    def test_serialise_invocation_non_dataclass(self) -> None:
        """_serialise_invocation handles non-dataclass objects gracefully."""

        class NonDataclass:
            role = "custom_role"

        res = _serialise_invocation(NonDataclass())
        assert res == {"role": "custom_role"}

        res_bare = _serialise_invocation(object())
        assert res_bare == {"role": "unknown"}

    def test_sync_properties_and_accessors(self) -> None:
        """Verify factory, tome_id, metadata, active_leaf_id, entries accessors."""
        with tempfile.TemporaryDirectory() as tmp:
            tome, factory = _temp_tome(tmp)
            assert tome.factory is factory
            assert tome.tome_id == tome.metadata.id
            assert tome.compatibility_report is None
            assert tome.active_leaf_id is None
            tome.record_message(role="user", content="hello")
            assert tome.active_leaf_id is not None
            assert len(tome.get_entries()) >= 1
            assert len(tome.get_context_entries()) >= 1

    @pytest.mark.asyncio
    async def test_start_and_shutdown_idempotence(self) -> None:
        """start returns early if started; shutdown returns early if not started."""
        with tempfile.TemporaryDirectory() as tmp:
            tome, _factory = _temp_tome(tmp)
            tome._started = True
            # Calling start while started is a no-op
            await tome.start()

            tome._started = False
            # Calling shutdown while not started is a no-op
            await tome.shutdown()

            tome._started = True
            await tome.shutdown(reason="switch", target_session_file="other.jsonl")
            assert tome._started is False

    def test_advance_leaf_edge_cases(self) -> None:
        """_advance_leaf handles None and catches exceptions."""
        with tempfile.TemporaryDirectory() as tmp:
            tome, factory = _temp_tome(tmp)
            tome._advance_leaf(None)

            entry = tome.record_message(role="user", content="hello")
            assert entry is not None

            # When append_leaf raises, _advance_leaf catches and logs
            tome._write_handle.append_leaf = MagicMock(
                side_effect=RuntimeError("disk error")
            )
            tome._advance_leaf(entry)

    def test_record_compaction_and_custom_sync(self) -> None:
        """Test record_compaction and record_custom when stopped vs running."""
        with tempfile.TemporaryDirectory() as tmp:
            tome, _factory = _temp_tome(tmp)
            tome._started = False
            assert (
                tome.record_compaction(
                    summary="sum",
                    mana_before=100,
                    retained_tail=[],
                )
                is None
            )
            assert tome.record_custom("custom_type") is None
            assert tome.record_tome_info({"name": "x"}) is None

            tome._started = True
            c_entry = tome.record_compaction(
                summary="sum",
                mana_before=100,
                retained_tail=[],
                first_kept_entry_id="k-1",
                parent_id="p-1",
            )
            assert c_entry is not None

            cust_entry = tome.record_custom(
                custom_type="meta",
                data={"key": "val"},
                parent_id="p-1",
            )
            assert cust_entry is not None

            cust_entry_default_parent = tome.record_custom(
                custom_type="meta",
                data={"key": "val"},
                parent_id=None,
            )
            assert cust_entry_default_parent is not None

            info_entry = tome.record_tome_info({"name": "titled"})
            assert info_entry is not None

    @pytest.mark.asyncio
    async def test_safe_emit_error_handling(self) -> None:
        """_safe_emit and _safe_emit_first catch exceptions from rune runner."""
        runner = MagicMock()
        runner.emit_async = AsyncMock(side_effect=RuntimeError("emit fail"))
        runner.emit_first = AsyncMock(side_effect=RuntimeError("emit_first fail"))

        with tempfile.TemporaryDirectory() as tmp:
            factory = TomeHandleFactory(Path(tmp))
            write = factory.create_tome("/tmp")
            read = factory.open_read(write.tome_id)
            tome = MvgeTome(factory, write, read, runner)

            # Neither should raise
            await tome._safe_emit(SigilHook.SESSION_START, {})
            res = await tome._safe_emit_first(SigilHook.SESSION_START, {})
            assert res is None

            tome.bind_runner(None)
            await tome._safe_emit(SigilHook.SESSION_START, {})
            assert await tome._safe_emit_first(SigilHook.SESSION_START, {}) is None

    @pytest.mark.asyncio
    async def test_open_force_fork_failure_raises_tome_incompatible(self) -> None:
        """When force_fork=True and fork raises, TomeIncompatibleError is raised."""
        with tempfile.TemporaryDirectory() as tmp:
            factory = TomeHandleFactory(Path(tmp))
            write = factory.create_tome("/tmp", model="old-model")
            tome_id = write.tome_id

            # Mock validate_session_compatibility to return incompatible report
            report = SessionCompatibilityReport(
                compatible=False,
                tome_id=tome_id,
                diagnostics=[
                    Diagnostic(
                        kind=DiagnosticKind.MODEL_MISMATCH,
                        rune_name="core",
                        message="mismatch",
                    )
                ],
                model_mismatch=("old-model", "new-model"),
            )
            with (
                pytest.MonkeyPatch.context() as mp,
            ):
                mp.setattr(
                    "mvgeos_agent.agent_session.validate_session_compatibility",
                    lambda *args, **kwargs: report,
                )
                mp.setattr(
                    factory,
                    "create_branched_tome",
                    MagicMock(side_effect=RuntimeError("fork failed")),
                )

                with pytest.raises(TomeIncompatibleError) as exc_info:
                    await MvgeTome.open(
                        factory,
                        tome_id,
                        force_fork=True,
                        strict=True,
                    )
                assert exc_info.value.model_mismatch == ("old-model", "new-model")

    def test_reconstruct_invocations_assistant_content_variants(self) -> None:
        """Assistant content as list, string, and non-string payloads."""
        with tempfile.TemporaryDirectory() as tmp:
            tome, _factory = _temp_tome(tmp)

            tome.record_message(
                role="assistant",
                content=[{"type": ContentType.TEXT, "text": "blocks"}],
            )
            tome.record_message(role="assistant", content="str response")
            tome.record_message(role="assistant", content=12345)
            tome.record_message(
                role="assistant",
                content="with stop",
                # stop_reason travels in the payload for reconstructed turns
            )

            invocations = tome.reconstruct_invocations()
            assert len(invocations) == 4
            assert invocations[0].content == [{"type": "text", "text": "blocks"}]
            assert invocations[1].content == [
                {"type": ContentType.TEXT, "text": "str response"}
            ]
            assert invocations[2].content == [
                {"type": ContentType.TEXT, "text": "12345"}
            ]

    def test_reconstruct_invocations_spell_result_variants(self) -> None:
        """spellResult/tool content as list, string, and non-string payloads."""
        with tempfile.TemporaryDirectory() as tmp:
            tome, _factory = _temp_tome(tmp)

            tome.record_message(
                role="spellResult",
                content=[{"type": ContentType.TEXT, "text": "res"}],
            )
            tome.record_message(role="tool", content="str content")
            tome.record_message(role="spellResult", content=777)

            invocations = tome.reconstruct_invocations()
            assert len(invocations) == 3
            assert invocations[0].content == [{"type": "text", "text": "res"}]
            assert invocations[1].content == [
                {"type": ContentType.TEXT, "text": "str content"}
            ]
            assert invocations[2].content == [{"type": ContentType.TEXT, "text": "777"}]

    def test_reconstruct_invocations_user_content_parts_preserved(self) -> None:
        """User content parts survive a tome round-trip as a list."""
        parts = [
            {"type": "text", "text": "look at this"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,aGk="},
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            tome, _factory = _temp_tome(tmp)
            tome.record_message(role="user", content=parts)
            tome.record_message(role="user", content="plain string")

            invocations = tome.reconstruct_invocations()
            assert len(invocations) == 2
            assert invocations[0].content == parts
            assert invocations[1].content == "plain string"

    def test_reconstruct_invocations_retained_tail_user_parts_preserved(self) -> None:
        """Retained tail user content parts are not stringified."""
        parts = [
            {
                "type": "file",
                "file": {
                    "filename": "doc.pdf",
                    "file_data": "data:application/pdf;base64,aGk=",
                },
            }
        ]
        entries = [
            TomeEntry(
                id="c1",
                parent_id=None,
                type=TomeEntryType.COMPACTION,
                timestamp=1000.0,
                payload={
                    "summary": "Compacted conversation",
                    "manaBefore": 5000,
                    "retainedTail": [{"role": "user", "content": parts}],
                },
            ),
        ]
        factory = MagicMock()
        factory.get_entries_for_context.return_value = entries
        factory.get_leaf_id.return_value = "c1"
        write, read = _mock_handles()

        tome = MvgeTome(factory, write, read)
        invocations = tome.reconstruct_invocations()

        assert len(invocations) == 2
        assert isinstance(invocations[1], SummonerRequest)
        assert invocations[1].content == parts

    def test_reconstruct_invocations_invalid_stop_reason_falls_back(
        self,
    ) -> None:
        """An unknown stop_reason payload falls back to STOP."""
        import json

        with tempfile.TemporaryDirectory() as tmp:
            tome, factory = _temp_tome(tmp)
            raw = {
                "role": "assistant",
                "content": "hello",
                "stop_reason": "not-a-reason",
            }
            with factory.open_write(tome.tome_id).path.open("a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {
                            "id": "raw-1",
                            "parentId": None,
                            "type": "message",
                            "timestamp": 1000.0,
                            "payload": raw,
                        }
                    )
                    + "\n"
                )

            invocations = tome.reconstruct_invocations()
            assert len(invocations) == 1
            assert invocations[0].stop_reason == StopReason.STOP

    def test_reconstruct_invocations_complex_types(self) -> None:
        """Reconstruct invocations with compaction and spellResult messages."""
        with tempfile.TemporaryDirectory() as tmp:
            tome, _factory = _temp_tome(tmp)

            # Record a compaction with various retainedTail formats
            retained_tail = [
                SummonerRequest(role="user", content="user query"),
                MvgeResponse(
                    role="assistant",
                    content=[{"type": ContentType.TEXT, "text": "blocks"}],
                    stop_reason=StopReason.STOP,
                ),
                MvgeResponse(
                    role="assistant",
                    content="str response",  # type: ignore[arg-type]
                    stop_reason=StopReason.STOP,
                ),
                MvgeResponse(
                    role="assistant",
                    content=12345,  # type: ignore[arg-type]
                    stop_reason=StopReason.STOP,
                ),
                SpellResultMessage(
                    role="spellResult",
                    content=[{"type": ContentType.TEXT, "text": "result"}],
                    spell_name="bash",
                    spell_cast_id="c1",
                ),
                SpellResultMessage(
                    role="tool",
                    content="tool string",  # type: ignore[arg-type]
                    spell_name="read",
                    spell_cast_id="c2",
                ),
                SpellResultMessage(
                    role="spellResult",
                    content=999,  # type: ignore[arg-type]
                    spell_name="calc",
                    spell_cast_id="c3",
                ),
            ]
            comp = tome.record_compaction(
                summary="summary text",
                mana_before=500,
                retained_tail=retained_tail,
            )
            assert comp is not None
            tome._advance_leaf(comp)

            # Record direct spellResult and tool messages
            tome.record_message(
                role="spellResult",
                content=[{"type": ContentType.TEXT, "text": "res"}],
            )
            tome.record_message(
                role="tool",
                content="str content",
            )
            tome.record_message(
                role="spellResult",
                content=777,
            )

            invocations = tome.reconstruct_invocations()
            assert len(invocations) > 0
            roles = [inv.role for inv in invocations]
            assert "user" in roles
            assert "assistant" in roles
            assert "spellResult" in roles
