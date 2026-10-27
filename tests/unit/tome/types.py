from __future__ import annotations

from mvge.tome.types import (
    CURRENT_SESSION_VERSION,
    TomeEntry,
    TomeEntryType,
    TomeIntegrityIssue,
    TomeIntegrityReport,
    TomeMetadata,
    TomeVersionError,
)


def test_current_session_version() -> None:
    assert CURRENT_SESSION_VERSION == 1


def test_tome_entry_to_dict_round_trip() -> None:
    entry = TomeEntry(
        id="entry-1",
        parent_id=None,
        type=TomeEntryType.MESSAGE,
        timestamp=1000.0,
        payload={"text": "hello"},
    )
    assert entry.id == "entry-1"
    assert entry.type == TomeEntryType.MESSAGE
    assert entry.to_dict() == {
        "id": "entry-1",
        "parentId": None,
        "type": "message",
        "timestamp": 1000.0,
        "payload": {"text": "hello"},
    }


def test_message_payload_shape() -> None:
    entry = TomeEntry(
        id="msg-1",
        parent_id=None,
        type=TomeEntryType.MESSAGE,
        timestamp=1000.0,
        payload={
            "role": "user",
            "content": "hello",
            "model": "openai/gpt-4o",
            "provider": "openai",
        },
    )
    assert entry.payload["role"] == "user"
    assert entry.payload["content"] == "hello"
    assert entry.to_dict()["type"] == "message"


def test_leaf_payload_shape() -> None:
    entry = TomeEntry(
        id="leaf-1",
        parent_id=None,
        type=TomeEntryType.LEAF,
        timestamp=1001.0,
        payload={"targetId": "msg-1"},
    )
    assert entry.payload == {"targetId": "msg-1"}
    assert entry.to_dict()["payload"] == {"targetId": "msg-1"}


def test_tome_info_payload_shape() -> None:
    entry = TomeEntry(
        id="info-1",
        parent_id=None,
        type=TomeEntryType.TOME_INFO,
        timestamp=1002.0,
        payload={"name": "my tome"},
    )
    assert entry.payload["name"] == "my tome"
    titled = TomeEntry(
        id="info-2",
        parent_id=None,
        type=TomeEntryType.TOME_INFO,
        timestamp=1003.0,
        payload={"title": "titled"},
    )
    assert titled.payload["title"] == "titled"


def test_entry_type_values() -> None:
    assert TomeEntryType.MESSAGE.value == "message"
    assert TomeEntryType.LABEL.value == "label"
    assert TomeEntryType.COMPACTION.value == "compaction"
    assert TomeEntryType.CUSTOM.value == "custom"
    assert TomeEntryType.LEAF.value == "leaf"
    assert TomeEntryType.TOME_INFO.value == "tome_info"


def test_tome_version_error_properties() -> None:
    err = TomeVersionError(4)
    assert err.version == 4
    assert "4" in str(err)

    custom = TomeVersionError("future", "Custom version error")
    assert custom.version == "future"
    assert str(custom) == "Custom version error"


def test_tome_metadata_required_fields() -> None:
    meta = TomeMetadata(
        id="tome-1",
        created_at="2026-01-01T00:00:00+00:00",
        cwd="/home/user/project",
        parent_tome_id=None,
        active_leaf_id=None,
        model="openai/gpt-4o",
        contemplation_level="high",
        spells=["bash", "read_file"],
    )
    assert meta.id == "tome-1"
    assert meta.cwd == "/home/user/project"
    assert meta.model == "openai/gpt-4o"
    assert meta.contemplation_level == "high"
    assert meta.spells == ["bash", "read_file"]


def test_tome_metadata_defaults() -> None:
    meta = TomeMetadata(
        id="tome-2",
        created_at="2026-01-01T00:00:00+00:00",
        cwd="/tmp",
    )
    assert meta.parent_tome_id is None
    assert meta.active_leaf_id is None
    assert meta.schema_version == "1.0"
    assert meta.version == 1
    assert meta.model is None
    assert meta.contemplation_level is None
    assert meta.spells == []


def test_tome_integrity_report_properties() -> None:
    issue = TomeIntegrityIssue(line_number=2, message="Bad line", raw_line="bad")
    assert issue.line_number == 2
    assert issue.message == "Bad line"
    assert issue.raw_line == "bad"

    valid = TomeIntegrityReport(valid=True, tome_id="tome-1")
    assert bool(valid) is True
    assert len(valid.issues) == 0
    assert valid.total_lines == 0
    assert valid.valid_entries_count == 0

    invalid = TomeIntegrityReport(valid=False, tome_id="tome-1", issues=[issue])
    assert bool(invalid) is False
    assert len(invalid.issues) == 1
    assert invalid.issues[0] == issue
