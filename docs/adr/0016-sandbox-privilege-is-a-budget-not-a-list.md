# ADR 0016: A Sandbox Allow-List Is a Privilege Budget, Not a Wish List

## Status

Accepted

## Date

2026-10-05

## Context

`heal-my-goap` synthesises GOAP action code at runtime with an LLM and runs it
through `Mvge host sandbox executor`
(`mvge-core/src/mvge_core/sandbox.py`). That code is not code a human wrote
and reviewed. It is code a model wrote, this run, from a gap description built
out of world state that the model itself observed.

Until [SOM-14](../SOM/issues/SOM-14) was closed, `heal-my-goap` passed
`allowed_modules={"os", "subprocess", "pathlib", "urllib", "json", "re"}`, and
the AST visitor inspected only bare `ast.Name` call targets. That combination
granted command execution: `os.system` and `subprocess.run` were rejected in
their bare form and accepted through an attribute. Both executor layers now
refuse attribute access rooted at a `FORBIDDEN_NAMES` member, and the allow-list
no longer carries `os` or `subprocess`. That closed the escape.

What remained was not a bug. It was a decision nobody had made, which is why it
is written here.

Measured against the shipped Rune, with the post-fix executor and the real
`HEAL_MY_GOAP_ALLOWED_MODULES`:

| Capability | Status |
| --- | --- |
| Command execution via `os` / `subprocess` | Closed |
| Credential theft via `os.environ` | Closed |
| Filesystem write via `pathlib.Path(...).write_text(...)` | **Open** |
| Network egress via `urllib.request.urlopen` | **Open** |

Bare `open(...)` is refused, so the write path is `pathlib` specifically.

## Decision

### 1. `allowed_modules` may not name a forbidden module

**Landed.** Engine commit `3db0e23`.

The allow-list constrains the import surface. It has never been a grant, and
after this decision it cannot be read as one. Naming a `FORBIDDEN_NAMES` member
is refused at the import, not tolerated until first use.

A forbidden module that parses but is unusable at every site is worse than one
that never parses. Under the permissive reading, a Rune author who writes
`allowed_modules={"os", "json"}` gets a configuration that looks valid, passes
the test that pins it, and then fails much later, inside a subprocess, on
synthesised code it did not write, with `Forbidden attribute access: os.system`
as the only clue. The refusal belongs at the configuration, not at the symptom.

This retires `ALWAYS_FORBIDDEN_MODULES`. `sys` is already a member of
`FORBIDDEN_NAMES`, so a single check - refuse any `FORBIDDEN_NAMES` member
before consulting the allow-list, at both AST validation and runtime import -
covers it. Two mechanisms collapse into one and the special case disappears.
This is a deliberate change to a published contract; the two engine tests that
pin the permissive reading change with it.

### 2. No Rune may reach `subprocess`, and there is no escape hatch

`execute_code` refuses code that reaches `subprocess` or `os`. Permanently. Not
narrowly, not under audit.

The reason is the writer of the code. Every other sandbox in this repository
executes code a human wrote, reviewed, and committed. This one executes code a
model wrote this run from a description the model built from text it read. A
capability granted here is granted to a model that can be steered by anything
that reached its context. An audited hatch would not reduce that exposure; it
would only add a documented route to it.

Where the rule is enforced is split by ownership:

- **The mechanism is the engine's.** `MvgeSandbox` refuses the capability
  outright, for every caller, including `api.sandbox` as handed to every Rune
  through `RuneLifecycle`.
- **The policy is the marketplace's.** A Rune's `allowed_modules` is Rune
  configuration, so the gate that polices it belongs in the repository that owns
  Rune configuration: `mvge-marketplace/tests/test_sandbox_privilege_contract.py`,
  which is
  already collected by that repository's existing pytest run and needs no new
  CI wiring. Neither existing gate can host it, and neither should:
  `mvge-core/scripts/check_marketplace_contract.py` asserts that imports
  resolve, and `mvge-marketplace/tests/test_global_agents_dir_contract.py` is
  scoped to the global layer resolver. This is a third kind of gate and it is
  given a third kind of home.

### 3. The engine-wide fix stands; the per-Rune review becomes the gate

The SOM-14 defect was in the engine's visitor, so the engine-wide fix is the
correct scope and it is not narrowed. The per-Rune obligation is a different
and recurring one, and the gate from decision 2 discharges it continuously:
a new Rune that allow-lists a forbidden module fails the marketplace suite
without anyone needing to remember to look.

### 4. `pathlib` and `urllib` come out of the action privilege set

**Landed.** Marketplace commit `a4f02bf`.

The synthesised-action allow-list is reduced to `{"json", "re"}`.

An allow-list is a budget, and a speculative entry spends it. `os` and
`subprocess` were speculative too, and that speculation was the vulnerability.
Nothing shipped uses the other two: the synthesiser's own prompt instructs the
model to emit `"code_payload": null`, so the executed-code path is the exception
rather than the routine one, and no synthesised payload in `heal-my-goap`
references `pathlib` or `urllib`. The planner's actual output is preconditions
and effects. Keeping them was headroom granted to model-written code in
exchange for a use nobody has made.

The write and egress grants are not merely unnecessary, they are the two
irreversible ones. A written file outlives the run. An exfiltration leaves no
artifact to notice. Neither is recoverable after the fact, which is what
separates them from the capabilities already closed.

If a planning action later needs to read a file or reach a network, the correct
answer is a host-mediated capability with an audit record, not a standard-library
import on a list. That is a larger piece of work and it is not authorised here.
Reversing this decision is a one-line change to one constant.

### 5. The vendored executor copy is deleted

**Landed.** Marketplace commit `5222531`.

`runes/heal-my-goap/mvge_runes_heal_my_goap/sandbox.py` is a near-duplicate of
the engine's module. It has already drifted once and had to be patched in
lockstep, which is the whole argument against keeping it: two copies of a
security control are one finding and two fixes.

It has no dependency justification. `heal-my-goap` already imports from
`mvge_core`, and `GoapEngine` receives the engine's executor through
`api.sandbox`. The vendored copy is reachable only where no executor is
injected, which is the standalone example path. Removing it is consistent with
**Zero Backward Compatibility Burden**: a vendored near-duplicate of an engine
module is a proprietary fallback.

<!-- adr-contract: FORBIDDEN_NAMES, ASTSafetyVisitor, MvgeSandbox, MvgeSandbox.validate_ast, MvgeSandbox.execute_code -->

## Consequences

**Positive**

- An allow-list can no longer be written that looks permissive and is not. The
  failure lands on the configuration.
- One import rule instead of a rule plus a special case. `ALWAYS_FORBIDDEN_MODULES`
  is retired rather than preserved.
- The privilege set of model-written code is `json` and `re`. The two
  irreversible capabilities are no longer granted.
- A new Rune that widens its own privilege fails the marketplace suite.

**Negative**

- This is a breaking change to `validate_ast` and `execute_code`. A caller that
  allow-lists a forbidden module and relies on the import parsing will now be
  refused. That caller does not exist in this ecosystem today.
- A synthesised action that wanted to write a file or make a request will now
  fail at validation. None shipped does.
- Standalone use of `heal-my-goap` without an injected executor loses its
  fallback and must take the engine's executor.

**Neutral**

- The engine's `MvgeSandbox` mechanism is unchanged by decision 1's
  implementation; only the rule that decides which imports reach it moves.
