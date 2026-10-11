# MvgeOS

MvgeOS is an operating system for AI agents: a Mvge (agent) casts Spells (tools) against models served by Realms (model providers), conversations persist as Tomes, and capabilities extend through Runes.

## Language

**Mvge** (pronounced "Mage"):
An autonomous agent that acts on behalf of a Summoner by casting Spells against models served by Realms.
_Avoid_: Agent (as a spoken term), assistant, bot

**Spell**:
A tool a Mvge can cast during a run.
_Avoid_: Tool (as a spoken term), function, action

**Realm**:
A model provider — a service that serves models to Mvges. OpenRouter is a Realm that routes requests onward to many upstream providers. For a direct Realm (e.g., Google), the Realm and the upstream provider are the same organization; the distinction only matters for router Realms.
_Avoid_: Backend, endpoint

**Provider** (plain English):
The organization that provides a model (e.g., Google, Anthropic, NVIDIA). Derived from the model-ID prefix (`google/gemini...` → provider = "google"). Not a stored field on `Model` — computed from the ID.
_Avoid_: Realm (do not conflate with the abstraction)

**Mana**:
Tokens — the unit in which model usage is measured and budgeted.
_Avoid_: Tokens (as a spoken term), credits, units

**Mana Pool**:
The model's context window — the maximum number of Mana (tokens) that fit in a single request context. Renamed from `context_window` in code.
_Avoid_: Context window (as a spoken term)

**Mana Budget**:
The cap on total Mana the Mvge can spend during a single run. **Not currently implemented.** Enforcement was removed from the loop because Pi has no equivalent — Pi handles context pressure with compaction, not by aborting the run. It will return as a user-installed Rune once the loop inversion adds a stop-capable sigil (no sigil can halt a run today; only `BEFORE_SPELL_CAST` can veto). Distinct from Contemplation Budget, which is a per-request Realm parameter, not a run-level cap.

**Mana Used**:
Cumulative Mana consumed during a run (`mana_used`). Carried on every `MESSAGE_END` and `TURN_END` event; reduced into `MvgeState` by `MvgeHarness`. Read by compaction to size the Mana Pool.

**Tome**:
A single persisted conversation between a Summoner and a Mvge, recorded as an append-only sequence of Invocations. Per the Protocol Boundary Pattern, stored on disk in standard session directories (`~/.agents/sessions/`, `.agents/sessions/`) using standard JSONL v1 wire format (`type: "session"`, `parentSession`) — Pi-inspired, but not byte-compatible with Pi session files. "Session" is the standard protocol term at filesystem, wire, and CLI command boundaries (`mvgeos session`); "Tome" is the domain term inside the persona, Python model, and user presentation (`mvgeos tome` alias).
_Avoid_: Session (as an internal domain/persona term; use Tome internally and Session at protocol boundaries), conversation, chat

**Invocation**:
Any single message in a Tome's transcript — a SummonerRequest, an MvgeResponse, or a SpellResultMessage.
_Avoid_: Message (as a spoken term), turn, event

**Invocation Transcript**:
The rendered structure of one Mvge Invocation — its ordered parts (contemplation, execution steps, text, artifacts) as shown to the Summoner. Parts are authoritative; plain-text content and contemplation lists are projections of the parts.
_Avoid_: Bubble builder, parts assembler

**Leaf**:
The active position marker in a Tome — an explicit entry pointing to the current tip of a conversation branch. The "current position" is the entry the Leaf points to.
_Avoid_: Head, cursor, pointer

**Fork**:
A new Tome created by branching from an entry in a parent Tome. The child Tome's header stores a reference to the parent Tome (`parentSession`). The branch path (ancestors up to the chosen entry) is copied into the child.
_Avoid_: Branch (as a noun — the branch is implicit in the tree; Fork is the action and result)

**Summoner**:
The user on whose behalf a Mvge acts. A SummonerRequest is the Summoner's input to a run.
_Avoid_: User (as a spoken term), human, operator

