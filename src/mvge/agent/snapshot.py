from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mvge.agent.config_manager import ConfigLayer, ConfigValue
from mvge.core.spells import MvgeSpell
from mvge.runes.types import (
    Diagnostic,
    RuneManifest,
    SkillDiagnostic,
    SkillManifest,
    SpellDefinition,
)

if TYPE_CHECKING:
    from mvge.agent.environment import (
        ResolvedPrompt,
    )


class SpellSource(StrEnum):
    BUILTIN = "builtin"
    RUNE = "rune"


@dataclass
class SnapshotSpell:
    name: str
    description: str
    source: SpellSource
    source_rune: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class SnapshotRune:
    name: str
    version: str
    description: str
    scope: str
    path: str
    enabled: bool
    hooks: list[str] = field(default_factory=list)
    entry_point: str = ""
    shortcuts: list[str] = field(default_factory=list)
    system_deps: list[str] = field(default_factory=list)
    python_deps: list[str] = field(default_factory=list)


@dataclass
class SnapshotConfigEntry:
    key: str
    value: Any
    layer: str
    source_file: str | None = None


@dataclass
class SnapshotPrompt:
    source: str
    path: str | None = None
    text: str = ""


@dataclass
class SnapshotSkill:
    name: str
    description: str
    scope: str
    path: str
    version: str = ""
    license: str = ""
    compatibility: str = ""
    allowed_tools: str = ""
    disable_model_invocation: bool = False


@dataclass
class SnapshotDiagnostic:
    kind: str
    name: str
    message: str
    scope: str | None = None
    path: str = ""
    target: str = "rune"


@dataclass
class RuntimeSnapshot:
    agent_name: str
    model: str
    spells: list[SnapshotSpell] = field(default_factory=list)
    runes: list[SnapshotRune] = field(default_factory=list)
    config: list[SnapshotConfigEntry] = field(default_factory=list)
    prompt: SnapshotPrompt | None = None
    skills: list[SnapshotSkill] = field(default_factory=list)
    diagnostics: list[SnapshotDiagnostic] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(dataclasses.asdict(self), indent=indent, default=str)


def to_snapshot_spell(spell: MvgeSpell | SpellDefinition) -> SnapshotSpell:
    source_rune = getattr(spell, "source_rune", None)
    if isinstance(spell, SpellDefinition) or source_rune is not None:
        return SnapshotSpell(
            name=spell.name,
            description=spell.description,
            source=SpellSource.RUNE,
            source_rune=source_rune,
            parameters=spell.parameters,
        )
    return SnapshotSpell(
        name=spell.name,
        description=spell.description,
        source=SpellSource.BUILTIN,
        parameters=spell.parameters,
    )


def to_snapshot_rune(manifest: RuneManifest) -> SnapshotRune:
    return SnapshotRune(
        name=manifest.name,
        version=manifest.version,
        description=manifest.description,
        scope=manifest.scope.value,
        path=manifest.path,
        enabled=manifest.enabled,
        hooks=[h.value for h in manifest.hooks],
        entry_point=manifest.entry_point,
        shortcuts=[sc.key for sc in manifest.shortcuts],
        system_deps=list(manifest.system_deps),
        python_deps=list(manifest.python_deps),
    )


def to_snapshot_config_entry(
    key: str, val: ConfigValue, source_file: str | Path | None
) -> SnapshotConfigEntry:
    return SnapshotConfigEntry(
        key=key,
        value=val.value,
        layer=val.layer.value,
        source_file=str(source_file) if source_file is not None else None,
    )


def to_snapshot_prompt(resolved: ResolvedPrompt) -> SnapshotPrompt:
    return SnapshotPrompt(
        source=resolved.source.value,
        path=str(resolved.path) if resolved.path is not None else None,
        text=resolved.text,
    )


def to_snapshot_skill(skill: SkillManifest) -> SnapshotSkill:
    return SnapshotSkill(
        name=skill.name,
        description=skill.description,
        scope=skill.scope.value,
        path=skill.path,
        version=skill.version,
        license=skill.license,
        compatibility=skill.compatibility,
        allowed_tools=skill.allowed_tools,
        disable_model_invocation=skill.disable_model_invocation,
    )


def to_snapshot_diagnostic(
    diag: Diagnostic | SkillDiagnostic,
) -> SnapshotDiagnostic:
    if isinstance(diag, SkillDiagnostic):
        return SnapshotDiagnostic(
            kind=diag.kind.value,
            name=diag.skill_name,
            message=diag.message,
            scope=diag.scope.value if diag.scope is not None else None,
            path=diag.path,
            target="skill",
        )
    return SnapshotDiagnostic(
        kind=diag.kind.value,
        name=diag.rune_name,
        message=diag.message,
        scope=diag.scope.value if diag.scope is not None else None,
        path=diag.path,
        target="rune",
    )


def assemble_snapshot(
    agent_name: str,
    model: str,
    spells: list[MvgeSpell | SpellDefinition],
    rune_manifests: list[RuneManifest],
    config_values: dict[str, ConfigValue],
    config_source_files: dict[ConfigLayer, Path | None],
    resolved_prompt: ResolvedPrompt,
    skills: list[SkillManifest],
    rune_diagnostics: list[Diagnostic],
    skill_diagnostics: list[SkillDiagnostic],
) -> RuntimeSnapshot:
    config_source_paths: dict[ConfigLayer, str | None] = {
        layer: str(path) if path is not None else None
        for layer, path in config_source_files.items()
    }

    return RuntimeSnapshot(
        agent_name=agent_name,
        model=model,
        spells=[to_snapshot_spell(s) for s in spells],
        runes=[to_snapshot_rune(m) for m in rune_manifests],
        config=[
            to_snapshot_config_entry(key, val, config_source_paths.get(val.layer))
            for key, val in config_values.items()
        ],
        prompt=to_snapshot_prompt(resolved_prompt),
        skills=[to_snapshot_skill(s) for s in skills],
        diagnostics=[to_snapshot_diagnostic(d) for d in rune_diagnostics]
        + [to_snapshot_diagnostic(d) for d in skill_diagnostics],
    )
