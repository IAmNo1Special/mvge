"""Architecture guard: every package's ``__all__`` is its real public API.

The marketplace clones this repository and imports engine internals from
``main``. When ``DEFAULT_TOME_DIR`` was removed in favour of the
``sessions_dir()`` resolver, ``pi-codec`` and ``session-search`` broke on
the marketplace's ``main`` with ten import errors, and nothing in this
repository noticed.

``__all__`` is the declaration that makes such a break visible ahead of
time. A symbol that is importable but undeclared can be renamed or
removed with no signal here, while a consumer is already relying on it --
``selfmod-bridge`` was importing ``AuditError``, which was reachable through
the package namespace but absent from ``__all__``. This test keeps the
declaration honest in both directions so the cross-repo contract check has
something authoritative to compare against.
"""

from __future__ import annotations

import importlib
import types

import pytest

_PACKAGES = (
    "mvge.core",
    "mvge.agent",
    "mvge.provider",
    "mvge.runes",
    "mvge.tome",
    "mvge.cli",
)


def _public_surface(package: str) -> tuple[set[str], set[str]]:
    """Return ``(declared, actual)`` public names for a package.

    Two kinds of attribute are excluded from ``actual``. Submodules, because
    importing one binds it as an attribute of its package and that is an
    artefact of import order rather than part of the declared surface. And
    module-valued attributes whose name is not an identifier, which is how
    pytest's assertion rewriter injects its own globals (``@py_builtins``,
    ``@pytest_ar``) into every module it rewrites.
    """
    module = importlib.import_module(package)
    declared = set(module.__all__)
    attrs = {name for name in dir(module) if not name.startswith("_")}

    excluded: set[str] = set()
    for name in attrs:
        value = getattr(module, name, None)
        if not isinstance(value, types.ModuleType):
            continue
        is_own_submodule = getattr(value, "__name__", "").startswith(f"{package}.")
        if is_own_submodule or not name.isidentifier():
            excluded.add(name)

    return declared, attrs - excluded


@pytest.mark.parametrize("package", _PACKAGES)
def test_all_declares_only_names_that_exist(package: str) -> None:
    """Every name in ``__all__`` resolves.

    A stale entry promises a symbol that is gone, so anything trusting the
    declaration -- including the cross-repo contract check -- breaks on it.
    """
    declared, actual = _public_surface(package)

    assert not (declared - actual), (
        f"{package}.__all__ declares names that do not exist: "
        f"{sorted(declared - actual)}"
    )


@pytest.mark.parametrize("package", _PACKAGES)
def test_all_covers_every_public_name(package: str) -> None:
    """No public name is importable but undeclared.

    Such a name can be renamed or removed with no signal in this repository,
    while a consumer is already relying on it.
    """
    declared, actual = _public_surface(package)

    assert not (actual - declared), (
        f"{package} exposes public names missing from __all__: "
        f"{sorted(actual - declared)}. Add them to __all__ if they are "
        f"supported API, or make them private if they are not."
    )


@pytest.mark.parametrize("package", _PACKAGES)
def test_all_has_no_duplicates(package: str) -> None:
    """``__all__`` is a set, not a list that repeats itself."""
    module = importlib.import_module(package)
    exported = module.__all__

    assert len(exported) == len(set(exported)), (
        f"{package}.__all__ contains duplicate entries"
    )
