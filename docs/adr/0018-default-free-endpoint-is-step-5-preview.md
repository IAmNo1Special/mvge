# ADR 0018: The Default Free Endpoint Is `step-5-preview-free`

## Status

Accepted (amends 0015's model, not its Realm)

## Date

2026-10-10

## Amends

[ADR 0015](0015-opencode-zen-default-realm.md), which chose OpenCode Zen as the
default *Realm*. The Realm decision stands and nothing in its Contract moves;
this changes only which free endpoint on that Realm the default points at.

## Context

ADR 0015 set the default model to `opencode/space-bunny-free`. That slug then
became the documented five-minute first run, and the first run began failing with
HTTP 429 on the task the quickstart names — with none of our own load running
against the endpoint. 429 from a free tier with zero of our traffic is a
provider-side limit on that one endpoint, not a consequence of anything in this
repository, and it is not fixable in prose for the same reason ADR 0015 gives:
the engine default points at a saturated endpoint.

The failure mode is narrow, which is what makes it cheap to decide. Two of the
four things that make a default safe are unchanged and remain unchanged:

- The **Realm** is still `opencode` (ADR 0015), so `DEFAULT_REALM`, the no-key
  free tier, and `model_requires_credential`'s per-model exemption all behave
  identically. Nothing downstream keys off the model slug.
- The model is still **free**, so the quickstart still asks for no card.

What needs replacing is exactly one thing: the endpoint.

## Decision

`DEFAULT_MODEL` becomes `opencode/step-5-preview-free`, and the shipped catalog
carries it alongside `space-bunny-free` rather than instead of it. A Summoner who
chose the old slug with `-m` keeps it.

The choice was made on evidence available without an OpenCode credential, which
is the only kind available. Against the live Zen catalogue
(`https://opencode.ai/zen/v1/models`) and `models.dev`'s opencode provider:

- The slug exists in the live catalogue (87 models, 12 carrying a `-free` suffix).
- It is priced $0 input, $0 output against a 1,000,000-token context window —
  comparable to `space-bunny-free`'s 1,048,576, so the swap does not shrink a
  session.
- It reports tool calls, reasoning, and structured output. `space-bunny-free`
  reports the first two and returns null for the third, so this is not a
  downgrade on any axis the engine reads.
- It is the model that served the session that made this change.

`space-bunny-free` stays catalogued precisely because it is still selectable:
removing it would retire a Summoner's explicit `-m` choice, and the defect being
fixed is the *default*, not the model.

## What this decision does not claim

It does not claim the new endpoint cannot saturate. Nothing here measures that,
and this ADR is written to keep the distinction visible rather than to inherit a
promise the evidence does not support — which would put this repository in the
position ADR 0015 was already in, of shipping a "free" default that fails a
first run.

Settling it needs repeated authenticated requests against the endpoint, which
this change had no credential to make. The instrument that can settle it is
`verify-install`'s clean-machine task: it runs the documented quickstart against
the published wheel, so a saturated default turns the run red with a reason a
person can act on. That is the follow-up, and until it passes on the new slug the
right description of this change is "a different endpoint, chosen on evidence",
not "a route around saturation".

## Contract

No new symbols. `DEFAULT_MODEL` and `DEFAULT_REALM` keep their names and their
kinds; only the value of the first changes, and `DEFAULT_REALM` is deliberately
not re-derived from it, for the reason ADR 0015 records — the slug's prefix is
the provider, and the Realm serving that provider is a separate fact.

The invariant this change does have to hold, and which is now asserted as a
property rather than as a count: every model the shipped catalog carries on the
`opencode` Realm is free. ADR 0015's test stated that as a count — the Realm
carries exactly one model, the default — which was true only while one Zen model
shipped, and which a second free model would have failed while remaining correct
on the property that mattered. The count form is replaced by:

- the Realm carries at least one catalogued model,
- every one of them is free, and
- `DEFAULT_MODEL` is among them.

## Consequences

- The documented first run points at an endpoint that has not been observed
  failing this session. It may still fail; the difference is that if it does,
  the failure is on a different endpoint rather than on the same one.
- Two free Zen models ship, so `REALMS_WITH_ANONYMOUS_FREE_TIER` and the
  fallback derivation are exercised against a second model for the first time.
  Both still hold: the fallback exists for models the catalog does not carry,
  and these two are carried.
- `space-bunny-free` is now a non-default option, which means it is covered by
  the catalog tests only as a catalog entry rather than as the thing every suite
  boots on. The suites that used it as a concrete example id keep using it and
  keep passing, because it is still catalogued.
- The real risk of this decision is not visible at merge and cannot be made
  visible here: a default that saturates weeks later. That is why
  `verify-install`'s task, not this ADR, is the thing that would catch it.
