"""Resolve the agent a CLI subcommand runs as.

``--agent-name`` may be given before the subcommand (parsed by the group
callback) or on the subcommand itself. The group's value used to be parsed
and then discarded, so ``mvgeos --agent-name X info`` silently ran the
default agent. Every subcommand that accepts ``--agent-name`` resolves
through :func:`resolve_agent_name`, which walks up the Click context chain.
"""

from __future__ import annotations

from typing import Any

import typer
from mvgeos_core.constants import DEFAULT_AGENT_NAME


def resolve_agent_name(ctx: typer.Context | None, explicit: str | None) -> str:
    """Resolve the effective agent name for a subcommand.

    Precedence: the subcommand's own ``--agent-name``, then the nearest
    ancestor context that named one (``--agent-name`` given before the
    subcommand), then the default agent.
    """
    if explicit:
        return explicit
    seen: Any = ctx
    while seen is not None:
        params = getattr(seen, "params", None) or {}
        value = params.get("agent_name")
        if value:
            return str(value)
        seen = getattr(seen, "parent", None)
    return DEFAULT_AGENT_NAME
