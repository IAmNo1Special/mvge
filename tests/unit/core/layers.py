"""Unit tests for mvge.core.layers — the one resolved layer stack.

The interface is the test surface: these assert on resolved paths and their
derived scopes, never on the resolver's internal shape.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mvge.core.layers import (
    SCOPE_PRECEDENCE,
    ResolvedLayer,
    Scope,
    agent_dir,
    agent_extensions_dir,
    agent_skills_dir,
    agent_spells_dir,
    agents_dir,
    approval_dir,
    auth_dir,
    auth_file,
    default_rune_paths,
    extensions_dir,
    global_agents_dir,
    global_file,
    history_file,
    models_file,
    project_skills_dir,
    resolve_rune_layers,
    resolve_rune_paths,
    scope_rank,
    sessions_dir,
    skills_dir,
)


def test_global_agents_dir_defaults_to_home_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the override, the global layer is ``~/.agents``."""
    monkeypatch.delenv("MVGE_GLOBAL_DIR", raising=False)

    assert global_agents_dir() == Path("~/.agents").expanduser()


def test_global_agents_dir_honours_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``$MVGE_GLOBAL_DIR`` relocates the whole global layer."""
    relocated = tmp_path / "relocated_agents"
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(relocated))

    assert global_agents_dir() == relocated
    assert extensions_dir() == relocated / "extensions"


def test_global_dir_override_relocates_user_layers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The user and agent search layers follow the override.

    Regression: both layers were built from literal ``~/.agents``
    components, so the override moved the audit log and the CLI's
    discovery but left the install/uninstall targets on the real home.
    A rune could then be installed successfully and load nowhere.
    """
    relocated = tmp_path / "relocated_agents"
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(relocated))

    paths = resolve_rune_paths("test-agent")

    assert paths[0] == relocated / "extensions"
    assert paths[1] == relocated / "agents" / "test-agent" / "extensions"


def test_sessions_dir_follows_the_global_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tome storage relocates with the global layer.

    Regression: this was a module-level constant evaluated at import time
    from a literal ``~/.agents``, so the override moved the extensions and
    the audit log but left session storage on the real home -- and, because
    the value was frozen at import, setting the variable after startup did
    nothing at all.
    """
    relocated = tmp_path / "relocated_agents"
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(relocated))

    assert sessions_dir() == relocated / "sessions"


def test_sessions_dir_defaults_to_home_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MVGE_GLOBAL_DIR", raising=False)

    assert sessions_dir() == Path("~/.agents/sessions").expanduser()


def test_sessions_dir_reads_the_environment_at_call_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The override is honoured even when set after import.

    The old constant could not satisfy this: it was resolved once, when
    ``mvge.core.layers`` was first imported.
    """
    monkeypatch.delenv("MVGE_GLOBAL_DIR", raising=False)
    before = sessions_dir()

    relocated = tmp_path / "late_agents"
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(relocated))

    assert sessions_dir() == relocated / "sessions"
    assert sessions_dir() != before


