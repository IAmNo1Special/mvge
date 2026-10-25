# ADR 0021: The GenAI Attribute Vocabulary Belongs to the Rune That Uses It

## Status

Accepted

## Date

2026-10-12 08:28Z

## Context

`mvge-core/src/mvge_core/telemetry.py` published the OpenTelemetry
Generative AI semantic conventions: nineteen `gen_ai.*` attribute-name
constants, one `mvge.orchestrator`, and three helpers. All of it was
re-exported from `mvge_core/__init__.py`.

Nothing in the engine consumed any of it. The only references in the whole
repository were the re-export, the module's own test file, and the line in the
dependency-direction guard that lists it as importable. The single real
consumer was one marketplace Rune, `opentelemetry-bridge`, which imported 16 of
the 21 names.

That shape is the failure mode this repository has spent the rest of the
2026-10-11 architecture review closing. The review's other four candidates —
agent-name resolution, the Realm seam, the catalog, and the declared
Rune-importable surface — all removed or fenced a coupling where the engine
carried an opinion or a capability on behalf of a consumer that lived
elsewhere. Telemetry was the same thing wearing a different hat, and the review
flagged it as the one candidate that cut both ways: it could be read as "the
engine owns the GenAI vocabulary" or as "the engine ships telemetry it said it
did not ship".

The engine itself argued against keeping it. The root README states there is no
telemetry in the engine. That line is a statement about analytics and
phone-home, not about this module, but the two readings sit in the same file
and a reader cannot tell them apart. A module named `telemetry.py`, re-exported
from the package root, contradicts the sentence a user meets first.

The counter-argument was drift: one Rune carrying a private copy of the
attribute names could fall out of step with the engine. Measured, the argument
is weak. These are OTEL semantic-convention strings — standard names from an
external specification that change on the OTEL working group's schedule, not on
this repository's. The engine never emitted a span, so it was never a source of
truth for the vocabulary; it was only a mirror.

## Decision

The vocabulary moves to the consumer. `opentelemetry-bridge` now owns
`mvge_runes_opentelemetry_bridge/genai_attributes.py`, and
`mvge_core.telemetry` is deleted along with its test file, its re-export, its
entry in the dependency-direction guard, and its declaration in
`RUNE_IMPORTABLE_MODULES`.

The move drops four names that were used by nothing anywhere in either
repository: `GEN_AI_AGENT_NAME`, `GEN_AI_RESPONSE_MODEL`,
`GEN_AI_TOOL_DEFINITIONS`, and the two `create_genai_chat_attributes` and
`create_genai_tool_attributes` builders. They are not kept as deprecated
companions for callers that do not exist. Carrying them forward would relocate
dead weight, and the Rune has no reason to inherit it.

The ordering was deliberate and load-bearing. The marketplace migrated first,
against an engine that still declared `mvge_core.telemetry`, so the
marketplace's contract gate passed both before and after the engine dropped the
declaration. Reversing the two commits breaks the marketplace's main on the
next engine push, which is precisely the failure the declared surface exists to
make visible instead of silent.

Instrumentation is now a Rune capability, discovered like any other. This
matches ADR 0006's conclusion that Runes own capabilities and the engine owns
mechanism, and ADR 0015's shape for Realm-specific behaviour living outside the
engine.

## Contract

The engine no longer publishes a telemetry module, and the Rune-importable
surface is the only place that promise is checked:

- `RUNE_IMPORTABLE_MODULES` does not admit `mvge_core.telemetry`.
- `check_source` reports any Rune importing a module absent from it.
- `Violation` and `check_tree` carry the unpublished and undeclared kinds.

The symbols a Rune must use instead are the marketplace's, in
`mvge_runes_opentelemetry_bridge.genai_attributes`, and are outside this
repository's checkable surface.

<!-- adr-contract: RUNE_IMPORTABLE_MODULES, check_source, Violation, check_tree -->

## Consequences

- The engine's published surface loses one module and 21 exported names that
  no engine code called.
- The README's "No telemetry in the engine" is now true of the module surface
  as well as the analytics claim, which was always the reading a user gives it.
- Two ADRs do not need amending: neither named this module in a Contract.
- If a second Rune ever needs GenAI attributes, it imports the vocabulary from
  `opentelemetry-bridge` or declares its own. The engine does not become the
  shared home again for a consumer set it does not belong to, because the
  contract gate now makes an engine-side re-export a deliberate declaration
  rather than an accident.
- Nothing is verified about the spans this Rune emits against the OTEL
  specification. This decision moves ownership of the attribute names; it says
  nothing about whether the resulting traces are conformant, which the Rune's
  own suite is the place to assert.