**Rune**:
An extension: a packaged unit that hooks into a Mvge's lifecycle and can register Spells, commands, shortcuts, and Realms. Per the Protocol Boundary Pattern, discovered on disk from standard extension directories (`~/.agents/extensions/`, `~/.agents/agents/{agent}/extensions/`, `{cwd}/.agents/extensions/`). Built-in Runes live at `~/.agents/agents/<agent>/runes/<name>/` (e.g., `skill_evolution`). "Extension" is the standard protocol term at filesystem and CLI command boundaries (`mvgeos extension`); "Rune" is the domain term inside the persona, Python model, and user presentation (`mvgeos rune` alias).
_Avoid_: Extension (as an internal domain/persona term; use Rune internally and Extension at protocol boundaries), plugin, addon, Skill

**Skill**:
A capability pack discovered per `agentskills.io` via `SKILL_SCOPES` (`PROJECT:.agents/skills`, `USER:~/.agents/skills`, `AGENT:~/.agents/.mvgeos/{agent}/skills`): a directory named `^[a-z0-9]+(-[a-z0-9]+)*$` (1-64 chars, must match `name` in frontmatter) containing `SKILL.md` (YAML frontmatter `name`/`description` 1-1024 chars + markdown instructions) and per WikiSkill `PURPOSE.md` (`Origin` + `Patterns Addressed` + `Evolution History`). Disclosed progressively (catalog → full instructions → resources), never executed directly. Distinct from Rune (executable extension) and Spell (executable tool).
_Avoid_: Tool, Spell, Rune, plugin

**Skill Evolution Store**:
The persistent, compounding store that compiles `Raw Experience` into `skill_evolution/index.md` (catalog `[name]` linking to `patterns/name.md`: specific desc), `skill_evolution/logs.md`, `skill_evolution/skill-impact.md` (harness-appended diff + `R_val` + `Accepted/Rejected`), and `skill_evolution/patterns/*.md` (10-30 lines: what, root cause, commands, workarounds) via `SkillEvolutionMaintainer` patch ops (`append`/`replace`/`insert_after` exact target). Formerly referred to as Knowledge Store; updated to eliminate ambiguous knowledge naming.
_Avoid_: Knowledge, wiki, memory, store

**Raw Experience**:
Immutable execution traces `raw_experience/iter_<k>/<trace>.json` (`T_train,k` per paper `§3.1`), the sole input to `SkillEvolutionMaintainer` alongside existing `Skill Evolution Store`. Never mutated, only appended.
_Avoid_: Raw Knowledge, raw, traces, history

**skill_evolution Rune**:
The Rune at `~/.agents/agents/coding_mvge/runes/skill_evolution/` that is our implementation of `WikiSkill: Compiling Agent Experience into Persistent Knowledge for Skill Evolution` (`arxiv:2608.27454`). It harvests `Raw Experience` via `AFTER_INVOCATION`, consolidates into `Skill Evolution Store` (`ExperienceConsolidator`), and evolves `Skills` atomically (`SkillEvolutionEngine` with same-location saves via `SkillManifest.path`). Provides Spells `skill_evolution_consolidate`/`skill_evolution_export`, never mutates `.skill-lock.json` (reproducibility lock `~/.agents/.skill-lock.json` per `vercel-labs/skills`).
_Avoid_: knowledge_skill, wiki_skill, WikiSkill (as a spoken term for the rune)

**Channeling**:
Streaming a response from a Realm — the act of receiving tokens incrementally. The protocol method is `channel()` (formerly `stream()`); the renderer is `ChannelRenderer`.
_Avoid_: Streaming, stream (as a spoken term)

**Sigil**:
A callback a Rune registers on a lifecycle hook. The hook points themselves are `SigilHook` enum values (e.g., `BEFORE_SPELL_CAST`, `AFTER_PROVIDER_RESPONSE`).
_Avoid_: Callback (as a spoken term), hook (for the callback — the hook is the lifecycle point)