def test_every_layer_resolver_follows_the_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No global-layer path is left pinned to the real home.

    Each of these was a literal ``~/.agents`` in some module, so a run with
    the override set still read or wrote the developer's real agents,
    credentials, skills, and session history.
    """
    relocated = tmp_path / "relocated_agents"
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(relocated))

    assert extensions_dir() == relocated / "extensions"
    assert sessions_dir() == relocated / "sessions"
    assert agents_dir() == relocated / "agents"
    assert agent_dir("coding_mvge") == relocated / "agents" / "coding_mvge"
    assert (
        agent_extensions_dir("coding_mvge")
        == relocated / "agents" / "coding_mvge" / "extensions"
    )
    assert (
        agent_skills_dir("coding_mvge")
        == relocated / "agents" / "coding_mvge" / "skills"
    )
    assert (
        agent_spells_dir("coding_mvge")
        == relocated / "agents" / "coding_mvge" / "spells"
    )
    assert skills_dir() == relocated / "skills"
    assert auth_dir() == relocated / "auth"
    assert auth_file("openrouter") == relocated / "auth" / "openrouter.json"
    assert approval_dir() == relocated / "approval"
    assert models_file() == relocated / "models.json"
    assert history_file() == relocated / "history"
    assert global_file("APPEND_SYSTEM.md") == relocated / "APPEND_SYSTEM.md"


def test_global_dir_override_relocates_every_rune_search_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every derived search path, not just the global one, follows the override.

    The criterion that matters: an isolated run must load, install and
    discover from one relocated tree. A single path left on the real home
    means a rune installed into the sandbox still loads from the developer's
    own installation.
    """
    relocated = tmp_path / "relocated_agents"
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(relocated))
    project = tmp_path / "project"
    project.mkdir()

    layers = resolve_rune_layers("test-agent", project_dir=project)

    assert layers == [
        ResolvedLayer(Scope.USER, relocated / "extensions"),
        ResolvedLayer(Scope.AGENT, relocated / "agents" / "test-agent" / "extensions"),
        ResolvedLayer(Scope.PROJECT, project / ".agents" / "extensions"),
    ]
    # The two global-owned layers sit under the relocated root. The project
    # layer is anchored to the project by definition, so it must not.
    global_owned = [layer for layer in layers if layer.scope is not Scope.PROJECT]
    assert [layer.path for layer in global_owned] == [
        relocated / "extensions",
        relocated / "agents" / "test-agent" / "extensions",
    ]
    assert all(relocated in layer.path.parents for layer in global_owned)


def test_project_layer_omitted_without_project_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No project_dir means no project layer — never ambient CWD.

    Regression: the project entry used to be the CWD-relative
    ``.agents/extensions``, so a process launched with its working
    directory at the real home silently loaded the real home's runes
    even with HOME isolated elsewhere.
    """
    (tmp_path / ".agents" / "extensions").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    paths = resolve_rune_paths("test-agent")

    assert all(p.is_absolute() for p in paths)
    assert Path(".agents/extensions") not in paths


def test_project_layer_is_omitted_not_defaulted_under_ambient_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The omitted project layer is absent, not merely not-first.

    A layer resolved to the ambient CWD would still load the CWD's code, so
    "not in front" is not good enough: it must not be resolved at all.
    """
    cwd_dir = tmp_path / "cwd"
    (cwd_dir / ".agents" / "extensions").mkdir(parents=True)
    monkeypatch.chdir(cwd_dir)

    scopes = [layer.scope for layer in resolve_rune_layers("test-agent")]

    assert Scope.PROJECT not in scopes
    assert all(
        layer.path != cwd_dir / ".agents" / "extensions"
        for layer in resolve_rune_layers("test-agent")
    )


def test_project_layer_anchored_to_project_dir(tmp_path) -> None:
    """An explicit project_dir anchors the project layer beneath it."""
    project = tmp_path / "project"
    (project / ".agents" / "extensions").mkdir(parents=True)

    paths = resolve_rune_paths("test-agent", project_dir=project)

    assert paths[2] == project / ".agents" / "extensions"
    assert paths[2].is_absolute()


def test_project_layer_accepts_str_project_dir(tmp_path) -> None:
    """String project dirs anchor the same as Path ones."""
    project = tmp_path / "project"
    project.mkdir()

    paths = resolve_rune_paths("test-agent", project_dir=str(project))

    assert paths[2] == project / ".agents" / "extensions"


def test_project_skills_dir_anchored_to_project_dir(tmp_path: Path) -> None:
    """An explicit project_dir anchors the project skills layer beneath it."""
    project = tmp_path / "project"

    resolved = project_skills_dir(project)

    assert resolved == project / ".agents" / "skills"
    assert resolved.is_absolute()


def test_project_skills_dir_accepts_str_project_dir(tmp_path: Path) -> None:
    """String project dirs anchor the same as Path ones."""
    project = tmp_path / "project"

    assert project_skills_dir(str(project)) == project / ".agents" / "skills"


