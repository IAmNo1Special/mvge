"""What is known about each Realm, and how to derive it from a model slug.

The engine ships no Realm implementations. A Realm exists only once a Rune
registers its factory, which leaves three separate places needing the same three
facts about a Realm:

- the registry, which has to name the Rune to install when resolution fails;
- the CLI, which prompts with that name and reads the matching credential;
- the GUI, which renders that name on a badge and a transcript message.

Three copies of those facts is how a model that needs ``opencode-realm`` ends up
telling the Summoner to install ``openrouter-realm``. So they live here, once,
with the registry that acts on them.

The tables record what we ship. The functions derive, so a Realm that has not
been added to a table still produces a usable answer instead of nothing -- which
matters because these run on the failure path, where returning a blank is the
one outcome that leaves a Summoner with no next step. See ADR-0004.
"""

from __future__ import annotations

from typing import Final

#: The marketplace Rune that provides each Realm we ship.
REALM_RUNES: Final[dict[str, str]] = {
    "openrouter": "openrouter-realm",
    "opencode": "opencode-realm",
}

#: Where each Realm's API lives.
#:
#: Needed by the shipped baseline catalog, which is read without contacting
#: anyone: an entry there is what a Summoner sees before they have installed a
#: Rune. A Rune's own registration still wins when present, because
#: ``compose_model`` overlays it -- this is the floor, not the authority.
REALM_BASE_URLS: Final[dict[str, str]] = {
    "openrouter": "https://openrouter.ai/api/v1",
    "opencode": "https://opencode.ai/zen/v1",
}

#: Environment variable each Realm's API key is read from.
#:
#: Read before the on-disk credential so an explicit export wins, and before the
#: openrouter fallback so a Realm never receives another Realm's key -- which
#: authenticates at the wrong host and then fails every call with a message that
#: names the model rather than the credential.
REALM_API_KEY_ENV: Final[dict[str, str]] = {
    "openrouter": "OPENROUTER_API_KEY",
    "opencode": "OPENCODE_API_KEY",
}

#: Realms that have no credential at all, for any of their models.
#:
#: A categorically different fact from the one below, and kept apart from it
#: because the two need different questions asked of them. ``ollama`` is a local
#: daemon: there is no key to present and no paid tier to distinguish, so the
#: exemption is the whole Realm. No Ollama Realm ships in the marketplace --
#: ``docs/troubleshooting.md`` records it as unreachable -- so this preserves an
#: out-of-tree Realm Rune's local path rather than a shipped one. Reading the
#: exemption off a ``ollama/`` prefix on the model id was a proxy for a Realm,
#: and wrong for any Realm whose slug does not start with its own name.
REALMS_WITHOUT_CREDENTIAL: Final[frozenset[str]] = frozenset({"ollama"})

#: Realms that serve their *free tier* without a credential.
#:
#: Recorded rather than declared by the Rune, because every credential gate runs
#: before any Rune is loaded: the CLI callback exits before ``_run_agent`` is
#: called and ``Mvge.initialize`` raises before ``_load_runes``, so a manifest
#: field could not be read at either of them. On a clean machine the Realm this
#: exempts is not installed yet, so there is nothing there to ask.
#:
#: **Empty, and empty is the accurate value.** ``opencode`` was here on the
#: strength of ADR-0015's measurement that Zen's free tier answers a request
#: carrying no ``Authorization`` header. On 2026-10-11 that stopped being true:
#: every free slug on the live Zen catalogue began answering
#: ``FreeTierError: OpenCode's free tier can only be used from within OpenCode``,
#: including ``space-bunny-free``, which had answered a real completion hours
#: earlier the same day. OpenRouter's ``:free`` slugs answer ``401`` and always
#: have. So there is no Realm left whose free tier is anonymous (ADR 0020).
#:
#: The table is left in place rather than deleted because the exemption is a real
#: concept and a Realm can offer it again; a Realm added here must be one that
#: answers a request carrying no ``Authorization`` header, measured, not inferred
#: from its free tier existing. Re-add it with the measurement that justifies it.
#:
#: This is deliberately *not* a Realm-wide exemption when it is non-empty. Zen
#: also serves keyed paid models, and a Realm-wide one would attempt them
#: anonymously and fail at the host with a message about the model rather than
#: about the missing credential.
#: The pair of them -- this table and the model's own ``is_free`` -- is what
#: ``mvgeos_provider.model_registry.model_requires_credential`` combines.
REALMS_WITH_ANONYMOUS_FREE_TIER: Final[frozenset[str]] = frozenset()