**Contemplation**:
The depth of reasoning the Mvge applies during a run, requested by the Summoner (or set by the Mvge's defaults) and conveyed to the Realm as reasoning effort. Optionally capped by `contemplation_budget` — a per-request parameter sent to the Realm (Pi's `ThinkingBudgets`), not a run-level cap. Do not conflate with Mana Budget.
_Avoid_: Reasoning (as a spoken term), thinking (for the concept)

**MvgeOS GUI**:
The native desktop interface for MvgeOS, built on NiceGUI with PyWebView, providing a 1:1 visual experience for multi-turn Summoner interactions, live Spell tracking, Git diff inspection, and Tome navigation.
_Avoid_: Web client, frontend dashboard


## Loop architecture

**Loop Core** (`run_loop`):
The stateless heart of the turn cycle. Takes a frozen `LoopContext`, a `StreamFn`, an `emit` sink, and `LoopCallbacks`; returns the new Invocations. The loop **drives** the Realm: it calls `StreamFn` once per turn with the running transcript, channels the response, casts Spells, and goes round again while Spell results, steering, or follow-ups remain. Imports no `Realm`, no `RuneRunner`, no `SigilHook`, no `MvgeTome`, no `EventBus` — all outside contact flows through `emit` and the callbacks. Mirrors Pi's `runAgentLoop`.

**Stream Fn**:
`Callable[[list[MvgeInvocation]], AsyncIterator[RealmResponse]]` — one Realm request per call. Built by `BaseMvge._make_stream_fn` from a Realm and Model. Keeps the provider layer out of the core; tests supply a plain function returning canned responses. Mirrors Pi's `StreamFn`.

**Loop Context**:
Frozen snapshot of everything the core reads (~9 fields). Diverges from Pi, which passes a mutable `AgentContext`; the frozen dataclass is what enforces the seam.

**Loop Callbacks**:
The value-returning extension points: `transform_context`, `before_realm_headers`, `before_spell_cast` (veto), `after_spell_result`, `should_stop_after_turn`, `prepare_next_turn`, plus the queue drains `get_steering_messages` and `get_follow_up_messages`. Every callback is optional and must not raise — return a safe fallback instead. `MvgeHarness` honours this contract when building them from a Rune runner. Mirrors Pi's `AgentLoopConfig` callbacks.

**Steering** / **Follow-up**:
Steering Invocations are injected between turns while the Mvge is still working; follow-ups resume it after it would otherwise settle. The loop drains `MvgeState.steer_queue` after each turn that cast no Spells, and `followup_queue` at the outer-loop boundary. Each queue's drain strategy is controlled by `MvgeState.queue_mode` (`QueueMode.ALL` drains the entire queue, `QueueMode.ONE_AT_A_TIME` drains one message at a time, leaving the rest queued for subsequent drain points — matching Pi's `PendingMessageQueue.drain()` semantics).

**MvgeHarness**:
The session-aware operational owner of the agent loop. Orchestrates session lifecycle, prompt dispatch, compaction history synchronization, event fan-out, and turn driving by executing `run_loop` directly behind a deep interface. Mirrors Pi's `AgentHarness`.

