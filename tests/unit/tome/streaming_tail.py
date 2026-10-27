from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mvge.tome.handle import TomeHandleFactory
from mvge.tome.types import TomeEntry, TomeEntryType, TomeVersionError


def _write_raw_tome(
    tome_dir: Path, tome_id: str, entries_data: list[dict[str, Any]]
) -> Path:
    tome_file = tome_dir / f"{tome_id}.jsonl"
    header = {
        "type": "session",
        "version": 1,
        "id": tome_id,
        "timestamp": "2026-01-01T00:00:00+00:00",
        "cwd": str(tome_dir),
        "schema_version": "1.0",
    }
    with tome_file.open("w", encoding="utf-8") as f:
        f.write(json.dumps(header) + "\n")
        for entry in entries_data:
            f.write(json.dumps(entry) + "\n")
    return tome_file


def _message(
    entry_id: str, role: str, content: str, timestamp: float = 1000.0
) -> TomeEntry:
    return TomeEntry(
        id=entry_id,
        parent_id=None,
        type=TomeEntryType.MESSAGE,
        timestamp=timestamp,
        payload={"role": role, "content": content},
    )


def _leaf(entry_id: str, target: str) -> TomeEntry:
    return TomeEntry(
        id=entry_id,
        parent_id=None,
        type=TomeEntryType.LEAF,
        timestamp=1001.0,
        payload={"targetId": target},
    )


