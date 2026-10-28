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
#:
#: ``opencode`` and ``opencode-go`` are two entries provided by the one
#: ``opencode-realm`` Rune, so they deliberately map to the same Rune. The
#: convention fallback would name a second Rune, ``opencode-go-realm``,
#: which does not exist -- which is why Go needs an explicit entry here.
REALM_RUNES: Final[dict[str, str]] = {
    "openrouter": "openrouter-realm",
    "opencode": "opencode-realm",
    "opencode-go": "opencode-realm",
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
    "opencode-go": "https://opencode.ai/zen/go/v1",
    # Gateway host only: the Pollinations Rune resolves the /openai path
    # itself, as opencode-realm resolves its chat-completions path.
    "pollinations": "https://text.pollinations.ai",
}

#: Environment variable each Realm's API key is read from.
#:
#: Read before the on-disk credential so an explicit export wins, and before the
#: openrouter fallback so a Realm never receives another Realm's key -- which
#: authenticates at the wrong host and then fails every call with a message that
#: names the model rather than the credential.
#: ``opencode`` and ``opencode-go`` deliberately share ``OPENCODE_API_KEY``:
#: one credential serves both OpenCode products, and the provider name, not
#: the key, chooses the billing lane. The fallback would invent
#: ``OPENCODE-GO_API_KEY``, which is not a variable anyone sets.
REALM_API_KEY_ENV: Final[dict[str, str]] = {
    "openrouter": "OPENROUTER_API_KEY",
    "opencode": "OPENCODE_API_KEY",
    "opencode-go": "OPENCODE_API_KEY",
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
#: ``pollinations`` is here on a measurement, not on its free tier existing.
#: Measured 2026-10-27: a POST to ``https://text.pollinations.ai/openai``
#: with model ``openai`` and no ``Authorization`` header answered HTTP 200
#: with ``user_tier: "anonymous"`` and model ``gpt-oss-20b``. That is the
#: legacy Pollinations lane; the current Pollinations guide documents no
#: anonymous tier, so this lane can close the way Zen's did (ADR 0020).
#: It is therefore a bootstrap fallback only, never a foundation: a
#: Pollinations session is told explicitly, before heavy use, to get a
#: free OpenRouter key (see ``_run_agent``), and nothing silently falls
#: back to Pollinations.
#:
#: ``opencode`` was here on the strength of ADR-0015's measurement that
#: Zen's free tier answers a request carrying no ``Authorization`` header.
#: On 2026-10-11 that stopped being true: every free slug on the live
#: Zen catalogue began answering ``FreeTierError: OpenCode's free tier
#: can only be used from within OpenCode``, including ``space-bunny-free``,
#: which had answered a real completion hours earlier the same day.
#: OpenRouter's ``:free`` slugs answer ``401`` and always have.
#:
#: This is deliberately *not* a Realm-wide exemption when it is non-empty.
#: A Realm in this set still serves keyed paid models, and a Realm-wide
#: exemption would attempt them anonymously and fail at the host with a
#: message about the model rather than about the missing credential.
#: The pair of them -- this table and the model's own ``is_free`` -- is what
#: ``mvge.provider.model_registry.model_requires_credential`` combines.
REALMS_WITH_ANONYMOUS_FREE_TIER: Final[frozenset[str]] = frozenset({"pollinations"})

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

#: The default model for each Realm we ship, keyed by Realm.
#:
#: This table replaces the old single global default model. A default belongs
#: to a Realm, not to the engine: an opencode user with no model set must get
#: an opencode model, not ``openrouter/free`` with an OpenRouter key prompt
#: attached. The resolution order is explicit model first, then the stored
#: Realm preference through this table, then no model at all -- never a
#: silent fallback to one Realm. See ADR-0028, which supersedes the
#: default-model part of ADR-0020 (the anonymous-free-tier measurement in
#: ADR-0020 stands).
#:
#: Every entry must be a free slug in the shipped catalog; the tests in
#: ``tests/unit/provider/realms.py`` hold that. A Realm added here must be
#: one whose default actually answers, measured, not inferred from the
#: catalog -- that is the ADR-0018/0019 lesson.
#:
#: Realms can extend the table at runtime through
#: :func:`register_realm_default_model` (an out-of-tree Realm Rune's path).
#: The credential gate runs before any Rune is loaded, so the gate itself
#: reads only this shipped table, never a Rune registration.
REALM_DEFAULT_MODELS: Final[dict[str, str]] = {
    "openrouter": "openrouter/free",
    "opencode": "opencode/space-bunny-free",
    # Bootstrap fallback only (see the anonymous-free-tier note above):
    # a single anonymous model, about one request per 15 seconds, on a
    # legacy lane that can close. Never a global default.
    "pollinations": "pollinations/openai",
}


def default_model_for_realm(realm: str) -> str | None:
    """Return the default model for ``realm``, or None if it has none.

    An empty or unknown Realm has no default on purpose: returning a
    guessed model here would reintroduce the global-default bug through
    the back door, with the caller's Realm silently replaced by another.
    """
    if not realm:
        return None
    return REALM_DEFAULT_MODELS.get(realm)


def register_realm_default_model(realm: str, model_id: str) -> None:
    """Register ``model_id`` as the default model for ``realm``.

    Intended for Realm Runes that ship outside this repository. The
    shipped table above is a plain dict, so registration is an explicit
    assignment rather than a merge: the last registration wins, and a
    caller that needs the shipped value reads the table before any Rune
    runs.
    """
    if not realm or not model_id:
        raise ValueError("realm and model_id must both be non-empty")
    REALM_DEFAULT_MODELS[realm] = model_id


def realm_for_model_id(model_id: str) -> str:
    """The Realm prefix of a model slug, or the whole id when it has no prefix.

    Mvge names a model ``<realm>/<model-id>``. Everything downstream -- which
    key to read, which Rune to offer, which base URL to call -- keys off this
    prefix, so it is derived once here rather than by each caller re-splitting
    the string and getting the no-slash case subtly different.

    This is the slug's own prefix and nothing more. For a *routed* slug it is
    the provider rather than the Realm, so a caller that needs to know which
    Realm will serve the model wants
    :func:`mvge.provider.model_registry.serving_realm_for_model` instead. The
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
    tests; :attr:`mvge.core.channel.Model.free` keeps its own copy of the
    Realm set, because core sits below this package in the dependency graph.
    """
    return REALM_FREE_SUFFIXES.get(realm, "")


__all__ = [
    "REALM_API_KEY_ENV",
    "REALM_BASE_URLS",
    "REALM_DEFAULT_MODELS",
    "REALM_FREE_SUFFIXES",
    "REALM_RUNES",
    "REALMS_WITHOUT_CREDENTIAL",
    "REALMS_WITH_ANONYMOUS_FREE_TIER",
    "api_key_env_for_realm",
    "default_model_for_realm",
    "free_suffix_for_realm",
    "realm_for_model_id",
    "register_realm_default_model",
    "rune_for_realm",
]
