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


def _leaf(entry_id: str, target: str) -> TomeEntry:
    return TomeEntry(
        id=entry_id,
        parent_id=None,
        type=TomeEntryType.LEAF,
        timestamp=1001.0,
        payload={"targetId": target},
    )


def test_session_lifecycle_with_integrity_audit_and_crash_recovery(
    tmp_path: Path,
) -> None:
    factory = TomeHandleFactory(tmp_path)

    # 1. Create and populate a tome session
    write = factory.create_tome("/workspace/myproject", tome_id="t1")
    write.append(_message("m1", "user", "Implement feature X"))
    write.append(_leaf("l1", "m1"))
    write.append(_message("m2", "assistant", "Sure, starting now", parent_id="m1"))
    write.append(_leaf("l2", "m2"))

    # 2. Initial integrity audit
    report = factory.verify_integrity("t1")
    assert report.valid is True
    assert len(report.issues) == 0
    assert report.valid_entries_count == 4  # 2 messages, 2 leaves

    # 3. Simulate mid-write power loss / process crash by appending incomplete entry
    with write.path.open("a", encoding="utf-8") as f:
        f.write('{"id": "partially_written", "parentId": "m2", "type": "messa')

    # 4. Audit detects damaged line with precise line number
    corrupt_report = factory.verify_integrity("t1")
    assert corrupt_report.valid is False
    assert len(corrupt_report.issues) == 1
    assert corrupt_report.issues[0].line_number == 6
    assert corrupt_report.valid_entries_count == 4
    assert corrupt_report.total_lines == 6

    # 5. Read operations recover all uncorrupted entries without crashing
    recovered_entries = factory.get_entries("t1")
    assert len(recovered_entries) == 4
    assert [e.id for e in recovered_entries[:2]] == ["m1", "l1"]
    assert recovered_entries[2].id == "m2"

    # 6. A fresh factory successfully opens the session header
    fresh_factory = TomeHandleFactory(tmp_path)
    loaded_meta = fresh_factory.open_tome("t1")
    assert loaded_meta is not None
    assert loaded_meta.id == "t1"

    # 7. The resumer repairs the torn tail and the tome stays usable
    truncated = fresh_factory.open_write("t1").repair_torn_tail()
    assert truncated > 0
    repaired = factory.verify_integrity("t1")
    assert repaired.valid is True
    assert repaired.valid_entries_count == 4
