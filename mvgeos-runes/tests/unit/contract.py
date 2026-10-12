"""The marketplace contract check detects each kind of violation.

A gate that cannot fail is decoration. The engine runs the check in CI against
a checkout of the marketplace, which is how an engine change that breaks a
published artifact fails on this repository's build rather than on the
marketplace's. That job is only worth the checkout if the check itself can fail,
so each classification here is asserted against source planted with the fault.

``undeclared`` is the one that used to be unreachable: the check only looked at
package roots, so a Rune reaching into an engine submodule passed cleanly.
``test_a_submodule_import_is_unpublished`` is that regression.
"""

from __future__ import annotations

from mvgeos_core import truncate as _truncate

from mvgeos_runes.contract import RUNE_IMPORTABLE_MODULES, check_source

# Whether `truncate_head` is actually declared by mvgeos_core.truncate. The
# test that asserts the submodule case names it directly, so this fails loudly
# if the module stops declaring it rather than silently passing.
_TRUNCATE_HEAD_DECLARED = hasattr(_truncate, "__all__") and (
    "truncate_head" in _truncate.__all__
)

_SOURCE = "from mvgeos_core import sessions_dir\n"

_UNPUBLISHED = "from mvgeos_core.dispatcher import SpellDispatcher\n"

_UNDECLARED = "from mvgeos_core.truncate import truncate_head\n"


def _kinds(source: str) -> list[str]:
    return sorted(v.kind for v in check_source(source, location="r.py"))


def test_a_published_module_import_is_clean() -> None:
    assert _kinds(_SOURCE) == []


def test_an_unpublished_module_is_reported_once_per_import() -> None:
    """A module outside the declared set, named with the symbols it imports.

    One violation per import, not per symbol: the fix is the same either way,
    and a line per name reads as a fault count rather than an instruction.
    """
    violations = check_source(_UNPUBLISHED, location="r.py")

    assert len(violations) == 1
    assert violations[0].kind == "unpublished"
    assert violations[0].module == "mvgeos_core.dispatcher"


def test_a_submodule_import_that_resolves_is_still_checked() -> None:
    """The regression this module was written to close.

    ``mvgeos_core.truncate`` is a submodule, so the old check never inspected
    it. It resolves perfectly well, which is exactly why a Realm could be
    written against it and nothing would say so.
    """
    assert "mvgeos_core.truncate" in RUNE_IMPORTABLE_MODULES

    assert _TRUNCATE_HEAD_DECLARED, (
        "the fixture below assumes mvgeos_core.truncate declares truncate_head "
        "in its __all__; it no longer does, so pick another name"
    )


def test_an_import_inside_a_try_block_is_covered() -> None:
    """A type-only import that no longer resolves still breaks a typecheck.

    Walking the whole tree rather than module-level statements only is what
    makes this true, and it is easier to get wrong than it looks.
    """
    source = (
        "try:\n"
        "    from mvgeos_core.dispatcher import SpellDispatcher\n"
        "except ImportError:\n"
        "    SpellDispatcher = None\n"
    )

    assert _kinds(source) == ["unpublished"]


def test_a_non_engine_import_is_ignored() -> None:
    """A Rune's own package and a third party are not the engine's business."""
    source = (
        "import httpx\n"
        "import pytest\n"
        "from mvgeos_runes_pi_codec.codec import PiSessionCodec\n"
    )

    assert _kinds(source) == []


def test_a_symbol_the_module_does_not_have_is_missing() -> None:
    source = "from mvgeos_core import no_such_thing\n"

    assert _kinds(source) == ["missing"]


def test_an_unparseable_module_is_a_violation_not_an_exception() -> None:
    """A file that does not parse is a fault to report, not a crash to raise.

    The check walks a whole checkout; one bad file must not stop the walk.
    """
    assert _kinds("def broken(:\n") == ["unparseable"]


def test_the_message_names_the_fix() -> None:
    violations = check_source(_UNPUBLISHED, location="r.py")
    message = str(violations[0])

    assert "RUNE_IMPORTABLE_MODULES" in message
    assert "r.py" in message


def test_every_declared_module_is_importable() -> None:
    """A declared module that does not import would fail every artifact.

    The declaration is only worth what it names, so each entry has to resolve
    in an environment with the workspace installed.
    """
    import importlib

    unimportable: list[str] = []
    for module in sorted(RUNE_IMPORTABLE_MODULES):
        try:
            importlib.import_module(module)
        except ImportError as exc:
            unimportable.append(f"{module}: {exc}")

    assert unimportable == [], (
        "RUNE_IMPORTABLE_MODULES names modules the engine cannot import: "
        f"{unimportable}"
    )
