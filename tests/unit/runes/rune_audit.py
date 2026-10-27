"""Unit tests for the engine-owned rune-op audit log."""

import json
import os
import re
import stat
from pathlib import Path

import pytest

from mvge.runes.rune_audit import (
    AUDIT_FILENAME,
    AuditError,
    RuneAuditLog,
    default_rune_ops_dir,
    utcnow,
)

TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def read_records(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_append_writes_single_jsonl_record(tmp_path: Path) -> None:
    log = RuneAuditLog(tmp_path / "extensions")
    log.append_event({"op": "revise_persona", "outcome": "ok"})

    records = read_records(tmp_path / "extensions" / AUDIT_FILENAME)
    assert len(records) == 1
    assert records[0]["op"] == "revise_persona"
    assert records[0]["outcome"] == "ok"


@pytest.mark.skipif(
    os.name == "nt",
    reason=(
        "Windows has no POSIX permission bits: os.chmod cannot express "
        "0o700/0o600 there, so the mode-bit assertion cannot pass. The audit "
        "log relies on default Windows ACLs on that platform; a Windows ACL "
        "implementation would remove this skip."
    ),
)
def test_append_sets_restrictive_permissions(tmp_path: Path) -> None:
    log = RuneAuditLog(tmp_path / "extensions")
    log.append_event({"op": "teach", "outcome": "ok"})

    assert stat.S_IMODE(os.stat(tmp_path / "extensions").st_mode) == 0o700
    assert (
        stat.S_IMODE(os.stat(tmp_path / "extensions" / AUDIT_FILENAME).st_mode) == 0o600
    )


def test_append_is_append_only(tmp_path: Path) -> None:
    log = RuneAuditLog(tmp_path)
    log.append_event({"op": "a"})
    log.append_event({"op": "b"})

    records = read_records(tmp_path / AUDIT_FILENAME)
    assert [r["op"] for r in records] == ["a", "b"]


def test_append_raises_audit_error_when_data_dir_unusable(
    tmp_path: Path,
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir", encoding="utf-8")
    log = RuneAuditLog(blocker)

    with pytest.raises(AuditError, match="audit append failed"):
        log.append_event({"op": "scaffold_spell"})


def test_rotation_archives_full_log_without_losing_records(
    tmp_path: Path,
) -> None:
    log = RuneAuditLog(tmp_path, max_bytes=10, max_age_days=-1)
    log.append_event({"op": "first-op-with-a-long-name"})
    log.append_event({"op": "second"})

    current = read_records(tmp_path / AUDIT_FILENAME)
    assert [r["op"] for r in current] == ["second"]
    archives = list(tmp_path.glob("audit-*.jsonl"))
    assert len(archives) == 1
    assert [r["op"] for r in read_records(archives[0])] == ["first-op-with-a-long-name"]


def test_recent_returns_newest_last_and_skips_corrupt_lines(
    tmp_path: Path,
) -> None:
    log = RuneAuditLog(tmp_path, max_age_days=-1)
    log.append_event({"op": "one"})
    log.append_event({"op": "two"})
    with open(tmp_path / AUDIT_FILENAME, "a", encoding="utf-8") as handle:
        handle.write("not json\n")

    assert [r["op"] for r in log.recent(10)] == ["one", "two"]
    assert [r["op"] for r in log.recent(2)] == ["two"]
    assert log.recent(5) == log.recent(99)


def test_recent_empty_when_no_log(tmp_path: Path) -> None:
    assert RuneAuditLog(tmp_path).recent(10) == []


def test_utcnow_format() -> None:
    assert TIMESTAMP_RE.match(utcnow()) is not None


def test_default_dir_prefers_global_dir_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(tmp_path / "global"))
    assert default_rune_ops_dir() == tmp_path / "global" / "extensions"


def test_default_dir_falls_back_to_user_agents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MVGE_GLOBAL_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    # Windows' ntpath.expanduser ignores HOME and reads USERPROFILE.
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert default_rune_ops_dir() == tmp_path / ".agents" / "extensions"


def test_rotation_never_overwrites_existing_archive(tmp_path: Path) -> None:
    log = RuneAuditLog(tmp_path / "extensions", max_bytes=1)
    log.append_event({"op": "first"})
    log.append_event({"op": "second"})
    log.append_event({"op": "third"})

    archives = sorted((tmp_path / "extensions").glob("audit-*.jsonl"))
    assert len(archives) == 2
    ops = [record["op"] for archive in archives for record in read_records(archive)]
    assert sorted(ops) == ["first", "second"]
