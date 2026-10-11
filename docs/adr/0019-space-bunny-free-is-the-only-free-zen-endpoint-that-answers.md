# ADR 0019: `space-bunny-free` Is the Only Free Zen Endpoint That Answers a Stranger

## Status

Accepted

## Date

2026-10-10 23:00Z

## Supersedes

[ADR 0018](0018-default-free-endpoint-is-step-5-preview.md), which moved the
default to `step-5-preview-free`. ADR 0018's *premise* — that a different free
Zen endpoint could carry the documented first run — is what this decision
measurably refutes. ADR 0015, which chose Zen as the default Realm, is
unaffected and stands.

## Context

`space-bunny-free` was observed returning 429 on the quickstart's task with
none of our own load running, which reads as a provider-side limit on one
endpoint. ADR 0018 treated that as "pick a different free slug" and chose
`step-5-preview-free`.

The claim in ADR 0018 was checked against a catalogue and not against a
request. It verified the slug existed, was priced $0/$0, carried a
1,000,000-token window, and reported tool calls, reasoning and structured
output. Every one of those is true and none of them is the property that
matters: **whether the endpoint will answer a request from a machine that is
not OpenCode.**

## Decision

Measured, once, from this machine, one request each, no credential — the free
tier needs none, which is what makes this a cheap measurement to repeat:

| slug | result |
| --- | --- |
| `space-bunny-free` | **200, a real completion** |
| `step-5-preview-free` | `FreeTierError: OpenCode's free tier can only be used from within OpenCode` |
| `longcat-2.5-preview-free` | same `FreeTierError` |
| `jev-1.13-free` | same `FreeTierError` |
| `muse-spark-1.3-contributor-free` | same `FreeTierError` |
| `muse-spark-1.2-contributor-free` | same `FreeTierError` |
| `mimo-v2.6-flash-free` | same `FreeTierError` |
| `nemotron-3-ultra-free` | same `FreeTierError` |
| `nemotron-3.5-lightning-free` | same `FreeTierError` |
| `ling-3.1-flash-free` | same `FreeTierError` |
| `exo-free` | `ModelDeprecated` |
| `ling-3.0-flash-fin-free` | `server_error: Upstream request failed: Model is unavailable.` |

**Eleven of the twelve free slugs on the live Zen catalogue cannot serve a
request that does not originate inside OpenCode.** `space-bunny-free` is the
only one that can, and it answered from this machine during the same window in
which the others refused.

So the default reverts to `space-bunny-free`, and the catalogue entry ADR 0018
added is removed. The defect that started this — a 429 on the quickstart task —
is therefore **not fixable by choosing another free Zen endpoint**, and was
never fixable that way. What it is:

- an intermittent provider-side limit on the one endpoint that will answer us,
  or
- something about that request that can be made to saturate.

The first cannot be engineered around. The second has not been measured. This
ADR records the finding and deliberately does not promise a fix, because the
last one promised "an endpoint that is not saturating" and shipped a default
that cannot complete a request at all.

## Contract

No new symbols. `DEFAULT_MODEL` returns to `opencode/space-bunny-free` and
`DEFAULT_REALM` was never touched by ADR 0018 or this one.

The invariant ADR 0018 restated — every model the shipped catalog carries on the
`opencode` Realm is free, and `DEFAULT_MODEL` is among them — reverts to the
original count form, because the Realm is back to carrying one Zen model. The
property form was not wrong; it was written for a state that this decision
undoes.

## Consequences

- The documented first run works again. `verify-install` step 5 fails on ADR
  0018's slug with `FreeTierError` and this is what caught it; the same run's
  steps 1–4 pass, so the failure is the request and not the packaging.
- **The quickstart's five-minute promise is unverified until a clean-machine
  run completes against `space-bunny-free`.** The 429 that started this was
  observed; it may be intermittent. Nothing here settles that, and no
  alternative Zen endpoint exists to fall back to.
- The real options for the 429, none of which is "pick another slug":
  - make the engine's retry actually cover the observed failure window. ADR
    0015's default retry is about 1.9s of total wait against an outage that
    lasts minutes, which is the gap ADR 0018 identified and never addressed.
  - move the default Realm off Zen entirely, accepting that Zen's free tier is
    the one needing no credential and therefore the one that makes the
    quickstart free.
  - register an OpenCode-origin request path, which is what the other eleven
    slugs are gated on and is a product decision, not an engine one.
- A catalogue check is not a behaviour check. ADR 0018 verified its choice
  against metadata; the failure was in whether the endpoint would answer. For a
  default, the second question is the only one, and it is one HTTP request to
  ask — which is why this ADR records twelve of them rather than one.
