# ADR 0002: dotagents Protocol Compliance

## Status

Superseded by [ADR 0014](0014-one-resolved-layer-stack.md)

> **Superseded 2026-10-04.** The `.agents` directory decision below still holds
> and is still correct. The *directory scheme* does not: no `.agents/.mvge/`
> subdirectory exists in any `src/` today, and ADR-0014 replaces it with standard
> protocol names (`sessions/`, `agents/`, `extensions/`, `auth/`,
> `models.json`) resolved through a single module. This ADR also predates
> `MVGE_GLOBAL_DIR`, which relocates the whole global layer.
>
> Read this ADR for the *why* of adopting `.agents/`. Read
> [ADR 0014](0014-one-resolved-layer-stack.md) for the *what* and *where*.

## Context

Mvge needs a configuration directory convention. The .agents Protocol (dotagentsprotocol.com) defines an open directory convention for AI agent configuration. It mandates a `.agents/` directory with a two-layer system (global `~/.agents/` and workspace `./.agents/`).

## Decision

Mvge adheres to the `.agents/` directory name per the dotagents protocol. Mvge-specific data is namespaced under `.agents/.mvge/`:

- `.agents/.mvge/runes/manifest.json` — Rune registry
- `.agents/.mvge/tomes/` — Tome JSONL files
- `.agents/.mvge/auth/` — API keys and credentials
- `.agents/.mvge/models.json` — Model configuration

## Consequences

- Configuration is portable across tools that support the .agents protocol
- The `.agents/` directory is committed to the repository (workspace layer)
- Mvge-specific files are isolated under the `mvge/` subdirectory
- Other tools reading `.agents/` can discover Mvge config in the conventional location
