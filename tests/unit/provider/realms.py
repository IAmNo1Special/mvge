"""What the engine knows about each Realm, and how it derives the rest.

The tables here are the facts; these tests are what keeps them honest and keep
the derivations from quietly disagreeing with the tables. Every one of them is
about the failure path, because that is where these functions run: a Summoner
who cannot resolve a model needs to be told something they can act on, and the
difference between a useful name and a wrong one is the whole value of the
module.
"""

from __future__ import annotations

import pytest

from mvge.core.channel import Model
from mvge.provider.base import NoRealmRegisteredError
from mvge.provider.model_registry import (
    list_models,
    model_requires_credential,
    resolve_default_model,
    serving_realm_for_model,
)
from mvge.provider.realms import (
    REALM_API_KEY_ENV,
    REALM_BASE_URLS,
    REALM_DEFAULT_MODELS,
    REALM_FREE_SUFFIXES,
    REALM_RUNES,
    REALMS_WITH_ANONYMOUS_FREE_TIER,
    api_key_env_for_realm,
    default_model_for_realm,
    free_suffix_for_realm,
    realm_for_model_id,
    rune_for_realm,
)
from mvge.provider.registry import RealmRegistry, no_realm_registered

# ---------------------------------------------------------------------------
# Deriving a Realm from a slug
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("slug", "expected"),
    [
        ("opencode/space-bunny-free", "opencode"),
        ("openrouter/free", "openrouter"),
        ("nvidia/nemotron-3-ultra-550b-a55b:free", "nvidia"),
        # No slash: the whole id is the prefix, because there is nothing else it
        # could be. Returning "" here would silently resolve to the wrong Realm.
        ("gpt-5.5", "gpt-5.5"),
        # A trailing slash is not a prefix.
        ("opencode/", "opencode/"),
        ("", ""),
    ],
)
def test_realm_for_model_id(slug: str, expected: str) -> None:
    assert realm_for_model_id(slug) == expected


# ---------------------------------------------------------------------------
# Deriving the Realm that will actually serve a slug
# ---------------------------------------------------------------------------


def test_a_routed_slug_resolves_to_the_realm_that_serves_it() -> None:
    """The credential must be read from the Realm the request goes to.

    A routed slug's prefix is the *provider*, not the Realm. Reading the key
    off it looks for ``NVIDIA_API_KEY`` and ``~/.agents/auth/nvidia.json`` for a
    model the shipped catalog serves as ``openrouter``, so the documented
    credential silently does nothing: ``mvge export`` writes
    ``openrouter.json`` and the next run asks for a variable that Realm never
    reads. Only an exported ``OPENROUTER_API_KEY`` kept it working, which is the
    one path the credential file exists to avoid needing.
    """
    assert (
        serving_realm_for_model("nvidia/nemotron-3-ultra-550b-a55b:free")
        == "openrouter"
    )
    assert (
        api_key_env_for_realm(
            serving_realm_for_model("nvidia/nemotron-3-ultra-550b-a55b:free")
        )
        == "OPENROUTER_API_KEY"
    )


def test_an_uncatalogued_slug_falls_back_to_its_prefix() -> None:
    """A Rune may serve a model the shipped catalog has never heard of.

    That is the feature that makes Runes worth having, so the derivation has to
    keep producing an answer for an id no catalog carries. The prefix is the
    best available answer there, and it is the answer a directly-addressed Realm
    needs -- Zen publishes bare ids with no provider tier to choose.

    The premise is asserted rather than assumed. Other suites lean on
    ``opencode/glm-5`` being uncatalogued -- that is what makes it a *paid*
    model on a Realm whose free tier is anonymous, which is the only shipped way
    to ask whether a credential is required of a Realm other than openrouter. If
    the catalog ever carries it as free, those suites would silently stop
    discriminating, so this fails first.
    """
    assert serving_realm_for_model("opencode/a-model-nobody-catalogued") == "opencode"
    assert serving_realm_for_model("groq/some-model") == "groq"
    assert serving_realm_for_model("gpt-5.5") == "gpt-5.5"
    assert serving_realm_for_model("") == ""

    catalogued = {m.id for m in list_models()}
    assert "opencode/glm-5" not in catalogued
    # Every model the shipped catalog carries on the `opencode` Realm is free.
    # The `opencode` Realm has no anonymous free tier any more (ADR-0020), so
    # the credential decision no longer hangs on this, but the property is
    # still asserted: shipping a paid opencode model would let someone
    # accidentally bill through a Realm whose free tier is the safer choice.
    shipped_zen = [m for m in list_models() if m.realm == "opencode"]
    assert shipped_zen, "no opencode-Realm model ships, so the Realm has no entry"
    assert all(m.free for m in shipped_zen), "a paid opencode-Realm model ships"
    assert "openrouter/free" not in {m.id for m in shipped_zen}


