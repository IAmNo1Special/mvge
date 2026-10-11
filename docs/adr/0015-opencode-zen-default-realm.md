# ADR 0015: OpenCode Zen as the Default Realm

## Status

Accepted

## Context

The default model was `nvidia/nemotron-3-ultra-550b-a55b:free`, an OpenRouter
free endpoint. It returned `Upstream error from Nvidia: Service temporarily
overloaded` on three of three clean-machine runs of the task the quickstart
documents (SOM-29). The documented five-minute first run was false, and the
failure was not fixable in prose: it is an engine default pointing at an endpoint
that is at capacity.

Zen's free tier needs **no API key** — a request with no `Authorization` header
returns HTTP 200 with `"cost": "0"`. That is the reason this decision is cheap:
the default Realm resolves and runs for a Summoner who has signed up for nothing.

Two properties of the default decide how a first run behaves, and they are
worth separating because the fix for one is not the fix for the other:

1. **The endpoint has capacity.** Not something the engine can influence.
2. **The model is free.** A paid default means the documented first run asks for
   a card.

OpenCode Zen serves a free tier — `space-bunny-free` among thirteen — on the
standard Chat Completions endpoint, reachable with one key. Its catalog
(`/zen/v1/models`) publishes slash-free bare ids, so a model is addressed
directly with no provider tier to choose.

Three engine facts made adopting it more than a constant change:

- **The engine ships no Realm implementations.** `RealmRegistry` starts empty and
  only a Rune factory ever fills it, so no model resolves on a clean machine until
  a Rune is installed. That was already true; the change makes the default depend
  on it rather than sit beside it.
- **The Rune name in the remedy was hardcoded.** `NoRealmRegisteredError` always
  told the Summoner to install `openrouter-realm`, so a default needing
  `opencode-realm` would have named the wrong Rune — a message that reads as
  actionable and is not.
- **The API key was read from one variable.** `OPENROUTER_API_KEY` alone. Sending
  that to Zen authenticates at the wrong host and then fails every call with a
  message naming the model, so the Summoner is sent to look at the wrong thing.

## Decision

