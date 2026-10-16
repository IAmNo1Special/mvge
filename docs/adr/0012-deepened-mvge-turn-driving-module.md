# ADR 0012: Deepened Mvge Turn-Driving Module

## Status

Accepted

## Date

2026-09-12

## Context

The `Mvge` class (~1059 lines) had become a shallow god module: ~40 public members (14 constructor params, ~19 property aliases, ~20 methods) that pass through to `MvgeHarness`, `MvgeEnvironment`, `RuneLifecycle`, and `TomeLedger` while leaking mutations across the seam (`MvgeState`, `RuneRunner`, `ConfigManager` privates). Two spell-resolution adapters (`_build_spells` in `Mvge` with validation vs `_resolve_spells` in `Harness` append-only) diverged, causing stale removals and unvalidated per-run adds. The `LoopCallbacks` fan-out duplicated fallback logic 9×. Model switching via `set_model_and_realm` existed but lacked the Pi-shaped configured-vs-captured split and emitted no config-change event. Tests poked private internals (`_runner`, `_state`, `_harness`) rather than asserting on the interface.

## Decision

Deepen `MvgeHarness` as the single turn-driving module behind a narrow seam. Shrink `Mvge` to a construction adapter that wires Realm/Tome/Rune/Environment adapters and forwards to Harness.

### 1. Seam owner
`MvgeHarness` owns the turn loop, spell dispatch, compaction, sigil dispatch, and emit fan-out. `Mvge` becomes a thin construction adapter (~100 lines) with no runtime depth.

### 2. Seam shape (Harness interface)

Amended 2026-10-14 by issue #164 (design-it-twice). The signatures first
written here were a strawman: three independent re-derivations agreed they
were wrong in the specific ways §2.4 records. The strawman is kept in the
history of this file; what follows replaces it as the contract.

The seam is three capability protocols plus a thin composing facade. New
file `mvgeos-agent/src/mvgeos_agent/harness/capabilities.py` — the three
protocols below — is the seam. Everything else is implementation detail.

```python
@runtime_checkable
class TurnDriver(Protocol):
    """Owns the hot path: exactly one turn at a time."""

    async def run_turn(self, invocation: SummonerRequest) -> MvgeInvocation: ...
    def steer(self, text: str) -> None: ...  # enqueue-and-drain, sync
    def follow_up(self, text: str) -> None: ...  # enqueue-and-drain, sync
    def abort(self) -> None: ...  # idempotent, sync
    @property
    def turn_in_flight(self) -> bool: ...


@runtime_checkable
class SessionLifecycle(Protocol):
    """Owns slow-path session transactions. Never runs concurrently
    with itself (internal lock); defers to the TurnDriver at turn
    boundaries."""

    async def reload(self) -> ReloadResult: ...
    def reload_spells(self) -> None: ...
    async def switch_tome(self, tome: MvgeTome) -> None: ...
    async def fork_tome(self, entry_id: str | None = None) -> str: ...
    async def checkout_leaf(self, leaf_id: str) -> None: ...
    async def undo(self) -> str | None: ...
    async def compact(self) -> str: ...
    async def close(self) -> None: ...


@runtime_checkable
class AgentConfiguration(Protocol):
    """Owns session configuration. Writes never touch an in-flight turn;
    every mutation emits CONFIG_CHANGE. The in-flight turn's captured
    values are unreachable from here by construction."""

    def set_model_and_realm(self, model: Model, realm: Realm) -> None: ...
    def set_contemplation_level(self, level: ContemplationLevel | str) -> None: ...
    def set_plan_mode(self, enabled: bool) -> None: ...
    def set_enabled_spells(self, spell_names: Sequence[str] | None) -> None: ...
    @property
    def queue_mode(self) -> QueueMode: ...
    @queue_mode.setter
    def queue_mode(self, mode: QueueMode | str) -> None: ...
    @property
    def snapshot(self) -> ExecutionSnapshot: ...  # read-only observability


class MvgeHarness:
    """Thin composition of the three capabilities over one internal
    runtime. Wiring only — no flat forwarding."""

    def __init__(self, *, runtime: _SessionRuntime) -> None: ...
    @property
    def driver(self) -> TurnDriver: ...
    @property
    def lifecycle(self) -> SessionLifecycle: ...
    @property
    def configuration(self) -> AgentConfiguration: ...
```

Callers take the capability they need: the TUI takes a `TurnDriver`, the
file watcher takes a `SessionLifecycle`, the settings UI takes an
`AgentConfiguration`. `MvgeAgent` protocol methods become one-line forwards
(`steer` → `driver.steer`, `reload` → `lifecycle.reload`, …); `Mvge` keeps
construction only. `MvgeState` moves to `harness/_state.py`, unexported —
internal implementation, never in a protocol signature.

