# ADR 0020: No Free Tier Answers a Stranger

## Status

Accepted

## Date

2026-10-11 09:40Z

## Falsifies

[ADR 0015](0015-opencode-zen-default-realm.md), which chose OpenCode Zen as the
default Realm **on the premise that its free tier needs no API key**:

> Zen's free tier needs no API key — a request with no `Authorization` header
> returns HTTP 200 with `"cost": "0"`. That is the reason this decision is
> cheap: the default Realm resolves and runs for a Summoner who has signed up
> for nothing.

That sentence was measured true when it was written. It is measured false now,
and 0015's *mechanism* — the Realm tables, the per-model credential exemption,
the anonymous free-tier set — is unaffected and stands. What is falsified is the
one premise the decision called cheap.

## Context

`verify-install` on 0.6.29 failed at step 5, the documented first task:

```
[1/5] uvx mvge --help -> exit 0
[2/5] resolved tree -> mvge 0.6.29, ... all seven at 0.6.29
[3/5] rune install opencode-realm -> exit 0
[4/5] mvge install coding_mvge -> exit 0
[5/5] real task -> exit 1 in 0.74s
[   0.74s] Error: OpenCode's free tier can only be used from within OpenCode
```

Steps 1 through 4 pass, so the packaging, the dependency resolution, the
install chain and the `==` pins are sound. The failure is the request, which is
the one thing a default exists to make.

Measured directly, one anonymous request each, against the live endpoints:

| Realm | model | anonymous result |
| --- | --- | --- |
| opencode | `space-bunny-free` | `FreeTierError: OpenCode's free tier can only be used from within OpenCode` |
| openrouter | `openrouter/free` | `401 No cookie auth credentials found` |
| openrouter | `nvidia/nemotron-3.5-lightning:free` | `401` |
| openrouter | `google/gemma-4-31b-it:free` | `401` |
| openrouter | `openai/gpt-oss-20b:free` | `401` |
| openrouter | `liquid/lfm-2.5-2.6b:free` | `401` |

`space-bunny-free` answered a real completion at 23:00Z the same evening this
was re-measured, three attempts, three refusals. That is a provider-side change
within hours, not a property of our code.

## Decision

**`DEFAULT_MODEL` becomes `openrouter/free` and `DEFAULT_REALM` becomes
`openrouter`, and the first run gains a credential step.**

`openrouter/free` is OpenRouter's Free Models Router: it only ever routes to a
free endpoint and picks a different one when one saturates. That is the one
property no concrete free slug has, and it is the failure that happened twice —
a specific free endpoint being saturated was the original ADR-0015 problem, and
the Zen endpoint failing was the second.

This does require `OPENROUTER_API_KEY`, and the earlier draft of this ADR
rejected exactly this change. That rejection was right about the danger and
wrong about the remedy, so it is worth being precise about the difference:

- The danger was that moving the default to a Realm that needs a key would green
  `verify-install` — which passes `OPENROUTER_API_KEY` through — while a reader
  with no key still could not run anything. A green gate reached by giving the
  gate a credential the reader does not have is a false green.
- The remedy was treated as "therefore don't move the default." But the false
  green does not come from the *key*; it comes from the key being **invisible**.
  A default that needs a credential and says so — a README whose step 0 is
  `export OPENROUTER_API_KEY=...`, a gate that exits 2 without the variable
  rather than skipping, an ADR saying the promise changed — is not a false
  green. It is a documented key step.

So the credential is made explicit everywhere it now matters:

- The README's first run is four steps, and step 0 is the key. The two claims
  that no longer hold — that the default runs with no credential, and that
  there is no credential step — are corrected in place rather than quietly
  dropped.
- `verify-install` treats a missing `OPENROUTER_API_KEY` as exit 2, with the
  variable named. It is not skipped, and it is not lenient.
- `REALMS_WITH_ANONYMOUS_FREE_TIER` is now empty, because no Realm has an
  anonymous free tier any more. Every model, on every Realm, asks for a key.

The model is still **free**. Nothing here introduces a cost to a Summoner: the
key identifies you to a provider that has stopped serving anonymous callers, and
OpenRouter's free tier bills at zero. What changes is that identity is required
before the free path will answer.

Three claims that followed from ADR-0015's premise are corrected rather than
left to rot:

- `docs/adr/0015` — Status records that its premise is falsified by this ADR.
  The Realm tables and the per-model credential exemption it introduced stand
  unchanged; only the premise that a credential was ever unnecessary fails.
- `README.md` — the quickstart gains step 0; "runs with no credential at all"
  and "there is no credential step" are both corrected.
- `scripts/verify_install.py` — no longer describes a credentialless free tier,
  and now documents why the gate requiring a credential is the correct signal.

## Contract

`DEFAULT_MODEL` changes value and `DEFAULT_REALM` changes value, and they change
together — the pair is held in step by
`test_default_realm_agrees_with_the_shipped_catalog`, which is what catches one
moving without the other.

`REALMS_WITH_ANONYMOUS_FREE_TIER` becomes empty. It is retained rather than
deleted because the exemption is a real concept a Realm can earn back, and
re-earning it must come with a measurement. `test_no_realm_has_an_anonymous_free_tier_any_more`
is what fails if a Realm is added back without one.

`verify-install` is **not** weakened to make this green. The change is that the
gate's requirement is honest rather than skipped: a missing key is exit 2 and a
named variable, never a pass.

## Consequences

- The first run is four steps, one of which is a free OpenRouter key. That is a
  real change to what "no account, no key" meant, and the README says so
  plainly rather than leaving the old promise to be discovered.
- The `opencode` Realm keeps working for anyone holding an OpenCode credential,
  and `opencode/glm-5`-style paid models still read `OPENCODE_API_KEY`. Nothing
  is removed; the default simply stops being on it.
- If OpenCode reopens its anonymous tier, reverting this is a revert to ADR
  0015's premise: put `opencode` back in the anonymous set, set the default
  Realm to `opencode`, and the README loses step 0. The measurement that would
  justify it is the same twelve requests recorded here.
- The false-green concern is discharged, not dismissed. It is discharged by the
  key being documented, not by the key existing. A future change that moves the
  default behind a credential *without* a README step is the regression this ADR
  exists to name.
- The measuring habit that produced 0019 and this ADR is the durable part. Both
  failures in this chain were a catalogue or a premise standing in for a
  request. Asking the provider is one HTTP request, and the two times it was
  skipped are the two times the answer was wrong.
