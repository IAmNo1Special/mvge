"""Unit tests for the agent-name resolution module.

Every case here is a reason not to guess. The three that matter are the ones
that make the engine name nothing: an agent the Summoner asked for wins, an
installed one is adopted only when they named none and there is exactly one,
and an unknown name survives so the error surfaces where it is actionable
instead of being silently swapped for another artifact.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mvge.agent.agent_name import (
    installed_agent_names,
    is_known_agent,
    resolve_active_agent_name,
    validate_agent_name,
)
from mvge.core.constants import DEFAULT_AGENT_NAME


def _agent_scope(base: Path, *names: str) -> Path:
    root = base / "agents"
    for name in names:
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


# --- installed_agent_names -------------------------------------------------


def test_installed_agent_names_lists_directories_only(tmp_path: Path) -> None:
    scope = _agent_scope(tmp_path, "beta", "alpha")
    (scope / "stray.txt").write_text("not a Mvge", encoding="utf-8")

    assert installed_agent_names(agent_config_base=scope) == ["alpha", "beta"]


def test_installed_agent_names_on_an_absent_scope(tmp_path: Path) -> None:
    assert installed_agent_names(agent_config_base=tmp_path / "nowhere") == []


# --- resolve_active_agent_name ---------------------------------------------


def test_the_requested_name_wins_when_it_is_backed(tmp_path: Path) -> None:
    scope = _agent_scope(tmp_path, "alpha", "beta")

    assert resolve_active_agent_name("beta", agent_config_base=scope) == "beta"


def test_the_default_adopts_the_single_installed_agent(tmp_path: Path) -> None:
    """One candidate means there is nothing to guess wrong.

    This is the zero-config first run: the Summoner named nothing, exactly one
    Mvge is installed, and it runs. The name is discovered, not named, so the
    engine carries no artifact slug here.
    """
    scope = _agent_scope(tmp_path, "whatever_it_is_called")

    assert resolve_active_agent_name(agent_config_base=scope) == "whatever_it_is_called"


def test_the_default_does_not_guess_between_two_installed_agents(
    tmp_path: Path,
) -> None:
    """Two agents is a real choice, and picking is the second precedence pass.

    The engine returns the default rather than adopting one, so the Summoner is
    told to name theirs instead of silently running whichever won a sort.
    """
    scope = _agent_scope(tmp_path, "alpha", "beta")

    assert resolve_active_agent_name(agent_config_base=scope) == DEFAULT_AGENT_NAME


def test_an_explicit_request_survives_having_nothing_behind_it(
    tmp_path: Path,
) -> None:
    """Naming an agent is a request, so it is not quietly overridden.

    `DEFAULT_AGENT_NAME` means "they named none". Any other name is a request,
    and a request that finds nothing must fail where the Summoner can see it --
    which is what makes the unknown-name error reachable at all.
    """
    scope = _agent_scope(tmp_path, "alpha")

    assert resolve_active_agent_name("typo", agent_config_base=scope) == "typo"


def test_no_agents_installed_leaves_the_default_alone(tmp_path: Path) -> None:
    scope = tmp_path / "agents"

    assert resolve_active_agent_name(agent_config_base=scope) == DEFAULT_AGENT_NAME


def test_a_syntactically_invalid_name_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Agent names must contain"):
        resolve_active_agent_name("../escape", agent_config_base=tmp_path)


# --- is_known_agent / validate_agent_name ----------------------------------


def test_the_default_is_known_without_a_directory(tmp_path: Path) -> None:
    assert is_known_agent(DEFAULT_AGENT_NAME, agent_config_base=tmp_path) is True


def test_a_name_with_a_directory_is_known(tmp_path: Path) -> None:
    scope = _agent_scope(tmp_path, "alpha")

    assert is_known_agent("alpha", agent_config_base=scope) is True


def test_a_syntactically_valid_unknown_name_is_not_known(tmp_path: Path) -> None:
    assert is_known_agent("typo", agent_config_base=tmp_path) is False


def test_a_project_scoped_agent_is_known(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    (project / ".agents" / "agents" / "alpha").mkdir(parents=True)

    assert is_known_agent("alpha", project_dir=project) is True


def test_validate_raises_with_an_actionable_message(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="mvge config set --agent-name typo"):
        validate_agent_name("typo", agent_config_base=tmp_path)


def test_validate_allows_creating_anything_syntactically_valid(
    tmp_path: Path,
) -> None:
    validate_agent_name("brand-new", agent_config_base=tmp_path, allow_create=True)
