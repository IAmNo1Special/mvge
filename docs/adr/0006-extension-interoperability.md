# ADR 0006: Extension Interoperability at Data Level

## Status

Superseded by [ADR 0002](0002-dotagents-protocol.md),
[ADR 0013](0013-cache-free-format-agnostic-tome-persistence.md) and
[ADR 0014](0014-one-resolved-layer-stack.md)

> **Superseded 2026-10-04.** The *thesis* — extension interoperability is
> achieved at the data/config level, not the runtime level — still holds and is
> load-bearing. Four specifics below are wrong:
>
> - **"The rune manifest lives at `.agents/.mvge/runes/manifest.json`"** — that
>   path does not exist. Rune manifests live in `extensions/<name>/manifest.json`.
>   See [ADR 0014](0014-one-resolved-layer-stack.md).
> - **"A session started in Pi can be resumed in Mvge and vice versa"** — this
>   was asserted and could not be delivered, because Mvge's Tome v1 format is
>   not byte-compatible with Pi's. It is now *conditionally* true: the `pi-codec`
>   Rune reads, resumes, appends to, compacts and forks real Pi sessions natively.
>   See [ADR 0013](0013-cache-free-format-agnostic-tome-persistence.md).
> - **"Shared `auth.json` format (both read the same credentials)"** — superseded
>   by per-provider files at `auth/<provider>.json`.
> - **"Shared session JSONL format (both read/write the same schema)"** — split
>   by the `SessionCodec` seam: Mvge writes Tome v1, Pi sessions are read
>   through their own codec.

## Context

Mvge mirrors Pi's extension architecture. Pi extensions are TypeScript modules running in Bun/V8. Mvge runes are Python modules running in CPython. These runtimes are incompatible for shared executable code.

## Decision

Extension interoperability is achieved at the data/config level, not the runtime level:
- Shared JSON manifest format (`manifest.json`)
- Shared session JSONL format (both read/write the same schema)
- Shared auth.json format (both read the same credentials)
- Shared tool definitions (both use the same JSON schema)

Mvge runes are Python modules (`.py` files) loaded dynamically from `.agents/.mvge/runes/`. Pi extensions are TypeScript modules loaded by Bun. TUI extensions from Pi cannot run in Mvge (different rendering engine).

The rune manifest lives at `.agents/.mvge/runes/manifest.json`.

## Consequences

- Both Pi and Mvge can use the same extension config files
- A session started in Pi can be resumed in Mvge and vice versa
- Auth credentials are shared at rest (same file format)
- No cross-runtime code sharing (TypeScript cannot run in Python and vice versa)
- TUI extensions require separate implementations for each platform
