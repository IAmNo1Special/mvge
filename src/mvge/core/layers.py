"""The one resolved layer stack for the ``.agents`` protocol.

This module is the only place a Rune search path is derived. Its docstring
names the failure it exists to prevent:

    the install path and the discovery path resolving differently is what
    allows a rune to install successfully and then load nowhere.

Two rules make that impossible rather than merely unlikely.

**One call.** Installation, discovery, the CLI's Rune command mount, and
the agent's rune loading all go through :func:`resolve_rune_layers`. There
is no second derivation to drift.

**Scope is derived, never inferred.** :class:`Scope` is a *ranking rule*
over the layer stack, not a label a caller attached to a path it happened
to concatenate. A layer's scope comes from the layer it was placed in.
Nothing substring-matches a path to guess where it came from -- that was
the bug this replaces, and it mislabelled any bespoke directory whose name
happened to contain a standard layer's shape.

Layers run most general to most specific: ``global -> user -> agent ->
project``. The global layer is the tree root; the user layer is its
``extensions/``, the agent layer is its ``agents/<name>/extensions/``, and
the project layer is anchored beneath an explicitly named project
directory.

The same shape governs Skills: :func:`skills_dir` is the user layer and
:func:`project_skills_dir` is the project layer, anchored the same way and
omitted when no project is named. Skills and Runes are both things a Mvge
loads from a named project, so they answer that question the same way.

The project layer is **omitted, never defaulted**. A caller that passes no
project directory gets no project layer, so the ambient working directory
can never leak into a search path and load code the caller did not name.
A wrong cwd here loads the wrong *code*, which is why this differs from
content resolution: a wrong cwd in a persona chain costs a missing
paragraph, and nothing else.

Every global-layer path honours ``$MVGEOS_GLOBAL_DIR`` (``GLOBAL_DIR_ENV``),
and it is read on every call rather than frozen at import. That is what
relocates the whole layer stack -- global runes, Tomes, auth, approval
state, model cache, audit log -- into one sandbox, and it is what makes an
isolated run possible.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from mvge.core.constants import (
    GLOBAL_DIR_ENV,
    PROJECT_RUNE_PATH,
    PROJECT_SKILL_PATH,
)


class Scope(StrEnum):
    """Which layer of the stack a resolved path belongs to.

    The value is the layer's name; the *rank* (see :data:`SCOPE_PRECEDENCE`)
    is the precedence. Runes and Skills both address layers through this
    enum, so a rune discovered in the agent layer and a skill discovered in
    the agent layer name it the same way.
    """

    GLOBAL = "global"
    USER = "user"
    AGENT = "agent"
    PROJECT = "project"


RuneScope = Scope
"""Alias used where a layer names a Rune's origin.

Runes and Skills both address their origin through the same four layers, so
both vocabularies name them the same way. ``SkillScope`` lives beside the
Skill types that use it, in the runes package. (Named without its dotted path
because ``dependency_direction`` treats any mention of a first-party package
in this file as an import.)
"""


#: Layer order, most general to most specific. Index *is* the rank: a lower
#: rank wins a name collision, because ``load_runes_from_paths`` is
#: first-wins and the loader is handed layers in rank order.
SCOPE_PRECEDENCE: tuple[Scope, ...] = (
    Scope.GLOBAL,
    Scope.USER,
    Scope.AGENT,
    Scope.PROJECT,
)


def scope_rank(scope: Scope) -> int:
    """Precedence rank for a layer. Lower wins."""
    return SCOPE_PRECEDENCE.index(scope)


@dataclass(frozen=True, slots=True)
class ResolvedLayer:
    """One layer of the resolved stack: a path and the layer it came from.

    A caller-declared extra states its own scope. That is not the inference
    this module replaced -- inference means reading a path and guessing, and
    nothing here does that. An extra is the caller's most specific
    declaration about where its own directory belongs, so it is named
    outright.
    """

    scope: Scope
    path: Path

    @property
    def rank(self) -> int:
        return scope_rank(self.scope)


def global_agents_dir() -> Path:
    """The global ``.agents`` directory: ``$MVGEOS_GLOBAL_DIR`` or ``~/.agents``.

    The single resolver for the global layer. Every global-layer path is
    derived here rather than spelled out, because the install path and the
    discovery path resolving differently is what allows a rune to install
    successfully and then load nowhere.
    """
    override = os.environ.get(GLOBAL_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path("~/.agents").expanduser()


def sessions_dir() -> Path:
    """Tome storage: ``<global>/sessions``.

    A function rather than a module constant so the override is read when
    it is needed, not frozen at import. The previous
    ``DEFAULT_SESSION_DIR`` was resolved once during import and could not be
    redirected afterwards.
    """
    return global_agents_dir() / "sessions"


def agents_dir() -> Path:
    """Installed agent packages: ``<global>/agents``."""
    return global_agents_dir() / "agents"


def agent_dir(name: str) -> Path:
    """One agent's directory: ``<global>/agents/<name>``."""
    return agents_dir() / name


