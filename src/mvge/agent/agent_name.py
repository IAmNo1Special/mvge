"""Which Mvge an invocation runs.

Every question about an agent name is answered here, because a second
answerer is how the engine came to carry a marketplace artifact's slug:
two call sites, two different rules, the same hardcoded name, and a
second precedence pass that ADR-0014 was written to end.

The engine discovers what is installed and never names one. That is what
lets the marketplace change its catalog, rename an artifact, or ship a
second Mvge without a release here.

The Summoner's own naming always wins. Discovery only speaks when they
said nothing actionable and there is nothing to guess wrong.
"""

from __future__ import annotations

import re
from pathlib import Path

from mvge.core.constants import DEFAULT_AGENT_NAME
from mvge.core.layers import agents_dir

AGENT_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

#: The one name the engine answers for without looking at the disk. It names
#: the engine's own fallback agent, not an artifact anyone ships, so it is a
#: constant rather than a discovery result.
ENGINE_DEFAULT_AGENT = DEFAULT_AGENT_NAME


def installed_agent_names(*, agent_config_base: Path | None = None) -> list[str]:
    """Agent names with an agent-scope directory, sorted.

    The agent scope is ``<global>/agents/<name>/``. Only directories count:
    a file at that depth is not an agent, and `sys.path` receives the agent
    root, so a stray file here is a latent import collision rather than a Mvge.
    """
    base = agent_config_base if agent_config_base is not None else agents_dir()
    if not base.is_dir():
        return []
    return sorted(child.name for child in base.iterdir() if child.is_dir())


def is_known_agent(
    agent_name: str,
    *,
    agent_config_base: Path | None = None,
    project_dir: Path | None = None,
) -> bool:
    """Whether an agent name is valid and usable.

    An agent is known when the name is syntactically right *and* something
    backs it: the engine's own default, an agent-scope directory, or a
    project-scope one. A syntactically valid name with nothing behind it is
    a typo, and saying so is the point.
    """
    if not isinstance(agent_name, str) or not AGENT_NAME_PATTERN.match(agent_name):
        return False

    if agent_name == DEFAULT_AGENT_NAME:
        return True

    base = (
        agent_config_base.expanduser()
        if agent_config_base is not None
        else agents_dir()
    )
    if (base / agent_name).is_dir():
        return True

    proj_dir = project_dir or Path.cwd()
    return (proj_dir / ".agents" / "agents" / agent_name).is_dir()


def validate_agent_name(
    agent_name: str,
    *,
    agent_config_base: Path | None = None,
    project_dir: Path | None = None,
    allow_create: bool = False,
) -> None:
    """Validate that an agent name exists and is syntactically valid.

    Raises ValueError with an actionable message if invalid or unknown.
    """
    if not isinstance(agent_name, str) or not AGENT_NAME_PATTERN.match(agent_name):
        raise ValueError(
            f"Invalid agent name '{agent_name}'. Agent names must contain "
            "only alphanumeric characters, dashes, and underscores."
        )

    if allow_create:
        return

    if is_known_agent(
        agent_name, agent_config_base=agent_config_base, project_dir=project_dir
    ):
        return

    base = (
        agent_config_base.expanduser()
        if agent_config_base is not None
        else agents_dir()
    )
    agent_dir = base / agent_name
    raise ValueError(
        f"Unknown agent '{agent_name}'. Agent configuration or directory "
        f"does not exist at '{agent_dir}'. Configure it with "
        f"'mvgeos config set --agent-name {agent_name} <key> <value>'."
    )


def resolve_active_agent_name(
    requested: str = DEFAULT_AGENT_NAME,
    *,
    agent_config_base: Path | None = None,
) -> str:
    """The Mvge an invocation runs, given what the Summoner asked for.

    Three rules, in order. Each is a reason not to guess.

    1. **What they asked for, when it is backed.** A name with an agent-scope
       directory is that Mvge, spelled their way.
    2. **What is installed, when they named nothing.** ``DEFAULT_AGENT_NAME``
       means the Summoner asked for no particular Mvge. With exactly one
       agent in the agent scope, adopt it: one candidate means there is
       nothing to guess wrong, and it is how a first run works with an
       installed Mvge and no flag.
    3. **Their name, unchanged.** Any name other than the default was a
       request, so it survives even when nothing backs it -- an unknown name
       then surfaces where it is actionable instead of being silently swapped
       for another. Two or more installed agents is the same case: a real
       choice, and an engine that picks for you here is the second precedence
       pass this module exists to end.
    """
    if not isinstance(requested, str) or not AGENT_NAME_PATTERN.match(requested):
        raise ValueError(
            f"Invalid agent name '{requested}'. Agent names must contain "
            "only alphanumeric characters, dashes, and underscores."
        )

    base = agent_config_base if agent_config_base is not None else agents_dir()
    if (base / requested).is_dir():
        return requested

    if requested == DEFAULT_AGENT_NAME:
        candidates = installed_agent_names(agent_config_base=agent_config_base)
        if len(candidates) == 1:
            return candidates[0]

    return requested