#: Realms that spell their free tier with a ``-free`` suffix rather than the
#: ``:free`` suffix OpenRouter uses.
#:
#: OpenRouter writes ``:free``; OpenCode Zen writes ``-free``. Both are read,
#: because a cost guard that calls a free model paid is worse than one that
#: calls a paid model free, and because a Summoner who picked a free model
#: expects it to be listed as free.
REALM_FREE_SUFFIXES: Final[dict[str, str]] = {
    "openrouter": ":free",
    "opencode": "-free",
}

#: The Realm that serves ``mvgeos_core.constants.DEFAULT_MODEL``.
#:
#: Stated rather than derived, because the two are not the same question. For a
#: routed slug like ``nvidia/nemotron-3-ultra-550b-a55b:free`` the id prefix is
#: the *provider* and the Realm is ``openrouter``, so reading the Realm off the
#: slug would open the GUI's cascading selector on a Realm that cannot serve the
#: model it starts on.
#:
#: A copy is only safe while a test holds it to the shipped catalog -- see
#: ``test_default_realm_agrees_with_the_shipped_catalog`` in
#: ``mvgeos-provider/tests/unit/realms.py``. That test is the point: changing
#: ``DEFAULT_MODEL`` without this is the drift it exists to catch.
DEFAULT_REALM: Final[str] = "openrouter"


def realm_for_model_id(model_id: str) -> str:
    """The Realm prefix of a model slug, or the whole id when it has no prefix.

    MvgeOS names a model ``<realm>/<model-id>``. Everything downstream -- which
    key to read, which Rune to offer, which base URL to call -- keys off this
    prefix, so it is derived once here rather than by each caller re-splitting
    the string and getting the no-slash case subtly different.

    This is the slug's own prefix and nothing more. For a *routed* slug it is
    the provider rather than the Realm, so a caller that needs to know which
    Realm will serve the model wants
    :func:`mvgeos_provider.model_registry.serving_realm_for_model` instead. The
    two are kept apart because they answer different questions: this one is
    what the id is *spelled*, and the GUI's cascading selector needs exactly
    that, since a provider tier is what it lets a Summoner choose.
    """
    prefix, separator, rest = model_id.partition("/")
    return prefix if separator and rest else model_id


def rune_for_realm(realm: str) -> str:
    """The marketplace Rune that provides ``realm``, or ``""`` when unknowable.

    Falls back to the ``<realm>-realm`` convention, which is what both Runes we
    ship actually use, so a newly added Realm needs no table entry before it can
    be named in an error.

    An empty realm yields ``""`` rather than ``"-realm"``. Every caller of this
    function is on a failure path that ends in either a prompt or an install
    call, and handing either one the string ``"-realm"`` would be worse than
    admitting we do not know: it looks like a name, so nothing catches it.
    """
    if not realm:
        return ""
    known = REALM_RUNES.get(realm)
    return known if known is not None else f"{realm}-realm"


def api_key_env_for_realm(realm: str) -> str:
    """The environment variable ``realm``'s API key is read from, or ``""``.

    An unmapped Realm falls back to ``<REALM>_API_KEY``, the shape every
    provider in this ecosystem already uses, so a new Realm works before it has
    a table entry.
    """
    if not realm:
        return ""
    known = REALM_API_KEY_ENV.get(realm)
    return known if known is not None else f"{realm.upper()}_API_KEY"


def free_suffix_for_realm(realm: str) -> str:
    """The id suffix ``realm`` uses to mark a free model, or ``""``.

    Deliberately one Realm at a time. Reading ``-free`` off any id would let a
    paid model be reported as free on the strength of its name, which is the one
    mistake a cost guard must not make. The mapping is exported for reporting and
    tests; :attr:`mvgeos_core.channel.Model.free` keeps its own copy of the
    Realm set, because core sits below this package in the dependency graph.
    """
    return REALM_FREE_SUFFIXES.get(realm, "")


__all__ = [
    "DEFAULT_REALM",
    "REALM_API_KEY_ENV",
    "REALM_BASE_URLS",
    "REALM_FREE_SUFFIXES",
    "REALM_RUNES",
    "REALMS_WITHOUT_CREDENTIAL",
    "REALMS_WITH_ANONYMOUS_FREE_TIER",
    "api_key_env_for_realm",
    "free_suffix_for_realm",
    "realm_for_model_id",
    "rune_for_realm",
]