def agent_extensions_dir(name: str) -> Path:
    """Agent-scope rune directory: ``<global>/agents/<name>/extensions``."""
    return agent_dir(name) / "extensions"


def agent_skills_dir(name: str) -> Path:
    """Agent-scope skills directory: ``<global>/agents/<name>/skills``."""
    return agent_dir(name) / "skills"


def agent_spells_dir(name: str) -> Path:
    """Agent-scope Spells directory: ``<global>/agents/<name>/spells``.

    Added because a Rune reached for its own Spells root and there was
    nothing to reach for: the host package spells this path out in both its
    installer and its spell discovery, and the Rune that searches it spelled
    out a different one. A Rune may not import the host package -- the
    direction is ``agent -> runes -> core`` -- so without this resolver the
    only options were a wrong literal or an inverted host dependency.
    """
    return agent_dir(name) / "spells"


def skills_dir() -> Path:
    """User-scope skills: ``<global>/skills``."""
    return global_agents_dir() / "skills"


def project_skills_dir(project_dir: str | Path) -> Path:
    """Project-scope skills: ``<project>/.agents/skills``.

    The project layer of the skills scheme, and the only way to name it.
    ``project_dir`` is required rather than defaulted, for the reason
    :func:`resolve_rune_layers` states: the project layer is *omitted* when a
    caller names no project, so the ambient working directory can never leak
    into a search path. A Skill is instructions the Mvge will follow, so a
    search rooted at a checkout the caller did not name loads the wrong ones
    -- the same failure a wrong cwd causes when it loads the wrong Rune.

    This exists because the engine spelled the path out in two places and the
    Rune spelled a third form of it out: ``mvgeos-gui`` joined
    ``project_path / ".agents" / "skills"`` and the ``seeker`` Rune joined
    ``Path(".agents") / "skills"`` with no project at all, which bound it to
    the working directory at the moment of the call. See ADR 0017.
    """
    return _anchor_project_dir(project_dir) / PROJECT_SKILL_PATH


def auth_dir() -> Path:
    """Credential store: ``<global>/auth``."""
    return global_agents_dir() / "auth"


def auth_file(provider: str) -> Path:
    """One provider's credential file: ``<global>/auth/<provider>.json``."""
    return auth_dir() / f"{provider}.json"


def approval_dir() -> Path:
    """Approval policy and grants: ``<global>/approval``."""
    return global_agents_dir() / "approval"


def models_file() -> Path:
    """Model cache: ``<global>/models.json``."""
    return global_agents_dir() / "models.json"


def history_file() -> Path:
    """REPL history: ``<global>/history``."""
    return global_agents_dir() / "history"


def global_file(name: str) -> Path:
    """A file directly in the global layer: ``<global>/<name>``."""
    return global_agents_dir() / name


def extensions_dir() -> Path:
    """The user-scope rune directory: ``<global>/extensions``."""
    return global_agents_dir() / "extensions"


def _anchor_project_dir(project_dir: str | Path) -> Path:
    """Anchor a project directory to an absolute path.

    A relative project directory is resolved against the current working
    directory *here*, once, at the point a project layer was explicitly
    requested. That is not the ambient leak the layer stack guards against:
    the caller named a project, so a relative name has to mean something.
    A caller that wants no project layer passes nothing.
    """
    anchor = Path(project_dir).expanduser()
    if not anchor.is_absolute():
        anchor = Path.cwd() / anchor
    return anchor