`ExecutionSnapshot`: `configured_model`, `captured_model`,
`configured_realm`, `captured_realm`, `contemplation_budget`,
`active_spell_count`, plus `mana_used`, `is_channeling`, `spell_version`,
`turn_in_flight`, `invocation_count`, `spell_names` (read-only polling
surface for CLI/GUI/tests; everything else is on the event stream).

#### 2.1 Reload without Mvge re-growing

`Mvge.reload()` becomes `return await self._harness.lifecycle.reload()`.
The transaction (turn-boundary queue, serialization lock, candidate build,
synchronous swap, watcher handover with bounded retry, one audit record
per execution) lives in the lifecycle implementation with its
construction-injected collaborators (rune-lifecycle factory, environment
builder, provider registry, audit writer). Because the driver reads
per-turn from the shared runtime, reload no longer rebuilds the harness —
the swap is reference updates on the runtime.

#### 2.2 Spell refresh

`SessionLifecycle.reload_spells()` re-runs validated spell resolution and
refreshes the spell cache. The per-turn read consults
`RuneRunner.spell_version`: unchanged version → cache hit (no
`inspect.signature`, no `RuneSpellWrapper` churn); changed → rebuild cache
and index. The legacy append-only merge is deleted.

#### 2.3 State ownership

After `Mvge.initialize()` returns, the sole writer of every state field is
the harness package: `TurnDriver` owns turn-scoped fields (invocations
turn appends, `is_streaming`, queues, `mana_used`, captured model/realm),
`SessionLifecycle` owns session-scoped fields (`system_prompt`, spell
cache/index, `rune_runner`, `agent_tome`, environment), `AgentConfiguration`
owns config-scoped fields (configured model/realm, contemplation, queue
mode, spell filters). Cross-capability coordination goes through explicit
narrow channels (`turn_in_flight`, an `on_turn_end` hook), never through
reaching into another capability's fields. The `CompactionRunner` grows a
real `rebind(realm=…, model=…, tome=…)` method instead of being poked.

#### 2.4 Re-derivation notes (why the strawman was wrong)

1. `run(stream_fn, model, contemplation_level, signal, prompt)` kept the
   Realm outside the seam and let `Mvge._make_stream_fn` close over
   `state.spells`/`state.system_prompt` — state flowing out through a
   closure. Re-derived: `run_turn(invocation)` takes no `stream_fn`; the
   driver builds the provider closure per turn from values it captured
   itself.
2. `steer(invocation) -> list[MvgeInvocation]` — wrong shape. Steering is
   asynchronous (arrives at the next drain point); a synchronous return
   implies the caller waits for a turn. Re-derived: `steer(text) -> None`,
   enqueue-and-drain. (The implementation already had this shape;
   the ADR was wrong and the code was right.)
3. `with_model(model) -> ContextManager` — zero adapters justify the seam.
   Per the one-adapter-means-hypothetical rule it is explicitly dropped
   until a real caller exists.

Full comparison of the three independently derived designs is kept in the
Appendix of this ADR.

