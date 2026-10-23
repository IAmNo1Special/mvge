# mvgeos-Core — Agent Instructions

This package owns the canonical loop vocabulary for MvgeOS: abort primitives,
invocations, spells, events, state, and the pure turn loop (`run_loop` with
injected `StreamFn` and `emit` sink). It has zero first-party dependencies.

## Package-Specific Conventions

- All code follows the red-green-refactor TDD cycle: write failing test first, then implement
- No inline imports (`await import()`, `import("pkg").Type`). Top-level imports only
- Use `pathlib.Path` for all file path operations — never raw string concatenation
- Mock sync methods with `MagicMock()`, async methods with `AsyncMock()` — mixing causes "coroutine never awaited" warnings
- Zero first-party dependencies: never import `mvgeos-agent`, `mvgeos-provider`,
  `mvgeos-tome`, `mvgeos-runes`, `mvgeos-cli`, or `mvgeos-gui` from
  this package. Leaf packages (`provider`, `runes`, `tome`) depend on core, not
  the reverse.

## Testing

```bash
# Run this package's tests
uv run python -m pytest mvgeos-core/tests/
```

Test paths follow pattern: `mvgeos-core/tests/unit/<module>.py` and `mvgeos-core/tests/integration/<module>.py`

## Key Types (target boundary)

| Type | Purpose |
| --- | --- |
| `AbortError` / `AbortSignal` / `AbortController` | Cooperative cancellation primitives |
| `StopReason` | Realm stop reasons (`pending`, `stop`, `spellUse`, ...) |
| `MvgeResponse` / `SummonerRequest` / `SpellResultMessage` | Invocation transcript types |
| `MvgeSpell` / `SpellResult` / `SpellStatus` | Spell definition and outcome |
| `MvgeState` | Mutable session state — lives in `mvgeos-agent`, which reduces core events into it |
| `LoopContext` / `LoopCallbacks` | Frozen loop input and extension points |
| `StreamFn` / `EmitSink` / `run_loop` | Injected Realm caller, event sink, pure turn loop |
| `SpellDispatcher` / `EventBus` | Tool-call execution and event propagation |

## Dependency Direction

`mvgeos-provider`, `mvgeos-runes`, and `mvgeos-agent` depend on core.
`mvgeos-tome` is dependency-free (JSON payloads only). Enforced by
`dependency_direction` guard tests in core and provider.
