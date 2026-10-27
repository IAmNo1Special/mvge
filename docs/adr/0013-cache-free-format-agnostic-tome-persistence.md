# ADR 0013: Cache-Free, Format-Agnostic Tome Persistence

## Status

Accepted

## Date

2026-10-04

## Supersedes

The *mechanism* half of [ADR 0003](0003-full-jsonl-schema.md) and
[ADR 0007](0007-unified-tome-session-system.md). Their decisions survive; the
implementations they named do not.

## Context

ADR-0007 unified Mvge on one JSONL file per Tome, which was the right call and
is still exactly what the code does. Every mechanism it named is gone:

| ADR-0007 / ADR-0003 named | Reality |
| --- | --- |
| `TomeLedger` | No such symbol in any `src/`. Replaced by `TomeHandleFactory` + `TomeHandle`. |
| "in-memory `Index` rebuilt on startup" | No index. `TomeHandleFactory` docstring: *"Holds no caches: every query rescans the directory or revalidates the file revision."* |
| `filelock` | Zero hits in any `src/`. `portalocker` is used (`mvge-tome/src/mvge_tome/handle.py:16`). |
| "Pi's 12 entry types" | Six: `MESSAGE`, `LABEL`, `COMPACTION`, `CUSTOM`, `LEAF`, `TOME_INFO`. |

The in-memory index turned out to be the wrong shape, not merely the wrong name.
An index is a coherence hazard: any number of handles, threads, and processes may
write the same file, and there is no cheap way to know when the index is stale. It
had to be invalidated by hand on every write path, and every write path that
forgot was a silent-corruption bug waiting to happen.

Meanwhile ADR-0003 committed Mvge to *"the exact same JSONL session schema as
Pi (all 12 entry types)"*. Pi's format is not byte-compatible with what Mvge
actually writes — `TomeV1Codec.fork` says so explicitly
(`mvge-tome/src/mvge_tome/codec.py:301-305`: *"tome-v1 forks go through
TomeHandleFactory.create_branched_tome"*). That commitment made genuine Pi
interoperability impossible without a conversion step nobody wanted.

## Decision

The JSONL file is the source of truth. Nothing caches state that outlives a
revision check, and format shape belongs to a codec.

1. **The file is authoritative.** No index, no long-lived cache. Any coherence
   question is answered by re-reading the file.

2. **Reads revalidate against a `Revision` token.** `Revision` is a frozen
   `slots` dataclass of `(mtime_ns, size, ino)` built from `path.stat()`
   (`mvge-tome/src/mvge_tome/handle.py:37-51`). A `TomeHandle` keeps an
   in-memory mirror purely as a read-through accelerator and reloads the whole
   file whenever the token moves. Disk is the only authority; the mirror is a
   hint that is checked before it is trusted.

3. **No module holds state across calls.** `TomeHandleFactory` holds no caches
   at all — `list_tomes()` rescans the directory on every invocation, and
   `open_recent()` restats every file. Any number of factories, handles, threads
   and processes stay coherent because none of them is a source of truth.

4. **Format shape is owned by a `SessionCodec`.** The first codec whose
   `detect(header)` accepts a file claims it; detection failure in one codec
   never breaks the scan. `TomeV1Codec` is always first and new Tomes are always
   created in the built-in format. Runes register further codecs through
   `session_codecs` in their manifest.

5. **Concurrency is a kernel lease, not an in-process lock.** Each mutating call
   acquires `portalocker.Lock(f"{path}.lock", timeout=30)` *after* a
   thread-local `RLock`; the order is always local-then-lease
   (`handle.py:206-226`). Read mode takes no lease — reads are stat-validated
   snapshots.

6. **Every append is durable before it returns.** One line appended, `flush`,
   `os.fsync(file)`, then `fsync(parent)` so the directory entry survives a crash
   (`handle.py:427-441`). Whole-file rewrites — compaction, fork, codec migration
   — are tmp-file + `os.replace` + dir fsync (`handle.py:382-425`), never
   truncate-then-write.

7. **A torn tail is repaired by the writer, not the reader.** `repair_torn_tail`
   operates on raw bytes so platform newline translation cannot corrupt the file,
   touches only the tail, and returns the number of bytes truncated
   (`handle.py:445-493`). Damaged *middle* lines are preserved for readers to
   skip and for the auditor to flag — repair never guesses.

8. **Foreign formats are opt-in, not core.** Because the codec owns the shape,
   Pi sessions are readable, resumable, appendable, compactable and forkable
   through the `pi-codec` Rune — natively, without conversion into a Tome.

## Contract

Symbols this decision commits to. An implementer may rely on these existing;
renaming any of them is a breaking change to this ADR.

<!-- adr-contract: SessionCodec, TomeV1Codec, AppendPlan, Revision, TomeHandle, TomeHandleFactory, repair_torn_tail, append_leaf, list_tomes, get_leaf_id, portalocker -->

## Consequences

**Positive**

- Cache coherence becomes a property of the filesystem rather than of our
  bookkeeping. Cross-process safety needs no index-invalidation protocol.
- Adding a format is adding a codec, not writing a migration.
- ADR-0006's claim that "a session started in Pi can be resumed in Mvge and
  vice versa" becomes *true* — but conditionally, and as an installed Rune rather
  than a build-time assumption. That is a better answer than the one ADR-0006
  asserted and could not deliver.
- A Tome id reaching the filesystem is allowlisted (`_TOME_FILE_PATTERN`), so a
  caller-supplied id cannot escape the Tome directory.

**Negative**

- Reads that would have been O(1) through an index are now O(file). A warm
  `TomeHandle` pays only a `stat()`; a cold one pays a full parse.
- `TomeHandle.append` revalidates before appending, which re-reads the file. On a
  long Tome this is quadratic in entries. Accepted for now because correctness
  under concurrent writers is worth more than append throughput; revisit if a
  measured Tome size makes it hurt.
- The lease serialises individual mutating calls, not compound operations. Two
  writers must still be a single writer process per Tome. `TomeHandle.locked()`
  states this explicitly rather than implying stronger guarantees than it has.
