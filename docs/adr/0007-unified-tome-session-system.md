# ADR 0007: Unified Tome Session System on Pi-Compatible JSONL Format

## Status

Accepted — mechanism superseded by [ADR 0013](0013-cache-free-format-agnostic-tome-persistence.md)

> **Mechanism superseded 2026-10-04.** The *decision* — one `{tome_id}.jsonl`
> per Tome, header line plus entry lines, no `entries/` subdirectory, no
> `index.json` — is still exactly what the code does and is not in question.
>
> Every mechanism named below has been replaced:
>
> | Named here | Reality |
> | --- | --- |
> | `TomeLedger` | No such symbol in any `src/`. Replaced by `TomeHandleFactory` + `TomeHandle`. |
> | "In-memory `Index` rebuilt on startup from JSONL files" | No index. Cache-free by decision — the index was a cross-process coherence hazard, and invalidating it by hand on every write path was itself a source of bugs. |
> | "File locking via `filelock`" | `portalocker` (`mvge-tome/src/mvge_tome/handle.py:16`). Zero `filelock` hits in any `src/`. |
> | "Pi's 12 entry types" | Six. See also [ADR 0003](0003-full-jsonl-schema.md). |
> | `.agents/.mvge/sessions/` | `<global>/sessions/`. See [ADR 0014](0014-one-resolved-layer-stack.md). |
>
> **[ADR 0013](0013-cache-free-format-agnostic-tome-persistence.md)** records the
> design that replaced the index and the fixed file format. Note the direction of
> travel: "Pi-compatible" gave way to "format-agnostic with Pi as one adapter
> among several", which is what actually made Pi support achievable.

## Context

Mvge had two parallel session systems:

- `SessionManager` — Pi-compatible single JSONL per session, no Mvge features (branching, locking, indexing)
- `TomeLedger` — Custom format (`entries/{id}.jsonl` + `index.json` + `.meta.json`), full Mvge features

This created confusion, dual storage (`sessions/*.jsonl` + `sessions/entries/*.jsonl`), and maintenance burden.

## Decision

Unify on a single `TomeLedger` using Pi's session JSONL format:

- One `{tome_id}.jsonl` file per tome in `.agents/.mvge/sessions/`
- Header line: `{"type": "session", "version": 1, "id": "...", "timestamp": "...", "cwd": "...", "parentSession": "...", "activeLeafId": "..."}`
- Entry lines: Pi's 12 entry types (invocation, spellResult, modelChange, etc.)
- File locking via `filelock` for concurrency
- In-memory `Index` rebuilt on startup from JSONL files
- No `entries/` subdirectory, no `index.json`
- `SessionManager` class removed; CLI uses Mvge terminology (`tome`, `fork`, etc.)

## Consequences

- Pi session files work natively — users can resume Pi sessions in Mvge
- Single source of truth for session storage
- All Mvge features preserved (branching/forking, metadata, leaf tracking)
- Simplified codebase (~300 lines removed from `session.py`)
- CLI commands use consistent Mvge terminology: `mvge tome list|show|create|fork|export`
