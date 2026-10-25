"""Unit tests for agent-name propagation from the CLI group to subcommands.

Issue #168: ``--agent-name`` placed before a subcommand was parsed by the
root callback and then discarded -- each subcommand fell back to its own
default, silently running the wrong agent.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from typer.testing import CliRunner

from mvge.cli.agent_name import resolve_agent_name
from mvge.core.constants import DEFAULT_AGENT_NAME


def _ctx(*levels: dict[str, Any]) -> Any:
    """Build a fake Click context chain, outermost level first.

    Returns the innermost context, as a subcommand would receive it.
    """
    ctx: Any = None
    for params in levels:
        ctx = SimpleNamespace(params=params, parent=ctx)
    return ctx


def test_explicit_subcommand_flag_wins() -> None:
    """The subcommand's own --agent-name beats everything else."""
    ctx = _ctx({"agent_name": "group-agent"})
    assert resolve_agent_name(ctx, "sub-agent") == "sub-agent"


def test_group_flag_reaches_subcommand() -> None:
    """--agent-name before the subcommand is honored via the parent context."""
    ctx = _ctx({"agent_name": "group-agent"}, {"agent_name": None})
    assert resolve_agent_name(ctx, None) == "group-agent"


def test_group_flag_reaches_nested_subcommand() -> None:
    """Nested groups (``mvgeos --agent-name X config show``) walk the chain."""
    ctx = _ctx(
        {"agent_name": "group-agent"},
        {"agent_name": None},  # config_app callback has no agent_name
        {"agent_name": None},  # the show command itself
    )
    assert resolve_agent_name(ctx, None) == "group-agent"


def test_nearest_ancestor_wins() -> None:
    """The closest scope naming an agent wins."""
    ctx = _ctx({"agent_name": "group-agent"}, {"agent_name": "mid-agent"})
    assert resolve_agent_name(ctx, None) == "mid-agent"


def test_default_when_nothing_names_an_agent() -> None:
    """No flag anywhere falls back to the default agent."""
    ctx = _ctx({"agent_name": None}, {"agent_name": None})
    assert resolve_agent_name(ctx, None) == DEFAULT_AGENT_NAME
    assert resolve_agent_name(None, None) == DEFAULT_AGENT_NAME


def test_group_flag_reaches_real_subcommand() -> None:
    """End to end: ``mvgeos --agent-name X config show`` resolves X."""
    from mvge.cli.main import app

    runner = CliRunner()
    with patch("mvge.cli.commands.config._get_manager") as mock_mgr:
        mock_mgr.return_value.load.return_value = {}
        result = runner.invoke(app, ["--agent-name", "coding_mvge", "config", "show"])

    assert result.exit_code == 0, result.output
    assert mock_mgr.call_args[0][0] == "coding_mvge"


def test_subcommand_flag_beats_group_flag_end_to_end() -> None:
    """End to end: an explicit subcommand flag still wins over the group flag."""
    from mvge.cli.main import app

    runner = CliRunner()
    with patch("mvge.cli.commands.config._get_manager") as mock_mgr:
        mock_mgr.return_value.load.return_value = {}
        result = runner.invoke(
            app,
            [
                "--agent-name",
                "group-agent",
                "config",
                "show",
                "--agent-name",
                "sub-agent",
            ],
        )

    assert result.exit_code == 0, result.output
    assert mock_mgr.call_args[0][0] == "sub-agent"


def test_no_flag_uses_default_end_to_end() -> None:
    """End to end: no flag anywhere keeps the default agent."""
    from mvge.cli.main import app

    runner = CliRunner()
    with patch("mvge.cli.commands.config._get_manager") as mock_mgr:
        mock_mgr.return_value.load.return_value = {}
        result = runner.invoke(app, ["config", "show"])

    assert result.exit_code == 0, result.output
    assert mock_mgr.call_args[0][0] == DEFAULT_AGENT_NAME
