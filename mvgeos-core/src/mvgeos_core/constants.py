"""Protocol-facing constants for the ``.agents`` layer scheme.

Five names, all of them contract: the ones that describe the ``.agents``
layer scheme itself. Nothing here *derives* a path -- resolution lives in
:mod:`mvgeos_core.layers`, which is the single place a search path comes
from.

``GLOBAL_DIR_ENV``, ``PROJECT_RUNE_PATH`` and ``PROJECT_SKILL_PATH`` are the
protocol-facing names and must not be renamed; ``Scope`` lives with the ranking
rule it feeds, in ``layers``. A feature-scoped environment variable -- the CLI's
``MVGEOS_EXTENSION_DIR``, say -- belongs to the feature that reads it, not
here: this module is the layer scheme, not every literal in the engine.

The same rule keeps the Realm tables out. Which Rune provides a Realm, where
that Realm lives, and what its key is called are facts about Realms, they
need derivation rather than storage, and three packages have to agree on
them -- so they live together in the provider layer, next to the registry
that acts on them. See ADR-0004.

See ADR-0014.
"""

from __future__ import annotations

DEFAULT_AGENT_NAME = "default-mvge"

#: The model every MvgeOS package falls back to.
#:
#: Resolving this needs two things that are not in the engine: a Rune that
#: registers the ``opencode`` Realm, and an ``OPENCODE_API_KEY``. On a clean
#: machine the CLI offers to install ``opencode-realm`` when the first is
#: missing, which is the same bargain the previous default made with
#: ``openrouter-realm``.
#:
#: Pick a free tier deliberately. This slug is what a first-time Summoner runs,
#: so a saturated endpoint here is a broken quickstart rather than a slow one --
#: which is exactly how the previous default failed.
DEFAULT_MODEL = "opencode/space-bunny-free"

#: Environment variable that relocates the global ``.agents`` layer. The
#: default is ``~/.agents``; setting this moves every global-layer read and
#: write, which is what makes an isolated run possible. Honoured by every
#: resolver in :mod:`mvgeos_core.layers`, on every call.
GLOBAL_DIR_ENV = "MVGEOS_GLOBAL_DIR"

#: Project-layer rune directory, relative to an anchored project directory.
#: Always the standard protocol name -- never a proprietary subdirectory.
PROJECT_RUNE_PATH = ".agents/extensions"

#: Project-layer skills directory, relative to an anchored project directory.
#: The sibling of :data:`PROJECT_RUNE_PATH`: both name the project layer of
#: the same ``.agents`` scheme, for the two things a project can carry.
#: Added by ADR 0017, which closed a live gap where this string was
#: spelled out relative to the working directory instead.
PROJECT_SKILL_PATH = ".agents/skills"