def test_project_skills_dir_never_follows_the_ambient_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A named project wins over whatever the working directory happens to be.

    Regression: the ``seeker`` Rune held ``Path(".agents") / "skills"`` as a
    module constant and resolved it against the CWD at the call, so running
    from an unrelated checkout searched that checkout's skills. ADR 0017.
    """
    decoy = tmp_path / "decoy"
    (decoy / ".agents" / "skills").mkdir(parents=True)
    monkeypatch.chdir(decoy)

    named = tmp_path / "named"

    assert project_skills_dir(named) == named / ".agents" / "skills"
    assert decoy / ".agents" / "skills" not in (project_skills_dir(named).resolve(),)


def test_project_skills_dir_and_project_rune_path_are_siblings(
    tmp_path: Path,
) -> None:
    """Skills and Runes are two things one project layer carries."""
    project = tmp_path / "project"

    skills = project_skills_dir(project)
    runes = resolve_rune_paths("test-agent", project_dir=project)[2]

    assert skills.parent == runes.parent
    assert skills.name == "skills"
    assert runes.name == "extensions"


def test_project_skills_dir_resolves_relative_project_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative project_dir is anchored once, at the moment it is named.

    Same rule as ``_anchor_project_dir``: the caller named a project, so a
    relative name has to mean something. It is resolved here rather than
    later, which is what keeps the returned path stable if the process then
    changes directory.
    """
    monkeypatch.chdir(tmp_path)

    resolved = project_skills_dir("some-project")

    assert resolved == tmp_path / "some-project" / ".agents" / "skills"
    assert resolved.is_absolute()


def test_project_layer_keeps_position_before_extension_dir(tmp_path) -> None:
    """Precedence order is unchanged: home, agent, project, extension_dir."""
    project = tmp_path / "project"
    project.mkdir()

    paths = resolve_rune_paths("test-agent", "/ext", project_dir=project)

    assert paths[2] == project / ".agents" / "extensions"
    assert paths[3] == Path("/ext")


class TestScopeRanking:
    def test_precedence_runs_general_to_specific(self) -> None:
        assert SCOPE_PRECEDENCE == (
            Scope.GLOBAL,
            Scope.USER,
            Scope.AGENT,
            Scope.PROJECT,
        )

    def test_ranks_increase_monotonically(self) -> None:
        ranks = [scope_rank(scope) for scope in SCOPE_PRECEDENCE]

        assert ranks == sorted(ranks)
        assert len(set(ranks)) == len(ranks)

    def test_layer_rank_comes_from_its_scope(self) -> None:
        layer = ResolvedLayer(Scope.AGENT, Path("/x"))

        assert layer.rank == scope_rank(Scope.AGENT)

    def test_layer_exposes_rank_for_sorting(self) -> None:
        layers = [
            ResolvedLayer(Scope.PROJECT, Path("/p")),
            ResolvedLayer(Scope.USER, Path("/u")),
            ResolvedLayer(Scope.AGENT, Path("/a")),
        ]

        ordered = [layer.path for layer in sorted(layers, key=lambda lyr: lyr.rank)]

        assert ordered == [Path("/u"), Path("/a"), Path("/p")]

    def test_stack_is_already_ranked(self, tmp_path: Path) -> None:
        """The resolver's own output satisfies the ranking rule it defines."""
        project = tmp_path / "project"
        project.mkdir()

        layers = resolve_rune_layers("test-agent", project_dir=project)

        assert [layer.rank for layer in layers] == sorted(
            layer.rank for layer in layers
        )