def test_the_serving_realm_agrees_with_the_catalog_for_every_shipped_model() -> None:
    """The derivation must not disagree with what dispatch will do.

    ``RealmRegistry`` dispatches on the composed ``Model.realm``, so if this
    derivation ever answered something else the credential would be read from a
    Realm the request is not sent to. Asserted across the whole shipped catalog
    rather than one slug: the two are the same lookup, and a single case cannot
    tell a correct derivation from a coincidence.
    """
    for model in list_models():
        assert serving_realm_for_model(model.id) == model.realm


# ---------------------------------------------------------------------------
# Naming the Rune that provides a Realm
# ---------------------------------------------------------------------------


def test_every_realm_we_ship_names_its_rune() -> None:
    for realm in REALM_RUNES:
        assert rune_for_realm(realm) == REALM_RUNES[realm]


def test_rune_for_an_unknown_realm_follows_the_naming_convention() -> None:
    """A Realm nobody has added to the table still produces a usable name.

    This runs on the failure path, so returning nothing would leave a Summoner
    with no next step -- which is the one outcome the message exists to avoid.
    """
    assert rune_for_realm("groq") == "groq-realm"


def test_rune_for_an_unknown_realm_is_empty() -> None:
    """An unknown Realm must not produce a name that merely looks like one.

    Every caller either prompts or calls install with the result. ``rune_for_realm
    ("")`` returning ``"-realm"`` would hand both a string that reads as a Rune
    and names nothing.
    """
    assert rune_for_realm("") == ""


def test_the_rune_named_for_a_realm_is_one_we_publish() -> None:
    """A derived name must not claim a Rune exists when none does.

    The badge and the install prompt both trust this. Offering ``ollama-realm``
    would send someone to a Rune the marketplace does not have, which is worse
    than saying nothing.
    """
    assert rune_for_realm("ollama") not in REALM_RUNES.values()


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def test_each_realms_key_comes_from_its_own_environment_variable() -> None:
    """Handing one Realm's key to another authenticates at the wrong host.

    The failure that follows names the model rather than the credential, so the
    Summoner is sent looking in the wrong place entirely.
    """
    assert api_key_env_for_realm("opencode") == "OPENCODE_API_KEY"
    assert api_key_env_for_realm("opencode-go") == "OPENCODE_API_KEY"
    assert api_key_env_for_realm("openrouter") == "OPENROUTER_API_KEY"


def test_an_unmapped_realm_key_follows_the_ecosystem_shape() -> None:
    assert api_key_env_for_realm("groq") == "GROQ_API_KEY"
    assert api_key_env_for_realm("") == ""


def test_the_key_environments_are_distinct() -> None:
    """Two Realms sharing a variable name means one silently overwrites the other.

    The one intentional exception is Zen and Go: they are two provider
    entries for two billing products behind the one OpenCode credential,
    so sharing ``OPENCODE_API_KEY`` is the design, not an overwrite -- the
    value is the same either way, and a second variable would send a
    Summoner to set a key the product never reads.
    """
    values = list(REALM_API_KEY_ENV.values())
    duplicates = {v for v in values if values.count(v) > 1}
    assert duplicates == {"OPENCODE_API_KEY"}
    assert REALM_API_KEY_ENV["opencode"] == REALM_API_KEY_ENV["opencode-go"]


def test_opencode_go_names_the_opencode_realm_rune_and_its_base() -> None:
    """Go must resolve to the one Rune that provides it, and to Go's base.

    Without the explicit table entries, the convention fallback would name
    ``opencode-go-realm`` (which does not exist) and invent
    ``OPENCODE-GO_API_KEY`` (which nobody sets).
    """
    assert rune_for_realm("opencode-go") == "opencode-realm"
    assert REALM_BASE_URLS["opencode-go"] == "https://opencode.ai/zen/go/v1"
    assert REALM_RUNES["opencode-go"] == "opencode-realm"


def test_opencode_go_has_no_default_model() -> None:
    """Go is a paid subscription, so it must not gain a free default.

    Every per-Realm default in this table is a free slug a first-time
    Summoner runs; giving Go one would either name a paid model as a
    first run or invent a free Go model that does not exist.
    """
    assert default_model_for_realm("opencode-go") is None
    assert "opencode-go" not in REALM_DEFAULT_MODELS