**Emit Sink**:
The single async channel out of the core: `Callable[[MvgeEvent], Awaitable[None]]`. `MvgeHarness._emit` fans one event out to four effects — `MvgeState` reduction, the event bus, the mapped Sigil, and Tome recording. Recording happens only on `MESSAGE_END`, which the core emits for Summoner, Mvge, and Spell-result Invocations alike (Pi's one-recording-point model).

**Spell Dispatcher** (`SpellDispatcher`):
The deep module responsible for executing tool call batches concurrently (`asyncio.gather`) or fallback sequential execution (`mvgeos_core/dispatcher.py`). Evaluates `before_spell_cast` vetoes and `after_spell_result` transforms per task, preserves assistant request order, isolates exceptions into `SpellResultMessage(is_error=True)`, and evaluates batch termination (`terminate` flag) matching Pi's `executeToolCalls`.

**MvgeEnvironment**:
The deep module responsible for resolving agent configuration, system prompts, guidelines, and runtime snapshot introspection (`mvgeos_agent/environment.py`). Consolidates layered config loading, prompt resolution, and diagnostic collection behind a single `resolve()` seam. Mirrors Pi's `AgentSessionServices` + `ResourceLoader`.

**Two-Layer Invariant Scaffolding**:
The architecture that strictly decouples agent persona and behavioral guidelines (Layer 1: stored in colocated `SYSTEM.md` and `GUIDELINES.md`) from dynamic, engine-rendered Agent-Computer Interface scaffolding (Layer 2: `Active spells:`, `Environment:`, `Self-Modification & Customization:` on-demand reference pointers, and `<project_context>`).

**Spell Auto-Discovery**:
The engine capability in `discover_spells_from_dir` that discovers tools from a caller-adjacent `spells/` directory via 3-tier precedence (`__all__` in `__init__.py` -> function matching file stem -> single public function -> `SpellDiscoveryError`). Allows zero-boilerplate agent creation (`root_mvge = Mvge(name="...")`).



## Compaction

**Compaction**:
Replacing the earlier part of a transcript with a summary once it crowds the Mana Pool. Triggered after each Mvge Invocation, matching where Pi checks it. Pure measuring and splitting live in `mvgeos_agent/compaction.py`; the side effects (Realm call, events, Tome record) live in `CompactionRunner`. On any failure the run continues uncompacted — compaction is a recovery mechanism and must never become a new failure mode.

**Cut Point**:
Where a transcript is split. A Spell result is never a valid cut point: it must stay with the Invocation that requested it.

**Retained Tail**:
The recent Invocations kept verbatim after compaction. Persisted on the Tome's compaction entry alongside the summary and `manaBefore`, so context can be rebuilt without replaying what the summary replaced.

**Realm.complete()**:
A non-channelled Realm request, used for standalone calls that are not part of a Tome's transcript. Takes wire-format messages rather than Invocations, and deliberately offers no Spells. Compaction summaries use it.

## Retry

Two layers in `mvgeos_provider/retry.py`, mirroring Pi:

- **`retry_realm_request`** — one HTTP request. Retries 408/409/429/5xx, honours `Retry-After` (failing hard beyond 60s rather than stalling), backs off exponentially capped at 8s with downward jitter, and respects an `x-should-retry` header.
- **`retry_invocation`** — a whole Invocation. Realms report transient trouble as a `RealmResponse` carrying an error rather than raising, so this layer classifies the response: a Realm-set `error_code` is authoritative, otherwise the error prose is matched against deterministic patterns (quota, billing) before transient ones.

Both accept a `signal` that is currently ignored, reserved so the abort work does not reshape the interfaces.

## Default Model

The system-wide default model across all MvgeOS packages and test suites is `opencode/space-bunny-free` (`DEFAULT_MODEL` in `mvgeos_core.constants`, with the serving Realm recorded as `DEFAULT_REALM` in `mvgeos_provider.realms`).

It is a free OpenCode Zen endpoint, chosen because the previous default — an OpenRouter free endpoint — was at capacity on three of three clean-machine runs of the documented first task, which made the quickstart's promise false. Resolving it needs the `opencode-realm` Rune and an `OPENCODE_API_KEY`; the CLI offers to install the Rune when it is missing. See ADR-0015.

## Operational Capabilities

- **Abort**: Implemented via `AbortController`, `AbortSignal`, and `BaseMvge.abort()`, providing cooperative cancellation across channeling and spell execution.
- **Queue modes**: `ONE_AT_A_TIME` drains one message per turn; `ALL` drains the entire queue. Both `queue_mode` and `one-at-a-time` semantics implemented.
- **Command Dispatcher** (`CommandDispatcher`): The deep module in `mvgeos-agent` responsible for parsing, routing, and executing interactive slash commands against the `MvgeAgent` interface, returning structured `CommandOutcome` values across the seam to Summoner presentation adapters.

