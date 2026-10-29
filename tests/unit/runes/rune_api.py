"""Unit tests for RuneAPI.audit: the engine-stamped rune-op audit event."""

import json
import os
import re
from pathlib import Path

import httpx
import pytest

from mvge.runes.rune_api import RuneAPI
from mvge.runes.rune_audit import (
    AUDIT_FILENAME,
    AuditError,
    RuneAuditLog,
)
from mvge.runes.rune_runner import RuneRunner

TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def read_records(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def make_api(
    tmp_path: Path, rune_name: str | None = "selfmod-bridge"
) -> tuple[RuneAPI, Path]:
    log = RuneAuditLog(tmp_path)
    api = RuneAPI(RuneRunner(), rune_name, audit_log=log)
    return api, tmp_path / AUDIT_FILENAME


def test_audit_stamps_rune_identity_and_timestamp(tmp_path: Path) -> None:
    api, audit_path = make_api(tmp_path)

    api.audit(
        "revise_persona",
        outcome="ok",
        code="ok",
        message="persona patched",
        target="SYSTEM.md",
    )

    (record,) = read_records(audit_path)
    assert record["rune"] == "selfmod-bridge"
    assert record["op"] == "revise_persona"
    assert record["outcome"] == "ok"
    assert record["code"] == "ok"
    assert record["message"] == "persona patched"
    assert record["target"] == "SYSTEM.md"
    assert TIMESTAMP_RE.match(record["timestamp"]) is not None


def test_audit_extra_fields_cannot_forge_stamps(tmp_path: Path) -> None:
    api, audit_path = make_api(tmp_path)

    api.audit(
        "teach",
        outcome="failed",
        code="snapshot_failed",
        message="disk died",
        extra={"rune": "evil-rune", "timestamp": "forged", "note": "kept"},
    )

    (record,) = read_records(audit_path)
    assert record["rune"] == "selfmod-bridge"
    assert TIMESTAMP_RE.match(record["timestamp"]) is not None
    assert record["note"] == "kept"


def test_audit_stamps_unknown_when_rune_name_missing(tmp_path: Path) -> None:
    api, audit_path = make_api(tmp_path, rune_name=None)

    api.audit("self_snapshot", outcome="ok", code="ok", message="snapped")

    (record,) = read_records(audit_path)
    assert record["rune"] == "unknown"


def test_audit_rejects_empty_op_name(tmp_path: Path) -> None:
    api, _ = make_api(tmp_path)

    with pytest.raises(ValueError, match="op name"):
        api.audit("", outcome="ok", code="ok", message="m")


def test_audit_propagates_audit_error_loudly(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir", encoding="utf-8")
    api = RuneAPI(RuneRunner(), "r", audit_log=RuneAuditLog(blocker))

    with pytest.raises(AuditError):
        api.audit("scaffold_rune", outcome="ok", code="ok", message="m")


# NOTE: Windows environment variables are case-insensitive, so
# NO_PROXY and no_proxy are the SAME slot there. These tests must never
# assume two distinct slots: each case sets exactly one name, after
# clearing both (clearing is a no-op for the second name on Windows).
@pytest.mark.parametrize("name", ["NO_PROXY", "no_proxy"])
def test_sanitize_no_proxy_env_rewrites_bracketed_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    api, _ = make_api(tmp_path)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setenv(name, "[::1],localhost")
    assert api.sanitize_no_proxy_env() is True
    assert os.environ[name] == "localhost"


@pytest.mark.parametrize("name", ["NO_PROXY", "no_proxy"])
def test_sanitize_no_proxy_env_leaves_clean_env_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    api, _ = make_api(tmp_path)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setenv(name, "localhost")
    assert api.sanitize_no_proxy_env() is False
    assert os.environ[name] == "localhost"


def test_sanitize_no_proxy_env_lets_rune_build_its_own_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rune position, library use: the factory holds only ``api``.

    Calling the helper before building the client is the whole fix for
    a rune the engine never sees a client from. No request is made, so
    the test stays hermetic: construction alone parses NO_PROXY.
    """
    api, _ = make_api(tmp_path)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setenv("NO_PROXY", "[::1],localhost")
    assert api.sanitize_no_proxy_env() is True
    with httpx.Client() as client:
        assert not client.is_closed


def test_audit_default_log_lands_in_user_scope_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(tmp_path / "global"))
    api = RuneAPI(RuneRunner(), "some-rune")

    api.audit("teach", outcome="failed", code="snapshot_failed", message="m")

    audit_path = tmp_path / "global" / "extensions" / AUDIT_FILENAME
    assert audit_path.is_file()
    (record,) = read_records(audit_path)
    assert record["rune"] == "some-rune"
