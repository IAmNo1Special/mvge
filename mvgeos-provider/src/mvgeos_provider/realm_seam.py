"""The Realm seam: what a Realm supplies, in the order the engine calls it.

A Realm is the one engine module a third party must subclass, so its interface
is a published fact rather than an internal detail. Until now it was the latter:
the hooks lived inside a 755-line implementation module and were discoverable
only by reading it end to end -- or by reading the other Realm someone already
shipped. One of them (``_prepare_request_url_and_headers``) was never called by
the engine at all, and was a convention between the two Runes that use it. A
Realm author had no way to know which was which.

This module is the declaration. It holds no behaviour; every name it carries is
resolved by a test that reads ``mvgeos_provider.sse`` directly, so the
declaration cannot drift from the implementation it describes.

Where the seam lives
--------------------
``SSEStreamingRealm`` stays in ``sse.py``. That is deliberate: it *is* an
implementation -- the retry loop, the rate-limit interpretation, the response
assembly -- and this seam describes it rather than replacing it. The value here
is that a Realm author can read one screen instead of one module, and that
removing a hook is a visible edit rather than a silent break on the next push.

The seam is package-root importable (``from mvgeos_provider import
SSEStreamingRealm, SSEChunk``), which is what puts it under ``__all__`` -- and
therefore under the marketplace contract check that a submodule import escapes.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass

from mvgeos_provider import SSEChunk, SSEStreamingRealm


@dataclass(frozen=True)
class _Hook:
    """One member of the Realm seam, and how a Realm may treat it.

    ``seam`` is the distinction a Realm author most needs and the one the
    engine used to make impossible to find. A member with ``seam=True`` is
    theirs to override -- the engine calls it precisely so a Realm can change
    that behaviour. A member with ``seam=False`` is engine machinery the engine
    *also* calls on itself, listed here so nobody overrides it by accident:
    rewriting either one silently changes every Realm at once.

    ``called_by_engine`` separates both from a third case, a convention between
    Runes that the engine never reaches for at all.
    """

    name: str
    required: bool
    called_by_engine: bool
    seam: bool
    why: str


#: Every member of the seam, in the order ``stream()`` reaches them.
#:
#: ``required`` marks what must be supplied for a Realm to work at all.
#: ``SSEStreamingRealm`` declares those two as ``@abstractmethod``, and the
#: contract test reads that set rather than trusting this table.
REALM_SEAM_HOOKS: tuple[_Hook, ...] = (
    _Hook(
        name="realm_name",
        required=False,
        called_by_engine=True,
        seam=True,
        why="Names the Realm in events and diagnostics. Defaults to the "
        "model's realm, then to 'sse'.",
    ),
    _Hook(
        name="is_router",
        required=False,
        called_by_engine=True,
        seam=True,
        why="Whether the CLI and GUI ask the Summoner to pick a provider "
        "before running. Answering True for a flat gateway puts a one-option "
        "prompt in front of every run.",
    ),
    _Hook(
        name="_prepare_request",
        required=True,
        called_by_engine=True,
        seam=True,
        why="Builds the URL, headers, and JSON body for one turn. The payload "
        "must not set stream_options: the engine sets include_usage itself.",
    ),
    _Hook(
        name="_parse_sse_chunk",
        required=True,
        called_by_engine=True,
        seam=True,
        why="Translates one decoded wire chunk into an SSEChunk delta. Return "
        "None for a chunk with no delta; returning None for an error chunk "
        "drops it silently.",
    ),
    _Hook(
        name="_parse_usage",
        required=False,
        called_by_engine=True,
        seam=True,
        why="Maps a provider usage payload onto the engine's mana breakdown. "
        "Only override it for a payload the default translation cannot read.",
    ),
    _Hook(
        name="_parse_error",
        required=False,
        called_by_engine=True,
        seam=True,
        why="Classifies a non-200 response synchronously. Its async sibling is "
        "the one the retry loop actually calls.",
    ),
    _Hook(
        name="_parse_error_async",
        required=False,
        called_by_engine=True,
        seam=True,
        why="The hook the retry loop reaches for. Override this one to change "
        "how a Realm interprets a failure; overriding only the sync twin leaves "
        "the retry loop doing the classifying.",
    ),
    _Hook(
        name="_realm_name",
        required=False,
        called_by_engine=True,
        seam=True,
        why="Resolves the Realm name per model, reading realm_name. Override it "
        "for a Realm that serves more than one name.",
    ),
    _Hook(
        name="_prepare_request_url_and_headers",
        required=False,
        called_by_engine=False,
        seam=True,
        why="A shared convention between Runes, not an engine hook. Both "
        "shipped Realms define it and call it from their own complete() "
        "implementations, because SSEStreamingRealm supplies no non-streaming "
        "path. It is here so that is visible: the engine does not call it, so "
        "removing it breaks nothing in the engine, and a Realm author reading "
        "sse.py would never learn it exists.",
    ),
    _Hook(
        name="_consume_stream",
        required=False,
        called_by_engine=True,
        seam=False,
        why="Engine machinery: reads the SSE line loop and yields RealmResponse "
        "deltas. Overriding it changes how every Realm channels, so it is not "
        "part of the seam -- it is listed so nobody does it by accident.",
    ),
    _Hook(
        name="_build_final_response",
        required=False,
        called_by_engine=True,
        seam=False,
        why="Engine machinery: assembles the terminal RealmResponse from "
        "accumulated deltas and maps the stop reason. Same warning as "
        "_consume_stream.",
    ),
)


def seam_module() -> str:
    """Where the seam's implementation lives, for error messages and docs."""
    return "mvgeos_provider.sse"


def published_names() -> tuple[str, ...]:
    """The names the package root re-exports as the seam's entry points.

    The package root imports this module's definitions from ``sse``, so
    importing it back would be a cycle. Reading ``__module__`` off the imported
    objects is how the names are resolved without a second import path.
    """
    return (
        SSEStreamingRealm.__module__ + ".SSEStreamingRealm",
        SSEChunk.__module__ + ".SSEChunk",
    )


def declared_hooks(cls: type) -> set[str]:
    """Every name in :data:`REALM_SEAM_HOOKS` that ``cls`` actually defines.

    A hook a Realm inherits without defining is still part of the seam -- the
    engine calls it -- so inherited members count. What does *not* count is a
    name the declaration carries that the class no longer has anywhere in its
    MRO, which is what the contract test asserts against.
    """
    return {
        hook.name for hook in REALM_SEAM_HOOKS if hasattr(cls, hook.name)
    }


def abstract_hooks(cls: type) -> frozenset[str]:
    """The hooks a subclass must supply, read from the class itself.

    Read from ``__abstractmethods__`` rather than restated, so a realm author
    and the engine cannot disagree about what is mandatory.
    """
    abstract: frozenset[str] = getattr(cls, "__abstractmethods__", frozenset())
    return frozenset(abstract)


def signature_of(cls: type, name: str) -> inspect.Signature | None:
    """The signature of one seam hook, or ``None`` when it is not defined."""
    attribute = getattr(cls, name, None)
    if attribute is None:
        return None
    try:
        return inspect.signature(attribute)
    except (TypeError, ValueError):
        # A property, or a C-level callable with no introspectable signature.
        return None
