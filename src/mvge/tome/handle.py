from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import uuid
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import portalocker

from mvge.tome.codec import SessionCodec, TomeV1Codec
from mvge.tome.types import (
    CURRENT_SESSION_VERSION,
    ATIFMetrics,
    ATIFTrajectory,
    ATIFTrajectoryStep,
    TomeEntry,
    TomeEntryType,
    TomeIntegrityIssue,
    TomeIntegrityReport,
    TomeMetadata,
    TomeVersionError,
)

logger = logging.getLogger(__name__)

_LOCK_TIMEOUT = 30.0


@dataclass(frozen=True, slots=True)
class Revision:
    """Single invalidation token: filesystem identity + size + mtime."""

    mtime_ns: int
    size: int
    ino: int

    @staticmethod
    def from_path(path: Path) -> Revision | None:
        try:
            st = path.stat()
            return Revision(st.st_mtime_ns, st.st_size, st.st_ino)
        except FileNotFoundError:
            return None


def _generate_short_id() -> str:
    return uuid.uuid4().hex[:8]


def _generate_id() -> str:
    return uuid.uuid4().hex


_TOME_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def _validate_tome_id(tome_id: str) -> str:
    """Allowlist a tome id before it touches the filesystem.

    Tome ids become `{id}.jsonl` file names; anything outside the allowlist
    (path separators, `..`, absolute paths, empty strings) is rejected so a
    caller-supplied id can never escape the tome directory.
    """
    if not _TOME_ID_RE.match(tome_id):
        raise ValueError(f"Invalid tome id: {tome_id!r}")
    return tome_id


def _validate_path_label(label: str) -> str:
    """Allowlist a handle label derived from an explicit file path.

    Path-derived labels never touch the filesystem — the handle already
    holds a resolved path — so they may contain dots, spaces, and tildes:
    anything a foreign naming convention (Pi's timestamp-prefixed names,
    dotted backup names) might use. Separators, parent segments, and NUL
    are still rejected so the label can never be mistaken for a path on a
    later ``open_read(label)`` round-trip.
    """
    if (
        not label
        or "\x00" in label
        or "/" in label
        or "\\" in label
        or label in (".", "..")
    ):
        raise ValueError(f"Invalid session label: {label!r}")
    return label


def _timestamp_now() -> float:
    return datetime.now(UTC).timestamp()


def _timestamp_iso() -> str:
    return datetime.now(UTC).isoformat()


def _filter_entries_to_leaf(
    entries: Sequence[TomeEntry],
    leaf_id: str,
    entry_index: Mapping[str, TomeEntry] | None = None,
) -> list[TomeEntry]:
    """Filter entries to only include the branch ending at leaf_id.

    Traverses the parent chain backwards from leaf_id in O(K) time
    and returns ancestors ordered from root to leaf.
    """
    if entry_index is None:
        entry_index = {e.id: e for e in entries}
    path: list[TomeEntry] = []
    current_id: str | None = leaf_id
    visited: set[str] = set()
    while current_id and current_id not in visited:
        visited.add(current_id)
        entry = entry_index.get(current_id)
        if not entry:
            break
        path.append(entry)
        current_id = entry.parent_id
    path.reverse()
    return path


