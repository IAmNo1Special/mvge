from __future__ import annotations

import json
from pathlib import Path

import pytest

from mvge.tome.codec import AppendPlan, TomeV1Codec
from mvge.tome.handle import Revision, TomeHandle, TomeHandleFactory
from mvge.tome.types import TomeEntry, TomeEntryType, TomeVersionError


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


def _tome_info(entry_id: str, payload: dict, timestamp: float = 1002.0) -> TomeEntry:
    return TomeEntry(
        id=entry_id,
        parent_id=None,
        type=TomeEntryType.TOME_INFO,
        timestamp=timestamp,
        payload=payload,
    )


class TestMirrorCopies:
    def test_mutating_get_entries_result_does_not_poison_mirror(
        self, tmp_path: Path
    ) -> None:
        factory = TomeHandleFactory(tmp_path)
        write = factory.create_tome("/tmp", tome_id="t1")
        write.append(_message("m1", "user", "hello"))

        read = factory.open_read("t1")
        first = read.get_entries()
        first.append("POISON")  # type: ignore[arg-type]
        first.clear()

        assert [e.id for e in read.get_entries()] == ["m1"]

    def test_iter_entries_snapshot_is_stable(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        write = factory.create_tome("/tmp", tome_id="t1")
        write.append(_message("m1", "user", "one"))

        read = factory.open_read("t1")
        before = list(read.iter_entries())
        write.append(_message("m2", "user", "two"))

        assert [e.id for e in before] == ["m1"]
        assert [e.id for e in read.get_entries()] == ["m1", "m2"]

    def test_empty_tome_reads_empty_without_error(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        factory.create_tome("/tmp", tome_id="t1")

        read = factory.open_read("t1")
        assert read.get_entries() == []
        assert read.get_entries() == []
        assert list(read.iter_entries()) == []


class TestCrossHandleVisibility:
    def test_reader_sees_writer_appends_without_restart(self, tmp_path: Path) -> None:
        """Regression: GUI reads through its own handle while the agent
        persists via a separate one; no stale cache may hide new entries."""
        agent_factory = TomeHandleFactory(tmp_path)
        agent_write = agent_factory.create_tome("/project/a", tome_id="t1")

        gui_factory = TomeHandleFactory(tmp_path)
        gui_read = gui_factory.open_read("t1")
        assert gui_read.get_entries() == []

        agent_write.append(_tome_info("info-1", {"name": "Fix login bug"}))

        assert [e.id for e in gui_read.get_entries()] == ["info-1"]

    def test_list_tomes_sees_externally_created_tome(self, tmp_path: Path) -> None:
        gui_factory = TomeHandleFactory(tmp_path)
        assert gui_factory.list_tomes() == []

        agent_factory = TomeHandleFactory(tmp_path)
        agent_factory.create_tome("/project/a", tome_id="t1")

        assert [m.id for m in gui_factory.list_tomes()] == ["t1"]

    def test_two_writers_append_without_loss(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        factory.create_tome("/tmp", tome_id="t1")
        w1 = factory.open_write("t1")
        w2 = factory.open_write("t1")

        w1.append(_message("m1", "user", "one"))
        w2.append(_message("m2", "user", "two"))

        assert {e.id for e in factory.get_entries("t1")} == {"m1", "m2"}


class TestRevision:
    def test_missing_file_has_no_revision(self, tmp_path: Path) -> None:
        assert Revision.from_path(tmp_path / "nope.jsonl") is None
        handle = TomeHandle(tmp_path, "nope", "r")
        assert handle.get_revision() is None

    def test_revision_changes_on_write(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        write = factory.create_tome("/tmp", tome_id="t1")
        rev_before = write.get_revision()
        assert rev_before is not None

        write.append(_message("m1", "user", "hi"))

        assert write.get_revision() != rev_before

    def test_revision_covers_identity_not_just_mtime(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        factory.create_tome("/tmp", tome_id="t1")
        factory.create_tome("/tmp", tome_id="t2")

        r1 = factory.open_read("t1").get_revision()
        r2 = factory.open_read("t2").get_revision()
        assert r1 is not None
        assert r2 is not None
        assert r1.ino != r2.ino


class TestAppendDurability:
    def test_append_persists_jsonl_lines(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        write = factory.create_tome("/tmp", tome_id="t1")
        write.append(_message("m1", "user", "hello", parent_id=None))
        write.append(_leaf("l1", "m1"))

        lines = (tmp_path / "t1.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3
        assert json.loads(lines[0])["type"] == "session"
        assert json.loads(lines[1])["id"] == "m1"
        assert json.loads(lines[2])["payload"] == {"targetId": "m1"}

    def test_read_handle_cannot_append(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        factory.create_tome("/tmp", tome_id="t1")
        read = factory.open_read("t1")
        with pytest.raises(RuntimeError):
            read.append(_message("m1", "user", "nope"))

    def test_read_handle_cannot_replace(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        factory.create_tome("/tmp", tome_id="t1")
        read = factory.open_read("t1")
        with pytest.raises(RuntimeError):
            read.replace({"type": "session"}, [])

    def test_read_handle_cannot_repair(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        factory.create_tome("/tmp", tome_id="t1")
        read = factory.open_read("t1")
        with pytest.raises(RuntimeError):
            read.repair_torn_tail()


class TestAppendLeaf:
    def test_append_leaf_syncs_entry_and_header(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        write = factory.create_tome("/tmp", tome_id="t1")
        write.append(_message("m1", "user", "hello"))

        leaf = write.append_leaf("m1")

        assert leaf.payload == {"targetId": "m1"}
        entries = factory.get_entries("t1")
        assert [e.id for e in entries] == ["m1", leaf.id]
        meta = factory.open_tome("t1")
        assert meta is not None
        assert meta.active_leaf_id == "m1"

    def test_append_leaf_advances(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        write = factory.create_tome("/tmp", tome_id="t1")
        write.append(_message("m1", "user", "one"))
        write.append_leaf("m1")
        write.append(_message("m2", "user", "two"))
        write.append_leaf("m2")

        assert factory.get_leaf_id("t1") == "m2"
        meta = factory.open_tome("t1")
        assert meta is not None
        assert meta.active_leaf_id == "m2"

    def test_read_handle_cannot_append_leaf(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        factory.create_tome("/tmp", tome_id="t1")
        with pytest.raises(RuntimeError):
            factory.open_read("t1").append_leaf("m1")


class TestReplace:
    def test_replace_rewrites_atomically(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        write = factory.create_tome("/tmp", tome_id="t1")
        write.append(_message("m1", "user", "old"))

        meta = factory.open_tome("t1")
        assert meta is not None
        header = {
            "type": "session",
            "version": 1,
            "id": "t1",
            "timestamp": meta.created_at,
            "cwd": meta.cwd,
            "schema_version": "1.0",
        }
        write.replace(header, [_message("m2", "user", "new")])

        assert [e.id for e in factory.get_entries("t1")] == ["m2"]
        leftovers = list(tmp_path.glob("*.tmp"))
        assert leftovers == []

    def test_create_branched_tome_filters_to_leaf(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        write = factory.create_tome("/tmp", tome_id="parent")
        write.append(_message("e1", "user", "one"))
        write.append(_message("e2", "user", "two", parent_id="e1"))
        write.append(_message("e3", "user", "three", parent_id="e2"))

        forked = factory.create_branched_tome(
            parent_tome_id="parent", cwd="/tmp", fork_from_leaf_id="e2"
        )

        assert [e.id for e in factory.get_entries(forked.tome_id)] == ["e1", "e2"]
        meta = factory.open_tome(forked.tome_id)
        assert meta is not None
        assert meta.parent_tome_id == "parent"
        assert meta.active_leaf_id == "e2"

    def test_create_branched_tome_unknown_parent_raises(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        with pytest.raises(ValueError, match="Parent tome not found"):
            factory.create_branched_tome(
                parent_tome_id="missing", cwd="/tmp", fork_from_leaf_id=None
            )


class TestRepair:
    def test_repair_truncates_partial_tail(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        write = factory.create_tome("/tmp", tome_id="t1")
        write.append(_message("m1", "user", "kept"))

        with write.path.open("a", encoding="utf-8") as f:
            f.write('{"id": "partial", "type": "messa')

        truncated = factory.open_write("t1").repair_torn_tail()
        assert truncated > 0
        assert [e.id for e in factory.get_entries("t1")] == ["m1"]

    def test_repair_truncates_torn_multibyte_tail(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        write = factory.create_tome("/tmp", tome_id="t1")
        write.append(_message("m1", "user", "kept"))

        with write.path.open("ab") as f:
            f.write('{"id": "m2", "x": "éllo"}'.encode()[:18])

        truncated = factory.open_write("t1").repair_torn_tail()
        assert truncated > 0
        assert [e.id for e in factory.get_entries("t1")] == ["m1"]

    def test_repair_clean_file_truncates_nothing(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        write = factory.create_tome("/tmp", tome_id="t1")
        write.append(_message("m1", "user", "kept"))

        assert factory.open_write("t1").repair_torn_tail() == 0
        assert [e.id for e in factory.get_entries("t1")] == ["m1"]

    def test_repair_missing_file_truncates_nothing(self, tmp_path: Path) -> None:
        handle = TomeHandle(tmp_path, "missing", "w")
        assert handle.repair_torn_tail() == 0

    def test_repair_empty_file_truncates_nothing(self, tmp_path: Path) -> None:
        (tmp_path / "empty.jsonl").write_text("", encoding="utf-8")
        handle = TomeHandle(tmp_path, "empty", "w")
        assert handle.repair_torn_tail() == 0

    def test_repair_all_garbage_preserves_file(self, tmp_path: Path) -> None:
        (tmp_path / "garbage.jsonl").write_text(
            "not json\nmore garbage\n", encoding="utf-8"
        )
        handle = TomeHandle(tmp_path, "garbage", "w")
        assert handle.repair_torn_tail() == 0
        assert (tmp_path / "garbage.jsonl").read_text(encoding="utf-8") == (
            "not json\nmore garbage\n"
        )

    def test_repair_truncates_torn_tail_and_trailing_blanks(
        self, tmp_path: Path
    ) -> None:
        factory = TomeHandleFactory(tmp_path)
        write = factory.create_tome("/tmp", tome_id="t1")
        write.append(_message("m1", "user", "kept"))
        with write.path.open("a", encoding="utf-8") as f:
            f.write("\n")
            f.write('{"id": "partial", "type": "messa')

        assert factory.open_write("t1").repair_torn_tail() > 0
        lines = write.path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert [e.id for e in factory.get_entries("t1")] == ["m1"]


class TestFactoryEdges:
    def test_open_tome_unknown_id_returns_none(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        assert factory.open_tome("missing") is None

    def test_open_tome_resolves_file_path(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        tome_id = factory.create_tome("/tmp").tome_id

        meta = factory.open_tome(str(factory.tome_file(tome_id)))
        assert meta is not None
        assert meta.id == tome_id

    def test_open_tome_missing_header_fields_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "nocwd.jsonl").write_text(
            '{"type":"session","version":1,"id":"nocwd",'
            '"timestamp":"2026-01-01T00:00:00Z"}\n',
            encoding="utf-8",
        )
        factory = TomeHandleFactory(tmp_path)
        assert factory.open_tome("nocwd") is None
        assert factory.list_tomes() == []

    def test_tome_file_unknown_id_returns_raw_path(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        assert factory.tome_file("missing") == tmp_path / "missing.jsonl"

    def test_tome_file_resolves_prefix(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        tome_id = "098b2ee4a1b2c3d4e5f6789012345678"
        factory.create_tome("/tmp", tome_id=tome_id)
        assert factory.tome_file("098b2ee4") == tmp_path / f"{tome_id}.jsonl"

    def test_list_tomes_missing_dir_is_empty(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path / "nodir")
        assert factory.list_tomes() == []

    def test_open_recent_missing_dir_is_none(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path / "nodir")
        assert factory.open_recent("/tmp") is None

    def test_open_recent_no_cwd_match_is_none(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        factory.create_tome("/other")
        assert factory.open_recent("/tmp") is None

    def test_open_write_corrupt_header_raises(self, tmp_path: Path) -> None:
        (tmp_path / "corrupt.jsonl").write_text("NOT_JSON\n", encoding="utf-8")
        factory = TomeHandleFactory(tmp_path)
        with pytest.raises(ValueError, match="no readable header"):
            factory.open_write("corrupt")

    def test_create_branched_tome_unreadable_parent_raises(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "broken.jsonl").write_text('{"type":"other"}\n', encoding="utf-8")
        factory = TomeHandleFactory(tmp_path)
        with pytest.raises(ValueError, match="metadata not found"):
            factory.create_branched_tome(
                parent_tome_id="broken", cwd="/tmp", fork_from_leaf_id=None
            )

    def test_get_parent_summoner_entry_without_user_message(
        self, tmp_path: Path
    ) -> None:
        factory = TomeHandleFactory(tmp_path)
        write = factory.create_tome("/tmp", tome_id="t1")
        write.append(_message("m1", "assistant", "only assistant"))
        assert factory.get_parent_summoner_entry("t1") is None

    def test_verify_integrity_resolves_file_path(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        tome_id = factory.create_tome("/tmp").tome_id
        report = factory.verify_integrity(str(factory.tome_file(tome_id)))
        assert report.valid is True
        assert report.tome_id == tome_id

    def test_fsync_dir_posix_branch(self, tmp_path: Path, monkeypatch) -> None:
        import mvge.tome.handle as handle_mod

        monkeypatch.setattr(handle_mod.os, "name", "posix")
        monkeypatch.setattr(handle_mod.os, "open", lambda path, flags: 9)
        fsynced: list[int] = []
        monkeypatch.setattr(handle_mod.os, "fsync", fsynced.append)
        closed: list[int] = []
        monkeypatch.setattr(handle_mod.os, "close", closed.append)

        handle_mod._fsync_dir(tmp_path)

        assert fsynced == [9]
        assert closed == [9]


class TestResolution:
    def test_open_write_missing_raises_without_creating(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        with pytest.raises(FileNotFoundError, match="Tome not found"):
            factory.open_write("missing")
        assert not (tmp_path / "missing.jsonl").exists()

    def test_open_read_missing_raises(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        with pytest.raises(FileNotFoundError, match="Tome not found"):
            factory.open_read("missing")

    def test_open_tome_resolves_unique_prefix(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        tome_id = "098b2ee4a1b2c3d4e5f6789012345678"
        factory.create_tome("/tmp", tome_id=tome_id)

        assert factory.open_tome("098b2ee4") is not None
        assert factory.open_tome("098b2ee4").id == tome_id  # type: ignore[union-attr]

    def test_open_tome_exact_match_wins(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        factory.create_tome("/tmp", tome_id="098b2ee4")
        factory.create_tome("/tmp", tome_id="098b2ee4a1b2c3d4e5f6789012345678")

        meta = factory.open_tome("098b2ee4")
        assert meta is not None
        assert meta.id == "098b2ee4"

    def test_open_tome_ambiguous_prefix_returns_none(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        factory.create_tome("/tmp", tome_id="098b2ee4a1b2c3d4")
        factory.create_tome("/tmp", tome_id="098b2ee4e5f67890")

        assert factory.open_tome("098b2ee4") is None

    def test_open_tome_case_insensitive(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        tome_id = "098b2ee4a1b2c3d4e5f6789012345678"
        factory.create_tome("/tmp", tome_id=tome_id)

        meta = factory.open_tome("098B2EE4")
        assert meta is not None
        assert meta.id == tome_id

    def test_open_tome_empty_string_returns_none(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        factory.create_tome("/tmp", tome_id="098b2ee4a1b2c3d4")
        assert factory.open_tome("") is None

    def test_open_tome_raw_file_path(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        tome_id = "098b2ee4a1b2c3d4e5f6789012345678"
        factory.create_tome("/tmp", tome_id=tome_id)

        meta = factory.open_tome(str(factory.tome_file(tome_id)))
        assert meta is not None
        assert meta.id == tome_id

    def test_open_read_unsupported_version_raises(self, tmp_path: Path) -> None:
        (tmp_path / "future-5.jsonl").write_text(
            '{"type":"session","version":5,"id":"future-5",'
            '"timestamp":"2026-01-01T00:00:00Z","cwd":"/tmp"}\n',
            encoding="utf-8",
        )
        factory = TomeHandleFactory(tmp_path)
        with pytest.raises(TomeVersionError) as exc_info:
            factory.open_read("future-5")
        assert exc_info.value.version == 5

    def test_open_read_invalid_version_string_raises(self, tmp_path: Path) -> None:
        (tmp_path / "bad-version.jsonl").write_text(
            '{"type":"session","version":"not-int","id":"bad-version",'
            '"timestamp":"2026-01-01T00:00:00Z","cwd":"/tmp"}\n',
            encoding="utf-8",
        )
        factory = TomeHandleFactory(tmp_path)
        with pytest.raises(TomeVersionError):
            factory.open_read("bad-version")

    def test_list_tomes_skips_unsupported_versions(self, tmp_path: Path) -> None:
        (tmp_path / "valid-1.jsonl").write_text(
            '{"type":"session","version":1,"id":"valid-1",'
            '"timestamp":"2026-01-01T00:00:00Z","cwd":"/tmp"}\n',
            encoding="utf-8",
        )
        (tmp_path / "future-99.jsonl").write_text(
            '{"type":"session","version":99,"id":"future-99",'
            '"timestamp":"2026-01-01T00:00:00Z","cwd":"/tmp"}\n',
            encoding="utf-8",
        )
        factory = TomeHandleFactory(tmp_path)
        assert [m.id for m in factory.list_tomes()] == ["valid-1"]

    def test_open_recent_skips_incompatible_version(self, tmp_path: Path) -> None:
        (tmp_path / "future-recent.jsonl").write_text(
            '{"type":"session","version":4,"id":"future-recent",'
            '"timestamp":"2026-01-01T00:00:00Z","cwd":"/workspace/target"}\n',
            encoding="utf-8",
        )
        (tmp_path / "valid-recent.jsonl").write_text(
            '{"type":"session","version":1,"id":"valid-recent",'
            '"timestamp":"2026-01-01T00:00:00Z","cwd":"/workspace/target"}\n',
            encoding="utf-8",
        )
        factory = TomeHandleFactory(tmp_path)
        recent = factory.open_recent("/workspace/target")
        assert recent is not None
        assert recent.id == "valid-recent"


class TestTomeIdValidation:
    def test_open_tome_rejects_traversal(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside.jsonl"
        outside.write_text(
            '{"type":"session","version":1,"id":"outside",'
            '"timestamp":"2026-01-01T00:00:00Z","cwd":"/tmp"}\n',
            encoding="utf-8",
        )
        factory = TomeHandleFactory(tmp_path / "tomes")
        with pytest.raises(ValueError, match="Invalid tome id"):
            factory.open_tome("../outside")
        assert outside.exists()

    def test_tome_file_rejects_traversal(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path / "tomes")
        with pytest.raises(ValueError, match="Invalid tome id"):
            factory.tome_file("../../etc/passwd")

    def test_verify_integrity_rejects_traversal(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path / "tomes")
        with pytest.raises(ValueError, match="Invalid tome id"):
            factory.verify_integrity("../outside")

    def test_create_tome_rejects_traversal_and_writes_nothing_outside(
        self, tmp_path: Path
    ) -> None:
        factory = TomeHandleFactory(tmp_path / "tomes")
        with pytest.raises(ValueError, match="Invalid tome id"):
            factory.create_tome("/tmp", tome_id="../escaped")
        assert not (tmp_path / "escaped.jsonl").exists()

    def test_create_tome_rejects_absolute_id(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        with pytest.raises(ValueError, match="Invalid tome id"):
            factory.create_tome("/tmp", tome_id="/abs/path")

    def test_create_tome_duplicate_id_raises_and_preserves_original(
        self, tmp_path: Path
    ) -> None:
        factory = TomeHandleFactory(tmp_path)
        first = factory.create_tome("/tmp", tome_id="dup")
        first.append(_message("m1", "user", "original"))
        with pytest.raises(ValueError, match="already exists"):
            factory.create_tome("/tmp", tome_id="dup")
        assert [e.id for e in factory.get_entries("dup")] == ["m1"]

    def test_create_branched_tome_duplicate_id_raises(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        factory.create_tome("/tmp", tome_id="parent")
        factory.create_tome("/tmp", tome_id="taken")
        with pytest.raises(ValueError, match="already exists"):
            factory.create_branched_tome("parent", "/tmp", tome_id="taken")


def _write_v1_file(path: Path, session_id: str) -> None:
    path.write_text(
        json.dumps(
            {
                "type": "session",
                "version": 1,
                "id": session_id,
                "timestamp": "2026-01-01T00:00:00Z",
                "cwd": "/tmp",
            }
        )
        + "\n",
        encoding="utf-8",
    )


class TestForeignFilenames:
    """Files opened by explicit path may follow foreign naming conventions.

    Pi names sessions ``{timestamp}_{id}.jsonl``; users may also hand us
    dotted names like ``my.session.backup.jsonl``. The handle must tolerate
    any safe filename and identify the session by its header, not its stem.
    """

    def test_open_read_dotted_filename_by_path(self, tmp_path: Path) -> None:
        target = tmp_path / "my.session.backup.jsonl"
        _write_v1_file(target, "real-session-id")
        factory = TomeHandleFactory(tmp_path / "tomes")

        handle = factory.open_read(str(target))

        assert handle.get_metadata() is not None
        assert handle.get_metadata().id == "real-session-id"  # type: ignore[union-attr]

    def test_open_read_prefers_header_session_id_over_stem(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "2026-09-19T03-22-11-234Z_abc123.jsonl"
        _write_v1_file(target, "session-uuid-here")
        factory = TomeHandleFactory(tmp_path / "tomes")

        handle = factory.open_read(str(target))

        assert handle.tome_id == "session-uuid-here"

    def test_open_write_dotted_filename_appends(self, tmp_path: Path) -> None:
        target = tmp_path / "my.session.backup.jsonl"
        _write_v1_file(target, "real-session-id")
        factory = TomeHandleFactory(tmp_path / "tomes")

        writer = factory.open_write(str(target))
        writer.append(_message("m1", "user", "hello"))

        assert [e.id for e in factory.open_read(str(target)).get_entries()] == ["m1"]

    def test_open_read_unparseable_dotted_file_falls_back_to_stem(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "odd.name.jsonl"
        target.write_text("not json at all\n", encoding="utf-8")
        factory = TomeHandleFactory(tmp_path / "tomes")

        handle = factory.open_read(str(target))

        assert handle.tome_id == "odd.name"
        assert handle.get_entries() == []

    def test_create_tome_still_rejects_dotted_id(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        with pytest.raises(ValueError, match="Invalid tome id"):
            factory.create_tome("/tmp", tome_id="with.dot")
        assert not (tmp_path / "with.dot.jsonl").exists()


class TestExplicitPathOperations:
    """BUG-1/2/3: factory operations must work on explicit foreign paths.

    The CLI threads the user's locator through; these tests pin the factory
    side of that contract: verify/export/branch must treat an explicit path
    as authoritative and never re-resolve through the header id.
    """

    def _foreign(self, tmp_path: Path) -> tuple[TomeHandleFactory, Path]:
        factory = TomeHandleFactory(tmp_path / "tomes")
        target = tmp_path / "my.pi.session.backup.jsonl"
        _write_v1_file(target, "dotheaderid")
        with target.open("a", encoding="utf-8") as f:
            for eid, parent, role, content in (
                ("m1", None, "user", "hello"),
                ("m2", "m1", "assistant", "hi there"),
            ):
                f.write(
                    json.dumps(
                        {
                            "id": eid,
                            "parentId": parent,
                            "type": "message",
                            "timestamp": 1000.0,
                            "payload": {"role": role, "content": content},
                        }
                    )
                    + "\n"
                )
        return factory, target

    def test_verify_integrity_explicit_path_is_header_authoritative(
        self, tmp_path: Path
    ) -> None:
        """BUG-3: a file opened by explicit path is identified by its header;
        the stem-vs-header check must not fire."""
        factory, target = self._foreign(tmp_path)

        report = factory.verify_integrity(str(target))

        assert report.valid is True, [i.message for i in report.issues]

    def test_verify_integrity_id_lookup_still_flags_stem_mismatch(
        self, tmp_path: Path
    ) -> None:
        """In-dir lookups by id keep the strict stem check: a file whose
        header id disagrees with its filename is still flagged."""
        factory = TomeHandleFactory(tmp_path / "tomes")
        (tmp_path / "tomes").mkdir(parents=True, exist_ok=True)
        _write_v1_file(tmp_path / "tomes" / "other.jsonl", "dotheaderid")

        report = factory.verify_integrity("other")

        assert report.valid is False
        assert any("Header ID mismatch" in i.message for i in report.issues)

    def test_export_atif_trajectory_explicit_path(self, tmp_path: Path) -> None:
        factory, target = self._foreign(tmp_path)

        data = factory.export_atif_trajectory(str(target), "test-agent")

        assert data["trajectory_id"] == "dotheaderid"
        assert len(data["steps"]) == 2

    def test_create_branched_tome_accepts_explicit_path_parent(
        self, tmp_path: Path
    ) -> None:
        factory, target = self._foreign(tmp_path)
        (tmp_path / "tomes").mkdir(parents=True, exist_ok=True)

        forked = factory.create_branched_tome(parent_tome_id=str(target), cwd="/tmp")

        assert [e.id for e in forked.get_entries()] == ["m1", "m2"]
        assert forked.path.parent == tmp_path / "tomes"


class TestPlanAppendHandle:
    def test_rewrite_plan_replaces_file_atomically(self, tmp_path: Path) -> None:
        class RewriteCodec(TomeV1Codec):
            name = "rewrite"

            def plan_append(self, entry, existing, header):
                return AppendPlan(
                    lines=[{"entry": entry.id}],
                    stored=entry,
                    rewrite=True,
                    header={**header, "migrated": True},
                )

        factory = TomeHandleFactory(tmp_path)
        factory.create_tome("/tmp", tome_id="t1")

        write = TomeHandle(
            tmp_path, "t1", "w", RewriteCodec(), path=tmp_path / "t1.jsonl"
        )
        write.append(_message("m1", "user", "hello"))

        lines = (tmp_path / "t1.jsonl").read_text().splitlines()
        assert json.loads(lines[0])["migrated"] is True
        assert json.loads(lines[1]) == {"entry": "m1"}
        assert list(tmp_path.glob("*.tmp")) == []

    def test_multi_line_plan_appends_each_line(self, tmp_path: Path) -> None:
        class TxnCodec(TomeV1Codec):
            name = "txn"

            def plan_append(self, entry, existing, header):
                return AppendPlan(
                    lines=[[{"w": 1}, {"w": 2}], {"w": 3}],
                    stored=entry,
                )

        factory = TomeHandleFactory(tmp_path)
        factory.create_tome("/tmp", tome_id="t1")

        write = TomeHandle(tmp_path, "t1", "w", TxnCodec(), path=tmp_path / "t1.jsonl")
        write.append(_message("m1", "user", "hello"))

        lines = (tmp_path / "t1.jsonl").read_text().splitlines()
        assert json.loads(lines[1]) == [{"w": 1}, {"w": 2}]
        assert json.loads(lines[2]) == {"w": 3}


class TestSetName:
    def test_set_name_persists_and_preserves_entries(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        write = factory.create_tome("/tmp", tome_id="t1")
        write.append(_message("m1", "user", "hello"))
        write.set_name("Coffee Chat")

        read = factory.open_read("t1")
        assert read.get_metadata().name == "Coffee Chat"
        assert [e.id for e in read.get_entries()] == ["m1"]
        header_line = (tmp_path / "t1.jsonl").read_text().splitlines()[0]
        assert json.loads(header_line)["name"] == "Coffee Chat"

    def test_set_name_on_read_handle_raises(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        factory.create_tome("/tmp", tome_id="t1")
        read = factory.open_read("t1")
        with pytest.raises(RuntimeError):
            read.set_name("Nope")


class TestAppendTipLinesHandle:
    def test_tip_lines_append_without_rewrite(self, tmp_path: Path) -> None:
        class TipCodec(TomeV1Codec):
            name = "tip"

            def append_tip_lines(self, header, entries, leaf):
                return [{"tip": leaf.payload["targetId"]}]

        factory = TomeHandleFactory(tmp_path)
        factory.create_tome("/tmp", tome_id="t1")

        write = TomeHandle(tmp_path, "t1", "w", TipCodec(), path=tmp_path / "t1.jsonl")
        leaf = write.append_leaf("entry-9")

        assert leaf.payload == {"targetId": "entry-9"}
        lines = (tmp_path / "t1.jsonl").read_text().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[1]) == {"tip": "entry-9"}
        # the leaf marker is not a file entry for tip-only codecs
        assert [e.id for e in write.get_entries()] == []