class TestResolvedLayerScopes:
    def test_scope_is_derived_from_the_layer_not_the_path_string(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two layers built from identical-looking path shapes differ by scope.

        The old resolver substring-matched the path to infer scope, so a
        bespoke directory whose name happened to contain
        ``agents/<name>/extensions`` was reported as the agent layer. Scope
        comes from which layer the resolver placed it in, never from its text.
        """
        monkeypatch.delenv("MVGE_GLOBAL_DIR", raising=False)
        decoy = tmp_path / "agents" / "coder" / "extensions"
        decoy.mkdir(parents=True)

        layers = resolve_rune_layers(
            "coder", extras=[ResolvedLayer(Scope.PROJECT, decoy)]
        )

        agent_layers = [layer for layer in layers if layer.scope is Scope.AGENT]
        assert len(agent_layers) == 1
        assert agent_layers[0].path == (
            Path("~/.agents/agents/coder/extensions").expanduser()
        )

    def test_extra_keeps_its_declared_scope(self, tmp_path: Path) -> None:
        extra = tmp_path / "bespoke"

        layers = resolve_rune_layers(
            "coder", extras=[ResolvedLayer(Scope.AGENT, extra)]
        )

        assert layers[-1] == ResolvedLayer(Scope.AGENT, extra)

    def test_extras_are_ordered_by_rank_not_by_argument_order(
        self, tmp_path: Path
    ) -> None:
        """Precedence follows the layer a path belongs to.

        The decision this whole refactor exists for. Handed the two extras in
        the wrong order, the resolver must still put the agent layer ahead of
        the project layer -- otherwise the first-wins loader gives a bespoke
        project directory priority over the agent's own, purely because the
        caller concatenated the strings in a different sequence.
        """
        agent_extra = tmp_path / "agent-extra"
        project_extra = tmp_path / "project-extra"

        layers = resolve_rune_layers(
            "coder",
            extras=[
                ResolvedLayer(Scope.PROJECT, project_extra),
                ResolvedLayer(Scope.AGENT, agent_extra),
            ],
        )

        assert [layer.path for layer in layers][-2:] == [
            agent_extra,
            project_extra,
        ]

    def test_a_global_extra_outranks_the_whole_stack(self, tmp_path: Path) -> None:
        """``Scope.GLOBAL`` is rank zero, so it resolves first.

        The most general layer is the most general layer even when a caller
        declares it last.
        """
        bespoke = tmp_path / "bespoke"

        layers = resolve_rune_layers(
            "coder",
            extras=[
                ResolvedLayer(Scope.PROJECT, tmp_path / "proj"),
                ResolvedLayer(Scope.GLOBAL, bespoke),
            ],
        )

        assert layers[0] == ResolvedLayer(Scope.GLOBAL, bespoke)
        assert [layer.rank for layer in layers] == sorted(
            layer.rank for layer in layers
        )

    def test_same_rank_extras_keep_the_order_given(self, tmp_path: Path) -> None:
        """Ranking is stable: within a layer, the caller's order is the order.

        Two project-scoped directories are equally specific; the loader takes
        the first, so the caller's sequence has to survive ranking.
        """
        first = tmp_path / "first"
        second = tmp_path / "second"

        layers = resolve_rune_layers(
            "coder",
            extras=[
                ResolvedLayer(Scope.PROJECT, first),
                ResolvedLayer(Scope.PROJECT, second),
            ],
        )

        assert [layer.path for layer in layers][-2:] == [first, second]

    def test_extras_come_after_the_stack(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()

        layers = resolve_rune_layers(
            "coder",
            project_dir=project,
            extras=[ResolvedLayer(Scope.PROJECT, tmp_path / "bespoke")],
        )

        assert layers[-1].path == tmp_path / "bespoke"
        assert [layer.rank for layer in layers] == sorted(
            layer.rank for layer in layers
        )

    def test_default_rune_paths_is_the_stack_without_the_project_layer(
        self, tmp_path: Path
    ) -> None:
        project = tmp_path / "project"
        project.mkdir()

        assert default_rune_paths("coder") == [
            layer.path for layer in resolve_rune_layers("coder", project_dir=None)
        ]
        assert project / ".agents" / "extensions" not in default_rune_paths("coder")

    def test_same_inputs_resolve_identically(self, tmp_path: Path) -> None:
        """Resolution is a function of its inputs alone.

        Install path and discovery path ask the same question; when they get
        the same answer twice, a rune can no longer install successfully and
        then load nowhere.
        """
        project = tmp_path / "project"
        project.mkdir()
        extras = [ResolvedLayer(Scope.PROJECT, tmp_path / "bespoke")]

        first = resolve_rune_layers("coder", project_dir=project, extras=extras)
        second = resolve_rune_layers("coder", project_dir=project, extras=extras)

        assert first == second
        assert [lyr.scope for lyr in first] == [lyr.scope for lyr in second]

    def test_a_path_already_in_the_stack_is_not_repeated(self, tmp_path) -> None:
        """An extra that duplicates a stack layer resolves to one entry.

        Otherwise the same directory is scanned twice and a rune's factory
        runs twice per load.
        """
        duplicate = agents_dir() / "coder" / "extensions"

        paths = resolve_rune_paths("coder", str(duplicate))

        assert paths.count(duplicate) == 1