def _fsync_dir(dir_path: Path) -> None:
    """Fsync a directory so file creates/renames survive a crash.

    No-op on Windows, which cannot open a directory handle.
    """
    if os.name == "nt":
        return
    fd = os.open(dir_path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class TomeHandle:
    """A handle to a single tome; read handles share, write is exclusive.

    The on-disk JSONL file is the source of truth. Each handle keeps an
    in-memory mirror that is revalidated against a filesystem revision token
    on every read, so any number of handles across threads and processes stay
    coherent without shared caches.

    The handle is format-agnostic: a SessionCodec owns header parsing, entry
    parsing/serialisation, and branch-tip bookkeeping for the file's format.
    """

    def __init__(
        self,
        tome_dir: Path,
        tome_id: str,
        mode: str,
        codec: SessionCodec | None = None,
        *,
        path: Path | None = None,
    ) -> None:
        self._tome_dir = Path(tome_dir).expanduser().resolve()
        self._mode = mode  # "r" or "w"
        self._codec = codec if codec is not None else TomeV1Codec()
        if path is not None:
            # Explicit file: the id is an informational label only — the
            # resolved path is authoritative — so foreign naming conventions
            # (Pi's timestamp-prefixed names, dotted names) are tolerated.
            self._tome_id = _validate_path_label(tome_id)
            self._path = Path(path).expanduser().resolve()
        else:
            # The id becomes the file name; keep the strict allowlist so a
            # caller-supplied id can never escape the tome directory.
            self._tome_id = _validate_tome_id(tome_id)
            self._path = self._tome_dir / f"{self._tome_id}.jsonl"
        self._revision: Revision | None = None
        self._entries_cache: list[TomeEntry] | None = None
        self._entry_index: dict[str, TomeEntry] | None = None
        self._header: dict[str, Any] | None = None
        self._lease: portalocker.Lock | None = None
        self._local_lock = threading.RLock()

    @property
    def tome_id(self) -> str:
        return self._tome_id

    @property
    def codec(self) -> SessionCodec:
        return self._codec

    @property
    def path(self) -> Path:
        return self._path

    @property
    def mode(self) -> str:
        return self._mode

    @contextmanager
    def locked(self) -> Generator[None]:
        """Hold this handle's local mutex across a compound operation.

        Reentrant: may be held while calling append/append_leaf/replace.
        Note: this guards threads in this process only. Each mutating call
        still acquires and releases the kernel lease independently, so two
        processes can interleave their compound operations; keep multi-step
        mutations to a single writer process per tome.
        """
        with self._local_lock:
            yield

    @contextmanager
    def _acquire(self) -> Generator[None]:
        """Acquire the local mutex plus the kernel lease for write mode.

        Lock order is always local-then-lease. Read mode takes no lease:
        reads are stat-validated snapshots.
        """
        with self._local_lock:
            if self._mode == "w":
                self._lease = portalocker.Lock(
                    str(self._path) + ".lock",
                    timeout=_LOCK_TIMEOUT,
                )
                self._lease.acquire()
                self._revision = Revision.from_path(self._path)
            try:
                yield
            finally:
                if self._lease is not None:
                    self._lease.release()
                    self._lease = None

    # ── Read API (both modes) ──────────────────────────────────

    def _load_snapshot(self) -> list[TomeEntry]:
        """Load the full file into memory (Pi-style mirror)."""
        if not self._path.exists():
            self._entries_cache = []
            self._header = None
            self._revision = None
            return []
        with self._path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        if not lines:
            self._entries_cache = []
            self._header = None
            self._revision = Revision.from_path(self._path)
            return []
        try:
            header = json.loads(lines[0].strip())
        except json.JSONDecodeError:
            self._entries_cache = []
            self._header = None
            self._revision = Revision.from_path(self._path)
            return []
        if not isinstance(header, dict) or not self._codec.detect(header):
            # Not this codec's session document. A session-looking header at
            # an unsupported version still raises (legacy contract); anything
            # else reads as empty.
            if isinstance(header, dict) and self._codec.looks_like_session(header):
                self._codec.parse_header(header)  # raises TomeVersionError
            self._entries_cache = []
            self._header = None
            self._revision = Revision.from_path(self._path)
            return []
        # The codec owns version validation from here on.
        self._codec.parse_header(header)
        if self._codec.repair_on_open(str(self._path)):
            # The codec rewrote the file (e.g. Pi's torn-tail repair);
            # reload from the repaired file.
            return self._load_snapshot()
        self._header = header
        entries = self._codec.parse_entries(header, lines[1:], source=str(self._path))
        self._entries_cache = entries
        self._entry_index = None
        self._revision = Revision.from_path(self._path)
        return entries

    def get_entries(self) -> list[TomeEntry]:
        """Snapshot read; the mirror is revalidated on every call."""
        with self._local_lock:
            rev = Revision.from_path(self._path)
            if self._entries_cache is not None and self._revision == rev:
                return list(self._entries_cache)
            return list(self._load_snapshot())

    def get_entries_to_leaf(self, leaf_id: str) -> list[TomeEntry]:
        """Return the branch ending at leaf_id, using indexed traversal."""
        with self._local_lock:
            entries = self.get_entries()
            if self._entry_index is None or len(self._entry_index) != len(entries):
                self._entry_index = {e.id: e for e in entries}
            return _filter_entries_to_leaf(entries, leaf_id, self._entry_index)

    def get_metadata(self) -> TomeMetadata | None:
        """Return parsed header metadata."""
        with self._local_lock:
            rev = Revision.from_path(self._path)
            if self._header is not None and self._revision == rev:
                return self._codec.parse_header(self._header)
            self._load_snapshot()
            if self._header is not None:
                return self._codec.parse_header(self._header)
            return None

    def get_header(self) -> dict[str, Any] | None:
        """Return the raw format-native header document, if any."""
        with self._local_lock:
            rev = Revision.from_path(self._path)
            if self._header is not None and self._revision == rev:
                return dict(self._header)
            self._load_snapshot()
            if self._header is not None:
                return dict(self._header)
            return None

    def iter_entries(self) -> Generator[TomeEntry]:
        """Snapshot iteration over a materialized copy; holds no locks."""
        yield from self.get_entries()

    def get_revision(self) -> Revision | None:
        """Single invalidation token for this tome's file."""
        return self._revision or Revision.from_path(self._path)

    # ── Write API (write mode only) ────────────────────────────

    def append(self, entry: TomeEntry) -> None:
        """Append a single entry plus a durability barrier.

        Persistence is codec-planned: most codecs append one line, while a
        codec performing a format migration (Pi's v3-to-v4 upgrade on first
        write) gets an atomic whole-file rewrite.
        """
        if self._mode != "w":
            raise RuntimeError("Read handle cannot append")
        with self._acquire():
            self._load_snapshot()
            if self._entries_cache is None:
                raise RuntimeError("Failed to load tome snapshot before append")
            plan = self._codec.plan_append(
                entry, self._entries_cache, dict(self._header or {})
            )
            if plan.rewrite:
                if plan.header is None:
                    raise RuntimeError(
                        f"Codec {self._codec.name} requested a rewrite "
                        "without a new header"
                    )
                self._rewrite_lines_locked(plan.header, plan.lines)
            else:
                for line in plan.lines:
                    self._flush_line(line)
            self._entries_cache.append(plan.stored)
            if self._entry_index is not None:
                self._entry_index[plan.stored.id] = plan.stored

    async def append_async(self, entry: TomeEntry) -> None:
        """Asynchronously append a single entry via background thread."""
        await asyncio.to_thread(self.append, entry)

    def append_leaf(self, target_id: str) -> TomeEntry:
        """Point the session's Leaf at an entry.

        Branch-tip bookkeeping is codec-owned: Tome v1 appends a LEAF marker
        entry and records the header's activeLeafId; other formats record the
        tip the way their own readers expect (e.g. Pi stores it as a value
        write, not a marker entry).
        """
        if self._mode != "w":
            raise RuntimeError("Read handle cannot append")
        with self._acquire():
            self._load_snapshot()
            if self._entries_cache is None:
                raise RuntimeError("Failed to load tome snapshot before append_leaf")
            leaf = TomeEntry(
                id=_generate_short_id(),
                parent_id=None,
                type=TomeEntryType.LEAF,
                timestamp=_timestamp_now(),
                payload={"targetId": target_id},
            )
            tip_lines = self._codec.append_tip_lines(
                dict(self._header or {}), list(self._entries_cache), leaf
            )
            if tip_lines is None:
                header, entries = self._codec.apply_leaf(
                    dict(self._header or {}), list(self._entries_cache), leaf
                )
                self._replace_locked(header, entries)
            else:
                for line in tip_lines:
                    self._flush_line(line)
        return leaf

    async def append_leaf_async(self, target_id: str) -> TomeEntry:
        """Asynchronously point the session's Leaf at an entry via background thread."""
        return await asyncio.to_thread(self.append_leaf, target_id)

    def replace(self, header: dict[str, Any], entries: list[TomeEntry]) -> None:
        """Atomic full rewrite (compaction/fork) via tmp file + rename."""
        if self._mode != "w":
            raise RuntimeError("Read handle cannot replace")
        with self._acquire():
            self._replace_locked(header, entries)

    async def replace_async(
        self, header: dict[str, Any], entries: list[TomeEntry]
    ) -> None:
        """Asynchronously replace the file via background thread."""
        await asyncio.to_thread(self.replace, header, entries)

    def _replace_locked(self, header: dict[str, Any], entries: list[TomeEntry]) -> None:
        """Rewrite the file; caller must hold the lease via _acquire()."""
        tmp = self._path.with_name(f".{self._path.name}.{os.getpid()}.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                f.write(json.dumps(header) + "\n")
                for e in entries:
                    line = self._codec.serialize_entry(e)
                    if line is None:
                        continue
                    f.write(json.dumps(line) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path)
            _fsync_dir(self._path.parent)
            self._entries_cache = list(entries)
            self._entry_index = None
            self._header = dict(header)
            self._revision = Revision.from_path(self._path)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)

    def _rewrite_lines_locked(self, header: dict[str, Any], lines: list[Any]) -> None:
        """Atomically replace the file with a header plus raw body lines.

        The body lines are JSON values (one per file line); the entries
        cache is left for the caller to update. Used for codec-planned
        rewrites such as format migrations.
        """
        tmp = self._path.with_name(f".{self._path.name}.{os.getpid()}.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                f.write(json.dumps(header) + "\n")
                for line in lines:
                    f.write(json.dumps(line) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path)
            _fsync_dir(self._path.parent)
            self._header = dict(header)
            self._revision = Revision.from_path(self._path)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)

    def _flush_line(self, line: dict[str, Any] | list[Any]) -> None:
        """Durability barrier: append one serialised line, fsync file and dir.

        A line is usually a JSON object; codecs with multi-write
        transactions (Pi) flush a JSON array as one line.
        """
        if self._entries_cache is None:
            raise RuntimeError("No snapshot loaded; nothing to flush")
        text = json.dumps(line) + "\n"
        with self._path.open("a", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        _fsync_dir(self._path.parent)
        self._revision = Revision.from_path(self._path)

    # ── Repair (resumer calls, not readers) ────────────────────

    def repair_torn_tail(self) -> int:
        """Truncate a partial last line left by a crashed writer.

        Operates on raw bytes so platform newline translation can never
        corrupt the file. Only the tail is touched: middle lines (even
        damaged ones) are preserved for readers to skip and the auditor
        to flag. Returns the number of bytes truncated.
        """
        if self._mode != "w":
            raise RuntimeError("Read handle cannot repair")
        with self._acquire():
            if not self._path.exists():
                return 0
            with self._path.open("rb") as f:
                content = f.read()
            if not content:
                return 0
            lines = content.split(b"\n")
            last_complete = -1
            for i, raw in enumerate(lines):
                if i == len(lines) - 1 and raw == b"":
                    continue  # trailing newline, not a line
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    continue  # torn multi-byte sequence
                if not text.strip():
                    continue
                try:
                    json.loads(text)
                except json.JSONDecodeError:
                    continue
                last_complete = i
            if last_complete < 0:
                return 0  # nothing parseable: corruption, not a torn tail
            repaired = b"\n".join(lines[: last_complete + 1]) + b"\n"
            if repaired == content:
                return 0
            with self._path.open("r+b") as f:
                f.seek(0)
                f.write(repaired)
                f.truncate()
                f.flush()
                os.fsync(f.fileno())
            # Invalidate the mirror so the next read reloads.
            self._revision = None
            self._entries_cache = None
            self._header = None
            return len(content) - len(repaired)


class TomeHandleFactory:
    """Stateless entry point for session persistence.

    Holds no caches: every query rescans the directory or revalidates the
    file revision, so any number of factories, handles, threads, and
    processes stay coherent. The JSONL file is the source of truth.

    The factory is format-agnostic: each file is claimed by the first
    SessionCodec whose ``detect`` accepts its header. The built-in Tome v1
    codec always comes first; rune-registered codecs follow in registration
    order. New sessions are always created in the built-in format.
    """

    def __init__(
        self, tome_dir: Path, codecs: Sequence[SessionCodec] | None = None
    ) -> None:
        self._tome_dir = Path(tome_dir).expanduser().resolve()
        self._codecs: list[SessionCodec] = [TomeV1Codec(), *(codecs or [])]

    @property
    def dir(self) -> Path:
        return self._tome_dir

    @property
    def codecs(self) -> list[SessionCodec]:
        return list(self._codecs)

    def _resolve_tome_id(self, tome_id: str) -> str | None:
        """Resolve a full, case-insensitive, or prefix id to a file stem."""
        if not tome_id:
            return None
        if not self._tome_dir.exists():
            return None
        stems = [f.stem for f in self._tome_dir.glob("*.jsonl") if f.is_file()]
        if tome_id in stems:
            return tome_id
        exact = [s for s in stems if s.lower() == tome_id.lower()]
        if len(exact) == 1:
            return exact[0]
        prefix = [s for s in stems if s.lower().startswith(tome_id.lower())]
        if len(prefix) == 1:
            return prefix[0]
        return None

    def _detect_file(self, path: Path) -> tuple[SessionCodec, dict[str, Any]] | None:
        """Claim a file for the first codec that detects its header.

        Returns None when the file has no readable session header or no
        codec claims it. A misbehaving codec's detect() never breaks the scan.
        """
        if not path.is_file():
            return None
        header = self._peek_header(path)
        if not isinstance(header, dict):
            return None
        for codec in self._codecs:
            try:
                if codec.detect(header):
                    return codec, header
            except Exception:
                logger.warning(
                    "Session codec %s failed to detect %s; skipping",
                    getattr(codec, "name", codec),
                    path,
                )
        return None

    def _peek_header(self, path: Path) -> Any:
        """Read and JSON-parse a file's first line; None when unreadable."""
        try:
            with path.open("r", encoding="utf-8") as f:
                first = f.readline()
            if not first.strip():
                return None
            return json.loads(first)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return None

    def _claim(self, path: Path) -> tuple[Path, SessionCodec]:
        """Pair a file with its codec.

        Files no codec detects are claimed by the built-in codec so reads
        degrade the legacy way: foreign documents read as empty, and
        session-looking headers at bad versions raise TomeVersionError from
        the read path.
        """
        detected = self._detect_file(path)
        if detected is not None:
            codec, _header = detected
            return path, codec
        return path, self._codecs[0]

    def _locate(self, tome_id: str) -> tuple[Path, SessionCodec]:
        """Resolve an id, prefix, stem, or file path to (file, codec).

        Raises FileNotFoundError when nothing matches.
        """
        if tome_id:
            candidate = Path(tome_id)
            if candidate.is_file():
                return self._claim(candidate)
            if self._tome_dir.exists():
                stem = self._resolve_tome_id(tome_id)
                if stem is not None:
                    return self._claim(self._tome_dir / f"{stem}.jsonl")
                # Fallback: codecs whose file name differs from the session id
                # (e.g. Pi's timestamp-prefixed file names).
                matches: list[tuple[Path, SessionCodec]] = []
                for f in sorted(self._tome_dir.glob("*.jsonl")):
                    detected = self._detect_file(f)
                    if detected is None:
                        continue
                    codec, header = detected
                    try:
                        meta = codec.parse_header(header)
                    except TomeVersionError:
                        raise
                    except (KeyError, TypeError, ValueError):
                        continue
                    wanted = tome_id.lower()
                    if meta.id.lower() == wanted:
                        return f, codec
                    if meta.id.lower().startswith(wanted):
                        matches.append((f, codec))
                if len(matches) == 1:
                    return matches[0]
        raise FileNotFoundError(f"Tome not found: {tome_id}")

    def _label_for(self, path: Path) -> str:
        """Best-effort session identity for a file opened by explicit path.

        Prefers the session id parsed from the file's header — the file's
        true identity, e.g. a Pi session UUID — over the filename stem, so
        foreign naming conventions never leak into the handle's identity.
        Falls back to the stem when the header is unreadable.
        """
        meta = self._metadata_for(path)
        if meta is not None and meta.id:
            try:
                return _validate_path_label(meta.id)
            except ValueError:
                pass
        return _validate_path_label(path.stem)

    def _metadata_for(self, path: Path) -> TomeMetadata | None:
        """Parse one file's header to metadata; None when unreadable.

        Raises TomeVersionError for session-looking headers no codec claims.
        """
        detected = self._detect_file(path)
        if detected is None:
            header = self._peek_header(path)
            if isinstance(header, dict):
                for codec in self._codecs:
                    try:
                        if codec.looks_like_session(header):
                            codec.parse_header(header)  # raises TomeVersionError
                    except TomeVersionError:
                        raise
                    except Exception:
                        continue
            return None
        codec, header = detected
        try:
            return codec.parse_header(header)
        except TomeVersionError:
            raise
        except (KeyError, TypeError, ValueError):
            return None

    def tome_file(self, tome_id: str) -> Path:
        """Resolve a tome id (or file path) to its JSONL file."""
        try:
            path, _codec = self._locate(tome_id)
            return path
        except FileNotFoundError:
            return self._tome_dir / f"{_validate_tome_id(tome_id)}.jsonl"

    def list_tomes(self) -> list[TomeMetadata]:
        """Rescan the directory on every call; never serve a stale list."""
        if not self._tome_dir.exists():
            return []
        metas: list[TomeMetadata] = []
        for f in sorted(self._tome_dir.glob("*.jsonl")):
            if not f.is_file():
                continue
            try:
                meta = self._metadata_for(f)
            except TomeVersionError as e:
                logger.warning("Failed to load header for %s: %s", f, e)
                continue
            if meta is not None:
                metas.append(meta)
        return metas

    def open_tome(self, tome_id: str) -> TomeMetadata | None:
        """Open tome metadata by id, prefix, or file path.

        Returns None when missing; raises TomeVersionError on bad versions.
        """
        try:
            path, _codec = self._locate(tome_id)
        except FileNotFoundError:
            if not tome_id:
                return None
            return self._metadata_for(
                self._tome_dir / f"{_validate_tome_id(tome_id)}.jsonl"
            )
        return self._metadata_for(path)

    def open_recent(self, cwd: str) -> TomeMetadata | None:
        """Most recently modified tome for a working directory."""
        if not self._tome_dir.exists():
            return None
        candidates = sorted(
            self._tome_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime_ns
        )
        for f in reversed(candidates):
            try:
                meta = self._metadata_for(f)
            except TomeVersionError:
                continue
            if meta is not None and meta.cwd == cwd:
                return meta
        return None

    def create_tome(
        self,
        cwd: str,
        *,
        tome_id: str | None = None,
        model: str | None = None,
        contemplation_level: str | None = None,
        spells: Sequence[str] | None = None,
    ) -> TomeHandle:
        """Create a new tome and return a write handle.

        Raises ValueError when a tome with the requested id already exists.
        """
        self._tome_dir.mkdir(parents=True, exist_ok=True)
        tid = _validate_tome_id(tome_id) if tome_id else _generate_id()
        if (self._tome_dir / f"{tid}.jsonl").exists():
            raise ValueError(f"Tome already exists: {tid}")
        header: dict[str, Any] = {
            "type": "session",
            "version": CURRENT_SESSION_VERSION,
            "id": tid,
            "timestamp": _timestamp_iso(),
            "cwd": cwd,
            "schema_version": "1.0",
        }
        if model is not None:
            header["model"] = model
        if contemplation_level is not None:
            header["contemplationLevel"] = contemplation_level
        if spells:
            header["spells"] = list(spells)

        write_handle = TomeHandle(self._tome_dir, tid, "w", self._codecs[0])
        write_handle.replace(header, [])
        return write_handle

    def create_branched_tome(
        self,
        parent_tome_id: str,
        cwd: str,
        fork_from_leaf_id: str | None = None,
        *,
        tome_id: str | None = None,
        model: str | None = None,
        contemplation_level: str | None = None,
        spells: Sequence[str] | None = None,
    ) -> TomeHandle:
        """Branch a tome by copying the ancestor chain up to a leaf entry.

        Forks stay format-native: a non-default codec forks through its own
        ``fork`` implementation (e.g. Pi produces a Pi v4 fork). Codecs
        without a native fork raise instead of silently converting formats.
        """
        resolved_parent = self._resolve_tome_id(parent_tome_id)
        if resolved_parent is None:
            # Explicit file paths are authoritative (foreign-filename
            # tolerance): a parent given as a path resolves to the file
            # itself instead of demanding an in-dir stem.
            if parent_tome_id and Path(parent_tome_id).is_file():
                resolved_parent = parent_tome_id
            else:
                raise ValueError(f"Parent tome not found: {parent_tome_id}")

        parent_read = self.open_read(resolved_parent)
        parent_codec = parent_read.codec
        if not isinstance(parent_codec, TomeV1Codec):
            tid = _validate_tome_id(tome_id) if tome_id else _generate_id()
            parent_path, _ = self._locate(resolved_parent)
            try:
                new_path = parent_codec.fork(
                    source=str(parent_path),
                    dest_dir=str(self._tome_dir),
                    new_id=tid,
                    leaf_id=fork_from_leaf_id,
                    cwd=cwd,
                )
            except NotImplementedError as exc:
                raise ValueError(
                    f"Cannot fork {parent_codec.name} sessions: {exc}"
                ) from exc
            return TomeHandle(
                self._tome_dir, tid, "w", parent_codec, path=Path(new_path)
            )

        parent_meta = parent_read.get_metadata()
        if parent_meta is None:
            raise ValueError(f"Parent tome metadata not found: {parent_tome_id}")

        parent_entries = parent_read.get_entries()
        entries = (
            parent_read.get_entries_to_leaf(fork_from_leaf_id)
            if fork_from_leaf_id
            else list(parent_entries)
        )

        tid = _validate_tome_id(tome_id) if tome_id else _generate_id()
        if (self._tome_dir / f"{tid}.jsonl").exists():
            raise ValueError(f"Tome already exists: {tid}")
        header: dict[str, Any] = {
            "type": "session",
            "version": CURRENT_SESSION_VERSION,
            "id": tid,
            "timestamp": _timestamp_iso(),
            "cwd": cwd or parent_meta.cwd,
            "schema_version": "1.0",
            "parentSession": parent_meta.id,
            "activeLeafId": fork_from_leaf_id or parent_meta.active_leaf_id,
        }
        header["model"] = model if model is not None else parent_meta.model
        header["contemplationLevel"] = (
            contemplation_level
            if contemplation_level is not None
            else parent_meta.contemplation_level
        )
        header["spells"] = (
            list(spells) if spells is not None else list(parent_meta.spells)
        )

        write_handle = TomeHandle(self._tome_dir, tid, "w", self._codecs[0])
        write_handle.replace(header, entries)
        return write_handle

    def open_read(self, tome_id: str) -> TomeHandle:
        """Open a read handle; raises FileNotFoundError when unknown."""
        path, codec = self._locate(tome_id)
        label = self._label_for(path)
        handle = TomeHandle(self._tome_dir, label, "r", codec, path=path)
        handle.get_metadata()
        return handle

    def open_write(self, tome_id: str) -> TomeHandle:
        """Open a write handle; raises FileNotFoundError when unknown.

        Tomes are only created via create_tome/create_branched_tome so the
        recorded cwd is always the project directory, never the tome dir.
        """
        path, codec = self._locate(tome_id)
        label = self._label_for(path)
        handle = TomeHandle(self._tome_dir, label, "w", codec, path=path)
        if handle.get_metadata() is None:
            raise ValueError(f"Tome has no readable header: {tome_id}")
        return handle

    def get_entries(
        self,
        tome_id: str,
        entry_type: TomeEntryType | None = None,
        limit: int | None = None,
    ) -> list[TomeEntry]:
        entries = self.open_read(tome_id).get_entries()
        if entry_type is not None:
            entries = [e for e in entries if e.type == entry_type]
        if limit is not None:
            entries = entries[-limit:]
        return entries

    def get_entry(self, tome_id: str, entry_id: str) -> TomeEntry | None:
        for entry in self.get_entries(tome_id):
            if entry.id == entry_id:
                return entry
        return None

    def get_leaf_id(self, tome_id: str) -> str | None:
        """The entry the session's Leaf currently points at.

        Branch-tip resolution is codec-owned: whatever the format uses to
        track its tip (Tome v1's LEAF entries, Pi's branch-tip value writes),
        the codec resolves it from the raw header and entries.
        """
        handle = self.open_read(tome_id)
        return handle.codec.leaf_id(handle.get_header() or {}, handle.get_entries())

    def list_leaves(self, tome_id: str) -> list[str]:
        """Content entries never referenced as another entry's parent."""
        content = [e for e in self.get_entries(tome_id) if e.type != TomeEntryType.LEAF]
        if not content:
            return []
        parent_ids = {e.parent_id for e in content if e.parent_id is not None}
        return [e.id for e in content if e.id not in parent_ids]

    def get_parent_summoner_entry(
        self, tome_id: str, leaf_id: str | None = None
    ) -> TomeEntry | None:
        """Parent of the most recent user message along the leaf branch."""
        target_leaf = leaf_id or self.get_leaf_id(tome_id)
        branch = self.get_entries_for_context(tome_id, leaf_id=target_leaf)
        for entry in reversed(branch):
            if (
                entry.type == TomeEntryType.MESSAGE
                and (entry.payload or {}).get("role") == "user"
            ):
                if entry.parent_id is None:
                    return None
                return self.get_entry(tome_id, entry.parent_id)
        return None

    def get_entries_for_context(
        self,
        tome_id: str,
        leaf_id: str | None = None,
        max_entries: int | None = None,
    ) -> list[TomeEntry]:
        if leaf_id:
            handle = self.open_read(tome_id)
            entries = handle.get_entries_to_leaf(leaf_id)
        else:
            entries = self.get_entries(tome_id)
        if max_entries is not None:
            entries = entries[-max_entries:]
        return entries

    def read_last_n_entries(self, tome_id: str, limit: int) -> list[TomeEntry]:
        if limit <= 0:
            return []
        return self.get_entries(tome_id)[-limit:]

    def _resolve_path(self, tome_id: str) -> Path | None:
        """Resolve an id, prefix, stem, or file path to a file, without any
        codec detection. Returns None when nothing matches."""
        if not tome_id:
            return None
        candidate = Path(tome_id)
        if candidate.is_file():
            return candidate
        stem = self._resolve_tome_id(tome_id)
        if stem is not None:
            return self._tome_dir / f"{stem}.jsonl"
        return None

    def verify_integrity(self, tome_id: str) -> TomeIntegrityReport:
        """Audit a tome file without touching any cache.

        The deep structural audit is Tome v1-specific; files owned by other
        codecs get a report that says so instead of a bogus pass/fail.
        Session-looking files no codec claims are audited as (broken) v1 so
        the report carries the version issue instead of raising.
        """
        path = self._resolve_path(tome_id)
        # Explicit file paths are authoritative: the header id is the
        # session's identity, so the stem-vs-header check below does not
        # apply (it only guards in-dir tomes against filename tampering).
        explicit_path = bool(tome_id) and Path(tome_id).is_file()
        target = path.stem if path is not None else _validate_tome_id(tome_id)
        if path is None or not path.exists():
            return TomeIntegrityReport(
                valid=False,
                tome_id=target,
                issues=[
                    TomeIntegrityIssue(
                        line_number=0,
                        message=f"Tome file does not exist: {path}",
                    )
                ],
            )
        detected = self._detect_file(path)
        codec = detected[0] if detected is not None else None
        if codec is not None and not isinstance(codec, TomeV1Codec):
            return TomeIntegrityReport(
                valid=False,
                tome_id=target,
                issues=[
                    TomeIntegrityIssue(
                        line_number=0,
                        message=(
                            "Integrity audit only supports Tome v1 sessions; "
                            f"'{path.name}' is owned by the "
                            f"{getattr(codec, 'name', codec)} codec."
                        ),
                    )
                ],
            )

        issues: list[TomeIntegrityIssue] = []
        total_lines = 0
        valid_entries_count = 0
        try:
            with path.open("r", encoding="utf-8") as f:
                header_line = f.readline()
                total_lines += 1
                header_raw = header_line.rstrip("\r\n")
                if not header_raw.strip():
                    issues.append(
                        TomeIntegrityIssue(
                            line_number=1,
                            message="Missing session header (line is empty)",
                            raw_line=header_raw,
                        )
                    )
                else:
                    try:
                        header = json.loads(header_raw)
                    except json.JSONDecodeError as e:
                        header = None
                        issues.append(
                            TomeIntegrityIssue(
                                line_number=1,
                                message=f"Corrupted session header: invalid JSON: {e}",
                                raw_line=header_raw,
                            )
                        )
                    if header is not None:
                        if not isinstance(header, dict):
                            issues.append(
                                TomeIntegrityIssue(
                                    line_number=1,
                                    message="Invalid session header: "
                                    "expected JSON object",
                                    raw_line=header_raw,
                                )
                            )
                        else:
                            if header.get("type") != "session":
                                issues.append(
                                    TomeIntegrityIssue(
                                        line_number=1,
                                        message="Invalid session header: "
                                        "missing or invalid 'type' "
                                        f"(expected 'session', got "
                                        f"{header.get('type')!r})",
                                        raw_line=header_raw,
                                    )
                                )
                            if not header.get("id") or not isinstance(
                                header.get("id"), str
                            ):
                                issues.append(
                                    TomeIntegrityIssue(
                                        line_number=1,
                                        message="Invalid session header: missing or "
                                        "invalid 'id'",
                                        raw_line=header_raw,
                                    )
                                )
                            elif header.get("id") != path.stem and not explicit_path:
                                issues.append(
                                    TomeIntegrityIssue(
                                        line_number=1,
                                        message=(
                                            "Header ID mismatch: expected "
                                            f"'{path.stem}', got '{header.get('id')}'"
                                        ),
                                        raw_line=header_raw,
                                    )
                                )
                            if "cwd" not in header or not isinstance(
                                header.get("cwd"), str
                            ):
                                issues.append(
                                    TomeIntegrityIssue(
                                        line_number=1,
                                        message="Invalid session header: missing or "
                                        "invalid 'cwd'",
                                        raw_line=header_raw,
                                    )
                                )
                            if "timestamp" not in header or not isinstance(
                                header.get("timestamp"), str
                            ):
                                issues.append(
                                    TomeIntegrityIssue(
                                        line_number=1,
                                        message="Invalid session header: missing or "
                                        "invalid 'timestamp'",
                                        raw_line=header_raw,
                                    )
                                )
                            version_raw = header.get("version", CURRENT_SESSION_VERSION)
                            try:
                                version = int(version_raw)
                                if version != CURRENT_SESSION_VERSION:
                                    issues.append(
                                        TomeIntegrityIssue(
                                            line_number=1,
                                            message=f"Unsupported session version: "
                                            f"{version}",
                                            raw_line=header_raw,
                                        )
                                    )
                            except (ValueError, TypeError):
                                issues.append(
                                    TomeIntegrityIssue(
                                        line_number=1,
                                        message="Invalid session version: "
                                        f"{version_raw}",
                                        raw_line=header_raw,
                                    )
                                )

                for idx, raw_line in enumerate(f, start=2):
                    total_lines += 1
                    line = raw_line.rstrip("\r\n")
                    if not line.strip():
                        issues.append(
                            TomeIntegrityIssue(
                                line_number=idx,
                                message="Empty or blank line in JSONL stream",
                                raw_line=line,
                            )
                        )
                        continue
                    try:
                        entry_raw = json.loads(line)
                    except json.JSONDecodeError as e:
                        issues.append(
                            TomeIntegrityIssue(
                                line_number=idx,
                                message=f"Invalid JSON (truncated or corrupted): {e}",
                                raw_line=line,
                            )
                        )
                        continue
                    if not isinstance(entry_raw, dict):
                        issues.append(
                            TomeIntegrityIssue(
                                line_number=idx,
                                message="Malformed entry: expected JSON object",
                                raw_line=line,
                            )
                        )
                        continue
                    broken = False
                    if not entry_raw.get("id") or not isinstance(
                        entry_raw.get("id"), str
                    ):
                        issues.append(
                            TomeIntegrityIssue(
                                line_number=idx,
                                message="Malformed entry: missing or invalid 'id' "
                                "field",
                                raw_line=line,
                            )
                        )
                        broken = True
                    entry_type_raw = entry_raw.get("type")
                    if not entry_type_raw or not isinstance(entry_type_raw, str):
                        issues.append(
                            TomeIntegrityIssue(
                                line_number=idx,
                                message="Malformed entry: missing or invalid 'type' "
                                "field",
                                raw_line=line,
                            )
                        )
                        broken = True
                    else:
                        try:
                            TomeEntryType(entry_type_raw)
                        except ValueError:
                            issues.append(
                                TomeIntegrityIssue(
                                    line_number=idx,
                                    message="Malformed entry: invalid entry type "
                                    f"'{entry_type_raw}'",
                                    raw_line=line,
                                )
                            )
                            broken = True
                    ts = entry_raw.get("timestamp")
                    if (
                        ts is None
                        or isinstance(ts, bool)
                        or not isinstance(ts, (int, float))
                    ):
                        issues.append(
                            TomeIntegrityIssue(
                                line_number=idx,
                                message="Malformed entry: missing or invalid "
                                "'timestamp' (must be number)",
                                raw_line=line,
                            )
                        )
                        broken = True
                    payload = entry_raw.get("payload")
                    if payload is not None and not isinstance(payload, dict):
                        issues.append(
                            TomeIntegrityIssue(
                                line_number=idx,
                                message="Malformed entry: 'payload' must be an "
                                "object/dict",
                                raw_line=line,
                            )
                        )
                        broken = True
                    if not broken:
                        valid_entries_count += 1

            return TomeIntegrityReport(
                valid=len(issues) == 0,
                tome_id=target,
                issues=issues,
                total_lines=total_lines,
                valid_entries_count=valid_entries_count,
            )
        except OSError as e:
            return TomeIntegrityReport(
                valid=False,
                tome_id=target,
                issues=[
                    TomeIntegrityIssue(
                        line_number=0, message=f"Failed to read file: {e}"
                    )
                ],
            )

    def replay_tome_trajectory(self, tome_id: str) -> list[ATIFTrajectoryStep]:
        """Convert session active branch to sequential list of ATIF trajectory steps."""
        meta = self.open_tome(tome_id)
        leaf_id = self.get_leaf_id(tome_id)
        branch = self.get_entries_for_context(tome_id, leaf_id=leaf_id)

        steps: list[ATIFTrajectoryStep] = []
        prev_timestamp: float | None = None

        for entry in branch:
            if entry.type != TomeEntryType.MESSAGE:
                continue

            payload = entry.payload or {}
            raw_role = payload.get("role", "")
            raw_content = payload.get("content", "")

            role: str
            content_str: str = ""
            tool_calls: list[dict[str, Any]] = []
            tool_call_id: str | None = None
            reasoning_content: str | None = None
            step_model = payload.get("model") or (meta.model if meta else None)

            if raw_role == "user":
                role = "user"
                if isinstance(raw_content, str):
                    content_str = raw_content
                elif isinstance(raw_content, list):
                    texts = [
                        block.get("text", "")
                        for block in raw_content
                        if isinstance(block, dict) and block.get("type") == "text"
                    ]
                    content_str = "\n".join(texts)
                else:
                    content_str = str(raw_content)

            elif raw_role == "assistant":
                role = "assistant"
                if isinstance(raw_content, str):
                    content_str = raw_content
                elif isinstance(raw_content, list):
                    text_parts: list[str] = []
                    reasoning_parts: list[str] = []
                    for block in raw_content:
                        if not isinstance(block, dict):
                            continue
                        block_type = block.get("type", "")
                        if block_type == "text":
                            text_parts.append(block.get("text", ""))
                        elif block_type in ("contemplation", "thinking"):
                            reasoning_parts.append(
                                block.get("thinking") or block.get("text") or ""
                            )
                        elif block_type in ("spell_cast", "tool_use"):
                            tool_calls.append(
                                {
                                    "id": block.get("id", ""),
                                    "name": (
                                        block.get("spell") or block.get("name") or ""
                                    ),
                                    "args": (
                                        block.get("args") or block.get("input") or {}
                                    ),
                                }
                            )
                    content_str = "\n".join(text_parts)
                    if reasoning_parts:
                        reasoning_content = "\n".join(reasoning_parts)
                else:
                    content_str = str(raw_content)

            elif raw_role in ("spellResult", "tool"):
                role = "tool"
                tool_call_id = payload.get("spell_cast_id") or payload.get(
                    "tool_call_id"
                )
                if isinstance(raw_content, str):
                    content_str = raw_content
                elif isinstance(raw_content, list):
                    texts = [
                        b.get("text", "")
                        for b in raw_content
                        if isinstance(b, dict) and "text" in b
                    ]
                    content_str = "\n".join(texts) if texts else json.dumps(raw_content)
                else:
                    content_str = (
                        json.dumps(raw_content)
                        if isinstance(raw_content, (dict, list))
                        else str(raw_content)
                    )
            else:
                continue

            latency_ms = 0.0
            if prev_timestamp is not None and entry.timestamp >= prev_timestamp:
                latency_ms = round((entry.timestamp - prev_timestamp) * 1000.0, 2)
            prev_timestamp = entry.timestamp

            steps.append(
                ATIFTrajectoryStep(
                    step_id=entry.id,
                    role=role,
                    content=content_str,
                    tool_calls=tool_calls,
                    tool_call_id=tool_call_id,
                    timestamp=entry.timestamp,
                    latency_ms=latency_ms,
                    model=step_model,
                    reasoning_content=reasoning_content,
                )
            )

        return steps

    def export_atif_trajectory(
        self,
        tome_id: str,
        agent_name: str,
    ) -> dict[str, Any]:
        """Export session trajectory conforming to ATIF.

        ``agent_name`` is required because a Tome does not record it:
        ``TomeMetadata`` carries the model and the Contemplation level, never
        the Mvge that produced the transcript. Defaulting it here would mean
        inventing a fact this module cannot know -- and the name it once
        invented belonged to a marketplace artifact.
        """
        resolved = self._resolve_tome_id(tome_id)
        if resolved is None and Path(tome_id).is_file():
            # Explicit file paths are authoritative: operate on the file as
            # given instead of demanding an in-dir stem.
            target = tome_id
        else:
            target = resolved or _validate_tome_id(tome_id)

        meta = self.open_tome(target)
        steps = self.replay_tome_trajectory(target)

        # Aggregate metrics from messages along the active branch
        input_tokens = 0
        output_tokens = 0
        reasoning_tokens = 0

        leaf_id = self.get_leaf_id(target)
        branch = self.get_entries_for_context(target, leaf_id=leaf_id)
        for entry in branch:
            if entry.type == TomeEntryType.MESSAGE:
                payload = entry.payload or {}
                mana = payload.get("mana_usage") or payload.get("usage") or {}
                in_tok = int(mana.get("prompt_tokens") or mana.get("input_tokens") or 0)
                out_tok = int(
                    mana.get("completion_tokens") or mana.get("output_tokens") or 0
                )
                reason_tok = int(
                    mana.get("reasoning_tokens")
                    or mana.get("contemplation_tokens")
                    or 0
                )
                input_tokens += in_tok
                output_tokens += out_tok
                reasoning_tokens += reason_tok

        metrics = ATIFMetrics(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            reasoning_tokens=reasoning_tokens,
        )

        trajectory = ATIFTrajectory(
            trajectory_id=meta.id if meta else target,
            agent_name=agent_name,
            model=meta.model if meta and meta.model else "",
            created_at=meta.created_at if meta else datetime.now(UTC).isoformat(),
            steps=steps,
            metrics=metrics,
            completed=True,
        )

        return trajectory.to_dict()