def test_a_realm_not_in_either_table_still_requires_a_credential() -> None:
    """A new Realm must work before it is added to any table.

    ``groq`` is in no table at all, and ``""`` is what a bare model slug derives
    to. Both default to *required*, so an omission cannot make a Realm silently
    unauthenticated -- which is the failure a table that recorded rules rather
    than exceptions would allow.
    """
    assert model_requires_credential("groq/some-model") is True
    assert model_requires_credential("") is True
    assert model_requires_credential(None) is True


def test_only_pollinations_has_an_anonymous_free_tier() -> None:
    """Pollinations alone earned the anonymous-free exemption, by measurement.

    ADR-0015 exempted `opencode` because Zen's free tier answered a request
    carrying no `Authorization` header. Measured 2026-10-11 (ADR-0020): every
    free slug on the live Zen catalogue refuses with ``FreeTierError: OpenCode's
    free tier can only be used from within OpenCode``, and OpenRouter's ``:free``
    slugs answer ``401``. Pollinations re-earned the exemption on a new
    measurement, 2026-10-27: a POST to ``https://text.pollinations.ai/openai``
    with model ``openai`` and no `Authorization` header answered HTTP 200
    with ``user_tier: "anonymous"``. That is a legacy lane whose current
    guide documents no anonymous tier, so it is a bootstrap fallback only.

    Any other Realm added here must come with a measurement of the same
    shape, as of the date its ADR records -- not with its free tier existing.
    """
    assert frozenset({"pollinations"}) == REALMS_WITH_ANONYMOUS_FREE_TIER
    assert model_requires_credential("pollinations/openai") is False
    assert model_requires_credential("opencode/space-bunny-free") is True
    assert model_requires_credential("openrouter/free") is True


def test_a_paid_model_on_zen_still_requires_a_credential() -> None:
    """``opencode`` is not exempt; its free tier is.

    A realm-wide exemption is the tempting simplification and it is wrong here for
    one reason: Zen has keyed paid models as well as keyless free ones, so the
    wide form silently authorises an unauthenticated paid call.
    """
    assert model_requires_credential("opencode/glm-5") is True


def test_a_realm_with_no_credential_at_all_is_exempt_for_every_model() -> None:
    """``ollama`` is a different fact from Zen's, and it stays realm-wide.

    A local daemon has no credential to present for *any* model, and none of its
    models are in the shipped catalog, so keying its exemption off ``is_free``
    would make every local run demand a variable the daemon has no concept of.
    There is no Ollama Realm in the marketplace -- ``docs/troubleshooting.md``
    records it as unreachable -- so this preserves an out-of-tree Realm Rune's
    local path rather than a shipped one.
    """
    assert model_requires_credential("ollama/llama3") is False
    assert model_requires_credential("ollama/some-model-nobody-has-catalogued") is False


def test_every_per_realm_default_requires_a_documented_credential() -> None:
    """Every per-Realm default needs a key, and the env var for it must
    be documented.

    This is the Realm-first form of the old global-default invariant.
    It used to assert the single default needs a key; that was true
    while Zen served anonymously and is measured false now (ADR-0020):
    no free tier answers an unauthenticated request any more. The
    invariant is inverted, not deleted, because the failure being
    guarded is the same one in both directions -- a default that cannot
    be reached is as broken as one that is free, and which way it broke
    should be a decision on the record.

    The second assertion is the part that earns the test: each default
    must name a Realm whose credential this repository actually
    documents, so that a reader who is told to set a key is told
    *which* key.
    """
    assert REALM_DEFAULT_MODELS, "no per-Realm default is recorded"
    for model_id in REALM_DEFAULT_MODELS.values():
        realm = realm_for_model_id(model_id)
        if realm in REALMS_WITH_ANONYMOUS_FREE_TIER:
            # An anonymous default needs no credential and therefore no
            # credential variable: Pollinations is the bootstrap fallback,
            # and demanding a key for it would defeat its purpose.
            assert model_requires_credential(model_id) is False
            continue
        assert model_requires_credential(model_id) is True

        assert realm in REALM_API_KEY_ENV, (
            f"the default {model_id} resolves to the {realm!r} Realm, "
            "which has no documented credential variable, so a reader "
            "told to authenticate would be told to set nothing"
        )
        assert REALM_API_KEY_ENV[realm], f"{realm!r} maps to an empty env var name"


# ---------------------------------------------------------------------------
# Per-Realm defaults, not a global default
# ---------------------------------------------------------------------------