### 3. State ownership
All `MvgeState` mutation moves behind the Harness seam. `MvgeState` becomes internal implementation; callers (CLI, GUI, tests) assert on `MvgeEvent` stream and returned `MvgeInvocation`s. Private-poking tests deleted (replace-don't-layer).

### 4. MvgeEnvironment
Remains a separate construction adapter (respects ADR-0009 two-layer split). `Mvge` calls `MvgeEnvironment.resolve()` at construction and passes the resolved prompt/config to Harness.

### 5. Spell resolution
- Construction-time validated resolution (single source of validation, rename, filter) as harness-internal spell policy; `Mvge` supplies discovery inputs, not the built set.
- `SessionLifecycle.reload_spells()` re-runs validated resolution and refreshes the spell cache + index; called from Rune watcher reload and manual `load_runes()`.
- The per-turn spell read consults `RuneRunner.spell_version`: unchanged version → cache hit (no per-turn rebuild); changed → rebuild cache and index. The legacy append-only merge is deleted.

Preserves all dynamic Rune behavior (adds mid-Tome work; removals now work; validation divergence deleted).

### 6. Sigil dispatch
Single dispatch table inside Harness with one fallback rule: 9 `SigilHook` callbacks mapped to `LoopCallbacks`, optional, never-raising. Callers supply a Rune runner or nothing — no per-callback overrides.

### 7. Emit + Tome record
All four effects (State reduction, event bus, Sigil map, Tome record on `MESSAGE_END`) internal to Harness. Observable surface = `MvgeEvent` stream + returned `MvgeInvocation`s. `tome_service.ledger` reads happen before/after runs, never mid-turn.

### 8. Model switching
Pi-shaped: Harness holds `configured_model`/`configured_realm`; each `run()`/`turn` captures a snapshot for the provider call. `set_model_and_realm` updates configured + emits `CONFIG_CHANGE` event + Tome `model_switch` entry; never touches the in-flight Channeling stream. (Amended 2026-10-14 by issue #164: the per-turn override `with_model` was explicitly dropped — zero adapters justify the seam.)

### 9. Config change event
Any config setter (`set_model_and_realm`, future `set_contemplation`, `set_tools`) emits `MvgeEventType.CONFIG_CHANGE { model?, realm?, contemplation?, tools? }` alongside domain-specific events.

## Consequences

### Positive
- **Locality**: Spell veto bugs, compaction races, sigil ordering, model-switch in-flight guarantees concentrate in one module.
- **Leverage**: CLI (`repl.py`), GUI (`agent_service.py`), and tests share one narrow seam.
- **Testability**: Harness tests assert on `run/steer/abort` + Events + Invocations; hermetic `StreamFn` replaces private poking.
- **Deletion test passes**: Removing the old pass-throughs concentrates ~800 lines of real depth.
- **Pi parity**: Matches `AgentLane` configured-vs-captured split + `config_update`; exceeds on spell validation centralization and sigil table deduplication.
- **Dynamic Runes preserved**: Adds mid-Tome work; removals fixed; version guard avoids rebuild cost.

### Negative
- Migration effort: ~15 files touch the old seam (CLI, GUI, tests, installer, runes loader).
- `Mvge` callers must migrate from `agent._state.xxx` to `agent.harness.snapshot.xxx` or Event stream.
- `SessionLifecycle.reload_spells()` is the new seam; callers using Rune watcher must adopt it.

## Contract

Symbols this decision commits to. All of these exist today.

<!-- adr-contract: MvgeHarness, ExecutionSnapshot, LoopCallbacks, CONFIG_CHANGE, reload_spells, spell_version, TurnDriver, SessionLifecycle, AgentConfiguration -->

**Contracted by #165.** The §2 Split Capabilities interface is implemented:
`TurnDriver`, `SessionLifecycle`, and `AgentConfiguration` are runtime-checkable
protocols; `MvgeHarness` implements all three and exposes them via `.driver`,
`.lifecycle`, and `.configuration`. `refresh_spells` was removed from the
marker — the harness is now the sole spell builder, consulting
`RuneRunner.spell_version` directly.

| Decided (issue #164) | Status |
| --- | --- |
| `TurnDriver`, `SessionLifecycle`, `AgentConfiguration` (new §2) | decided, implemented by #165 |
| `TurnDriver.steer(text) -> None` | decided: implemented by #165 (shape corrected from the strawman's `steer(invocation) -> list`) |
| `TurnDriver.abort() -> None` | decided: implemented by #165 (ownership moves from `Mvge` to the driver) |
| `MvgeHarness.with_model` | decided: explicitly dropped — zero adapters justify the seam |

`MvgeState` was removed from the marker: §3's *"make `MvgeState` internal"*
is now decided, not merely unmet — it becomes `harness/_state.py`,
unexported, never in a protocol signature. (Convergence note from the
#164 designs: the GUI's `agent_service.py` writes target the GUI's own
`AppState` view-model, not `MvgeState` — that leak-table row was a grep
false positive, verified against the tree.)

## Migration Plan (TDD)
1. Add `ExecutionSnapshot` + `config_change` event to Harness (tests first).
2. Implement `spell_version` consult + `SessionLifecycle.reload_spells()` (tests first).
3. Collapse `_resolve_spells` → delegation; delete duplicate validation logic.
4. Move all `MvgeState` mutation behind Harness; make `MvgeState` internal.
5. Replace `Mvge` with construction adapter; delete aliases/properties.
6. Update CLI/GUI/tests to new seam; delete private-poking tests.
7. Verify coverage floor holds; run full suite.

## ADR References
- Respects ADR-0007 (single JSONL Tome), ADR-0008 (NiceGUI), ADR-0009 (two-layer scaffolding).
- Builds on ADR-0005 (TDD) — migration is test-first.

## Appendix: §2 re-derivation record (issue #164)

Design-it-twice pass, 2026-10-14. Three interface designs were produced
independently by parallel designers from the same grounding (repo at
v0.7.3, this ADR, issue #164). Malcom adopted **C — Split Capabilities**;
§2 above is that design. This appendix keeps the comparison so a future
reader can see what was rejected and why.

### Points of convergence (settled — all three agreed independently)

1. **The GUI never writes engine state.** Issue #165's leak table lists
   `agent_service.py` writes to `state.is_channeling` / `state.total_mana_used`,
   but that `state` is the GUI's own `AppState` view-model
   (`mvgeos-gui/src/mvgeos_gui/state.py:315`), not `MvgeState`.
   `grep MvgeState mvgeos-gui/src mvgeos-cli/src` returns nothing. Verified
   against the tree, not just asserted. The GUI's only engine coupling is
   method calls plus the `MvgeEvent` stream — a seam-shape question, not a
   mutation leak. #165's criterion 1 loses a row.
2. **The CLI never writes engine state either.** It reads `agent._state`
   (invocations/spells/mana_used) in `formatting.py` only, read-only, on
   error-reporting paths. Those reads migrate to the snapshot.
3. **Steering is enqueue-and-drain, synchronous, never waiting.**
   `steer(text: str) -> None`. All three rejected the strawman's
   `steer(invocation) -> list[MvgeInvocation]`.
4. **Abort is synchronous and idempotent.** `abort() -> None`.
5. **`with_model` is explicitly dropped.** Zero adapters justify it
   (one-adapter-means-hypothetical).
6. **The Realm goes behind the seam.** The `StreamFn` closure is built
   inside the harness per turn from harness-owned state;
   `Mvge._make_stream_fn` dies. No outside closure over state survives.
7. **Reload moves behind the seam** via an injected construction
   collaborator (each design named it differently; the shape is the same:
   the harness owns the transaction order, the collaborator owns candidate
   construction).
8. **`MvgeState` becomes internal implementation.** Observability is the
   read-only `ExecutionSnapshot` plus the `MvgeEvent` stream plus returned
   `MvgeInvocation`s. Private-poking tests are deleted, not rewritten.
9. **`_spell_version` is finally consulted** (the counter whose docstring
   already claims the harness uses it); the legacy append-only merge in
   `_resolve_spells` is deleted outright.

### The three designs

**A. Turn-Driver Seam** (maximalist single interface): one fat
`MvgeHarness` with ~20 methods; `Mvge` shrinks to a `harness` property;
the `MvgeAgent` protocol is deleted. Reload orchestration in the harness;
candidate construction in a new internal `RuntimeBuilder`; validated spell
construction in a new internal `SpellPolicy`. Honest cost: 9-param
constructor, and the `harness` property's internality rests on convention.

**B. Command + Event Seam** (inbox vocabulary): the seam is data — frozen
command dataclasses (`RunCommand`, `SteerCommand`, `AbortCommand`,
`ReloadCommand`, … ~15 types). `enqueue(cmd) -> CommandReceipt` is sync and
non-blocking; `execute(cmd)` is async and awaited, with `@overload`s per
command. Collaborators: `RealmBinding`, `ModelResolver`, `ReloadPlanner`.
Thinnest points: the `ReloadPlanner` and the load-bearing `@overload`s.

**C. Split Capabilities** (adopted): three `@runtime_checkable`
protocols — `TurnDriver`, `SessionLifecycle`, `AgentConfiguration` — plus
a thin composing facade; callers take the capability they need. Thinnest
points: the shared `_SessionRuntime` bag (field ownership is documented
discipline) and the driver→`on_turn_end`→lifecycle chain for
reload-at-boundary.

### Comparison

**Depth.** All three keep turn-driving depth inside the harness package.
B adds dispatch depth (handlers must stay small). C distributes depth
across three single-job files. A concentrates it in one class again.

**Locality.** C wins: a reload bug lives in `lifecycle.py`, a config bug in
`configuration.py`, a turn bug in `driver.py` — and narrow dependents mean
a reload change cannot ripple into turn-driving callers. B is close. A is
weakest: one class owns everything, the shape that produced the
scattered-ownership bugs in the first place, relocated.

**Seam placement.** A: method calls on one object (simplest migration,
thinnest discipline). B: data — serializable, loggable, testable as
values; the enqueue/execute split makes sync-vs-async structural. C: three
protocols — narrowest dependents, `@runtime_checkable`, one seam file.

**The four constraints.** All three satisfy all four: (a) Realm behind the
seam — every design builds the `StreamFn` inside the harness;
(b) enqueue-and-drain steering; (c) every CLI/GUI/test mutation
enumerated with a home or declared internal; (d) reload behind the seam
with an injected construction collaborator.

### Why C

Best locality — the failure class that produced #166 was scattered
ownership, and C is the only design giving each ownership domain its own
module and protocol. Narrowest dependents. Checkable seam. Mechanical
caller migration via protocol forwards. B was the runner-up (serializable
command vocabulary, structural sync/async split). A was not recommended:
it satisfies the letter of the issue while re-creating the concentration
the ADR was written to dissolve.