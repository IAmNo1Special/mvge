"""Admission tests: the loader refuses flagged runes before execution."""

from __future__ import annotations

import json
from pathlib import Path

from mvge.runes.loader import load_factory_from_manifest
from mvge.runes.manifest import load_manifest
from mvge.runes.types import DiagnosticKind


def _write_rune(parent: Path, name: str, body: str, capabilities=None) -> Path:
    rune_dir = parent / name
    rune_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": name,
        "version": "0.1.0",
        "entry_point": "rune.py",
    }
    if capabilities is not None:
        manifest["capabilities"] = capabilities
    (rune_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (rune_dir / "rune.py").write_text(body, encoding="utf-8")
    return rune_dir


def test_flagged_rune_refused_before_execution(tmp_path: Path) -> None:
    marker = tmp_path / "marker.txt"
    body = (
        f"from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('pwned', encoding='utf-8')\n"
        "def rune_factory(api):\n"
        "    return api._runner\n"
    )
    rune_dir = _write_rune(tmp_path, "flagged", body)
    manifest = load_manifest(rune_dir)
    assert manifest is not None
    diagnostics: list = []
    factory = load_factory_from_manifest(manifest, rune_dir, diagnostics=diagnostics)
    assert factory is None
    assert not marker.exists()
    assert not (rune_dir / ".install-id").exists()
    assert any(
        d.kind == DiagnosticKind.LOAD_FAILURE
        and "admission" in d.message
        and "private_runner_access" in d.message
        for d in diagnostics
    )


def test_clean_rune_admitted(tmp_path: Path) -> None:
    rune_dir = _write_rune(
        tmp_path,
        "clean",
        "def rune_factory(api):\n    return None\n",
        capabilities=["spell"],
    )
    manifest = load_manifest(rune_dir)
    assert manifest is not None
    diagnostics: list = []
    factory = load_factory_from_manifest(manifest, rune_dir, diagnostics=diagnostics)
    assert factory is not None
    assert diagnostics == []


def test_facade_bypass_refused_at_admission(tmp_path: Path) -> None:
    # A rune declaring no capabilities that reaches the runner
    # through api._facade is refused before its factory runs,
    # even though the gate alone could not stop that spelling.
    rune_dir = _write_rune(
        tmp_path,
        "facade-bypass",
        "def rune_factory(api):\n    api._facade.register_spell(spell)\n",
        capabilities=[],
    )
    manifest = load_manifest(rune_dir)
    assert manifest is not None
    diagnostics: list = []
    factory = load_factory_from_manifest(manifest, rune_dir, diagnostics=diagnostics)
    assert factory is None
    assert any(
        d.kind == DiagnosticKind.LOAD_FAILURE and "private_runner_access" in d.message
        for d in diagnostics
    )


def test_undeclared_capability_use_refused_for_declared_rune(tmp_path: Path) -> None:
    rune_dir = _write_rune(
        tmp_path,
        "overreaching",
        "def rune_factory(api):\n    api.register_spell(spell)\n",
        capabilities=["hook"],
    )
    manifest = load_manifest(rune_dir)
    assert manifest is not None
    diagnostics: list = []
    factory = load_factory_from_manifest(manifest, rune_dir, diagnostics=diagnostics)
    assert factory is None
    assert any(
        d.kind == DiagnosticKind.LOAD_FAILURE
        and "undeclared_capability_use" in d.message
        for d in diagnostics
    )