`DEFAULT_MODEL` becomes `opencode/space-bunny-free`. The opencode Realm ships as
the `opencode-realm` Rune in `mvgeos-marketplace` (PR #17), and the CLI offers to
install it when resolution fails — the same bargain the previous default made
with `openrouter-realm`.

Facts about Realms move to `mvgeos-provider/realms.py`, and three consumers read
them from there instead of carrying their own:

- `NoRealmRegisteredError` carries `realm` and `rune_name`, and the CLI prompt and
  the GUI badge read the attribute. The GUI previously recovered the name by
  splitting the message on `"install "`.
- The CLI reads `<REALM>_API_KEY` for the selected model's Realm. It does not
  fall back to another Realm's variable, which an earlier version of this
  decision required it to do. See the amendment below.
- The baseline catalog carries a per-entry Realm, so an entry can name a base URL
  other than OpenRouter's.

The alternative considered was putting the Realm in `mvgeos-provider` rather than
in a Rune, so the default resolves with zero installs. Rejected: it keeps the
charter line that concrete Realms ship as Runes, and the install prompt already
handles the first run. See the decision document on SOM-58 for that trade in
full.

## Contract

Symbols this decision commits to. The Realm tables are the single place the
registry, the CLI and the GUI agree on a Rune name, a base URL and a credential
variable; the derivations must keep producing a usable answer for a Realm that is
not in a table, because they run on the failure path.

Whether a Realm needs a credential is not one fact but two, so it is two tables
and one derivation. `REALMS_WITH_ANONYMOUS_FREE_TIER` records a Realm whose *free
tier* is served without one — `opencode`, measured, a request carrying no
`Authorization` header is answered 200 with `"cost": "0"` — and the credential is
then optional *per model*, because Zen also serves keyed paid models.
`REALMS_WITHOUT_CREDENTIAL` records a Realm with no credential concept at all,
which is a categorically different fact and so stays a separate Realm-wide entry.

`model_requires_credential` combines them with the model's own `is_free`, so the
exemption is consulted per model. It is deliberately not the Realm-wide form: a
Realm-wide exemption would attempt a paid Zen model anonymously and fail at the
host with a message about the model rather than about the missing credential.
Both tables record *exceptions* rather than rules, so a newly added Realm needs no
engine edit and cannot be made to fail by an omission, and a model the shipped
catalog does not carry is treated as needing one.

`serving_realm_for_model` is what the tables are consulted against, so the Realm
that decides whether a credential is needed and the Realm whose credential file
is read cannot be two different Realms. It reads the baseline catalog rather than
`ModelRegistry`, for the same reason `model_requires_credential` does: this runs
on the credential gate and a prompt must not wait on an HTTP call.

The engine cannot defer any of this to a Rune's manifest: every credential gate
runs before any Rune is loaded, so a declaration on the Rune could not be read at
any of them, and on a clean machine the Realm being exempted is not installed yet.

<!-- adr-contract: DEFAULT_MODEL, REALM_RUNES, REALM_BASE_URLS, REALM_API_KEY_ENV, REALM_FREE_SUFFIXES, DEFAULT_REALM, REALMS_WITHOUT_CREDENTIAL, REALMS_WITH_ANONYMOUS_FREE_TIER, realm_for_model_id, serving_realm_for_model, rune_for_realm, api_key_env_for_realm, free_suffix_for_realm, model_requires_credential, no_realm_registered, NoRealmRegisteredError, Model.free -->

## Consequences

- The documented first run depends on a marketplace fetch to install the Rune. A
  fetch failure trades "provider is saturated" for "could not reach the
  marketplace". Both are first-run failures; this one is at least visible.
- **The install is asked for only where there is somebody to ask.** The offer is
  made on the answer to "is a human watching", not on the answer to "is this the
  interactive mode". A script, a pipe or CI has no TTY, and there `input()` raises
  `EOFError` — which the CLI caught and read as a refusal, so the one remedy
  available switched itself off exactly when nobody was there to decline it.
  With no terminal the Rune is installed without asking; with one, one-shot mode
  still reports the error and names the command, because a Summoner who is
  watching can be told to run it. Manifest-declared `python_deps` remain a
  separate gate that fails closed without a terminal: installing the Rune is not
  consent to fetch arbitrary packages on the strength of its manifest.
- **The self-heal retries once.** Installing is only half of it — the factory
  exists once the Rune has *loaded* — so the request runs again afterwards. An
  Rune that installs and registers nothing, or a marketplace entry pointing at
  the wrong directory, made that retry raise the same error, and an unbounded
  retry reinstalls on every frame until the stack runs out. One attempt, then the
  error is reported: the same Rune installed twice is the same Rune.
- `opencode/space-bunny-free`'s context window is **1048576**, measured against
  the live gateway rather than taken from documentation. Zen publishes no context
  length — all 86 entries in `/zen/v1/models` carry only `id`, `object`,
  `created` and `owned_by` — so the value had to be found by sending prompts of
  increasing size until the gateway refused one. An earlier attempt aimed at
  1,045,162 tokens, accepted, and 1,055,000, refused. A second, tighter probe
  narrows it to **1,048,465 accepted and 1,048,765 refused**, which leaves
  1048576 — 2^20, and the only round value in that interval — as the reading
  rather than an assumption. Accepted without error below that point at 200,165,
  1,000,165, 1,002,165, 1,010,165 and 1,040,165; refused above it at 1,050,165,
  1,100,165, 1,250,000, 1,500,165, 2,000,165 and 4,000,165.
  `should_compact` keeps 16384 tokens of headroom below the recorded value, so
  the recorded figure is the total rather than a ceiling that leaves no room for
  output. An earlier guess of 128000 was eight times too low and would have
  compacted a long session roughly every 128k tokens. This is a measurement of
  one provider on one day, not a published specification; re-measure before
  trusting it, and prefer the smaller figure if the two ever disagree.
- **Zen's free tier needs no key, and this contradicts a paragraph that used to
  sit here.** An earlier draft of this bullet claimed the opposite: that a
  request carrying no `Authorization` header was answered while
  `Authorization: Bearer <wrong>` was refused with `401 Invalid API key.`, and
  concluded that "a real `OPENCODE_API_KEY` is required to run Zen". The first
  half of that observation is still true and is still the reason the key is
  optional. The conclusion drawn from it was not: `Bearer <wrong>` is refused
  because it is wrong, which says nothing about whether *no* header is accepted,
  and the measurement above had already shown it is. The bullet now agrees with
  the Context section, which is where the fact was recorded correctly first.
- **An empty bearer cannot be sent at all**, and says nothing useful when it
  fails. `httpx` rejects the header value `Bearer ` locally, so the Rune raises
  `httpx.LocalProtocolError` instead of a credential error. Unreachable through
  the CLI and the GUI, both of which refuse to start without a resolved key, so
  this is recorded rather than fixed. A Summoner who reaches it through a script
  sees a transport fault where the truth is a missing credential.
- **No Realm is ever handed another Realm's credential.** Amended after
  measurement. The per-Realm variables and per-Realm credential files this
  decision introduced were added, but three older Realm-blind lookups survived
  beside them and overrode the new ones on the ordinary path:

  - `mvgeos_agent.mvge._resolve_api_key` read `OPENROUTER_API_KEY` first,
    unconditionally, so it resolved before any Realm was known.
  - The CLI read `OPENROUTER_API_KEY` as a fallback for every Realm.
  - The GUI's settings dialog keeps one credential in one keyring slot named
    `openrouter_api_key`, and offered it to every Realm.

  With `opencode` the default Realm, each of these sent an OpenRouter key to
  `opencode.ai`. That authenticates at the wrong host and hands the Summoner's
  credential to a host that never asked for it. The CLI fallback was also
  redundant for its own case: `api_key_env_for_realm("openrouter")` *is*
  `OPENROUTER_API_KEY`, so removing it changed nothing for OpenRouter and
  removed the leak for every other Realm.

  A Realm with no credential of its own now resolves to none. That is a correct
  answer rather than a missing step, and for Zen it is the normal case.
- `DEFAULT_REALM` is a copy of what the catalog says about `DEFAULT_MODEL`, since
  the two are not the same question: for a routed slug the id prefix is the
  *provider*, not the Realm. `test_default_realm_agrees_with_the_shipped_catalog`
  holds the copy honest, and fails if `DEFAULT_MODEL` moves without it.
- Which Realm serves a model is therefore **two derivations**, and they answer two
  questions. `realm_for_model_id` reads what the id is *spelled* — its prefix —
  which is what the GUI's cascading selector needs, since a provider tier is what
  it offers a Summoner. `serving_realm_for_model` reads where the request is
  actually *sent*, from the catalog entry's own `realm`, falling back to the
  prefix for a model the catalog does not carry because a Rune may serve one.
  Every credential gate consults the second. Before that, `realm_for_model_id`
  answered both, and for a routed slug it named a Realm that does not exist: the
  CLI looked for `NVIDIA_API_KEY` and `~/.agents/auth/nvidia.json` for a model
  openrouter serves, so the credential `mvgeos export` actually writes did nothing
  and an exported `OPENROUTER_API_KEY` was the only thing keeping it working.
- `Model.free` now reads a `-free` suffix, scoped to the Realms that use it.
  Scoping is the point: reading it globally would let a model id call a paid model
  free, which is the one mistake a cost guard must not make.
- Contemplation is unavailable on the default. Zen publishes no reasoning
  parameter for this tier, and the standard has no field for it, so the Realm caps
  output tokens instead. Contemplation deltas are still read, so an upstream model
  that thinks out loud lands in the transcript rather than being dropped.
## Verification

Driving the shipped `opencode-realm` against the live gateway, installed the way
the engine installs it and resolved through `RealmRegistry`:

| Fact | Measured |
|---|---|
| Plain channel | stream completes, `PONG` |
| Spells | offered as `tools`; the model casts `bash` with `{"command": "ls -la"}`, stop reason `spellUse` |
| Non-channelled `complete()` | returns text, used for compaction summaries |
| Credential | none required; `cost: 0` |
| Context window | 1,048,576 (1,045,162 accepted, ~1,055,000 refused) |

That live run also found a defect the hermetic suite could not. With no key, both
the Rune and `SSEStreamingRealm` built `Authorization: Bearer ` with nothing
after it, which is an illegal header value — httpx raises `LocalProtocolError`
before the request leaves the process. An `httpx` mock transport accepts the
malformed value, so every test passed. The one Summoner this Realm exists to
serve could not make a single call.

Both the Rune and the engine's client defaults now omit the header when there is
no credential.
