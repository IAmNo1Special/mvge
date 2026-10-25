"""Unit tests for session codec discovery from rune manifests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from mvge.runes.codecs import load_session_codecs
from mvge.runes.manifest import load_manifest
from mvge.runes.types import DiagnosticKind

FAKE_CODEC_MODULE = """
class FakeCodec:
    name = "fake-test"

    def detect(self, header):
        return header.get("kind") == "fake-test"

    def looks_like_session(self, header):
        return header.get("kind") == "fake-test"

    def parse_header(self, header):
        raise NotImplementedError

    def parse_entries(self, header, lines, *, source=""):
        return []

    def serialize_entry(self, entry):
        return None

    def serialize_new_entry(self, entry, existing):
        return {}, entry

    def plan_append(self, entry, existing, header):
        line, stored = self.serialize_new_entry(entry, existing)
        return {"lines": [line], "stored": stored, "rewrite": False}

    def append_tip_lines(self, header, entries, leaf):
        return None

    def apply_leaf(self, header, entries, leaf):
        return header, entries

    def leaf_id(self, header, entries):
        return None

    def repair_on_open(self, source):
        return False

    def fork(self, *, source, dest_dir, new_id):
        raise NotImplementedError
"""

INCOMPLETE_CODEC_MODULE = """
class IncompleteCodec:
    name = "incomplete"

    def detect(self, header):
        return False
"""


def _write_rune(
    extensions_dir: Path,
    name: str,
    *,
    session_codecs: list[str] | None = None,
    module_code: str | None = None,
    module_name: str = "fake_codec_mod",
    enabled: bool = True,
) -> Path:
    rune_dir = extensions_dir / name
    rune_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict = {"name": name, "version": "1.0.0", "enabled": enabled}
    if session_codecs is not None:
        manifest["session_codecs"] = session_codecs
    (rune_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if module_code is not None:
        (rune_dir / f"{module_name}.py").write_text(module_code, encoding="utf-8")
    return rune_dir


def test_manifest_parses_session_codecs(tmp_path: Path) -> None:
    rune_dir = _write_rune(
        tmp_path,
        "codec-rune",
        session_codecs=["my_mod:MyCodec", "other_mod:Other"],
    )
    manifest = load_manifest(rune_dir)
    assert manifest is not None
    assert manifest.session_codecs == ["my_mod:MyCodec", "other_mod:Other"]


def test_manifest_session_codecs_defaults_empty(tmp_path: Path) -> None:
    rune_dir = _write_rune(tmp_path, "plain-rune")
    manifest = load_manifest(rune_dir)
    assert manifest is not None
    assert manifest.session_codecs == []


def test_load_session_codecs_loads_declared_codec(tmp_path: Path) -> None:
    _write_rune(
        tmp_path,
        "codec-rune",
        session_codecs=["fake_codec_mod:FakeCodec"],
        module_code=FAKE_CODEC_MODULE,
    )
    codecs, diagnostics = load_session_codecs([tmp_path])
    assert diagnostics == []
    assert len(codecs) == 1
    assert codecs[0].name == "fake-test"
    assert codecs[0].detect({"kind": "fake-test"}) is True


def test_load_session_codecs_skips_runes_without_codecs(tmp_path: Path) -> None:
    _write_rune(tmp_path, "plain-rune")
    _write_rune(
        tmp_path,
        "codec-rune",
        session_codecs=["fake_codec_mod:FakeCodec"],
        module_code=FAKE_CODEC_MODULE,
    )
    codecs, _ = load_session_codecs([tmp_path])
    assert len(codecs) == 1


def test_load_session_codecs_skips_disabled_runes(tmp_path: Path) -> None:
    _write_rune(
        tmp_path,
        "codec-rune",
        session_codecs=["fake_codec_mod:FakeCodec"],
        module_code=FAKE_CODEC_MODULE,
        enabled=False,
    )
    codecs, _ = load_session_codecs([tmp_path])
    assert codecs == []


def test_load_session_codecs_bad_module_reports_diagnostic(
    tmp_path: Path,
) -> None:
    _write_rune(tmp_path, "codec-rune", session_codecs=["no_such_mod:Nope"])
    codecs, diagnostics = load_session_codecs([tmp_path])
    assert codecs == []
    assert len(diagnostics) == 1
    assert diagnostics[0].kind == DiagnosticKind.LOAD_FAILURE
    assert "no_such_mod" in diagnostics[0].message


def test_load_session_codecs_bad_spec_reports_diagnostic(tmp_path: Path) -> None:
    _write_rune(tmp_path, "codec-rune", session_codecs=["not-a-spec"])
    codecs, diagnostics = load_session_codecs([tmp_path])
    assert codecs == []
    assert len(diagnostics) == 1
    assert diagnostics[0].kind == DiagnosticKind.PARSE_WARNING


def test_load_session_codecs_incomplete_codec_reports_diagnostic(
    tmp_path: Path,
) -> None:
    _write_rune(
        tmp_path,
        "codec-rune",
        session_codecs=["fake_codec_mod:IncompleteCodec"],
        module_code=INCOMPLETE_CODEC_MODULE,
    )
    codecs, diagnostics = load_session_codecs([tmp_path])
    assert codecs == []
    assert len(diagnostics) == 1
    assert diagnostics[0].kind == DiagnosticKind.LOAD_FAILURE
    assert "parse_header" in diagnostics[0].message


def test_load_session_codecs_missing_dir_returns_empty(tmp_path: Path) -> None:
    codecs, diagnostics = load_session_codecs([tmp_path / "nope"])
    assert codecs == []
    assert diagnostics == []


def test_load_session_codecs_loads_from_multiple_dirs(tmp_path: Path) -> None:
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    _write_rune(
        dir_a,
        "rune-a",
        session_codecs=["mod_a:FakeCodec"],
        module_code=FAKE_CODEC_MODULE,
        module_name="mod_a",
    )
    _write_rune(
        dir_b,
        "rune-b",
        session_codecs=["mod_b:FakeCodec"],
        module_code=FAKE_CODEC_MODULE,
        module_name="mod_b",
    )
    codecs, _ = load_session_codecs([dir_a, dir_b])
    assert len(codecs) == 2


@pytest.fixture(autouse=True)
def _clean_sys_modules():
    yield
    stale = ("fake_codec_mod", "mod_a", "mod_b")
    for mod in [m for m in sys.modules if m.startswith(stale)]:
        del sys.modules[mod]
