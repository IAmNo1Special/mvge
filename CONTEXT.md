# MvgeOS

MvgeOS is an operating system for AI agents: a Mvge casts Spells against models served by Realms, conversations persist as Tomes, and capabilities extend through Runes.

## Language

### Core Persona & Turn Cycle

**Mvge** (pronounced "Mage"):
An autonomous agent that acts on behalf of a Summoner by casting Spells against models served by Realms.
_Avoid_: Agent (as a spoken term), assistant, bot

**Active Mvge**:
The concrete Mvge identity an invocation runs, resolved either from an explicit Summoner request or by adopting the single installed Mvge when none is specified.
_Avoid_: Default agent, boilerplate

**Summoner**:
The human or external orchestrator on whose behalf a Mvge acts.
_Avoid_: User (as a spoken term), human, operator

**Summoning**:
Delegating a scoped sub-task from a parent Mvge to a child Mvge that runs with its own persisted sub-session Tome, a restricted Spell set, live status attribution, and inherited cancellation and approval channels.
_Avoid_: Sub-agent spawning, ephemeral worker, handoff

**Profile**:
A named, curated bundle of a Mvge and complementary Runes (such as `minimal`, `coding`, or `autonomous`) that provisions a complete runtime tier.
_Avoid_: Preset, flavor, bundle

**Spell**:
A callable capability a Mvge can cast during a run to observe or mutate its environment, accompanied by a Summoner-visible action phrase and summary explaining why the Spell is being cast.
_Avoid_: Tool (as a spoken term), function, action

**Spell Context**:
The engine-supplied runtime envelope injected into a Spell execution carrying the cast identifier, cooperative cancellation signal, active Execution Backend, workspace root, and session metadata without exposing those parameters in the model-facing schema.
_Avoid_: ToolContext, global state, hidden arguments

**Realm**:
A model service that streams or completes Invocations for a Mvge, either directly for a single model family or as a router across many upstream Providers.
_Avoid_: Backend, endpoint, client

**Provider**:
The upstream organization that authors and hosts a model (derived from the model identifier prefix), distinct from the Realm that serves the request.
_Avoid_: Realm

**Channeling**:
Receiving a model response incrementally as a live stream of text, contemplation, and Spell cast deltas.
_Avoid_: Streaming (as a spoken term)

**Contemplation**:
The depth of reasoning effort a Mvge applies before acting during a turn.
_Avoid_: Thinking, reasoning effort, chain-of-thought

**Steering**:
A Summoner Invocation injected between turns while a Mvge is actively working to redirect the current run in flight.
_Avoid_: Interruption, mid-run prompt

**Follow-up**:
A queued Summoner Invocation that automatically resumes a Mvge after the current run would otherwise settle.
_Avoid_: Queued prompt, continuation

### Mana & Context Management

**Mana**:
The unit in which model context consumption and generation cost are measured.
_Avoid_: Tokens (as a spoken term), credits, units

**Mana Pool**:
The maximum amount of Mana that fits within a model's single request context.
_Avoid_: Context window (as a spoken term)

**Mana Used**:
The cumulative Mana consumed across turns within a run.
_Avoid_: Token count, usage total

**Mana Budget**:
A run-level cap on total Mana a Mvge may spend before halting.
_Avoid_: Max tokens, thinking budget, contemplation budget

**Compaction**:
Replacing the older prefix of a Tome's branch with a synthesized summary once the transcript approaches the Mana Pool limit.
_Avoid_: Truncation, context pruning, summarization

**Cut Point**:
The turn-aligned boundary in a transcript where Compaction separates summarized history from the Retained Tail; never splits a Spell result from the Invocation that requested it.
_Avoid_: Split index, truncation offset

**Retained Tail**:
The recent Invocations kept verbatim after Compaction so immediate conversational context remains intact.
_Avoid_: Recent window, sliding window

### Tome & Session Tree

**Tome**:
A persisted, tree-structured conversation between a Summoner and a Mvge recorded as an append-only sequence of entries.
_Avoid_: Session (as an internal domain term; use Session only at standard filesystem and wire protocol boundaries), conversation, chat log

