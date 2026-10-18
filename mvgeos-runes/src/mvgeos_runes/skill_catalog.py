"""SkillCatalog: the skill half of the RuneRunner / SkillCatalog split (issue #138).

Owns all skill-related state: loaded skill manifests, skill diagnostics,
dynamically registered skill paths, and the active-skills session set.

RuneRunner composes a SkillCatalog and exposes it via the ``skill_catalog``
property. Runes register skills through ``RuneAPI`` (which delegates to the
catalog); the engine reads the catalog through the runner.
"""

from __future__ import annotations

from pathlib import Path

from mvgeos_runes.types import (
    SkillDiagnostic,
    SkillLoad,
    SkillManifest,
)


class SkillCatalog:
    """Container for all skill-related state owned by the embedding layer.

    Skills are declarative capability packs (agentskills.io format), distinct
    from executable Spells and executable Runes. This catalog holds the
    discovered/registered manifests, diagnostics from discovery, dynamically
    registered skill directory paths, and the session's active-skill set.
    """

    def __init__(self) -> None:
        self._loaded_skills: list[SkillLoad] = []
        self._skill_diagnostics: list[SkillDiagnostic] = []
        self._registered_skill_paths: list[Path] = []
        self._active_skills: set[str] = set()

    # -- loading ---------------------------------------------------------

    def load_skills(
        self,
        loads: list[SkillLoad],
        diagnostics: list[SkillDiagnostic] | None = None,
    ) -> None:
        """Load skills from discovery.

        Args:
            loads: List of skill loads (manifests) from discovery.
            diagnostics: Optional list of skill diagnostics to retain for
                the runtime snapshot.
        """
        self._loaded_skills.extend(loads)
        if diagnostics is not None:
            self._skill_diagnostics.extend(diagnostics)

    def get_skills(self) -> list[SkillManifest]:
        """Get all loaded skill manifests."""
        return [load.manifest for load in self._loaded_skills]

    def register_skill_path(self, path: Path | str) -> None:
        """Register a skill directory path dynamically."""
        resolved = Path(path).expanduser().resolve()
        if resolved not in self._registered_skill_paths:
            self._registered_skill_paths.append(resolved)

    def get_registered_skill_paths(self) -> list[Path]:
        """Get all dynamically registered skill paths."""
        return list(self._registered_skill_paths)

    def register_skill(self, manifest: SkillManifest) -> None:
        """Register an in-memory SkillManifest directly."""
        self.load_skills([SkillLoad(manifest=manifest)])

    # -- diagnostics -----------------------------------------------------

    @property
    def skill_diagnostics(self) -> list[SkillDiagnostic]:
        """Diagnostics accumulated during skill discovery."""
        return list(self._skill_diagnostics)

    def extend_skill_diagnostics(self, diagnostics: list[SkillDiagnostic]) -> None:
        """Append loader diagnostics for paths that produced no skills."""
        self._skill_diagnostics.extend(diagnostics)

    # -- active skills ---------------------------------------------------

    def is_skill_active(self, name: str) -> bool:
        """Check if a skill has already been activated in this session."""
        return name in self._active_skills

    def mark_skill_active(self, name: str) -> None:
        """Mark a skill as active in this session."""
        self._active_skills.add(name)

    def get_active_skills(self) -> set[str]:
        """Get names of all skills activated in this session."""
        return set(self._active_skills)
