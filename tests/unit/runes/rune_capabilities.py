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
    assert set(CAPABILITY_GATES) == {
        "spell",
        "hook",
        "command",
        "realm",
        "skill",
        "filesystem",
    }
    # Phase 5a: filesystem is minted because its gate (the
    # subprocess ladder) now exists. It governs no RuneAPI
    # method, so its method tuple is empty by design.
    assert CAPABILITY_GATES["filesystem"] == ()
    assert "network" not in CAPABILITY_GATES


def test_parse_capabilities_drops_unknown_ids() -> None:
    known, dropped = parse_capabilities(["spell", "filesystem", "hook", "nope"])
    assert known == ["spell", "filesystem", "hook"]
    assert dropped == ["nope"]


def test_parse_capabilities_dedupes_and_keeps_order() -> None:
    known, dropped = parse_capabilities(["skill", "spell", "skill"])
    assert known == ["skill", "spell"]
    assert dropped == []


def test_manifest_parser_keeps_known_and_dropped(tmp_path: Path) -> None:
    rune_dir = _write_manifest(tmp_path, ["spell", "filesystem"])
    manifest = load_manifest(rune_dir)
    assert manifest is not None
    assert manifest.capabilities == ["spell", "filesystem"]
    assert manifest.dropped_capabilities == []


def test_manifest_without_capabilities_field_is_legacy(tmp_path: Path) -> None:
    rune_dir = tmp_path / "legacy"
    rune_dir.mkdir()
    (rune_dir / "manifest.json").write_text(
        json.dumps({"name": "legacy", "version": "0.1.0", "entry_point": "r.py"})
    )
    manifest = load_manifest(rune_dir)
    assert manifest is not None
    assert manifest.capabilities is None


def test_store_consent_to_declaration_records_consent_and_declaration(
    tmp_path: Path,
) -> None:
    store = CapabilityStore(tmp_path)
    store.consent_to_declaration("rune-a", ["spell", "hook"])
    assert store.granted("rune-a", ["spell", "hook"]) == {"spell", "hook"}
    assert store.declaration_changed("rune-a", ["spell", "hook"]) is False
    assert store.has_record("rune-a") is True
    reopened = CapabilityStore(tmp_path)
    assert reopened.granted("rune-a", ["spell", "hook"]) == {"spell", "hook"}


def test_store_has_record_is_false_before_any_consent(tmp_path: Path) -> None:
    store = CapabilityStore(tmp_path)
    assert store.has_record("rune-a") is False
    store.record_declaration("rune-a", ["spell"])
    assert store.has_record("rune-a") is True


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


def test_declaration_hash_covers_write_roots() -> None:
    # Phase 5a fix (F2): widening write roots changes the
    # declaration hash exactly as a capability change does.
    assert declaration_hash(["spell", "filesystem"], ["data"]) == declaration_hash(
        ["filesystem", "spell"], ["data"]
    )
    assert declaration_hash(["spell", "filesystem"], ["data"]) != declaration_hash(
        ["spell", "filesystem"], ["."]
    )
    assert declaration_hash(["spell", "filesystem"], ["data"]) != declaration_hash(
        ["spell", "filesystem"]
    )


