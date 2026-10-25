from __future__ import annotations

import json
from pathlib import Path

from mvge.agent.config_manager import ConfigLayer, ConfigValue
from mvge.agent.environment import (
    DEFAULT_SYSTEM_PROMPT,
    PromptSource,
    ResolvedPrompt,
)
from mvge.agent.snapshot import (
    RuntimeSnapshot,
    SnapshotConfigEntry,
    SnapshotDiagnostic,
    SnapshotPrompt,
    SnapshotRune,
    SnapshotSkill,
    SnapshotSpell,
    SpellSource,
    to_snapshot_config_entry,
    to_snapshot_prompt,
    to_snapshot_rune,
    to_snapshot_skill,
    to_snapshot_spell,
)
from mvge.core.constants import DEFAULT_MODEL
from mvge.core.spells import MvgeSpell
from mvge.runes.types import (
    Diagnostic,
    DiagnosticKind,
    RuneManifest,
    RuneScope,
    SkillDiagnostic,
    SkillDiagnosticKind,
    SkillManifest,
    SkillScope,
    SpellDefinition,
)


class TestSnapshotSpell:
    def test_rune_spell_has_source_rune(self) -> None:
        spell = SpellDefinition(
            name="custom_spell",
            description="From a rune",
            source_rune="my_rune",
        )
        snap = to_snapshot_spell(spell)
        assert snap.name == "custom_spell"
        assert snap.source == SpellSource.RUNE
        assert snap.source_rune == "my_rune"

    def test_builtin_spell_has_builtin_source(self) -> None:
        spell = MvgeSpell(name="bash", description="Run shell", parameters={})
        snap = to_snapshot_spell(spell)
        assert snap.name == "bash"
        assert snap.source == SpellSource.BUILTIN
        assert snap.source_rune is None

    def test_rune_spell_without_source_rune(self) -> None:
        spell = SpellDefinition(name="anon_spell", description="No source")
        snap = to_snapshot_spell(spell)
        assert snap.source == SpellSource.RUNE
        assert snap.source_rune is None

    def test_spell_parameters_passed_through(self) -> None:
        props = {"properties": {"x": {"type": "string"}}}
        spell = SpellDefinition(
            name="sp", description="d", parameters=props, source_rune="r"
        )
        snap = to_snapshot_spell(spell)
        assert snap.parameters == props

    def test_builtin_spell_parameters_passed_through(self) -> None:
        props = {"type": "object", "properties": {"cmd": {"type": "string"}}}
        spell = MvgeSpell(name="run", description="d", parameters=props)
        snap = to_snapshot_spell(spell)
        assert snap.parameters == props


class TestSnapshotRune:
    def test_from_manifest(self) -> None:
        manifest = RuneManifest(
            name="test-rune",
            version="1.2.3",
            description="A test rune",
            scope=RuneScope.USER,
            path="/tmp/test-rune",
            hooks=[],
            entry_point="main.py",
            shortcuts=[],
            system_deps=["git"],
            python_deps=["pydantic"],
            enabled=True,
        )
        snap = to_snapshot_rune(manifest)
        assert snap.name == "test-rune"
        assert snap.version == "1.2.3"
        assert snap.description == "A test rune"
        assert snap.scope == RuneScope.USER.value
        assert snap.path == "/tmp/test-rune"
        assert snap.enabled is True
        assert snap.entry_point == "main.py"
        assert snap.system_deps == ["git"]
        assert snap.python_deps == ["pydantic"]

    def test_disabled_rune(self) -> None:
        manifest = RuneManifest(
            name="disabled-rune",
            version="0.1.0",
            description="Disabled",
            scope=RuneScope.AGENT,
            path="/tmp/disabled",
            enabled=False,
        )
        snap = to_snapshot_rune(manifest)
        assert snap.enabled is False
        assert snap.scope == RuneScope.AGENT.value


