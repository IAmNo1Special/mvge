"""Unit tests for the Phase 2B capability model.

Capabilities are declared in a rune manifest and granted from
engine-owned, user-owned storage — never from the rune code dir.
Only gates that exist are minted: spell, hook, command, realm,
skill. Unknown ids are dropped, never granted. A declaration
change without re-consent refuses the new capability.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mvge.runes.capabilities import (
    CAPABILITY_GATES,
    CapabilityStore,
    declaration_hash,
    parse_capabilities,
)
from mvge.runes.manifest import load_manifest
from mvge.runes.rune_runner import RuneRunner
from mvge.runes.types import RuneLoad, RuneManifest, SpellDefinition


def _write_manifest(parent: Path, capabilities: object) -> Path:
    rune_dir = parent / "cap-rune"
    rune_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": "cap-rune",
        "version": "0.1.0",
        "entry_point": "rune.py",
        "capabilities": capabilities,
    }
    (rune_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (rune_dir / "rune.py").write_text("def rune_factory(api):\n    return None\n")
    return rune_dir


def test_capability_ids_are_only_existing_gates() -> None:
    assert set(CAPABILITY_GATES) == {"spell", "hook", "command", "realm", "skill"}
    assert "filesystem" not in CAPABILITY_GATES
    assert "network" not in CAPABILITY_GATES


def test_parse_capabilities_drops_unknown_ids() -> None:
    known, dropped = parse_capabilities(["spell", "filesystem", "hook", "nope"])
    assert known == ["spell", "hook"]
    assert dropped == ["filesystem", "nope"]


def test_parse_capabilities_dedupes_and_keeps_order() -> None:
    known, dropped = parse_capabilities(["skill", "spell", "skill"])
    assert known == ["skill", "spell"]
    assert dropped == []


def test_manifest_parser_keeps_known_and_dropped(tmp_path: Path) -> None:
    rune_dir = _write_manifest(tmp_path, ["spell", "filesystem"])
    manifest = load_manifest(rune_dir)
    assert manifest is not None
    assert manifest.capabilities == ["spell"]
    assert manifest.dropped_capabilities == ["filesystem"]


def test_manifest_without_capabilities_field_is_legacy(tmp_path: Path) -> None:
    rune_dir = tmp_path / "legacy"
    rune_dir.mkdir()
    (rune_dir / "manifest.json").write_text(
        json.dumps({"name": "legacy", "version": "0.1.0", "entry_point": "r.py"})
    )
    manifest = load_manifest(rune_dir)
    assert manifest is not None
    assert manifest.capabilities is None


def test_store_grants_only_consented_and_declared(tmp_path: Path) -> None:
    store = CapabilityStore(tmp_path)
    store.consent("rune-a", ["spell", "hook"])
    assert store.granted("rune-a", ["spell", "skill"]) == {"spell"}
    assert store.granted("rune-a", ["skill"]) == set()


def test_store_persists_across_instances(tmp_path: Path) -> None:
    store = CapabilityStore(tmp_path)
    store.consent("rune-a", ["spell"])
    store.record_declaration("rune-a", ["spell", "hook"])
    reopened = CapabilityStore(tmp_path)
    assert reopened.granted("rune-a", ["spell", "hook"]) == {"spell"}
    assert reopened.declaration_changed("rune-a", ["spell", "hook"]) is False


def test_declaration_change_without_reconsent_refuses_new_capability() -> None:
    store = CapabilityStore(None)
    store.record_declaration("rune-a", ["spell"])
    store.consent("rune-a", ["spell"])
    assert store.granted("rune-a", ["spell"]) == {"spell"}
    # The declaration grows; the new capability was never consented.
    assert store.declaration_changed("rune-a", ["spell", "skill"]) is True
    assert store.granted("rune-a", ["spell", "skill"]) == {"spell"}
    assert "skill" not in store.granted("rune-a", ["spell", "skill"])
    # Re-consent grants it.
    store.consent("rune-a", ["skill"])
    assert store.granted("rune-a", ["spell", "skill"]) == {"spell", "skill"}


def test_declaration_hash_is_stable_and_order_independent() -> None:
    assert declaration_hash(["spell", "hook"]) == declaration_hash(["hook", "spell"])
    assert declaration_hash(["spell"]) != declaration_hash(["hook"])


def _factory_registering_everything(results: dict[str, bool]):
    def factory(api):  # type: ignore[no-untyped-def]
        results["spell"] = api.register_spell(
            SpellDefinition(name="cap_spell", description="x")
        )
        results["command"] = api.register_command("cap_cmd", "x")
        results["realm"] = api.register_realm_factory("cap", lambda **kw: None)
        api.on(
            __import__(
                "mvge.runes.types", fromlist=["SigilHook"]
            ).SigilHook.BEFORE_MVGE_START,
            lambda payload: payload,
        )
        api.register_skill(
            __import__("mvge.runes.types", fromlist=["SkillManifest"]).SkillManifest(
                name="cap_skill", description="x"
            )
        )
        results["skill"] = len(api.get_skills()) > 0
        return None

    return factory


@pytest.mark.asyncio
async def test_capability_matrix_end_to_end() -> None:
    # Declared spell + hook only, consented for both: those two
    # register, the rest are denied — observable in the runner.
    runner = RuneRunner()
    runner.consent_capabilities("matrix-rune", ["spell", "hook"])
    manifest = RuneManifest(
        name="matrix-rune",
        version="0.1.0",
        description="",
        capabilities=["spell", "hook"],
    )
    results: dict[str, bool] = {}
    await runner.load_rune_loads(
        [RuneLoad(manifest=manifest, factory=_factory_registering_everything(results))]
    )
    assert results["spell"] is True
    from mvge.runes.types import SigilHook

    assert len(runner.get_sigil_handlers(SigilHook.BEFORE_MVGE_START)) == 1
    assert results["command"] is False
    assert results["realm"] is False
    assert results["skill"] is False
    assert runner.get_spell("cap_spell") is not None
    assert runner.get_spell("cap_cmd") is None


@pytest.mark.asyncio
async def test_declared_but_unconsented_is_denied() -> None:
    runner = RuneRunner()
    manifest = RuneManifest(
        name="unconsented",
        version="0.1.0",
        description="",
        capabilities=["spell"],
    )
    results: dict[str, bool] = {}
    await runner.load_rune_loads(
        [RuneLoad(manifest=manifest, factory=_factory_registering_everything(results))]
    )
    assert results["spell"] is False
    assert runner.get_spell("cap_spell") is None
    from mvge.runes.types import SigilHook

    assert runner.get_sigil_handlers(SigilHook.BEFORE_MVGE_START) == []


@pytest.mark.asyncio
async def test_legacy_undeclared_manifest_still_registers() -> None:
    # No capabilities field at all: pre-2B behaviour, unchanged.
    runner = RuneRunner()
    manifest = RuneManifest(name="legacy-rune", version="0.1.0", description="")
    await runner.load_rune_loads(
        [
            RuneLoad(
                manifest=manifest,
                factory=lambda api: api.register_spell(
                    SpellDefinition(name="legacy_spell", description="x")
                ),
            )
        ]
    )
    assert runner.get_spell("legacy_spell") is not None


def test_gate_allows_ad_hoc_apis_without_a_manifest() -> None:
    runner = RuneRunner()
    gate = runner.capability_gate()
    assert gate.allows("no-such-rune", "spell") is True
    assert gate.allows(None, "spell") is True
