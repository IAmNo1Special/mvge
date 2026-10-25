from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import pytest

from mvge.tome.handle import TomeHandle, TomeHandleFactory
from mvge.tome.types import TomeEntry, TomeEntryType, TomeIntegrityReport


def _write_raw_tome_file(
    tome_dir: Path, filename: str, lines: list[str | dict]
) -> Path:
    target = tome_dir / filename
    with target.open("w", encoding="utf-8") as f:
        for item in lines:
            if isinstance(item, dict):
                f.write(json.dumps(item) + "\n")
            else:
                f.write(item + "\n")
    return target


def _factory(tmp_path: Path) -> TomeHandleFactory:
    return TomeHandleFactory(tmp_path)


def _message(
    write: TomeHandle,
    entry_id: str,
    role: str,
    content: str,
    parent_id: str | None = None,
) -> TomeEntry:
    entry = TomeEntry(
        id=entry_id,
        parent_id=parent_id,
        type=TomeEntryType.MESSAGE,
        timestamp=time.time(),
        payload={"role": role, "content": content},
    )
    write.append(entry)
    return entry


def _leaf(write: TomeHandle, entry_id: str, target: str) -> TomeEntry:
    entry = TomeEntry(
        id=entry_id,
        parent_id=None,
        type=TomeEntryType.LEAF,
        timestamp=time.time(),
        payload={"targetId": target},
    )
    write.append(entry)
    return entry