class TestSnapshotConfigEntry:
    def test_from_config_value(self) -> None:
        val = ConfigValue(value="my-model", layer=ConfigLayer.AGENT)
        cfg_path = Path("/tmp/agent/config.json")
        entry = to_snapshot_config_entry("model", val, cfg_path)
        assert entry.key == "model"
        assert entry.value == "my-model"
        assert entry.layer == ConfigLayer.AGENT.value
        assert entry.source_file == str(cfg_path)

    def test_defaults_layer_has_no_source_file(self) -> None:
        val = ConfigValue(value=4096, layer=ConfigLayer.DEFAULTS)
        entry = to_snapshot_config_entry("max_tokens", val, None)
        assert entry.layer == ConfigLayer.DEFAULTS.value
        assert entry.source_file is None

    def test_list_value(self) -> None:
        val = ConfigValue(value=["bash", "read"], layer=ConfigLayer.CONSTRUCTOR)
        entry = to_snapshot_config_entry("spells_enabled", val, None)
        assert entry.value == ["bash", "read"]
        assert entry.layer == ConfigLayer.CONSTRUCTOR.value


class TestSnapshotPrompt:
    def test_from_custom_path(self) -> None:
        prompt_path = Path("/tmp/custom/SYSTEM.md")
        resolved = ResolvedPrompt(
            text="Custom prompt",
            source=PromptSource.CUSTOM_PATH,
            path=prompt_path,
        )
        snap = to_snapshot_prompt(resolved)
        assert snap.source == PromptSource.CUSTOM_PATH.value
        assert snap.path == str(prompt_path)
        assert snap.text == "Custom prompt"

    def test_from_builtin(self) -> None:
        resolved = ResolvedPrompt(text="builtin", source=PromptSource.BUILTIN)
        snap = to_snapshot_prompt(resolved)
        assert snap.source == PromptSource.BUILTIN.value
        assert snap.path is None
        assert snap.text == "builtin"


class TestSnapshotSkill:
    def test_from_manifest(self) -> None:
        manifest = SkillManifest(
            name="my-skill",
            description="A skill",
            scope=SkillScope.PROJECT,
            path="/tmp/skills/my-skill",
            version="1.0.0",
            license="MIT",
            compatibility="Python 3.14+",
            allowed_tools="read write",
            disable_model_invocation=True,
        )
        snap = to_snapshot_skill(manifest)
        assert snap.name == "my-skill"
        assert snap.description == "A skill"
        assert snap.scope == SkillScope.PROJECT.value
        assert snap.path == "/tmp/skills/my-skill"
        assert snap.version == "1.0.0"
        assert snap.license == "MIT"
        assert snap.compatibility == "Python 3.14+"
        assert snap.allowed_tools == "read write"
        assert snap.disable_model_invocation is True

    def test_defaults(self) -> None:
        manifest = SkillManifest(
            name="simple-skill",
            description="Simple",
            scope=SkillScope.USER,
            path="/tmp/simple",
        )
        snap = to_snapshot_skill(manifest)
        assert snap.version == ""
        assert snap.license == ""
        assert snap.disable_model_invocation is False


class TestSnapshotDiagnostic:
    def test_rune_diagnostic_fields(self) -> None:
        diag = Diagnostic(
            kind=DiagnosticKind.SHADOWED_RUNE,
            rune_name="shadowed-rune",
            message="shadowed by user scope",
            scope=RuneScope.USER,
            path="/tmp/shadowed-rune",
        )
        snap = SnapshotDiagnostic(
            kind=diag.kind.value,
            name=diag.rune_name,
            message=diag.message,
            scope=diag.scope.value if diag.scope else None,
            path=diag.path,
            target="rune",
        )
        assert snap.kind == "shadowed_rune"
        assert snap.name == "shadowed-rune"
        assert snap.target == "rune"
        assert snap.scope == "user"

    def test_skill_diagnostic_fields(self) -> None:
        diag = SkillDiagnostic(
            kind=SkillDiagnosticKind.PARSE_WARNING,
            skill_name="bad-skill",
            message="could not parse",
            scope=SkillScope.PROJECT,
            path="/tmp/bad-skill",
        )
        snap = SnapshotDiagnostic(
            kind=diag.kind.value,
            name=diag.skill_name,
            message=diag.message,
            scope=diag.scope.value if diag.scope else None,
            path=diag.path,
            target="skill",
        )
        assert snap.kind == "parse_warning"
        assert snap.name == "bad-skill"
        assert snap.target == "skill"
        assert snap.scope == "project"


