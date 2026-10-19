# ADR 0022: Sub-Mvge Summoning, Persistent Sub-Sessions, and Reactive Wakeup Seam

## Status

Accepted

## Date

2026-10-19

## Context

Benchmark agent frameworks take three incompatible approaches to sub-agent delegation:

1. `google/adk-python` (`AgentTool` and `transfer_to_agent`) runs sub-agents synchronously in an ephemeral `InMemorySessionService`, discarding the child session transcript on exit and failing if a child hits a long-running or human-in-the-loop tool call.
2. `anomalyco/opencode` and `huggingface/tau` spawn scoped child sessions (`@explore`, `@general`, `allowed_tool_names`), but couple the sub-agent definitions into the core application.
3. Antigravity provides a non-blocking, conversational sub-agent triad (`invoke_subagent`, `send_message`, `manage_subagents`) where every child has a persisted transcript, reports live tool-call status (`stateDetail`), wakes the parent reactively on completion without polling, and stays in an `idle` state so the parent can send follow-up instructions to the same warm child context.

In MvgeOS today, `mvgeos-core` and `mvgeos-agent` have no kernel mechanism for child Mvge delegation, forcing marketplace Runes such as `skill-evolution` (`proposer_mvge`) to construct ad-hoc child `Mvge` instances without shared `ApprovalPresenter` locking, parent `AbortSignal` propagation, event attribution, or standardized Tome persistence.

## Decision

We split **Summoning** across the `mvgeos` (kernel mechanism) and `mvgeos-marketplace` (capability policy) seam, adopting the Antigravity-style persistent, reactive sub-Mvge model:

1. **Kernel `SubMvgeManager` Seam (`mvgeos-agent` & `mvgeos-tome`)**:
   - `mvgeos-agent` owns the lifecycle mechanism (`SubMvgeManager`) for spawning, messaging, inspecting, and terminating child `Mvge` instances from a parent `Mvge` or `MvgeHarness`.
   - **Always-Persisted Sub-Session Tomes**: Every summoned child `Mvge` persists its full Tome transcript at `~/.agents/sessions/subsessions/<child_tome_id>.jsonl` (resolved via `mvgeos_core.layers.sessions_dir() / "subsessions"`), never ephemeral in-memory storage. Storing child Tomes under `subsessions/` keeps top-level `mvgeos tome list` uncluttered while preserving an immutable record of every child invocation, tool call, and error for `mvgeos-gui` drill-down (`conversation://<child_tome_id>`), `session-search` FTS5 indexing, `eval-bridge` trajectory scoring, and `skill-evolution` experience harvesting.
   - **Post-Rune Spell Scoping (`allowed_spells`)**: `Mvge` and `MvgeHarness` accept `allowed_spells: frozenset[str] | None = None`, applied *after* Rune spell composition and re-applied across `reload()` (matching `tau`'s `_filter_tools` guarantee) so read-only explorer sub-Mvges cannot access mutating Rune spells or mention them in `Active spells:`.
   - **Shared Lifecycle & Non-Closing Parent Runes**: Child Mvges share the parent's `RealmRegistry` and inherit `AbortSignal` cancellation (`AbortSignal.any([parent_signal, child_signal])`), while setting a non-closing guard on shared parent Rune resources (matching `adk-python`'s `set_skip_closing_plugins(True)`) so closing or finishing a child Mvge never tears down parent MCP connections or telemetry exporters.
   - **Event Lineage & Approval Presenter Mutex**: `MvgeEvent` and `ApprovalRequest` carry optional `sub_mvge_id: str | None` and `parent_spell_cast_id: str | None` attribution fields. `RuneRunner.request_approval` serializes interactive prompts through an `asyncio.Lock` on the presenter slot so concurrent child Mvges never interleave terminal or GUI approval dialogs.
   - **Reactive Wakeup & Conversational Continuation**: When a child Mvge finishes a run, it transitions to `idle` (retaining its warm Tome context) and pushes a completion notification into the parent `MvgeHarness`'s `followup_queue` / `steer_queue` so the parent wakes reactively without polling loops.

2. **Marketplace `submvge-bridge` Rune (`mvgeos-marketplace`)**:
   - Concrete sub-Mvge personas (`self`, `research`, `planner`, `proposer`), deterministic workflow compositions (`sequential`, `parallel`, `loop`), workspace isolation modes (`inherit` vs. `worktree`), and the three Summoning Spells (`summon_mvge`, `message_mvge`, `manage_mvges`) live in `mvgeos-marketplace/runes/submvge-bridge`.

## Contract

The kernel anchors for session persistence, spell filtering, turn driving, and approval presentation live in `mvgeos-core`, `mvgeos-tome`, `mvgeos-runes`, and `mvgeos-agent`:

- `sessions_dir` in `mvgeos_core.layers` resolves the root session directory under which `subsessions/` resides.
- `Mvge.set_enabled_spells` and `MvgeHarness.set_enabled_spells` filter the active spell surface.
- `MvgeHarness.steer` and `MvgeHarness.follow_up` enqueue reactive wakeup invocations on the parent harness.
- `RuneRunner.set_approval_presenter` and `RuneRunner.request_approval` mediate interactive approval requests.

<!-- adr-contract: sessions_dir, Mvge.set_enabled_spells, MvgeHarness.set_enabled_spells, MvgeHarness.steer, MvgeHarness.follow_up, RuneRunner.set_approval_presenter, RuneRunner.request_approval -->

## Consequences

- Every sub-Mvge execution leaves a permanent, inspectable JSONL Tome in `~/.agents/sessions/subsessions/` linked to the parent `SpellResultInvocation`.
- Parent Mvges can launch parallel research or coding sub-Mvges without polling and can send follow-up instructions to idle sub-Mvges via `message_mvge`.
- Concurrent child Mvges cannot corrupt terminal I/O during interactive `ApprovalGate` prompts.
