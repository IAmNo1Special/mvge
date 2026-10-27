"""Multi-format factory tests: the built-in codec plus a fake rune codec."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mvge.tome.codec import SessionCodec
from mvge.tome.handle import TomeHandleFactory
from mvge.tome.types import TomeEntry, TomeEntryType, TomeMetadata


class FakeCodec(SessionCodec):
    """A minimal second session format, standing in for a rune codec.

    Subclasses SessionCodec for the append/fork defaults; only the
    format-specific members are defined here.

    Files look like ``{"kind": "fake", "id": ...}`` headers with
    ``{"id": ..., "text": ...}`` body lines. The branch tip lives in the
    header's ``tipId`` field rather than in marker entries.
    """

    name = "fake"

    def detect(self, header: dict[str, Any]) -> bool:
        return isinstance(header, dict) and header.get("kind") == "fake"

    def looks_like_session(self, header: dict[str, Any]) -> bool:
        return isinstance(header, dict) and header.get("kind") == "fake"

    def parse_header(self, header: dict[str, Any]) -> TomeMetadata:
        return TomeMetadata(
            id=header["id"],
            created_at=header.get("created_at", ""),
            cwd=header.get("cwd", ""),
            version=1,
        )

    def parse_entries(
        self, header: dict[str, Any], lines: list[str], *, source: str
    ) -> list[TomeEntry]:
        entries = []
        for raw in lines:
            raw = raw.strip()
            if not raw:
                continue
            obj = json.loads(raw)
            entries.append(
                TomeEntry(
                    id=obj["id"],
                    parent_id=None,
                    type=TomeEntryType.MESSAGE,
                    timestamp=1000.0,
                    payload={"text": obj["text"]},
                )
            )
        return entries

    def serialize_entry(self, entry: TomeEntry) -> dict[str, Any] | None:
        return {"id": entry.id, "text": entry.payload.get("text", "")}

    def serialize_new_entry(
        self, entry: TomeEntry, existing: list[TomeEntry]
    ) -> tuple[dict[str, Any], TomeEntry]:
        return self.serialize_entry(entry), entry

    def apply_leaf(
        self,
        header: dict[str, Any],
        entries: list[TomeEntry],
        leaf: TomeEntry,
    ) -> tuple[dict[str, Any], list[TomeEntry]]:
        header["tipId"] = leaf.payload.get("targetId")
        return header, entries

    def leaf_id(self, header: dict[str, Any], entries: list[TomeEntry]) -> str | None:
        tip = header.get("tipId")
        return tip if isinstance(tip, str) and tip else None


def _write_fake(path: Path, session_id: str, texts: list[str]) -> None:
    lines = [json.dumps({"kind": "fake", "id": session_id, "cwd": "/tmp"})]
    lines += [json.dumps({"id": f"e{i}", "text": t}) for i, t in enumerate(texts)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_fake_codec_satisfies_protocol():
    assert isinstance(FakeCodec(), SessionCodec)


@pytest.fixture
def factory(tmp_path: Path) -> TomeHandleFactory:
    return TomeHandleFactory(tmp_path, codecs=[FakeCodec()])


def test_builtin_codec_always_first(factory: TomeHandleFactory):
    assert [c.name for c in factory.codecs] == ["tome-v1", "fake"]


def test_list_tomes_sees_both_formats(factory: TomeHandleFactory, tmp_path: Path):
    factory.create_tome("/tmp", tome_id="v1-tome")
    _write_fake(tmp_path / "prefix_f2.jsonl", "f2", ["hello"])
    metas = {m.id for m in factory.list_tomes()}
    assert metas == {"v1-tome", "f2"}


def test_open_tome_resolves_by_session_id_not_stem(
    factory: TomeHandleFactory, tmp_path: Path
):
    _write_fake(tmp_path / "prefix_f2.jsonl", "f2", ["hello"])
    meta = factory.open_tome("f2")
    assert meta is not None
    assert meta.id == "f2"


def test_open_read_parses_foreign_entries(factory: TomeHandleFactory, tmp_path: Path):
    _write_fake(tmp_path / "prefix_f2.jsonl", "f2", ["hello", "world"])
    entries = factory.get_entries("f2")
    assert [e.payload["text"] for e in entries] == ["hello", "world"]


def test_append_writes_native_format(factory: TomeHandleFactory, tmp_path: Path):
    _write_fake(tmp_path / "prefix_f2.jsonl", "f2", ["hello"])
    write = factory.open_write("f2")
    write.append(
        TomeEntry(
            id="e9",
            parent_id=None,
            type=TomeEntryType.MESSAGE,
            timestamp=1001.0,
            payload={"text": "appended"},
        )
    )
    lines = (tmp_path / "prefix_f2.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-1]) == {"id": "e9", "text": "appended"}
    # The file still parses as the fake format, not Tome v1.
    assert factory.open_read("f2").codec.name == "fake"
    assert [e.id for e in factory.get_entries("f2")] == ["e0", "e9"]


def test_leaf_round_trips_through_codec_tip(factory: TomeHandleFactory, tmp_path: Path):
    _write_fake(tmp_path / "prefix_f2.jsonl", "f2", ["hello"])
    write = factory.open_write("f2")
    write.append_leaf("e0")
    header = json.loads(
        (tmp_path / "prefix_f2.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert header["tipId"] == "e0"
    # No LEAF marker entry was written: the tip lives in the header.
    assert all(e.type != TomeEntryType.LEAF for e in factory.get_entries("f2"))
    assert factory.get_leaf_id("f2") == "e0"


def test_v1_leaf_still_uses_marker_entries(factory: TomeHandleFactory, tmp_path: Path):
    factory.create_tome("/tmp", tome_id="v1-tome")
    write = factory.open_write("v1-tome")
    write.append(
        TomeEntry(
            id="m1",
            parent_id=None,
            type=TomeEntryType.MESSAGE,
            timestamp=1000.0,
            payload={"role": "user", "content": "hi"},
        )
    )
    write.append_leaf("m1")
    assert factory.get_leaf_id("v1-tome") == "m1"
    assert any(e.type == TomeEntryType.LEAF for e in factory.get_entries("v1-tome"))


def test_unclaimed_files_still_skipped(factory: TomeHandleFactory, tmp_path: Path):
    (tmp_path / "junk.jsonl").write_text('{"nope": true}\n', encoding="utf-8")
    assert factory.list_tomes() == []
    assert factory.open_read("junk").get_entries() == []


def test_explicit_path_outside_tome_dir(factory: TomeHandleFactory, tmp_path: Path):
    outside = tmp_path / "outside.jsonl"
    _write_fake(outside, "out1", ["hi"])
    other_dir = tmp_path / "sessions"
    other_dir.mkdir()
    other_factory = TomeHandleFactory(other_dir, codecs=[FakeCodec()])
    assert other_factory.open_tome(str(outside)).id == "out1"
    assert [e.id for e in other_factory.get_entries(str(outside))] == ["e0"]


class ForkableFakeCodec(FakeCodec):
    name = "forkable-fake"

    def detect(self, header: dict[str, Any]) -> bool:
        return isinstance(header, dict) and header.get("kind") == "forkable-fake"

    def looks_like_session(self, header: dict[str, Any]) -> bool:
        return isinstance(header, dict) and header.get("kind") == "forkable-fake"

    def fork(
        self,
        *,
        source: str,
        dest_dir: str,
        new_id: str,
        leaf_id: str | None = None,
        cwd: str | None = None,
    ) -> str:
        dest = Path(dest_dir) / f"forked-{new_id}.jsonl"
        dest.write_text(
            json.dumps(
                {
                    "kind": "forkable-fake",
                    "id": new_id,
                    "cwd": cwd,
                    "leaf_id": leaf_id,
                    "source": source,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return str(dest)


def _write_forkable_fake(tmp_path: Path, session_id: str) -> None:
    path = tmp_path / f"{session_id}.jsonl"
    lines = [json.dumps({"kind": "forkable-fake", "id": session_id, "cwd": "/tmp"})]
    lines += [json.dumps({"id": "e0", "text": "hi"})]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestCodecAwareFork:
    def test_fork_delegates_to_non_v1_codec(self, tmp_path: Path) -> None:
        _write_forkable_fake(tmp_path, "f1")
        factory = TomeHandleFactory(tmp_path, codecs=[ForkableFakeCodec()])

        forked = factory.create_branched_tome(parent_tome_id="f1", cwd="/tmp")

        assert forked.codec.name == "forkable-fake"
        new_path = Path(forked.path)
        assert new_path.parent == tmp_path
        assert new_path.name.startswith("forked-")
        assert json.loads(new_path.read_text().splitlines()[0])["id"] == forked.tome_id

    def test_fork_delegates_leaf_id_to_codec(self, tmp_path: Path) -> None:
        _write_forkable_fake(tmp_path, "f1")
        factory = TomeHandleFactory(tmp_path, codecs=[ForkableFakeCodec()])

        forked = factory.create_branched_tome(
            parent_tome_id="f1", cwd="/projects/foo", fork_from_leaf_id="e0"
        )

        header = json.loads(Path(forked.path).read_text().splitlines()[0])
        assert header["leaf_id"] == "e0"

    def test_fork_delegates_cwd_to_codec(self, tmp_path: Path) -> None:
        _write_forkable_fake(tmp_path, "f1")
        factory = TomeHandleFactory(tmp_path, codecs=[ForkableFakeCodec()])

        forked = factory.create_branched_tome(parent_tome_id="f1", cwd="/projects/foo")

        header = json.loads(Path(forked.path).read_text().splitlines()[0])
        assert header["cwd"] == "/projects/foo"
        assert header["leaf_id"] is None

    def test_fork_delegates_source_and_dest_dir_to_codec(self, tmp_path: Path) -> None:
        _write_forkable_fake(tmp_path, "f1")
        factory = TomeHandleFactory(tmp_path, codecs=[ForkableFakeCodec()])

        forked = factory.create_branched_tome(parent_tome_id="f1", cwd="/projects/foo")

        header = json.loads(Path(forked.path).read_text().splitlines()[0])
        assert header["source"] == str(tmp_path / "f1.jsonl")
        assert Path(forked.path).parent == tmp_path

    def test_fork_without_native_fork_raises(self, tmp_path: Path) -> None:
        _write_fake(tmp_path / "f1.jsonl", "f1", ["hi"])
        factory = TomeHandleFactory(tmp_path, codecs=[FakeCodec()])

        with pytest.raises(ValueError, match="Cannot fork"):
            factory.create_branched_tome(parent_tome_id="f1", cwd="/tmp")

    def test_fork_still_v1_for_builtin_sessions(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path, codecs=[FakeCodec()])
        factory.create_tome("/tmp", tome_id="parent")

        forked = factory.create_branched_tome(parent_tome_id="parent", cwd="/tmp")

        assert forked.codec.name == "tome-v1"
        meta = factory.open_tome(forked.tome_id)
        assert meta is not None
        assert meta.parent_tome_id == "parent"