def test_write_roots_widening_triggers_reconsent_not_narrowing() -> None:
    # Phase 5a fix (F2): widening triggers declaration_changed;
    # narrowing and unchanged roots do not spuriously re-trigger.
    store = CapabilityStore(None)
    store.record_declaration("rune-a", ["spell", "filesystem"], ["data"])
    store.consent("rune-a", ["spell", "filesystem"])
    assert (
        store.declaration_changed("rune-a", ["spell", "filesystem"], ["data"]) is False
    )
    assert store.declaration_changed("rune-a", ["spell", "filesystem"], ["."]) is True
    assert store.declaration_changed("rune-a", ["spell", "filesystem"], []) is False
    # The widening is not effective until re-consent.
    assert store.granted_write_roots("rune-a", ["."]) == set()
    assert store.granted_write_roots("rune-a", ["data"]) == {"data"}
    # Narrowing takes effect immediately, without re-consent.
    assert store.granted_write_roots("rune-a", []) == set()
    # Re-consent to the widened declaration grants it and
    # clears the widening signal.
    store.consent("rune-a", ["spell", "filesystem"], ["."])
    assert store.granted_write_roots("rune-a", ["."]) == {"."}
    assert store.declaration_changed("rune-a", ["spell", "filesystem"], ["."]) is False


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
async def test_remaining_methods_gate_and_deny() -> None:
    # The matrix test covers spell/hook/command/realm/skill;
    # this one covers the second method of each multi-method
    # gate (shortcut, provider, spell gate, skill path).
    from mvge.core.approval import ApprovalReasonCode, ApprovalRequest, deny

    async def run(consented: bool) -> dict[str, object]:
        runner = RuneRunner()
        if consented:
            runner.consent_capabilities(
                "full-rune", ["spell", "hook", "command", "realm", "skill"]
            )
        manifest = RuneManifest(
            name="full-rune",
            version="0.1.0",
            description="",
            capabilities=["spell", "hook", "command", "realm", "skill"],
        )
        results: dict[str, object] = {}

        def factory(api):  # type: ignore[no-untyped-def]
            results["shortcut"] = api.register_shortcut("cap_key")
            results["provider"] = api.register_provider("cap_prov", {})
            api.register_spell_gate(lambda req: deny(req, ApprovalReasonCode.FAILURE))
            api.register_skill_path("/tmp/cap-skills")
            return None

        await runner.load_rune_loads([RuneLoad(manifest=manifest, factory=factory)])
        request = ApprovalRequest(cast_id="c1", spell_name="s")
        decision = await runner.evaluate_spell_gates(request)
        results["gate_denied"] = decision.outcome.name == "DENY"
        results["skill_paths"] = [
            str(p) for p in runner.skill_catalog.get_registered_skill_paths()
        ]
        results["shortcuts"] = [s.key for s in runner.get_shortcuts()]
        results["providers"] = list(runner.get_registered_providers())
        return results

    granted = await run(True)
    assert granted["shortcut"] is True
    assert granted["provider"] is True
    assert granted["gate_denied"] is True
    # The engine resolves the path (symlinked /tmp on macOS,
    # drive-lettered paths on Windows), so compare resolved forms.
    assert str(Path("/tmp/cap-skills").resolve()) in granted["skill_paths"]  # type: ignore[operator]
    assert "cap_key" in granted["shortcuts"]  # type: ignore[operator]
    assert "cap_prov" in granted["providers"]  # type: ignore[operator]

    denied = await run(False)
    assert denied["shortcut"] is False
    assert denied["provider"] is False
    assert denied["gate_denied"] is False
    assert denied["skill_paths"] == []
    assert denied["shortcuts"] == []
    assert denied["providers"] == []


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


def test_manifest_parser_keeps_and_drops_filesystem_roots(tmp_path: Path) -> None:
    # Phase 5a: relative roots are kept; absolute paths, parent
    # escapes, and empty strings are dropped fail-closed.
    # "/etc" is not absolute on Windows (drive-relative), so the
    # absolute example is spelled per-platform; the intent — an
    # absolute path is dropped — is identical on every platform.
    absolute_example = "/etc" if Path("/etc").is_absolute() else "C:/Windows"
    rune_dir = tmp_path / "roots-rune"
    rune_dir.mkdir()
    (rune_dir / "manifest.json").write_text(
        json.dumps(
            {
                "name": "roots-rune",
                "version": "0.1.0",
                "entry_point": "r.py",
                "capabilities": ["spell", "filesystem"],
                "filesystem_write_roots": [
                    ".",
                    "data",
                    absolute_example,
                    "../escape",
                    "",
                    "data",
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = load_manifest(rune_dir)
    assert manifest is not None
    assert manifest.filesystem_write_roots == [".", "data"]
    assert manifest.dropped_filesystem_write_roots == [
        absolute_example,
        "../escape",
        "",
    ]
