"""Unit tests for the Phase 2B static source audit.

The audit is an AST scan: it never imports or executes rune code.
A marker fixture proves that — a module whose top level would
write a file if executed must leave no file behind after audit.
Findings are blocking (refused at admission) or advisory (flagged
for review, not refused). The audit is a review aid and admission
gate only; it never claims to block in-process escape.
"""

from __future__ import annotations

from pathlib import Path

from mvge.runes.capabilities import CAPABILITY_GATES, CapabilityGate, CapabilityStore
from mvge.runes.source_audit import (
    audit_rune_dir,
    audit_source,
    blocking_findings,
)


def _flagged_kinds(findings: list) -> set[str]:  # type: ignore[type-arg]
    return {f.kind for f in findings}


def test_runner_attribute_access_is_blocking() -> None:
    findings = audit_source("def rune_factory(api):\n    return api._runner\n")
    assert "private_runner_access" in _flagged_kinds(findings)
    assert blocking_findings(findings)


def test_getattr_runner_escape_is_blocking() -> None:
    findings = audit_source(
        "def rune_factory(api):\n"
        "    runner = getattr(api, '_runner', None)\n"
        "    return runner\n"
    )
    assert "private_runner_access" in _flagged_kinds(findings)
    assert blocking_findings(findings)


def test_api_install_id_access_is_blocking() -> None:
    findings = audit_source("def rune_factory(api):\n    return api._install_id\n")
    assert "private_install_id_access" in _flagged_kinds(findings)
    assert blocking_findings(findings)


def test_own_install_id_attribute_is_not_flagged() -> None:
    # A rune's own gate object carrying its own _install_id (the
    # approval rune's pattern) is not the RuneAPI escape.
    findings = audit_source(
        "class Gate:\n    def __init__(self):\n        self._install_id = 'x'\n"
    )
    assert findings == []


def test_internal_engine_import_is_blocking() -> None:
    findings = audit_source("from mvge.runes.rune_runner import RuneRunner\n")
    assert "internal_engine_import" in _flagged_kinds(findings)
    assert blocking_findings(findings)


def test_bare_internal_module_import_is_blocking() -> None:
    # The bare spelling must not evade the dotted one.
    findings = audit_source("from mvge.runes import rune_runner\n")
    assert "internal_engine_import" in _flagged_kinds(findings)
    assert blocking_findings(findings)
    findings = audit_source("from mvge.runes import RunnerFacade\n")
    assert "internal_engine_import" in _flagged_kinds(findings)
    assert blocking_findings(findings)


def test_facade_chain_access_is_blocking() -> None:
    # api._facade reaches the live runner and bypasses the
    # capability gate; it must be refused like api._runner.
    findings = audit_source(
        "def rune_factory(api):\n    return api._facade.register_spell(spell)\n"
    )
    assert "private_runner_access" in _flagged_kinds(findings)
    assert blocking_findings(findings)
    findings = audit_source(
        "def rune_factory(api):\n"
        "    runner = getattr(api, '_facade', None)\n"
        "    return runner\n"
    )
    assert "private_runner_access" in _flagged_kinds(findings)
    assert blocking_findings(findings)
    findings = audit_source(
        "def rune_factory(api):\n    return api._facade._wrapped_runner\n"
    )
    assert "private_runner_access" in _flagged_kinds(findings)
    assert blocking_findings(findings)


def test_public_api_imports_are_not_flagged() -> None:
    findings = audit_source(
        "from mvge.runes.rune_api import RuneAPI\n"
        "from mvge.runes.types import SpellDefinition, SigilHook\n"
        "from mvge.runes import ExecutionMode\n"
    )
    assert findings == []


def test_registry_mutation_is_blocking() -> None:
    findings = audit_source(
        "def rune_factory(api):\n    api._facade._wrapped_runner._spells['x'] = 1\n"
    )
    assert "registry_mutation" in _flagged_kinds(findings)
    assert blocking_findings(findings)


def test_agent_own_spells_attribute_is_not_flagged() -> None:
    findings = audit_source("agent._spells = [spell]\n")
    assert findings == []


