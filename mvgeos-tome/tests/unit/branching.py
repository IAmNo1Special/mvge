from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from mvgeos_tome.handle import TomeHandleFactory
from mvgeos_tome.types import TomeEntry, TomeEntryType


def _factory(tmp: str) -> TomeHandleFactory:
    return TomeHandleFactory(Path(tmp))


def _message(
    write_handle,
    entry_id: str,
    role: str,
    content: str,
    parent_id: str | None = None,
    timestamp: float = 1000.0,
) -> TomeEntry:
    entry = TomeEntry(
        id=entry_id,
        parent_id=parent_id,
        type=TomeEntryType.MESSAGE,
        timestamp=timestamp,
        payload={"role": role, "content": content},
    )
    write_handle.append(entry)
    return entry


def _leaf(write_handle, entry_id: str, target: str) -> TomeEntry:
    entry = TomeEntry(
        id=entry_id,
        parent_id=None,
        type=TomeEntryType.LEAF,
        timestamp=1001.0,
        payload={"targetId": target},
    )
    write_handle.append(entry)
    return entry


class TestParentIdAndBranching:
    def test_persists_parent_id_in_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            factory = _factory(tmp)
            write = factory.create_tome("/tmp", tome_id="t1")
            e1 = _message(write, "e1", "user", "hello", parent_id=None)
            _leaf(write, "l1", e1.id)
            e2 = _message(write, "e2", "assistant", "hi", parent_id=e1.id)
            _leaf(write, "l2", e2.id)

            lines = [
                json.loads(line)
                for line in write.path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

            # Line 0 is header
            assert lines[0]["type"] == "session"

            # Find message entries
            messages = [entry for entry in lines if entry.get("type") == "message"]
            assert len(messages) == 2
            assert messages[0]["id"] == e1.id
            assert messages[0]["parentId"] is None
            assert messages[1]["id"] == e2.id
            assert messages[1]["parentId"] == e1.id

    def test_create_branched_tome_copies_ancestor_entries_to_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            factory = _factory(tmp)
            write = factory.create_tome("/tmp", tome_id="t1")

            e1 = _message(write, "e1", "user", "turn 1 request", parent_id=None)
            _leaf(write, "l1", e1.id)

            e2 = _message(write, "e2", "assistant", "turn 1 response", parent_id=e1.id)
            _leaf(write, "l2", e2.id)

            e3 = _message(write, "e3", "user", "turn 2 request", parent_id=e2.id)
            _leaf(write, "l3", e3.id)

            e4 = _message(write, "e4", "assistant", "turn 2 response", parent_id=e3.id)
            _leaf(write, "l4", e4.id)

            # Fork from e2
            forked = factory.create_branched_tome(
                parent_tome_id="t1",
                cwd="/tmp",
                fork_from_leaf_id=e2.id,
            )

            forked_entries = factory.get_entries(forked.tome_id)
            entry_ids = [e.id for e in forked_entries]

            # Fork should include e1 and e2, but NOT e3 or e4
            assert entry_ids == [e1.id, e2.id]
            meta = factory.open_tome(forked.tome_id)
            assert meta is not None
            assert meta.parent_tome_id == "t1"
            assert meta.active_leaf_id == e2.id

    def test_get_entries_for_context_reconstructs_ancestor_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            factory = _factory(tmp)
            write = factory.create_tome("/tmp", tome_id="t1")

            e1 = _message(write, "e1", "user", "1", parent_id=None)
            _leaf(write, "l1", e1.id)

            e2 = _message(write, "e2", "assistant", "2", parent_id=e1.id)
            _leaf(write, "l2", e2.id)

            # Branch A
            e3a = _message(write, "e3a", "user", "3a", parent_id=e2.id)
            _leaf(write, "l3a", e3a.id)

            e4a = _message(write, "e4a", "assistant", "4a", parent_id=e3a.id)
            _leaf(write, "l4a", e4a.id)

            # Branch B branching from e2
            e3b = _message(write, "e3b", "user", "3b", parent_id=e2.id)
            _leaf(write, "l3b", e3b.id)

            e4b = _message(write, "e4b", "assistant", "4b", parent_id=e3b.id)
            _leaf(write, "l4b", e4b.id)

            # Context for 4a should be: e1 -> e2 -> e3a -> e4a
            context_a = factory.get_entries_for_context("t1", leaf_id=e4a.id)
            assert [e.id for e in context_a] == [e1.id, e2.id, e3a.id, e4a.id]

            # Context for 4b should be: e1 -> e2 -> e3b -> e4b
            context_b = factory.get_entries_for_context("t1", leaf_id=e4b.id)
            assert [e.id for e in context_b] == [e1.id, e2.id, e3b.id, e4b.id]

    def test_get_leaf_id_falls_back_to_metadata_active_leaf_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            factory = _factory(tmp)
            write = factory.create_tome("/tmp", tome_id="t1")

            e1 = _message(write, "e1", "user", "turn 1", parent_id=None)
            _leaf(write, "l1", e1.id)

            forked = factory.create_branched_tome(
                parent_tome_id="t1",
                cwd="/tmp",
                fork_from_leaf_id=e1.id,
            )

            # Newly forked tome has no LEAF entry yet, but metadata has
            # active_leaf_id=e1.id
            assert factory.get_leaf_id(forked.tome_id) == e1.id

    def test_create_and_branched_tome_stores_and_overrides_session_config(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            factory = _factory(tmp)
            write = factory.create_tome(
                "/tmp",
                tome_id="t1",
                model="anthropic/claude-3.5-sonnet",
                contemplation_level="high",
                spells=["bash", "read_file"],
            )
            meta = factory.open_tome(write.tome_id)
            assert meta is not None
            assert meta.model == "anthropic/claude-3.5-sonnet"
            assert meta.contemplation_level == "high"
            assert meta.spells == ["bash", "read_file"]

            # Fork inheriting parent config
            forked1 = factory.create_branched_tome(
                parent_tome_id="t1",
                cwd="/tmp",
            )
            meta1 = factory.open_tome(forked1.tome_id)
            assert meta1 is not None
            assert meta1.model == "anthropic/claude-3.5-sonnet"
            assert meta1.contemplation_level == "high"
            assert meta1.spells == ["bash", "read_file"]

            # Fork overriding config
            forked2 = factory.create_branched_tome(
                parent_tome_id="t1",
                cwd="/tmp",
                model="openai/gpt-4o",
                contemplation_level="low",
                spells=["bash"],
            )
            meta2 = factory.open_tome(forked2.tome_id)
            assert meta2 is not None
            assert meta2.model == "openai/gpt-4o"
            assert meta2.contemplation_level == "low"
            assert meta2.spells == ["bash"]

    def test_get_entries_to_leaf_indexed_deep_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            factory = _factory(tmp)
            write = factory.create_tome("/tmp", tome_id="t1")
            prev_id: str | None = None
            branch_a: list[str] = []
            for i in range(50):
                eid = f"a_{i}"
                _message(write, eid, "user", f"msg {i}", parent_id=prev_id)
                branch_a.append(eid)
                prev_id = eid

            # Create side branch B branching off a_25
            prev_b: str | None = "a_25"
            branch_b: list[str] = list(branch_a[:26])
            for j in range(20):
                eid = f"b_{j}"
                _message(write, eid, "user", f"side {j}", parent_id=prev_b)
                branch_b.append(eid)
                prev_b = eid

            read = factory.open_read("t1")
            res_a = read.get_entries_to_leaf("a_49")
            assert [e.id for e in res_a] == branch_a

            res_b = read.get_entries_to_leaf("b_19")
            assert [e.id for e in res_b] == branch_b

    @pytest.mark.asyncio
    async def test_tome_handle_async_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            factory = _factory(tmp)
            write = factory.create_tome("/tmp", tome_id="t_async")
            e1 = TomeEntry(
                id="e1",
                parent_id=None,
                type=TomeEntryType.MESSAGE,
                timestamp=1000.0,
                payload={"role": "user", "content": "hi async"},
            )
            await write.append_async(e1)
            leaf = await write.append_leaf_async("e1")
            assert leaf.payload["targetId"] == "e1"

            read = factory.open_read("t_async")
            entries = read.get_entries()
            assert any(e.id == "e1" for e in entries)