def test_per_realm_defaults_agree_with_the_shipped_catalog() -> None:
    """Each per-Realm default is that Realm's own catalog entry.

    A default that named another Realm's model would send a credential
    to a host that never asked for it, which is the global-default bug
    in a table. So the table key and the catalog Realm must agree for
    every entry, not just for one Realm.
    """
    entry = list_models()
    for realm, model_id in REALM_DEFAULT_MODELS.items():
        default = next(m for m in entry if m.id == model_id)
        assert default.realm == realm


def test_resolve_default_model_is_realm_first() -> None:
    """Explicit model wins, then Realm preference, then nothing.

    The nothing case is the behaviour the task requires: a Summoner
    with no model and no Realm preference must not silently get an
    OpenRouter model. An unknown Realm also resolves to nothing rather
    than to another Realm's model.
    """
    assert (
        resolve_default_model(model="opencode/glm-5", realm="openrouter")
        == "opencode/glm-5"
    )
    assert resolve_default_model(realm="openrouter") == "openrouter/free"
    assert resolve_default_model(realm="opencode") == "opencode/space-bunny-free"
    assert resolve_default_model(realm="pollinations") == "pollinations/openai"
    assert resolve_default_model() is None
    assert resolve_default_model(realm="groq") is None
    assert resolve_default_model(realm="") is None


def test_default_model_for_realm_returns_none_without_a_default() -> None:
    assert default_model_for_realm("groq") is None
    assert default_model_for_realm("") is None


def test_the_opencode_default_is_not_a_measured_refusal() -> None:
    """The opencode default must be an endpoint that answers, not just
    a catalog entry.

    This is the guard ADR 0019 exists to be. ADR 0018 moved the default
    to `opencode/step-5-preview-free` on the strength of a catalogue
    check -- the slug existed, was priced $0/$0, carried a 1,000,000-
    token window, and reported tool calls, reasoning and structured
    output. Every one of those was true and none of them was whether
    the endpoint would answer. It did not:

        FreeTierError: OpenCode's free tier can only be used from within OpenCode

    `verify-install`'s step 5 caught it, which is the only reason it
    did not ship. The four steps before it passed, so the failure was
    the request and not the packaging -- and the request is the one
    thing a default exists to make.

    The check is offline here and that is deliberate. The suite makes
    no network calls, and a default that can only be validated by
    hitting the provider on every test run would be either flaky or
    skipped. What is asserted is the negative: the opencode default is
    not on the list of Zen free endpoints *measured* to refuse a
    stranger. That list is a dated snapshot because it is a snapshot.
    If Zen starts serving these slugs to strangers -- or starts
    refusing `space-bunny-free` -- the honest fix is to re-measure and
    update this table and the default in the same change.

    Measured 2026-10-11, one anonymous request each, against
    `https://opencode.ai/zen/v1/models`.
    """
    refused_by_zen_free_tier = {
        # FreeTierError: can only be used from within OpenCode.
        "opencode/step-5-preview-free",
        "opencode/longcat-2.5-preview-free",
        "opencode/mimo-v2.6-flash-free",
        "opencode/jev-1.13-free",
        "opencode/muse-spark-1.3-contributor-free",
        "opencode/muse-spark-1.2-contributor-free",
        "opencode/nemotron-3-ultra-free",
        "opencode/nemotron-3.5-lightning-free",
        "opencode/ling-3.1-flash-free",
        # Different failures, same consequence for a default.
        "opencode/exo-free",  # ModelDeprecated
        "opencode/ling-3.0-flash-fin-free",  # upstream unavailable
    }
    assert "opencode/space-bunny-free" not in refused_by_zen_free_tier


def test_every_per_realm_default_is_free() -> None:
    """Every per-Realm default is what a first-time Summoner runs.

    A paid default would mean the documented first run asks for a
    card, which is not what the quickstart promises.
    """
    for model_id in REALM_DEFAULT_MODELS.values():
        default = next(m for m in list_models() if m.id == model_id)
        assert default.free is True


def test_every_per_realm_default_has_a_base_url() -> None:
    """A baseline entry is read without contacting anyone.

    Before the Rune is installed this is the only base URL there is,
    so an empty one means the catalog cannot tell a Summoner where
    the model lives.
    """
    for realm, model_id in REALM_DEFAULT_MODELS.items():
        default = next(m for m in list_models() if m.id == model_id)
        assert default.base_url == REALM_BASE_URLS[realm]


# ---------------------------------------------------------------------------
# The error a Summoner actually sees
# ---------------------------------------------------------------------------