def test_sys_path_manipulation_is_blocking() -> None:
    findings = audit_source("import sys\nsys.path.append('/tmp/evil')\n")
    assert "sys_path_manipulation" in _flagged_kinds(findings)
    assert blocking_findings(findings)


def test_sys_modules_manipulation_is_advisory_only() -> None:
    # The sanctioned importlib pattern registers a module in
    # sys.modules before exec_module (the engine loader does the
    # same), so this is flagged for review but does not refuse
    # admission on its own.
    findings = audit_source("import sys\nsys.modules['lazy_mod'] = module\n")
    assert "sys_modules_manipulation" in _flagged_kinds(findings)
    assert blocking_findings(findings) == []


def test_undeclared_capability_use_is_blocking() -> None:
    findings = audit_source(
        "def rune_factory(api):\n    api.register_spell(spell)\n",
        declared_capabilities=["hook"],
    )
    assert "undeclared_capability_use" in _flagged_kinds(findings)
    assert blocking_findings(findings)


def test_declared_capability_use_is_clean() -> None:
    findings = audit_source(
        "def rune_factory(api):\n    api.register_spell(spell)\n",
        declared_capabilities=["spell"],
    )
    assert findings == []


def test_undeclared_manifest_skips_capability_use_checks() -> None:
    # Legacy manifests (no capabilities field) are not audited
    # for capability use — only for escapes.
    findings = audit_source(
        "def rune_factory(api):\n    api.register_spell(spell)\n",
        declared_capabilities=None,
    )
    assert findings == []


def test_clean_fixture_is_admitted() -> None:
    findings = audit_source(
        "from mvge.runes.types import SpellDefinition\n"
        "def rune_factory(api):\n"
        "    api.register_spell(SpellDefinition(name='ok', description='x'))\n",
        declared_capabilities=["spell"],
    )
    assert findings == []


def test_audit_never_executes_rune_code(tmp_path: Path) -> None:
    rune_dir = tmp_path / "marker-rune"
    rune_dir.mkdir()
    marker = tmp_path / "marker.txt"
    (rune_dir / "rune.py").write_text(
        f"from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n"
        "def rune_factory(api):\n    return None\n",
        encoding="utf-8",
    )
    findings = audit_rune_dir(rune_dir)
    assert not marker.exists()
    assert findings == []


def test_audit_dir_skips_venv_and_pycache(tmp_path: Path) -> None:
    rune_dir = tmp_path / "r"
    (rune_dir / ".venv" / "lib").mkdir(parents=True)
    (rune_dir / "__pycache__").mkdir()
    (rune_dir / ".venv" / "lib" / "evil.py").write_text(
        "def f(api):\n    return api._runner\n"
    )
    (rune_dir / "rune.py").write_text("def rune_factory(api):\n    return None\n")
    assert audit_rune_dir(rune_dir) == []


def test_parity_audit_and_runtime_agree_for_every_method() -> None:
    """One table drives both verdicts: for every capability method,
    the audit flags undeclared use exactly when the runtime gate
    denies it. Any drift fails here.
    """
    call_args = {
        "register_spell": "spell",
        "on": "hook, handler",
        "register_spell_gate": "handler",
        "register_command": "'cmd', 'x'",
        "register_shortcut": "'key'",
        "register_realm_factory": "'prefix', factory",
        "register_provider": "'name', {}",
        "register_skill": "manifest",
        "register_skill_path": "'path'",
    }
    store = CapabilityStore(None)
    for capability, methods in CAPABILITY_GATES.items():
        for method in methods:
            for declared in (
                [capability],
                ["other"] if capability != "skill" else ["spell"],
            ):
                # Build a declared set that either contains the
                # capability or does not, and consent to it fully.
                store.consent("parity-rune", declared)
                gate = CapabilityGate(store, {"parity-rune": declared})
                runtime_allows = gate.allows("parity-rune", capability)
                findings = audit_source(
                    f"def rune_factory(api):\n    api.{method}({call_args[method]})\n",
                    declared_capabilities=declared,
                )
                audit_flags = any(
                    f.kind == "undeclared_capability_use" for f in findings
                )
                assert audit_flags != runtime_allows, (
                    f"{method} declared={declared}: "
                    f"audit_flags={audit_flags} runtime_allows={runtime_allows}"
                )