class TestTomeIntegrityVerification:
    def test_verify_integrity_valid_tome(self, tmp_path: Path) -> None:
        factory = _factory(tmp_path)
        write = factory.create_tome("/test/workspace", tome_id="t1")
        e1 = _message(write, "e1", "user", "hello")
        _leaf(write, "l1", e1.id)

        report = factory.verify_integrity("t1")

        assert isinstance(report, TomeIntegrityReport)
        assert report.valid is True
        assert report.tome_id == "t1"
        assert len(report.issues) == 0
        assert report.total_lines == 3
        assert report.valid_entries_count == 2
        assert bool(report) is True

    def test_verify_integrity_missing_file(self, tmp_path: Path) -> None:
        factory = _factory(tmp_path)

        report = factory.verify_integrity("nonexistent-tome")

        assert report.valid is False
        assert len(report.issues) == 1
        assert report.issues[0].line_number == 0
        assert "does not exist" in report.issues[0].message.lower()

    def test_verify_integrity_empty_file(self, tmp_path: Path) -> None:
        factory = _factory(tmp_path)
        empty_file = tmp_path / "empty-tome.jsonl"
        empty_file.touch()

        report = factory.verify_integrity("empty-tome")

        assert report.valid is False
        assert len(report.issues) == 1
        assert report.issues[0].line_number == 1
        assert "empty" in report.issues[0].message.lower()

    def test_verify_integrity_corrupted_header_json(self, tmp_path: Path) -> None:
        _write_raw_tome_file(tmp_path, "bad-header.jsonl", ["{not-valid-json"])
        factory = _factory(tmp_path)

        report = factory.verify_integrity("bad-header")

        assert report.valid is False
        assert any(
            i.line_number == 1 and "header" in i.message.lower() for i in report.issues
        )

    def test_verify_integrity_header_not_object(self, tmp_path: Path) -> None:
        _write_raw_tome_file(tmp_path, "arr-header.jsonl", ['["not", "an", "object"]'])
        factory = _factory(tmp_path)

        report = factory.verify_integrity("arr-header")

        assert report.valid is False
        assert report.issues[0].line_number == 1
        assert "object" in report.issues[0].message.lower()

    def test_verify_integrity_header_type_mismatch(self, tmp_path: Path) -> None:
        header = {
            "type": "message",
            "id": "type-mismatch",
            "timestamp": "2026-01-01T00:00:00Z",
            "cwd": "/tmp",
        }
        _write_raw_tome_file(tmp_path, "type-mismatch.jsonl", [header])
        factory = _factory(tmp_path)

        report = factory.verify_integrity("type-mismatch")

        assert report.valid is False
        assert report.issues[0].line_number == 1
        assert "session" in report.issues[0].message.lower()

    def test_verify_integrity_header_id_mismatch(self, tmp_path: Path) -> None:
        header = {
            "type": "session",
            "id": "actual-id-999",
            "timestamp": "2026-01-01T00:00:00Z",
            "cwd": "/tmp",
        }
        _write_raw_tome_file(tmp_path, "expected-filename.jsonl", [header])
        factory = _factory(tmp_path)

        report = factory.verify_integrity("expected-filename")

        assert report.valid is False
        assert report.issues[0].line_number == 1
        assert "mismatch" in report.issues[0].message.lower()

    def test_verify_integrity_header_missing_required_fields(
        self, tmp_path: Path
    ) -> None:
        header = {
            "type": "session",
            "id": "missing-fields",
        }
        _write_raw_tome_file(tmp_path, "missing-fields.jsonl", [header])
        factory = _factory(tmp_path)

        report = factory.verify_integrity("missing-fields")

        assert report.valid is False
        assert report.issues[0].line_number == 1

    def test_verify_integrity_truncated_write_in_entries(self, tmp_path: Path) -> None:
        header = {
            "type": "session",
            "id": "truncated-tome",
            "timestamp": "2026-01-01T00:00:00Z",
            "cwd": "/tmp",
        }
        entry1 = {
            "id": "e1",
            "parentId": None,
            "type": "message",
            "timestamp": time.time(),
            "payload": {"role": "user", "content": "hello"},
        }
        truncated_line = '{"id": "e2", "parentId": "e1", "type": "messa'
        _write_raw_tome_file(
            tmp_path, "truncated-tome.jsonl", [header, entry1, truncated_line]
        )
        factory = _factory(tmp_path)

        report = factory.verify_integrity("truncated-tome")

        assert report.valid is False
        assert len(report.issues) == 1
        assert report.issues[0].line_number == 3
        assert (
            "json" in report.issues[0].message.lower()
            or "truncated" in report.issues[0].message.lower()
        )
        assert report.total_lines == 3
        assert report.valid_entries_count == 1

    def test_verify_integrity_invalid_json_entry(self, tmp_path: Path) -> None:
        header = {
            "type": "session",
            "id": "invalid-json",
            "timestamp": "2026-01-01T00:00:00Z",
            "cwd": "/tmp",
        }
        garbage_line = "<<<<< GARBAGE DATA >>>>>"
        _write_raw_tome_file(tmp_path, "invalid-json.jsonl", [header, garbage_line])
        factory = _factory(tmp_path)

        report = factory.verify_integrity("invalid-json")

        assert report.valid is False
        assert report.issues[0].line_number == 2
        assert "json" in report.issues[0].message.lower()

    def test_verify_integrity_entry_not_object(self, tmp_path: Path) -> None:
        header = {
            "type": "session",
            "id": "arr-entry",
            "timestamp": "2026-01-01T00:00:00Z",
            "cwd": "/tmp",
        }
        _write_raw_tome_file(tmp_path, "arr-entry.jsonl", [header, "[1, 2, 3]"])
        factory = _factory(tmp_path)

        report = factory.verify_integrity("arr-entry")

        assert report.valid is False
        assert report.issues[0].line_number == 2
        assert "object" in report.issues[0].message.lower()

    def test_verify_integrity_entry_missing_id(self, tmp_path: Path) -> None:
        header = {
            "type": "session",
            "id": "missing-id",
            "timestamp": "2026-01-01T00:00:00Z",
            "cwd": "/tmp",
        }
        entry = {
            "parentId": None,
            "type": "message",
            "timestamp": time.time(),
            "payload": {},
        }
        _write_raw_tome_file(tmp_path, "missing-id.jsonl", [header, entry])
        factory = _factory(tmp_path)

        report = factory.verify_integrity("missing-id")

        assert report.valid is False
        assert report.issues[0].line_number == 2
        assert "id" in report.issues[0].message.lower()

    def test_verify_integrity_entry_invalid_type(self, tmp_path: Path) -> None:
        header = {
            "type": "session",
            "id": "bad-type",
            "timestamp": "2026-01-01T00:00:00Z",
            "cwd": "/tmp",
        }
        entry = {
            "id": "e1",
            "parentId": None,
            "type": "completely_invalid_type_123",
            "timestamp": time.time(),
            "payload": {},
        }
        _write_raw_tome_file(tmp_path, "bad-type.jsonl", [header, entry])
        factory = _factory(tmp_path)

        report = factory.verify_integrity("bad-type")

        assert report.valid is False
        assert report.issues[0].line_number == 2
        assert "type" in report.issues[0].message.lower()

    def test_verify_integrity_entry_invalid_timestamp(self, tmp_path: Path) -> None:
        header = {
            "type": "session",
            "id": "bad-ts",
            "timestamp": "2026-01-01T00:00:00Z",
            "cwd": "/tmp",
        }
        entry = {
            "id": "e1",
            "parentId": None,
            "type": "message",
            "timestamp": "not-a-number",
            "payload": {},
        }
        _write_raw_tome_file(tmp_path, "bad-ts.jsonl", [header, entry])
        factory = _factory(tmp_path)

        report = factory.verify_integrity("bad-ts")

        assert report.valid is False
        assert report.issues[0].line_number == 2
        assert "timestamp" in report.issues[0].message.lower()

    def test_verify_integrity_entry_invalid_payload(self, tmp_path: Path) -> None:
        header = {
            "type": "session",
            "id": "bad-payload",
            "timestamp": "2026-01-01T00:00:00Z",
            "cwd": "/tmp",
        }
        entry = {
            "id": "e1",
            "parentId": None,
            "type": "message",
            "timestamp": time.time(),
            "payload": "string-not-dict",
        }
        _write_raw_tome_file(tmp_path, "bad-payload.jsonl", [header, entry])
        factory = _factory(tmp_path)

        report = factory.verify_integrity("bad-payload")

        assert report.valid is False
        assert report.issues[0].line_number == 2
        assert "payload" in report.issues[0].message.lower()

    def test_verify_integrity_empty_line_in_stream(self, tmp_path: Path) -> None:
        header = {
            "type": "session",
            "id": "blank-lines",
            "timestamp": "2026-01-01T00:00:00Z",
            "cwd": "/tmp",
        }
        entry = {
            "id": "e1",
            "parentId": None,
            "type": "message",
            "timestamp": time.time(),
            "payload": {},
        }
        _write_raw_tome_file(tmp_path, "blank-lines.jsonl", [header, "", entry])
        factory = _factory(tmp_path)

        report = factory.verify_integrity("blank-lines")

        assert report.valid is False
        assert any(i.line_number == 2 for i in report.issues)

    def test_verify_integrity_header_empty_line(self, tmp_path: Path) -> None:
        file_path = tmp_path / "empty-hdr.jsonl"
        file_path.write_text("\n", encoding="utf-8")
        factory = _factory(tmp_path)
        report = factory.verify_integrity("empty-hdr")
        assert report.valid is False
        assert report.issues[0].line_number == 1

    def test_verify_integrity_unsupported_version(self, tmp_path: Path) -> None:
        header = {
            "type": "session",
            "version": 4,
            "id": "unsupported-ver",
            "timestamp": "2026-01-01T00:00:00Z",
            "cwd": "/tmp",
        }
        _write_raw_tome_file(tmp_path, "unsupported-ver.jsonl", [header])
        factory = _factory(tmp_path)
        report = factory.verify_integrity("unsupported-ver")
        assert report.valid is False
        assert "unsupported" in report.issues[0].message.lower()

    def test_verify_integrity_invalid_version_string(self, tmp_path: Path) -> None:
        header = {
            "type": "session",
            "version": "bad-string",
            "id": "invalid-ver-str",
            "timestamp": "2026-01-01T00:00:00Z",
            "cwd": "/tmp",
        }
        _write_raw_tome_file(tmp_path, "invalid-ver-str.jsonl", [header])
        factory = _factory(tmp_path)
        report = factory.verify_integrity("invalid-ver-str")
        assert report.valid is False
        assert "invalid" in report.issues[0].message.lower()