**Invocation**:
A single conversational message in a Tome's transcript — a Summoner request, a Mvge response, or a Spell result.
_Avoid_: Message (as a spoken term), turn, event

**Invocation Transcript**:
The ordered, Summoner-visible projection of a single Mvge Invocation's parts (contemplation, Spell executions, text, and artifacts).
_Avoid_: Bubble builder, parts assembler

**Leaf**:
The active position marker pointing to the current tip entry of a conversation path within a Tome.
_Avoid_: Head, cursor, pointer

**Fork**:
A new Tome created by branching from a specific entry in a parent Tome, copying the ancestor path up to that entry.
_Avoid_: Branch (as a noun for the resulting Tome), clone

**Workspace Checkpoint**:
A recoverable snapshot of working-tree files associated with a Tome entry so rewinding or forking a Leaf can restore filesystem state alongside the conversation transcript.
_Avoid_: Git commit, backup

### Extensions, Skills & Seam Capabilities

**Rune**:
A packaged lifecycle extension that hooks into a Mvge via Sigils and can register Spells, commands, Realms, session codecs, and Approval Gates.
_Avoid_: Extension (as an internal domain term; use Extension only at standard filesystem and CLI protocol boundaries), plugin, addon, Skill

**Sigil**:
A callback registered by a Rune on a specific lifecycle hook point to observe, transform, or veto runtime behavior.
_Avoid_: Callback (as a spoken term), hook (for the callback itself — the hook is the lifecycle point)

**Capability Bridge**:
A Rune that binds an open specification or external protocol (such as `.agents` steering, `agentskills.io`, Open Knowledge Format, MADR, MCP, or OpenTelemetry) into a Mvge's system prompt, context, or Spells.
_Avoid_: Adapter, wrapper, integration

**Spell Gateway**:
A Rune that suppresses upfront Spell, Skill, and MCP schema injection to expose a minimal discovery surface, activating capabilities into the active allowlist on demand and evicting or unloading them when no longer needed.
_Avoid_: Tool router, proxy

**Skill**:
A progressively disclosed capability pack conforming to `agentskills.io` (frontmatter metadata, markdown instructions, and optional bundled resources) that teaches a Mvge how to perform a specialized task; never executed directly as a Spell or loaded as a Rune.
_Avoid_: Tool, Spell, Rune, plugin

**Plugin**:
A portable package adhering to the Agent Plugins specification that bundles Skills and MCP server configurations for distribution across `.agents`-compatible clients.
_Avoid_: Rune, extension

**Raw Experience**:
Immutable execution trajectories captured from completed runs as the empirical input to autonomous Skill synthesis and evaluation.
_Avoid_: Raw knowledge, logs, chat history

**Skill Evolution Store**:
The persistent, validated repository of failure patterns, impact metrics, and consolidation logs compiled from Raw Experience to evolve Skills atomically.
_Avoid_: Knowledge store, wiki, memory

### Security, Governance & Execution Substrate

**Approval Gate**:
A fail-closed policy checkpoint registered by a Rune that evaluates every Spell cast against security rules and session grants before execution is permitted.
_Avoid_: Permission middleware, tool interceptor

**Approval Presenter**:
The host UI channel bound to the runtime that renders an Approval Gate's interactive prompt to the Summoner and returns their decision.
_Avoid_: Confirmation dialog, UI callback

**Workspace Trust**:
The Summoner's explicit authorization permitting project-scoped Runes and Spells inside a working directory to be loaded and executed.
_Avoid_: Repo permission, auto-load

**Code Sandbox**:
Static verification that dynamically authored or synthesized Python code adheres to its declared module privilege budget and uses no forbidden host primitives.
_Avoid_: Execution Backend, container

**Execution Backend**:
The runtime substrate (such as the local host OS, a persistent container, or an ephemeral micro-VM) on which Spells execute commands and file operations.
_Avoid_: Code Sandbox, AST guard
