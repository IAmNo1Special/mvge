# ADR 0028: Realm-First Default Models

## Status

Accepted

## Date

2026-10-27

## Supersedes

[ADR 0020](0020-no-free-tier-answers-a-stranger.md), default-model part only.
ADR 0020's measurement stands untouched: no free tier answers a stranger,
its anonymous-free-tier table stays empty, and the per-model credential
exemption stands. What is superseded is only the decision that followed
from that measurement -- that the engine carries one global default model,
`openrouter/free`, and one global default Realm, `openrouter`.

## Context

The engine derived everything from the model a Summoner chose: which
Realm serves it, which credential variable is read, which Rune is
offered when the Realm is missing, which base URL is called. The one
place that derivation did not run was the fallback itself. A single
global `DEFAULT_MODEL` in `mvge.core.constants` was the fallback in
agent config, in the CLI key gate, in the TUI and in the REPL, and
`DEFAULT_REALM` in `mvge.provider.realms` was a copy of its Realm,
held in step by a test. So a Summoner whose Realm was opencode, with
no model set anywhere, was asked for `OPENROUTER_API_KEY` and offered
`openrouter-realm` -- the engine's choice, not theirs, wearing the
shape of a default.

That is a bug, not a preference. A default that names one provider for
every user is a provider choice, and a provider choice made silently
is the same failure the credential work in ADR 0020 was about: a key
sent to a host that never asked for it, and a first run that cannot
be explained by anything the Summoner chose.

The constants comment justifying the global default was not wrong
about its own property: `openrouter/free` is a router, and a router
avoids the saturated-single-endpoint failure that broke the quickstart
twice. It was wrong about the shape. A router property belongs to a
Realm's default, not to the engine's: `openrouter/free` is still the
openrouter default, and it still has that property, for Summoners who
choose openrouter.

## Decision

1. **Defaults belong to Realms.** `REALM_DEFAULT_MODELS` in
   `mvge.provider.realms` records one default model per shipped Realm:
   `openrouter` to `openrouter/free`, `opencode` to
   `opencode/space-bunny-free`. Every entry must be a free slug in the
   shipped catalog, and must be an endpoint measured to answer, not
   merely catalogued -- that is the ADR 0018/0019 lesson, and the
   opencode entry is held to the measured-refusal list in tests.
2. **Resolution is Realm-first, in one place.**
   `resolve_default_model` in `mvge.provider.model_registry` is the
   one resolver: an explicit model wins, then the stored Realm
   preference through the table, then nothing. Returning nothing for
   the no-preference case is the point. A caller that filled that
   hole with a guessed model would reintroduce the bug in a new
   place.
3. **The Realm preference is stored config.** Agent config gains a
   `realm` key, settable with `mvge config set realm <realm>`, carried
   through the same layer stack as every other config value. Agent
   coercion resolves the model from it; an unknown Realm's preference
   is an error naming the Realm, never a fallback to another Realm's
   model.
4. **The CLI gates on the resolved model, and bootstraps first.**
   The CLI key gate resolves through the same resolver (explicit
   `--model`, then config model, then Realm preference). With no
   model and no preference it does not assume OpenRouter: an
   interactive session is offered a Realm choice, a scripted run
   gets a Realm-choice error, and a first run that may bootstrap
   defers the gate to after bootstrap, where the key is resolved for
   the model that actually runs, per Realm.
5. **No global default remains.** `DEFAULT_MODEL` is removed from
   `mvge.core.constants` and `DEFAULT_REALM` is removed from
   `mvge.provider.realms`. The `DEFAULT_REALM` in `mvge.agent.auth`
   is a separate file-naming default for the credential file and is
   out of this decision's scope. The stale comment in constants that
   still described the old opencode default is removed with the
   constant it misdescribed.
6. **Realms can extend the table.** `register_realm_default_model`
   lets an out-of-tree Realm Rune register its default. The
   credential gate runs before any Rune is loaded, so the gate reads
   only the shipped table; a registered default takes effect for
   sessions resolved after the Rune loads.

## Contract

<!-- adr-contract: REALM_DEFAULT_MODELS, default_model_for_realm, register_realm_default_model, resolve_default_model, resolve_cli_model -->

## Consequences

- A Summoner with no model and no Realm preference gets a Realm
  choice, not an OpenRouter key prompt. That is a visible change to
  the first run, and it is the change this ADR exists to make.
- A Summoner who sets `realm` once gets that Realm's default on
  every run without naming a model, including after bootstrap.
- Tests that used to rely on the global default now name a model
  explicitly. That is not churn for its own sake: a test that
  silently inherited a provider was asserting less than it claimed.
- The first-run key step from ADR 0020 is unchanged for Summoners
  who choose a keyed Realm; it is only no longer assumed to be
  OpenRouter's key for everyone.
- A future keyless Realm (a Pollinations-style anonymous lane, or a
  local daemon) slots into this shape by adding one table entry and
  its credential exemption, with no new global default to argue
  about.
- ADR 0015's contract list is amended in the same commit to drop
  `DEFAULT_MODEL`, which this decision retires; its other contracted
  symbols stand, and the ADR contract check passes.