class TestRuntimeSnapshotSerialisation:
    def _full_snapshot(self) -> RuntimeSnapshot:
        return RuntimeSnapshot(
            agent_name="test-agent",
            model=DEFAULT_MODEL,
            spells=[
                SnapshotSpell(
                    name="bash",
                    description="Run shell",
                    source=SpellSource.BUILTIN,
                    parameters={},
                ),
                SnapshotSpell(
                    name="rune_spell",
                    description="From rune",
                    source=SpellSource.RUNE,
                    source_rune="my_rune",
                    parameters={},
                ),
            ],
            runes=[
                SnapshotRune(
                    name="my_rune",
                    version="1.0.0",
                    description="A rune",
                    scope="user",
                    path="/tmp/runes/my_rune",
                    enabled=True,
                    hooks=["turn_start"],
                    entry_point="main.py",
                    shortcuts=["ctrl+k"],
                    system_deps=[],
                    python_deps=[],
                ),
            ],
            config=[
                SnapshotConfigEntry(
                    key="model",
                    value="nvidia/nemotron",
                    layer="agent",
                    source_file="/home/user/.agents/agents/test-agent/config.json",
                ),
                SnapshotConfigEntry(
                    key="max_tokens",
                    value=4096,
                    layer="defaults",
                    source_file=None,
                ),
            ],
            prompt=SnapshotPrompt(
                source="builtin",
                path=None,
                text=DEFAULT_SYSTEM_PROMPT,
            ),
            skills=[
                SnapshotSkill(
                    name="my-skill",
                    description="A skill",
                    scope="project",
                    path="/tmp/skills/my-skill",
                    version="1.0.0",
                    license="MIT",
                    compatibility="",
                    allowed_tools="",
                    disable_model_invocation=False,
                ),
            ],
            diagnostics=[
                SnapshotDiagnostic(
                    kind="shadowed_rune",
                    name="duplicate-rune",
                    message="shadowed",
                    scope="user",
                    path="/tmp/dup",
                    target="rune",
                ),
                SnapshotDiagnostic(
                    kind="parse_warning",
                    name="bad-skill",
                    message="could not parse",
                    scope="project",
                    path="/tmp/bad",
                    target="skill",
                ),
            ],
        )

    def test_to_json_is_valid_json(self) -> None:
        snap = self._full_snapshot()
        result = json.loads(snap.to_json())
        assert isinstance(result, dict)

    def test_to_json_contains_agent_name(self) -> None:
        snap = self._full_snapshot()
        result = json.loads(snap.to_json())
        assert result["agent_name"] == "test-agent"

    def test_to_json_serialises_spells(self) -> None:
        snap = self._full_snapshot()
        result = json.loads(snap.to_json())
        assert len(result["spells"]) == 2
        assert result["spells"][0]["source"] == "builtin"
        assert result["spells"][1]["source"] == "rune"
        assert result["spells"][1]["source_rune"] == "my_rune"

    def test_to_json_serialises_runes(self) -> None:
        snap = self._full_snapshot()
        result = json.loads(snap.to_json())
        assert len(result["runes"]) == 1
        assert result["runes"][0]["name"] == "my_rune"
        assert result["runes"][0]["enabled"] is True
        assert result["runes"][0]["scope"] == "user"

    def test_to_json_serialises_config(self) -> None:
        snap = self._full_snapshot()
        result = json.loads(snap.to_json())
        assert len(result["config"]) == 2
        assert result["config"][0]["key"] == "model"
        assert result["config"][0]["layer"] == "agent"
        assert result["config"][1]["layer"] == "defaults"
        assert result["config"][1]["source_file"] is None

    def test_to_json_serialises_prompt(self) -> None:
        snap = self._full_snapshot()
        result = json.loads(snap.to_json())
        assert result["prompt"]["source"] == "builtin"
        assert result["prompt"]["path"] is None

    def test_to_json_serialises_skills(self) -> None:
        snap = self._full_snapshot()
        result = json.loads(snap.to_json())
        assert len(result["skills"]) == 1
        assert result["skills"][0]["name"] == "my-skill"
        assert result["skills"][0]["scope"] == "project"

    def test_to_json_serialises_diagnostics(self) -> None:
        snap = self._full_snapshot()
        result = json.loads(snap.to_json())
        assert len(result["diagnostics"]) == 2
        assert result["diagnostics"][0]["target"] == "rune"
        assert result["diagnostics"][1]["target"] == "skill"

    def test_to_json_empty_snapshot(self) -> None:
        snap = RuntimeSnapshot(
            agent_name="empty-agent",
            model=DEFAULT_MODEL,
        )
        result = json.loads(snap.to_json())
        assert result["agent_name"] == "empty-agent"
        assert result["spells"] == []
        assert result["runes"] == []
        assert result["config"] == []
        assert result["skills"] == []
        assert result["diagnostics"] == []

    def test_to_dict_returns_dataclass_dict(self) -> None:
        snap = RuntimeSnapshot(
            agent_name="test",
            model=DEFAULT_MODEL,
            spells=[
                SnapshotSpell(
                    name="bash",
                    description="",
                    source=SpellSource.BUILTIN,
                )
            ],
        )
        d = snap.to_dict()
        assert d["agent_name"] == "test"
        assert d["spells"][0]["source"] == "builtin"


