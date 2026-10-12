"""The Realm seam declaration matches the implementation it describes.

A declaration nobody checks is a docstring with extra steps. Every assertion
here reads ``mvgeos_provider.sse`` directly, so the seam cannot drift: adding a
hook the declaration omits, or removing one it carries, fails the suite.

The check that matters most is the precise one rather than the broad one. "The
class defines something the declaration omits" is too noisy -- the SSE module is
a 755-line implementation and its internals are not seam. What is load-bearing
is: *every method the engine calls on ``self`` that a subclass could override*.
That set is exactly what a Realm author must know, and it is derived from the
code, not restated.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from mvgeos_provider import __all__ as provider_all
from mvgeos_provider.realm_seam import (
    REALM_SEAM_HOOKS,
    abstract_hooks,
    published_names,
    signature_of,
)
from mvgeos_provider.sse import SSEChunk, SSEStreamingRealm

DECLARED = {hook.name: hook for hook in REALM_SEAM_HOOKS}


def _overridable_members(cls: type) -> set[str]:
    """Members a subclass can override, including inherited.

    Methods and properties, and plain class attributes too -- a Realm names
    its Realm with ``realm_name = "opencode"``, which is a class attribute and
    is part of the seam exactly as much as the hooks are.
    """
    members: set[str] = set()
    for klass in reversed(cls.__mro__):
        for name, value in vars(klass).items():
            if name.startswith("__"):
                continue
            if (
                inspect.isfunction(value)
                or isinstance(value, property)
                or not callable(value)
            ):
                members.add(name)
    return members


def _engine_calls(cls: type) -> set[str]:
    """Every ``self.<name>`` the class's own code reaches for."""
    called: set[str] = set()
    for value in vars(cls).values():
        if not inspect.isfunction(value):
            continue
        try:
            source = inspect.getsource(value)
        except (OSError, TypeError):  # pragma: no cover - defensive
            continue
        for node in ast.walk(ast.parse(textwrap.dedent(source))):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"
            ):
                called.add(node.attr)
    return called


def test_the_declaration_has_no_duplicate_names() -> None:
    names = [hook.name for hook in REALM_SEAM_HOOKS]
    assert len(names) == len(set(names)), (
        "REALM_SEAM_HOOKS names a hook twice, so one row shadows the other"
    )


def test_every_declared_hook_exists_on_the_seam() -> None:
    """The declaration must not name something the engine removed.

    The one permitted absence is the rest. ``_prepare_request_url_and_headers``
    is declared but not defined by the engine -- that is the whole reason it is
    in the table, so its absence is asserted rather than tolerated.
    """
    members = _overridable_members(SSEStreamingRealm)
    absent = set(DECLARED) - members
    assert absent == {"_prepare_request_url_and_headers"}, (
        "realm_seam declares a hook SSEStreamingRealm does not define, other "
        f"than the one Rune-only convention: {sorted(absent)}"
    )
    assert "_prepare_request_url_and_headers" not in members


def test_every_hook_the_engine_calls_is_classified() -> None:
    """No engine-called method is left unclassified.

    This is the direction that used to bite. ``_prepare_request_url_and_headers``
    was called by both shipped Realms for a while without appearing anywhere in
    the engine's public surface, and nothing said it was a convention between
    Runes rather than a hook the engine reached for.

    The table must total the set exhaustively, and each member must say which of
    the three cases it is. A new method the engine starts calling on itself fails
    here until somebody decides whether it is seam or machinery.
    """
    engine_reached = _overridable_members(SSEStreamingRealm) & _engine_calls(
        SSEStreamingRealm
    )
    undeclared = sorted(engine_reached - set(DECLARED))
    assert undeclared == [], (
        "SSEStreamingRealm calls a method a subclass could override that "
        f"realm_seam omits: {undeclared}. Add it, and classify it: seam=True "
        "if a Realm may override it, seam=False if it is engine machinery."
    )


def test_engine_machinery_is_not_the_rune_convention() -> None:
    """The table's three cases stay distinct.

    Seam members the engine calls, engine machinery it also calls, and the one
    Rune-only convention it never calls. Collapsing any two loses the
    classification a Realm author came for.
    """
    by_case = {(hook.called_by_engine, hook.seam) for hook in REALM_SEAM_HOOKS}
    assert by_case == {(True, True), (True, False), (False, True)}
    assert [hook.name for hook in REALM_SEAM_HOOKS if not hook.called_by_engine] == [
        "_prepare_request_url_and_headers"
    ]


@pytest.mark.parametrize("name", ["_prepare_request", "_parse_sse_chunk"])
def test_the_required_pair_is_abstract(name: str) -> None:
    assert name in abstract_hooks(SSEStreamingRealm)


def test_the_shared_rune_convention_is_not_an_engine_hook() -> None:
    """The classification a Realm author needs, asserted.

    ``SSEStreamingRealm`` never calls this. Both shipped Realms define it and
    call it from their own ``complete()``. Declaring it here -- and marking it
    engine-unreachable -- is the only thing that makes that fact findable.
    """
    hook = DECLARED["_prepare_request_url_and_headers"]

    assert hook.called_by_engine is False
    assert "_prepare_request_url_and_headers" not in _engine_calls(SSEStreamingRealm)


def test_the_seam_is_importable_from_the_package_root() -> None:
    """Submodule imports escape ``__all__``, and therefore the contract check.

    The marketplace's Rune contract validates package-root imports against
    ``__all__``. Importing the seam from ``mvgeos_provider.sse`` skips that
    entirely, which is how a Realm could be written against a hook the engine
    never promised. The published spelling is the checked one.
    """
    assert "SSEStreamingRealm" in provider_all
    assert "SSEChunk" in provider_all
    assert published_names() == (
        "mvgeos_provider.sse.SSEStreamingRealm",
        "mvgeos_provider.sse.SSEChunk",
    )


def test_the_required_pair_takes_the_documented_arguments() -> None:
    prepare = signature_of(SSEStreamingRealm, "_prepare_request")
    parse = signature_of(SSEStreamingRealm, "_parse_sse_chunk")

    assert prepare is not None
    assert list(prepare.parameters) == ["self", "model", "invocations", "config"]

    assert parse is not None
    assert list(parse.parameters) == ["self", "chunk"]


def test_the_chunk_delta_carries_the_documented_fields() -> None:
    assert list(SSEChunk.__dataclass_fields__) == [
        "content",
        "contemplation",
        "tool_calls",
        "finish_reason",
        "usage",
    ]
