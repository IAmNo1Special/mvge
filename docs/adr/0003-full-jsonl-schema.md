# ADR 0003: Full JSONL Session Schema

## Status

Accepted — mechanism superseded by [ADR 0013](0013-cache-free-format-agnostic-tome-persistence.md)

> **Mechanism superseded 2026-10-04.** The decision to store one Tome per JSONL
> file survives. Three mechanisms named below do not exist:
>
> | Named here | Reality |
> | --- | --- |
> | "the exact same JSONL session schema as Pi (all 12 entry types)" | Six types: `MESSAGE`, `LABEL`, `COMPACTION`, `CUSTOM`, `LEAF`, `TOME_INFO`. `TomeV1Codec` explicitly disclaims byte-compatibility with Pi. |
> | "an in-memory index accelerates querying and is rebuilt on startup" | No index. `TomeHandleFactory` holds no caches; every query rescans or revalidates against a `Revision` token. |
> | "cross-platform via `filelock`" | `portalocker` (`mvge-tome/src/mvge_tome/handle.py:16`). Zero `filelock` hits in any `src/`. |
>
> Committing to Pi's exact schema is what made native Pi interoperability
> impossible. [ADR 0013](0013-cache-free-format-agnostic-tome-persistence.md)
> replaces byte-compatibility with a `SessionCodec` seam, so Pi sessions are
> supported by an installed Rune rather than by a build-time assumption.

## Context

Mvge needs session persistence. The .agents protocol and Pi both use JSONL files for session storage. Pi's session JSONL schema defines 12 entry types as a discriminated union: invocation, spellResult, modelChange, contemplationLevelChange, toolCallsChange, label, branchSummary, compaction, custom, customMessage, leaf navigation, and sessionInfo.

## Decision

Mvge uses the exact same JSONL session schema as Pi (all 12 entry types). The schema is versioned with a `schema_version` field on each entry (`schema_version: "1.0"`). Each tome is stored as a single JSONL file (`{tome_id}.jsonl`) in `~/.agents/.mvge/tomes/` with a header line followed by entry lines. File locking prevents data corruption from concurrent access (cross-platform via `filelock`). An in-memory index accelerates querying and is rebuilt on startup.

## Consequences

- Session files are portable and readable outside of Mvge
- All 12 entry types from Pi's schema are supported from day one
- Schema versioning allows future migration without breaking existing sessions
- File locking prevents data corruption from concurrent access (cross-platform via `filelock`)
- No separate index.json file needed — index rebuilt on startup
