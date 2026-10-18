from __future__ import annotations

from pathlib import Path

from mvgeos_runes.skill_catalog import SkillCatalog
from mvgeos_runes.types import (
    SkillDiagnostic,
    SkillDiagnosticKind,
    SkillLoad,
    SkillManifest,
    SkillScope,
)


class TestSkillCatalog:
    def test_load_skills(self) -> None:
        catalog = SkillCatalog()
        skill1 = SkillManifest(
            name="skill-one",
            description="First skill",
            scope=SkillScope.PROJECT,
            path="/tmp/skill-one",
        )
        skill2 = SkillManifest(
            name="skill-two",
            description="Second skill",
            scope=SkillScope.USER,
            path="/tmp/skill-two",
        )
        loads = [SkillLoad(manifest=skill1), SkillLoad(manifest=skill2)]
        catalog.load_skills(loads)

        skills = catalog.get_skills()
        assert len(skills) == 2
        names = {s.name for s in skills}
        assert names == {"skill-one", "skill-two"}

    def test_get_skills_empty(self) -> None:
        catalog = SkillCatalog()
        assert catalog.get_skills() == []

    def test_load_skills_stores_diagnostics(self) -> None:
        catalog = SkillCatalog()
        skill = SkillManifest(
            name="loaded-skill",
            description="Loaded",
            scope=SkillScope.PROJECT,
            path="/tmp/loaded",
        )
        diag = SkillDiagnostic(
            kind=SkillDiagnosticKind.SHADOWED_SKILL,
            skill_name="dup-skill",
            message="shadowed",
            scope=SkillScope.USER,
            path="/tmp/dup",
        )
        catalog.load_skills([SkillLoad(manifest=skill)], diagnostics=[diag])

        assert catalog.skill_diagnostics == [diag]

    def test_skill_diagnostics_empty_by_default(self) -> None:
        catalog = SkillCatalog()
        assert catalog.skill_diagnostics == []

    def test_load_skills_without_diagnostics_still_works(self) -> None:
        catalog = SkillCatalog()
        skill = SkillManifest(
            name="no-diag", description="d", scope=SkillScope.PROJECT, path="/x"
        )
        catalog.load_skills([SkillLoad(manifest=skill)])
        assert catalog.skill_diagnostics == []
        assert len(catalog.get_skills()) == 1

    def test_load_skills_diagnostics_accumulate(self) -> None:
        catalog = SkillCatalog()
        diag1 = SkillDiagnostic(
            kind=SkillDiagnosticKind.PARSE_WARNING,
            skill_name="s1",
            message="bad",
        )
        diag2 = SkillDiagnostic(
            kind=SkillDiagnosticKind.SHADOWED_SKILL,
            skill_name="s2",
            message="dup",
        )
        catalog.load_skills([], diagnostics=[diag1])
        catalog.load_skills([], diagnostics=[diag2])
        assert catalog.skill_diagnostics == [diag1, diag2]

    def test_extend_skill_diagnostics_appends(self) -> None:
        catalog = SkillCatalog()
        sdiag = SkillDiagnostic(
            kind=SkillDiagnosticKind.PARSE_WARNING,
            skill_name="s1",
            message="warned",
        )
        catalog.extend_skill_diagnostics([sdiag])
        assert catalog.skill_diagnostics == [sdiag]

    def test_register_skill_path(self) -> None:
        catalog = SkillCatalog()
        catalog.register_skill_path("/tmp/skills")
        assert catalog.get_registered_skill_paths() == [Path("/tmp/skills").resolve()]

    def test_register_skill_path_dedupes(self) -> None:
        catalog = SkillCatalog()
        p = "/tmp/skills"
        catalog.register_skill_path(p)
        catalog.register_skill_path(p)
        assert catalog.get_registered_skill_paths() == [Path(p).resolve()]

    def test_register_skill(self) -> None:
        catalog = SkillCatalog()
        manifest = SkillManifest(name="api-skill", description="API registered skill")
        catalog.register_skill(manifest)
        assert catalog.get_skills() == [manifest]

    def test_is_skill_active(self) -> None:
        catalog = SkillCatalog()
        assert not catalog.is_skill_active("foo")
        catalog.mark_skill_active("foo")
        assert catalog.is_skill_active("foo")

    def test_get_active_skills(self) -> None:
        catalog = SkillCatalog()
        catalog.mark_skill_active("a")
        catalog.mark_skill_active("b")
        assert catalog.get_active_skills() == {"a", "b"}
