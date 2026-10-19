# ADR 0024: ADK-Style SpellContext, Mandatory Spell Intent Explanations, and Substrate Seams

## Status

Accepted

## Date

2026-10-19

## Context

Comparing MvgeOS's tool execution pipeline against `google/adk-python`, `huggingface/smolagents`, `deepseek-ai/deepseek-harness`, and `anomalyco/opencode` exposed five architectural gaps:

1. **Ad-Hoc Parameter Injection & Schema Leakage**: `generate_spell_schema` in `mvgeos_core.spell_schema` only excludes hardcoded parameter names `("spell_cast_id", "params", "signal", "on_update", "client")`. As a result, runtime parameters such as `workspace_root` in `coding_mvge/spells/bash.py` leak into the LLM-facing JSON Schema, and spells have no clean way to receive an `ExecutionBackend`, `tome_id`, or `agent_name`.
2. **Broken Sync/Async Callable Handling**: `FunctionSpell.execute` only checks `inspect.iscoroutinefunction(self.func)` without unwrapping `functools.partial` or callable objects (`async def __call__`), and `SpellDefinition.execute` in `mvgeos-runes` unconditionally `await`s handlers, crashing on synchronous Rune spell handlers and making `CodeAct` (`smolagents`-style Python tool composition) awkward to bridge.
3. **Unexplained Spell Casts**: Unlike Antigravity (which requires `toolAction` and `toolSummary` on every tool call), MvgeOS spells do not require the model to state *why* it is casting a spell, depriving the Summoner, `approval-rune`, `mvgeos-gui`, `session-search`, and `skill-evolution` of human-readable intent metadata.
4. **Host-Bound Execution & Missing CodeAct Bridge**: `coding_mvge` spells execute subprocesses and file I/O directly on the host OS rather than routing through a pluggable `ExecutionBackend` (Local Host vs. Docker container vs. remote micro-VM), and `MvgeSandbox` is only used for static AST validation rather than stateful Python-as-Action tool composition.
5. **Missing Leaf-Change Sigil & Loop Short-Circuit Hooks**: `MvgeHarness.undo` and `checkout_leaf` emit no Sigil hook for `opencode` v2 shadow-git workspace checkpoint restoration; `LoopCallbacks.should_stop_after_turn` is not wired to `RuneRunner`; and `BEFORE_PROVIDER_REQUEST` cannot short-circuit `stream_fn`.

## Decision

1. **ADK-Inspired `SpellContext` & Type-Driven Schema Exclusion (`mvgeos-core`, `mvgeos-agent`, `mvgeos-runes`)**:
   - Define a frozen `SpellContext` dataclass in `mvgeos_core.spells` carrying `spell_cast_id`, `signal`, `on_update`, `backend: ExecutionBackend | None`, `workspace_root: Path | None`, `project_root`, `tome_id`, `agent_name`, `spell_action`, `spell_summary`, and `state`.
   - Adopt `google/adk-python`'s `unwrap_callable` and `find_context_parameter(func)`: any parameter annotated as `SpellContext` or `SpellContext | None` (or named `ctx` / `spell_context`) is automatically excluded from the LLM JSON Schema and unconditionally populated by `SpellDispatcher` at call time.

2. **Unified Sync (`def`) and Async (`async def`) Spell Execution (`CallableSpec` & `_invoke_callable`)**:
   - Both `FunctionSpell` and `SpellDefinition` unwrap callables via `unwrap_callable` and support synchronous (`def`) and asynchronous (`async def`) functions uniformly.
   - Synchronous spells invoked from the async loop run via a `ContextVar`-scoped thread-pool runner that clears itself inside worker threads so nested synchronous calls in `CodeAct` run inline without thread-pool deadlocks.

3. **Mandatory `spell_action` & `spell_summary` Intent Fields on Every Spell Schema (`mvgeos-core`)**:
   - `generate_spell_schema` / `SpellDispatcher` injects required `spell_action` (brief `-ing` phrase explaining the specific action and rationale) and `spell_summary` (2–5 word noun phrase) properties into every model-facing Spell JSON Schema.
   - `SpellDispatcher` extracts `spell_action` and `spell_summary` before invoking the underlying Python callable, attaching them to `SpellContext`, `ApprovalRequest`, `BeforeSpellCastEvent`, `SPELL_EXECUTION_START`, and sub-Mvge `state_detail` badges.

