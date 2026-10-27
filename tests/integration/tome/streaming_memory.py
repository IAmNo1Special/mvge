from __future__ import annotations

import json
from pathlib import Path

from mvge.tome.handle import TomeHandleFactory


def _create_session_file(
    tome_dir: Path, tome_id: str, entry_count: int = 5000
) -> tuple[Path, int]:
    tome_file = tome_dir / f"{tome_id}.jsonl"
    header = {
        "type": "session",
        "version": 1,
        "id": tome_id,
        "timestamp": "2026-01-01T00:00:00+00:00",
        "cwd": "/tmp",
        "schema_version": "1.0",
    }
    total_entries = 0
    with tome_file.open("w", encoding="utf-8") as f:
        f.write(json.dumps(header) + "\n")
        for i in range(entry_count):
            f.write(
                json.dumps(
                    {
                        "id": f"entry-{i:05d}",
                        "parentId": f"entry-{i - 1:05d}" if i > 0 else None,
                        "type": "message",
                        "timestamp": 1000.0 + i,
                        "payload": {
                            "role": "user",
                            "content": f"data-{i:05d}-" + "x" * 200,
                        },
                    }
                )
                + "\n"
            )
            total_entries += 1

    tail_entry = {
        "id": "final-tail-entry",
        "parentId": "entry-04999",
        "type": "message",
        "timestamp": 99999.0,
        "payload": {"role": "assistant", "content": "final tail marker"},
    }
    with tome_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(tail_entry) + "\n")
        total_entries += 1

    return tome_file, total_entries


def test_iteration_reads_every_entry_in_order(tmp_path: Path) -> None:
    tome_id = "large-session-stream"
    _create_session_file(tmp_path, tome_id)

    factory = TomeHandleFactory(tmp_path)
    streamed = list(factory.open_read(tome_id).iter_entries())

    assert len(streamed) == 5001
    assert streamed[0].id == "entry-00000"
    assert streamed[-1].id == "final-tail-entry"


def test_tail_reader_returns_last_n_in_order(tmp_path: Path) -> None:
    tome_id = "large-session-tail"
    _create_session_file(tmp_path, tome_id)

    factory = TomeHandleFactory(tmp_path)
    tail = factory.read_last_n_entries(tome_id, limit=10)

    assert len(tail) == 10
    assert tail[-1].id == "final-tail-entry"
    assert tail[-1].payload["content"] == "final tail marker"
    assert tail[0].id == "entry-04991"


def test_verify_integrity_counts_large_session(tmp_path: Path) -> None:
    tome_id = "large-session-audit"
    _create_session_file(tmp_path, tome_id)

    factory = TomeHandleFactory(tmp_path)
    report = factory.verify_integrity(tome_id)

    assert report.valid is True
    assert report.valid_entries_count == 5001