def test_the_error_names_the_rune_for_the_models_own_realm() -> None:
    """A model that needs opencode-realm must not be told to install openrouter.

    That was the literal this module replaced: the message is the only remedy
    available, so a hardcoded Rune name is a Rune name that goes stale the moment
    a second Realm exists.
    """
    error = no_realm_registered("opencode/space-bunny-free")
    assert error.rune_name == "opencode-realm"
    assert error.realm == "opencode"
    assert "mvge rune install opencode-realm" in str(error)


def test_the_error_carries_the_realm_even_when_the_model_has_none() -> None:
    """A model with no realm set falls back to its id prefix, and says so."""
    model = Model(
        id="opencode/space-bunny-free",
        name="Space Bunny Free",
        realm="",
        base_url="",
        api_key="",
    )
    error = no_realm_registered(model)
    assert error.realm == "opencode"
    assert error.rune_name == "opencode-realm"


def test_the_error_message_and_its_attributes_never_disagree() -> None:
    """The message is prose; the attributes are what callers act on.

    The GUI used to recover the Rune by splitting the message, which is why both
    have to carry the same name.
    """
    error = no_realm_registered("opencode/space-bunny-free")
    assert error.rune_name in str(error)


def test_an_unknown_slug_under_a_realm_names_that_realms_rune() -> None:
    """The missing-Rune and unknown-model cases need opposite answers.

    Installing the Rune is the correct first step for a Summoner who typed a
    valid model on a machine that has not installed its Rune, and it is the
    wrong advice for a Summoner who typed a model that does not exist. Before the
    distinction existed, both got the same message and the second one sent them
    to install something.
    """
    reg = RealmRegistry()
    with pytest.raises(NoRealmRegisteredError) as missing_rune:
        reg.resolve("opencode/space-bunny-free", api_key="k")
    assert missing_rune.value.rune_name == "opencode-realm"

    with pytest.raises(ValueError, match="Unknown model") as unknown:
        reg.resolve("totally-not-a-real-namespace/made-up", api_key="k")
    assert "Unknown model" in str(unknown.value)


def test_a_spellcheck_typo_is_still_reported_as_a_missing_realm() -> None:
    """Named for what a Summoner experiences, not for the tidiness it looks like.

    A typo under a Realm we ship cannot be told apart from a real model we have
    not catalogued -- and the advice is the same either way, because installing
    the Rune is what has to happen before the typo can even be detected.
    """
    reg = RealmRegistry()
    with pytest.raises(NoRealmRegisteredError) as exc:
        reg.resolve("opencode/space-bunny-fre", api_key="k")
    assert exc.value.rune_name == "opencode-realm"


def test_a_registered_realm_still_resolves_an_uncatalogued_slug() -> None:
    """A Rune-provided Realm serves models no catalog knows about.

    Refusing unknown ids under a registered Realm would refuse the feature that
    makes Runes worth having.
    """
    reg = RealmRegistry()
    reg.register_realm_factory(
        "opencode", lambda api_key="", base_url="", **kw: object()
    )
    model, _ = reg.resolve("opencode/a-model-nobody-catalogued", api_key="k")
    assert model.realm == "opencode"


# ---------------------------------------------------------------------------
# Free-model conventions
# ---------------------------------------------------------------------------


def test_each_realm_declares_at_most_one_free_suffix() -> None:
    assert all(suffix for suffix in REALM_FREE_SUFFIXES.values())


def test_free_suffix_is_per_realm_not_global() -> None:
    """Reading ``-free`` off any id would let a name call a paid model free.

    That is the one mistake a cost guard must not make, so the suffix belongs to
    the Realm that actually uses it.
    """
    assert free_suffix_for_realm("opencode") == "-free"
    assert free_suffix_for_realm("openrouter") == ":free"
    assert free_suffix_for_realm("ollama") == ""


def test_register_realm_default_model_validates_and_restores() -> None:
    """Registering a Realm default validates its inputs and is reversible."""
    from mvge.provider.realms import (
        REALM_DEFAULT_MODELS,
        default_model_for_realm,
        register_realm_default_model,
    )

    saved = dict(REALM_DEFAULT_MODELS)
    try:
        with pytest.raises(ValueError, match="non-empty"):
            register_realm_default_model("", "testrealm/test-free")
        with pytest.raises(ValueError, match="non-empty"):
            register_realm_default_model("testrealm", "")
        register_realm_default_model("testrealm", "testrealm/test-free")
        assert default_model_for_realm("testrealm") == "testrealm/test-free"
    finally:
        REALM_DEFAULT_MODELS.clear()
        REALM_DEFAULT_MODELS.update(saved)
    assert default_model_for_realm("testrealm") is None
