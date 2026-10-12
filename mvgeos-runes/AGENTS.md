# mvgeos-Runes — Agent Instructions

This package implements the Rune system: extension manifest parsing, dynamic loading, sigil hook registry, and hot reload. Runes are MvgeOS extensions that hook into the agent lifecycle.

## Package-Specific Conventions

- All code follows the red-green-refactor TDD cycle: write failing test first, then implement
- No inline imports (`await import()`, `import("pkg").Type`). Top-level imports only
- Use `pathlib.Path` for all file path operations — never raw string concatenation
- Mock sync methods with `MagicMock()`, async methods with `AsyncMock()` — mixing causes "coroutine never awaited" warnings

## Testing

```bash
# Run this package's tests
uv run pytest mvgeos-runes/tests/

# Run with coverage
uv run pytest mvgeos-runes/tests/ --cov
```

Test paths follow pattern: `mvgeos-runes/tests/unit/<module>.py` and `mvgeos-runes/tests/integration/<module>.py`

## Key Types

| Type | Purpose |
| --- | --- |
| `RuneManifest` | Rune metadata (name, version, description, hooks, entry_point) |
| `RuneRunner` | Extension host: registers sigils/spells/commands/shortcuts/providers |
| `RuneAPI` | Facade handed to rune factories |
| `RuneFactory` | `Callable[[RuneAPI], None \| Awaitable[None]]` |
| `SigilHook` | Enum of 23 lifecycle hook names |
| `RuneWatcher` | watchdog-based hot reload |
| `SpellDefinition` | Definition of a spell provided by a rune |
| `SkillManifest` | Parsed metadata for an agentskills.io skill |

## SigilHook Values

| Hook | When Fired |
| --- | --- |
| `BEFORE_INVOCATION` | Before an invocation is processed |
| `AFTER_INVOCATION` | After an invocation is processed |
| `BEFORE_SPELL_CAST` | Before a spell is executed (can veto/block) |
| `AFTER_SPELL_RESULT` | After a spell result is received |
| `BEFORE_PROVIDER_REQUEST` | Before sending request to Realm |
| `AFTER_PROVIDER_RESPONSE` | After receiving response from Realm |
| `BEFORE_PROVIDER_HEADERS` | Before provider headers are finalized |
| `TURN_START` / `TURN_END` | Turn lifecycle |
| `SESSION_START` / `SESSION_SHUTDOWN` | Session lifecycle |
| `SESSION_BEFORE_SWITCH` / `SESSION_BEFORE_FORK` | Session switch/fork |
| `COMPACTION_START` / `COMPACTION_END` | Compaction lifecycle |
| `CONTEXT_TRANSFORM` | Transform invocations before sending to Realm |
| `AGENT_START` / `AGENT_END` | Agent lifecycle |
| `BEFORE_MVGE_START` | Before Mvge initialization begins |
| `INPUT` | When new Summoner input is received |
| `SHOULD_STOP_AFTER_TURN` | Hook evaluating whether agent loop should halt |
| `PREPARE_NEXT_TURN` | Modify context or parameters for next turn |

## Rune Discovery Paths

Runes are usually user-installed, but a built-in one can ship inside an Mvge: `discover_agent_rune_extras` declares a colocated `runes/`, the agent config dir's `runes/` and `extensions/`, and a `runes/` inside an importable agent package. Each declares its own scope rather than being inferred from a path. Loaded from three standard scopes (in precedence order):

1. **User**: `~/.agents/extensions/`
2. **Agent**: `~/.agents/agents/{agent_name}/extensions/`
3. **Project**: `<project_dir>/.agents/extensions/` — anchored to an explicit
   project directory (the GUI's `--project`, the CLI's working directory).
   Never the ambient working directory: without an explicit anchor the
   project layer is omitted entirely.

## Dependencies

- `mvgeos-core` — canonical `ExecutionMode`, invocation and abort types
- `importlib.util` — dynamic module loading
- `watchdog` — file system events for hot reload

## Architecture

- Each Rune is a directory containing `manifest.json` + a Python entry file exposing `rune_factory`
- `RuneRunner.refresh_runes(extensions_dirs)` re-executes every Rune and is the hot-reload path; `load_rune_loads()` calls each factory with a fresh `RuneAPI`
- Sigils are emitted four ways: `emit_async` (fire-all), `emit_chain` (thread value), `emit_first` (first non-None), `emit_block` (handler can veto)
- `RuneWatcher` uses watchdog per rune directory with 0.5s debounce

## Spell-Gateway Contract

- A rune opts into gateway duty with `"spell_gateway": true` in `manifest.json`
  (parsed into `RuneManifest.spell_gateway`; non-bool values are treated as false)
- `RuneRunner` designates the first loaded claimant as `gateway_rune_name`;
  extra claimants get a `PARSE_WARNING` diagnostic and are ignored
- The engine (`Mvge._load_runes`) then narrows the model's spell view to the
  gateway rune's registered spells via the engine-owned global spell
  allowlist — an explicitly configured allowlist is never overridden
- Any rune may call `RuneAPI.widen_global_allowlist(names)` to reveal spells
  as they are discovered (e.g. `tool_search` hits, heal-my-goap's synthesized
  spells): widening is additive-only and a no-op when no allowlist is active,
  so a rune can never narrow the model's view or create the filter itself.
  Replacing or dropping the filter stays engine-only.
- This is a cooperation contract for honest runes, not a sandbox: rune
  factories run in-process Python and can already reach `api._runner`

