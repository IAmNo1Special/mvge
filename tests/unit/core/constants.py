"""Unit tests for mvge.core.constants.

``constants.py`` holds protocol-facing names and nothing else. Path
resolution moved to :mod:`mvge.core.layers`; this module asserts that
split stays true, because a resolver reappearing here is how two derivations
of the same path start disagreeing again.
"""

from __future__ import annotations

import inspect

from mvge.core import constants

#: ``from __future__ import annotations`` binds a feature object called
#: ``annotations`` in this module's namespace, so it is excluded alongside
#: the dunders when counting public names.
_FUTURE_FLAG_NAMES = frozenset({"annotations"})

#: The four contract names. These are protocol-facing: other code and
#: external tools spell them literally, so they are what this module is for.
CONTRACT_CONSTANTS = (
    "DEFAULT_AGENT_NAME",
    "DEFAULT_MODEL",
    "GLOBAL_DIR_ENV",
    "PROJECT_RUNE_PATH",
    "PROJECT_SKILL_PATH",
)


def test_constants_module_defines_no_callables() -> None:
    """No function, class or alias lives here.

    A re-export facade would give a symbol two import paths, and two import
    paths to one path-derivation helper is how installation and discovery
    come to disagree.
    """
    offenders = sorted(
        name
        for name, value in vars(constants).items()
        if not name.startswith("__")
        and (inspect.isfunction(value) or inspect.isclass(value))
    )

    assert offenders == []


def test_constants_module_exposes_exactly_the_contract() -> None:
    """Five names, no more.

    Anything added here is a path derivation or a duplicated contract
    symbol; both belong in ``layers``. The fifth is ``PROJECT_SKILL_PATH``,
    added by ADR 0017 when the project skills layer stopped being spelled
    out by hand at each site that needed it.
    """
    public = sorted(
        name
        for name, value in vars(constants).items()
        if not name.startswith("_")
        and name not in _FUTURE_FLAG_NAMES
        and not inspect.ismodule(value)
    )

    assert public == sorted(CONTRACT_CONSTANTS)