4. **Pluggable `ExecutionBackend` & Marketplace `codeact-rune` (`mvgeos-core` + `mvgeos-marketplace`)**:
   - `mvgeos-core` defines the `ExecutionBackend` protocol (defaulting to `LocalExecutionBackend`). `coding_mvge` spells route subprocess and filesystem operations through `ctx.backend`.
   - `codeact-rune` in `mvgeos-marketplace` executes AST-verified Python snippets (`MvgeSandbox`) with write-protected tool bindings (`smolagents` style) and AST auto-`await` transformation, routing every nested spell call through `SpellDispatcher` and the fail-closed `ApprovalGate`.

5. **Benchmark-Hardened Checkpoints (`opencode` v2), LSP (`opencode`), and Loop Inversion Hooks**:
   - `MvgeHarness` emits `SigilHook.SESSION_LEAF_CHANGED` on `undo`, `checkout_leaf`, and `fork`. `checkpoint-bridge` in `mvgeos-marketplace` uses `opencode` v2 shadow git plumbing (`objects/info/alternates`, lock-free `GIT_INDEX_FILE=index.<uuid>`, `git update-index`, 2 MiB untracked cap, ref-less `git pack-objects`) to snapshot and restore working-tree files per `Leaf`.
   - `lsp-bridge` in `mvgeos-marketplace` hooks `AFTER_SPELL_RESULT` on `edit`/`write` with a 150ms debounce / 3000ms cap, appending `<diagnostics file="...">ERROR [line:col] ...</diagnostics>`.
   - `MvgeHarness._build_callbacks` wires `LoopCallbacks.should_stop_after_turn` to `SigilHook.TURN_END` (unblocking `mana-budget` Runes) and allows `SigilHook.BEFORE_PROVIDER_REQUEST` to return a synthetic `AssistantInvocation` to short-circuit `stream_fn`.

## Contract

The existing engine symbols anchored by this decision live in `mvgeos-core`, `mvgeos-runes`, and `mvgeos-agent`:

- `MvgeSpell` and `MvgeSpell.execute` in `mvgeos_core.spells` define the base spell contract.
- `generate_spell_schema` in `mvgeos_core.spell_schema` generates spell JSON schemas.
- `SpellDispatcher` and `SpellDispatcher.dispatch_batch` in `mvgeos_core.dispatcher` and `LoopCallbacks` in `mvgeos_core.loop` drive spell execution and loop lifecycle hooks.
- `MvgeSandbox`, `MvgeSandbox.validate_ast`, and `ASTSafetyVisitor` in `mvgeos_core.sandbox` enforce AST privilege budgets.
- `FunctionSpell` in `mvgeos_agent.function_spell` and `SpellDefinition` in `mvgeos_runes.types` wrap Python functions and Rune spell handlers.
- `MvgeHarness.undo`, `MvgeHarness.checkout_leaf`, and `MvgeHarness.fork_tome` manage Tome leaf navigation.

<!-- adr-contract: MvgeSpell, MvgeSpell.execute, generate_spell_schema, SpellDispatcher, SpellDispatcher.dispatch_batch, LoopCallbacks, MvgeSandbox, MvgeSandbox.validate_ast, ASTSafetyVisitor, FunctionSpell, SpellDefinition, MvgeHarness.undo, MvgeHarness.checkout_leaf, MvgeHarness.fork_tome -->

## Consequences

- Spell authors write plain `def` or `async def` functions taking an optional `ctx: SpellContext | None = None` with zero schema leakage.
- Every spell cast carries explicit model-authored `spell_action` and `spell_summary` metadata across `mvgeos-gui`, `mvgeos-cli`, `approval-rune`, `session-search`, and `skill-evolution`.
- `/undo` and `/checkout` atomically restore both the Tome `Leaf` and working-tree files when `checkpoint-bridge` is active, and `edit`/`write` surface immediate LSP compiler/type diagnostics when `lsp-bridge` is active.