class TestReadResilienceWithCorruptedLines:
    def test_read_entries_skips_damaged_records_and_logs_warning(
        self, caplog: pytest.LogCaptureFixture, tmp_path: Path
    ) -> None:
        header = {
            "type": "session",
            "id": "resilient-tome",
            "timestamp": "2026-01-01T00:00:00Z",
            "cwd": "/tmp",
        }
        entry1 = {
            "id": "e1",
            "parentId": None,
            "type": "message",
            "timestamp": time.time(),
            "payload": {"role": "user", "content": "first valid"},
        }
        corrupted_line = '{"id": "e2", "parentId": "e1", TRUNCATED...'
        entry3 = {
            "id": "e3",
            "parentId": "e1",
            "type": "message",
            "timestamp": time.time(),
            "payload": {"role": "assistant", "content": "third valid"},
        }
        _write_raw_tome_file(
            tmp_path,
            "resilient-tome.jsonl",
            [header, entry1, corrupted_line, entry3],
        )
        factory = _factory(tmp_path)

        with caplog.at_level(logging.WARNING):
            entries = factory.get_entries("resilient-tome")

        assert len(entries) == 2
        assert entries[0].id == "e1"
        assert entries[1].id == "e3"
        assert any(
            "resilient-tome" in record.message and "line 3" in record.message
            for record in caplog.records
        )