class TestAssembleSnapshot:
    def test_assemble_with_mixed_spells(self) -> None:
        from mvge.agent.snapshot import assemble_snapshot

        builtin = MvgeSpell(name="bash", description="shell", parameters={})
        rune_spell = SpellDefinition(
            name="custom_tool",
            description="from rune",
            parameters={},
            source_rune="ext-rune",
        )
        manifest = RuneManifest(
            name="ext-rune",
            version="2.0.0",
            description="extension",
            scope=RuneScope.USER,
            path="/tmp/ext-rune",
            enabled=True,
            entry_point="main.py",
        )
        config_values = {
            "model": ConfigValue(value="gpt-4", layer=ConfigLayer.AGENT),
            "max_tokens": ConfigValue(value=8192, layer=ConfigLayer.DEFAULTS),
        }
        resolved_prompt = ResolvedPrompt(text="Hello", source=PromptSource.BUILTIN)
        skill_manifest = SkillManifest(
            name="some-skill",
            description="desc",
            scope=SkillScope.USER,
            path="/tmp/some-skill",
            version="0.2.0",
        )
        rune_diag = Diagnostic(
            kind=DiagnosticKind.LOAD_FAILURE,
            rune_name="broken",
            message="failed",
            scope=RuneScope.PROJECT,
            path="/tmp/broken",
        )
        skill_diag = SkillDiagnostic(
            kind=SkillDiagnosticKind.SHADOWED_SKILL,
            skill_name="dup-skill",
            message="shadowed",
            scope=SkillScope.USER,
            path="/tmp/dup-skill",
        )

        snap = assemble_snapshot(
            agent_name="test-agent",
            model="gpt-4",
            spells=[builtin, rune_spell],
            rune_manifests=[manifest],
            config_values=config_values,
            config_source_files={
                ConfigLayer.AGENT: Path("/agent/config.json"),
                ConfigLayer.PROJECT: Path("/project/config.json"),
            },
            resolved_prompt=resolved_prompt,
            skills=[skill_manifest],
            rune_diagnostics=[rune_diag],
            skill_diagnostics=[skill_diag],
        )

        assert snap.agent_name == "test-agent"
        assert snap.model == "gpt-4"
        assert len(snap.spells) == 2
        assert snap.spells[0].source == SpellSource.BUILTIN
        assert snap.spells[1].source == SpellSource.RUNE
        assert snap.spells[1].source_rune == "ext-rune"
        assert len(snap.runes) == 1
        assert snap.runes[0].version == "2.0.0"
        assert len(snap.config) == 2
        assert snap.config[0].layer == "agent"
        assert snap.config[1].layer == "defaults"
        assert snap.prompt.source == "builtin"
        assert len(snap.skills) == 1
        assert snap.skills[0].version == "0.2.0"
        assert len(snap.diagnostics) == 2
        assert snap.diagnostics[0].target == "rune"
        assert snap.diagnostics[1].target == "skill"

    def test_assemble_empty(self) -> None:
        from mvge.agent.snapshot import assemble_snapshot

        resolved_prompt = ResolvedPrompt(text="x", source=PromptSource.BUILTIN)

        snap = assemble_snapshot(
            agent_name="empty",
            model=DEFAULT_MODEL,
            spells=[],
            rune_manifests=[],
            config_values={},
            config_source_files={},
            resolved_prompt=resolved_prompt,
            skills=[],
            rune_diagnostics=[],
            skill_diagnostics=[],
        )

        assert snap.spells == []
        assert snap.runes == []
        assert snap.config == []
        assert snap.skills == []
        assert snap.diagnostics == []
        assert snap.prompt.source == "builtin"