class TestIterEntries:
    def test_iter_entries_yields_entries_sequentially(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        write = factory.create_tome(str(tmp_path), tome_id="t1")
        write.append(_message("m1", "user", "one"))
        write.append(_leaf("l1", "m1"))
        write.append(_message("m2", "assistant", "two"))

        entries = list(factory.open_read("t1").iter_entries())

        assert len(entries) == 3
        assert [e.id for e in entries] == ["m1", "l1", "m2"]
        assert entries[0].type == TomeEntryType.MESSAGE
        assert entries[1].type == TomeEntryType.LEAF
        assert entries[2].type == TomeEntryType.MESSAGE
        assert entries[0].payload["content"] == "one"

    def test_iter_entries_sees_external_appends(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        write = factory.create_tome(str(tmp_path), tome_id="t1")
        write.append(_message("m1", "user", "one"))

        read = factory.open_read("t1")
        assert [e.id for e in read.iter_entries()] == ["m1"]

        write.append(_message("m2", "user", "two"))
        assert [e.id for e in read.iter_entries()] == ["m1", "m2"]

    def test_iter_entries_skips_corrupted_lines_gracefully(
        self, tmp_path: Path
    ) -> None:
        tome_id = "test-corrupt"
        entries_data = [
            {
                "id": "e1",
                "parentId": None,
                "type": "message",
                "timestamp": 100.0,
                "payload": {"content": "first"},
            },
            {
                "id": "e2",
                "parentId": "e1",
                "type": "message",
                "timestamp": 101.0,
                "payload": {"content": "second"},
            },
        ]
        tome_file = _write_raw_tome(tmp_path, tome_id, entries_data)

        # Insert corrupt lines
        with tome_file.open("a", encoding="utf-8") as f:
            f.write("not valid json\n")
            f.write('{"id": "e3", "type": "invalid_type", "timestamp": 102.0}\n')
            f.write(
                json.dumps(
                    {
                        "id": "e4",
                        "parentId": "e2",
                        "type": "message",
                        "timestamp": 103.0,
                        "payload": {"content": "third"},
                    }
                )
                + "\n"
            )

        factory = TomeHandleFactory(tmp_path)
        streamed = list(factory.open_read(tome_id).iter_entries())
        assert [e.id for e in streamed] == ["e1", "e2", "e4"]

    def test_iter_entries_empty_file_or_header_only(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        factory.create_tome(str(tmp_path), tome_id="t1")

        assert list(factory.open_read("t1").iter_entries()) == []

    def test_iter_entries_missing_tome_raises(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        with pytest.raises(FileNotFoundError):
            factory.open_read("non-existent-tome")

    def test_iter_entries_unsupported_version_raises(self, tmp_path: Path) -> None:
        file_path = tmp_path / "v9.jsonl"
        file_path.write_text(
            '{"type": "session", "version": 9, "id": "v9", '
            '"timestamp": "2026-01-01T00:00:00Z", "cwd": "/tmp"}\n',
            encoding="utf-8",
        )
        factory = TomeHandleFactory(tmp_path)
        with pytest.raises(TomeVersionError):
            factory.open_read("v9")

    def test_iter_entries_invalid_version_raises(self, tmp_path: Path) -> None:
        file_path = tmp_path / "bad-ver.jsonl"
        file_path.write_text(
            '{"type": "session", "version": "not-a-num", "id": "bad-ver", '
            '"timestamp": "2026-01-01T00:00:00Z", "cwd": "/tmp"}\n',
            encoding="utf-8",
        )
        factory = TomeHandleFactory(tmp_path)
        with pytest.raises(TomeVersionError):
            factory.open_read("bad-ver")

    def test_iter_entries_header_invalid_json(self, tmp_path: Path) -> None:
        file_path = tmp_path / "bad-json.jsonl"
        file_path.write_text("NOT_JSON\n", encoding="utf-8")
        factory = TomeHandleFactory(tmp_path)
        assert factory.open_read("bad-json").get_entries() == []

    def test_iter_entries_header_not_session(self, tmp_path: Path) -> None:
        file_path = tmp_path / "not-session.jsonl"
        file_path.write_text('{"type": "other"}\n', encoding="utf-8")
        factory = TomeHandleFactory(tmp_path)
        assert factory.open_read("not-session").get_entries() == []

    def test_iter_entries_entry_not_object(self, tmp_path: Path) -> None:
        file_path = tmp_path / "entry-string.jsonl"
        file_path.write_text(
            '{"type": "session", "version": 1, "id": "entry-string", '
            '"timestamp": "2026-01-01T00:00:00Z", "cwd": "/tmp"}\n'
            '"just-a-string"\n\n',
            encoding="utf-8",
        )
        factory = TomeHandleFactory(tmp_path)
        assert factory.open_read("entry-string").get_entries() == []


class TestReadLastNEntries:
    def test_read_last_n_entries_bounded_slice(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        write = factory.create_tome(str(tmp_path), tome_id="t1")
        for i in range(10):
            write.append(_message(f"m{i}", "user", f"msg-{i}", timestamp=1000.0 + i))

        tail = factory.read_last_n_entries("t1", limit=3)
        assert len(tail) == 3
        assert [e.payload["content"] for e in tail] == [
            "msg-7",
            "msg-8",
            "msg-9",
        ]

    def test_read_last_n_entries_limit_greater_than_total(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        write = factory.create_tome(str(tmp_path), tome_id="t1")
        write.append(_message("m1", "user", "msg-0"))
        write.append(_message("m2", "user", "msg-1"))

        tail = factory.read_last_n_entries("t1", limit=100)
        assert len(tail) == 2
        assert [e.id for e in tail] == ["m1", "m2"]

    def test_read_last_n_entries_zero_or_negative_limit(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        write = factory.create_tome(str(tmp_path), tome_id="t1")
        write.append(_message("m1", "user", "msg-0"))

        assert factory.read_last_n_entries("t1", limit=0) == []
        assert factory.read_last_n_entries("t1", limit=-5) == []

    def test_read_last_n_entries_skips_corrupted_lines(self, tmp_path: Path) -> None:
        tome_id = "tail-corrupt"
        entries_data = [
            {
                "id": "e1",
                "parentId": None,
                "type": "message",
                "timestamp": 1.0,
                "payload": {"msg": "1"},
            },
            {
                "id": "e2",
                "parentId": "e1",
                "type": "message",
                "timestamp": 2.0,
                "payload": {"msg": "2"},
            },
        ]
        tome_file = _write_raw_tome(tmp_path, tome_id, entries_data)

        with tome_file.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "id": "e3",
                        "parentId": "e2",
                        "type": "message",
                        "timestamp": 3.0,
                        "payload": {"msg": "3"},
                    }
                )
                + "\n"
            )
            f.write("corrupted json\n")
            f.write(
                json.dumps(
                    {
                        "id": "e4",
                        "parentId": "e3",
                        "type": "message",
                        "timestamp": 4.0,
                        "payload": {"msg": "4"},
                    }
                )
                + "\n"
            )

        factory = TomeHandleFactory(tmp_path)
        tail = factory.read_last_n_entries(tome_id, limit=3)
        assert len(tail) == 3
        assert [e.id for e in tail] == ["e2", "e3", "e4"]

    def test_read_last_n_entries_continues_seeking_past_trailing_corrupt_lines(
        self, tmp_path: Path
    ) -> None:
        tome_id = "trailing-corrupt"
        entries_data = [
            {
                "id": f"valid-{i}",
                "parentId": None,
                "type": "message",
                "timestamp": float(i),
                "payload": {"msg": str(i)},
            }
            for i in range(5)
        ]
        tome_file = _write_raw_tome(tmp_path, tome_id, entries_data)

        # Append several corrupt / empty / malformed lines at the very end
        with tome_file.open("a", encoding="utf-8") as f:
            for _ in range(10):
                f.write("not valid json\n\n")

        factory = TomeHandleFactory(tmp_path)
        tail = factory.read_last_n_entries(tome_id, limit=3)
        assert len(tail) == 3
        assert [e.id for e in tail] == ["valid-2", "valid-3", "valid-4"]

    def test_read_last_n_entries_prefix_resolution(self, tmp_path: Path) -> None:
        factory = TomeHandleFactory(tmp_path)
        long_id = "0123456789abcdef0123456789abcdef"
        write = factory.create_tome(str(tmp_path), tome_id=long_id)
        write.append(_message("m1", "user", "msg1"))
        write.append(_message("m2", "user", "msg2"))

        tail = factory.read_last_n_entries("01234567", limit=1)
        assert len(tail) == 1
        assert tail[0].payload["content"] == "msg2"