def resolve_rune_layers(
    agent_name: str,
    *,
    project_dir: str | Path | None = None,
    global_dir: str | Path | None = None,
    extras: Sequence[ResolvedLayer] = (),
) -> list[ResolvedLayer]:
    """Resolve the Rune layer stack, most general layer first.

    Args:
        agent_name: Names the agent layer. ``{agent_name}`` placeholders in
            caller-supplied extras are expanded by the loader, not here.
        project_dir: Anchors the project layer. ``None`` omits that layer
            entirely -- it is never defaulted to the working directory.
        global_dir: Overrides the global layer. ``None`` reads
            ``$MVGEOS_GLOBAL_DIR``, else ``~/.agents``.
        extras: Caller-declared layers. Each states its own scope; an extra
            that already appears in the stack is dropped, because one
            directory is one layer scanned once. Placement is decided by rank,
            not by argument order -- so an agent-scoped extra given last still
            resolves ahead of a project-scoped one.

    Returns:
        Layers in rank order, ready to hand to a first-wins loader.

        The standard stack emits the three layers that actually carry Runes:
        the global root's ``extensions/`` is the *user* layer, and its
        ``agents/<name>/extensions/`` is the *agent* layer. The global layer
        is the tree they share, not a fourth Rune directory, so
        ``Scope.GLOBAL`` is rankable for a caller's own more-general
        directory but is never emitted here.

        Ranking is what makes precedence a property of the *layer* rather
        than of the order a caller happened to concatenate strings in. The
        sort is stable, so extras sharing a rank keep the order given: two
        equally specific directories are decided by the caller, not by a
        sort's internal tie-breaking.
    """
    root = (
        Path(global_dir).expanduser() if global_dir is not None else global_agents_dir()
    )
    resolved: list[ResolvedLayer] = [
        ResolvedLayer(Scope.USER, root / "extensions"),
        ResolvedLayer(Scope.AGENT, root / "agents" / agent_name / "extensions"),
    ]
    if project_dir is not None:
        resolved.append(
            ResolvedLayer(
                Scope.PROJECT,
                _anchor_project_dir(project_dir) / PROJECT_RUNE_PATH,
            )
        )

    seen = {layer.path for layer in resolved}
    for extra in extras:
        path = Path(extra.path).expanduser()
        if path in seen:
            continue
        seen.add(path)
        resolved.append(ResolvedLayer(scope=extra.scope, path=path))

    resolved.sort(key=lambda layer: layer.rank)
    return resolved


def default_rune_paths(
    agent_name: str, global_dir: str | Path | None = None
) -> list[Path]:
    """The user and agent rune layers, in precedence order.

    ``global_dir`` overrides the global layer when given, else
    ``$MVGEOS_GLOBAL_DIR``, else ``~/.agents``. The project layer is absent
    by construction: there is nothing to anchor it to.
    """
    return [
        layer.path for layer in resolve_rune_layers(agent_name, global_dir=global_dir)
    ]


def resolve_rune_paths(
    agent_name: str,
    extension_dir: str | None = None,
    project_dir: str | Path | None = None,
    global_dir: str | Path | None = None,
) -> list[Path]:
    """The Rune search paths, in precedence order.

    The bare-path projection of :func:`resolve_rune_layers`, for callers
    that need paths and no scopes. ``extension_dir`` is a declared extra and
    resolves last, matching every other "extra" in the engine.
    """
    extras = (
        (ResolvedLayer(Scope.PROJECT, Path(extension_dir)),) if extension_dir else ()
    )
    return [
        layer.path
        for layer in resolve_rune_layers(
            agent_name,
            project_dir=project_dir,
            global_dir=global_dir,
            extras=extras,
        )
    ]


__all__ = [
    "SCOPE_PRECEDENCE",
    "ResolvedLayer",
    "RuneScope",
    "Scope",
    "agent_dir",
    "agent_extensions_dir",
    "agent_skills_dir",
    "agent_spells_dir",
    "agents_dir",
    "approval_dir",
    "auth_dir",
    "auth_file",
    "default_rune_paths",
    "extensions_dir",
    "global_agents_dir",
    "global_file",
    "history_file",
    "models_file",
    "project_skills_dir",
    "resolve_rune_layers",
    "resolve_rune_paths",
    "scope_rank",
    "sessions_dir",
    "skills_dir",
]
