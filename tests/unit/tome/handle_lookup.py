from __future__ import annotations

from pathlib import Path

from mvge.tome.handle import TomeHandleFactory
from mvge.tome.types import TomeEntry, TomeEntryType


def _message(
    entry_id: str,
    role: str,
    content: str,
    parent_id: str | None = None,
    timestamp: float = 1000.0,
) -> TomeEntry:
    return TomeEntry(
        id=entry_id,
        parent_id=parent_id,
        type=TomeEntryType.MESSAGE,
        timestamp=timestamp,
        payload={"role": role, "content": content},
    )


def _leaf(entry_id: str, target: str, timestamp: float = 1001.0) -> TomeEntry:
    return TomeEntry(
        id=entry_id,
        parent_id=None,
        type=TomeEntryType.LEAF,
        timestamp=timestamp,
        payload={"targetId": target},
    )


def _factory(tmp_path: Path) -> TomeHandleFactory:
    return TomeHandleFactory(tmp_path)


class TestGetEntry:
    def test_finds_an_appended_entry(self, tmp_path: Path) -> None:
        factory = _factory(tmp_path)
        write = factory.create_tome("/tmp", tome_id="t1")
        write.append(_message("m1", "user", "hello"))

        found = factory.get_entry("t1", "m1")

        assert found is not None
        assert found.id == "m1"

    def test_returns_none_for_unknown_id(self, tmp_path: Path) -> None:
        factory = _factory(tmp_path)
        factory.create_tome("/tmp", tome_id="t1")

        assert factory.get_entry("t1", "nope") is None

    def test_does_not_leak_entries_across_tomes(self, tmp_path: Path) -> None:
        factory = _factory(tmp_path)
        factory.create_tome("/a", tome_id="tome-a")
        write_b = factory.create_tome("/b", tome_id="tome-b")
        write_b.append(_message("mb", "user", "in B"))

        assert factory.get_entry("tome-a", "mb") is None
        assert factory.get_entry("tome-b", "mb") is not None


class TestGetLeafId:
    def test_returns_none_before_any_leaf(self, tmp_path: Path) -> None:
        factory = _factory(tmp_path)
        write = factory.create_tome("/tmp", tome_id="t1")
        write.append(_message("m1", "user", "hello"))

        assert factory.get_leaf_id("t1") is None

    def test_returns_the_target_of_the_latest_leaf(self, tmp_path: Path) -> None:
        factory = _factory(tmp_path)
        write = factory.create_tome("/tmp", tome_id="t1")
        write.append(_message("m1", "user", "hello"))
        write.append(_leaf("l1", "m1"))

        assert factory.get_leaf_id("t1") == "m1"

    def test_advances_with_each_leaf(self, tmp_path: Path) -> None:
        factory = _factory(tmp_path)
        write = factory.create_tome("/tmp", tome_id="t1")
        write.append(_message("m1", "user", "one"))
        write.append(_leaf("l1", "m1"))
        write.append(_message("m2", "user", "two"))
        write.append(_leaf("l2", "m2"))

        assert factory.get_leaf_id("t1") == "m2"

    def test_does_not_leak_leaves_across_tomes(self, tmp_path: Path) -> None:
        factory = _factory(tmp_path)
        factory.create_tome("/a", tome_id="tome-a")
        write_b = factory.create_tome("/b", tome_id="tome-b")
        write_b.append(_message("mb", "user", "in B"))
        write_b.append(_leaf("lb", "mb"))

        assert factory.get_leaf_id("tome-a") is None
        assert factory.get_leaf_id("tome-b") == "mb"

    def test_falls_back_to_header_active_leaf_id(self, tmp_path: Path) -> None:
        factory = _factory(tmp_path)
        write = factory.create_tome("/tmp", tome_id="t1")
        write.append(_message("m1", "user", "turn 1"))
        write.append(_leaf("l1", "m1"))

        forked = factory.create_branched_tome(
            parent_tome_id="t1", cwd="/tmp", fork_from_leaf_id="m1"
        )

        # The forked tome carries the leaf in its header; entries hold messages.
        assert factory.get_leaf_id(forked.tome_id) == "m1"


class TestListLeaves:
    def test_empty_tome_has_no_leaves(self, tmp_path: Path) -> None:
        factory = _factory(tmp_path)
        factory.create_tome("/tmp", tome_id="t1")
        assert factory.list_leaves("t1") == []

    def test_tip_entries_are_leaves(self, tmp_path: Path) -> None:
        factory = _factory(tmp_path)
        write = factory.create_tome("/tmp", tome_id="t1")
        write.append(_message("m1", "user", "one"))
        write.append(_message("m2", "user", "two", parent_id="m1"))

        assert factory.list_leaves("t1") == ["m2"]


class TestGetEntries:
    def test_filters_by_type(self, tmp_path: Path) -> None:
        factory = _factory(tmp_path)
        write = factory.create_tome("/tmp", tome_id="t1")
        write.append(_message("m1", "user", "hello"))
        write.append(_leaf("l1", "m1"))

        assert len(factory.get_entries("t1")) == 2
        leaves = factory.get_entries("t1", entry_type=TomeEntryType.LEAF)
        assert [e.id for e in leaves] == ["l1"]

    def test_limit_takes_the_tail(self, tmp_path: Path) -> None:
        factory = _factory(tmp_path)
        write = factory.create_tome("/tmp", tome_id="t1")
        for i in range(5):
            write.append(_message(f"m{i}", "user", f"msg-{i}", timestamp=1000.0 + i))

        tail = factory.get_entries("t1", limit=2)
        assert [e.payload["content"] for e in tail] == ["msg-3", "msg-4"]

    def test_context_max_entries(self, tmp_path: Path) -> None:
        factory = _factory(tmp_path)
        write = factory.create_tome("/tmp", tome_id="t1")
        for i in range(5):
            write.append(_message(f"m{i}", "user", f"msg-{i}", timestamp=1000.0 + i))

        ctx = factory.get_entries_for_context("t1", max_entries=2)
        assert [e.payload["content"] for e in ctx] == ["msg-3", "msg-4"]

    def test_context_reconstructs_ancestor_chain(self, tmp_path: Path) -> None:
        factory = _factory(tmp_path)
        write = factory.create_tome("/tmp", tome_id="t1")
        write.append(_message("e1", "user", "1"))
        write.append(_leaf("l1", "e1"))
        write.append(_message("e2", "assistant", "2", parent_id="e1"))
        write.append(_leaf("l2", "e2"))
        write.append(_message("e3a", "user", "3a", parent_id="e2"))
        write.append(_leaf("l3a", "e3a"))
        write.append(_message("e4a", "assistant", "4a", parent_id="e3a"))
        write.append(_message("e3b", "user", "3b", parent_id="e2"))
        write.append(_message("e4b", "assistant", "4b", parent_id="e3b"))

        context_a = factory.get_entries_for_context("t1", leaf_id="e4a")
        assert [e.id for e in context_a] == ["e1", "e2", "e3a", "e4a"]

        context_b = factory.get_entries_for_context("t1", leaf_id="e4b")
        assert [e.id for e in context_b] == ["e1", "e2", "e3b", "e4b"]
