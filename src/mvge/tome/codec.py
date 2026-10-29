"""Session format codecs.

A codec owns everything about one session file format: detecting its files,
parsing headers and entries, serialising entries back to file lines, and
tracking the active branch tip (leaf). Mvge core stays format-agnostic;
Tome v1 is simply the built-in codec. Runes can register additional codecs
(e.g. Pi sessions) through ``session_codecs`` in their manifest.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from mvge.tome.types import (
    CURRENT_SESSION_VERSION,
    TomeEntry,
    TomeEntryType,
    TomeMetadata,
    TomeVersionError,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppendPlan:
    """Instructions for persisting one appended entry.

    ``lines`` holds JSON-serializable body lines, one element per file line.
    A multi-write transaction (e.g. Pi's entry-plus-tip commit) is a single
    element holding a JSON array, matching one-line-per-transaction layouts.

    When ``rewrite`` is True the handle atomically replaces the whole file
    with ``header`` + ``lines`` instead of appending; this is how a codec
    performs a format migration (e.g. Pi's v3-to-v4 upgrade on first write).
    ``header`` is required when ``rewrite`` is True.

    ``stored`` is the entry the handle keeps in its in-memory cache; codecs
    may stamp it with format-assigned fields such as sequence numbers.
    """

    lines: list[Any]
    stored: TomeEntry
    rewrite: bool = False
    header: dict[str, Any] | None = None


@runtime_checkable
class SessionCodec(Protocol):
    """One session file format.

    A codec owns detection, parsing, serialisation, branch-tip bookkeeping,
    fork, and validation for its format. Codecs may keep per-session read
    caches keyed by session id, but must not share mutable state across
    sessions. Each codec defines its own validation contract: Tome v1 skips
    damaged lines with a warning, while stricter formats (e.g. Pi) raise on
    structural problems during load.
    """

    name: str

    def detect(self, header: dict[str, Any]) -> bool:
        """Return True when this codec owns a file with the given header."""
        ...

    def looks_like_session(self, header: dict[str, Any]) -> bool:
        """Return True when the header belongs to this codec's format family,
        even at an unsupported version. Session-looking headers that no codec
        detects raise TomeVersionError; anything else is skipped as foreign."""
        ...

    def parse_header(self, header: dict[str, Any]) -> TomeMetadata:
        """Convert a detected header to session metadata.

        Raises TomeVersionError when the header claims an unsupported version.
        """
        ...

    def parse_entries(
        self, header: dict[str, Any], lines: list[str], *, source: str
    ) -> list[TomeEntry]:
        """Parse body lines into entries given the already-parsed header.

        Validation is codec-defined: see the class docstring.
        """
        ...

    def serialize_entry(self, entry: TomeEntry) -> dict[str, Any] | None:
        """Serialise one entry to a file line. Return None to omit the entry
        from the file (used by codecs for bookkeeping-only entries)."""
        ...

    def serialize_new_entry(
        self, entry: TomeEntry, existing: list[TomeEntry]
    ) -> tuple[dict[str, Any], TomeEntry]:
        """Serialise a brand-new entry being appended. Returns the file line
        and the entry to keep in the in-memory cache (codecs may stamp the
        entry with format-assigned fields such as sequence numbers)."""
        ...

    def plan_append(
        self,
        entry: TomeEntry,
        existing: list[TomeEntry],
        header: dict[str, Any],
    ) -> AppendPlan:
        """Plan the persistence of one appended entry.

        The default implementation appends a single line produced by
        serialize_new_entry. Codecs needing multi-line transactions or
        whole-file rewrites (e.g. Pi's v3-to-v4 migration on first write)
        override this.
        """
        line, stored = self.serialize_new_entry(entry, existing)
        return AppendPlan(lines=[line], stored=stored)

    def append_tip_lines(
        self,
        header: dict[str, Any],
        entries: list[TomeEntry],
        leaf: TomeEntry,
    ) -> list[Any] | None:
        """Return body lines to append for a branch-tip update, or None to
        fall back to apply_leaf plus a full rewrite.

        Tip-only codecs (Pi stores the tip as a value write, not a marker
        entry) override this. The leaf entry itself is not added to the
        entry cache: it was never a file entry, and tip-only codecs own tip
        bookkeeping outside the entry list.
        """
        return None

    def apply_leaf(
        self,
        header: dict[str, Any],
        entries: list[TomeEntry],
        leaf: TomeEntry,
    ) -> tuple[dict[str, Any], list[TomeEntry]]:
        """Record a new branch tip. Returns the updated header and entries."""
        ...

    def leaf_id(self, header: dict[str, Any], entries: list[TomeEntry]) -> str | None:
        """Resolve the current branch tip entry id, or None when unknown."""
        ...

    def repair_on_open(self, source: str) -> bool:
        """Repair open-time damage to the session file.

        Called after header detection on every snapshot load. Return True
        when the file was rewritten so the handle reloads it. The default
        is a no-op: Pi repairs a torn final line here through an atomic
        rewrite, mirroring its own open path.
        """
        return False

    def fork(
        self,
        *,
        source: str,
        dest_dir: str,
        new_id: str,
        leaf_id: str | None = None,
        cwd: str | None = None,
    ) -> str:
        """Create a native fork of the session file at ``source`` inside
        ``dest_dir``.

        ``leaf_id`` is the entry the fork branches from (defaults to the
        session's current leaf); ``cwd`` is the destination working directory
        (defaults to the source session's). Returns the new session file's
        path. Raises NotImplementedError when this codec has no native fork;
        the factory refuses the fork rather than silently converting formats.
        """
        raise NotImplementedError(f"{self.name} sessions cannot be forked natively")


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _version_of(header: dict[str, Any]) -> int | None:
    """Header version, defaulting to the current version when absent.

    Returns None when the version value is not parseable as an int.
    """
    return _coerce_int(header.get("version", CURRENT_SESSION_VERSION))


def _parse_tome_entry(raw: dict[str, Any]) -> TomeEntry:
    return TomeEntry(
        id=raw["id"],
        parent_id=raw.get("parentId"),
        type=TomeEntryType(raw["type"]),
        timestamp=float(raw["timestamp"]),
        payload=raw.get("payload", {}),
    )


class TomeV1Codec(SessionCodec):
    """The built-in Mvge session format."""

    name = "tome-v1"

    def detect(self, header: dict[str, Any]) -> bool:
        return (
            isinstance(header, dict)
            and header.get("type") == "session"
            and _version_of(header) == CURRENT_SESSION_VERSION
        )

    def looks_like_session(self, header: dict[str, Any]) -> bool:
        return isinstance(header, dict) and header.get("type") == "session"

    def parse_header(self, header: dict[str, Any]) -> TomeMetadata:
        version = _version_of(header)
        if version != CURRENT_SESSION_VERSION:
            raise TomeVersionError(
                header.get("version"),
                f"Unsupported Tome version: {header.get('version')!r} "
                f"(expected {CURRENT_SESSION_VERSION})",
            )
        return TomeMetadata(
            id=header["id"],
            created_at=header["timestamp"],
            cwd=header["cwd"],
            name=header.get("name"),
            parent_tome_id=header.get("parentSession"),
            active_leaf_id=header.get("activeLeafId"),
            schema_version=header.get("schema_version", "1.0"),
            version=version,
            model=header.get("model"),
            contemplation_level=header.get("contemplationLevel"),
            spells=list(header.get("spells", []) or []),
        )

    def parse_entries(
        self, header: dict[str, Any], lines: list[str], *, source: str
    ) -> list[TomeEntry]:
        entries: list[TomeEntry] = []
        for line_no, raw in enumerate(lines, start=2):
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError as exc:
                logger.warning(
                    "Skipping damaged line %d in %s: %s", line_no, source, exc
                )
                continue
            try:
                entries.append(_parse_tome_entry(obj))
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                logger.warning(
                    "Skipping damaged line %d in %s: %s", line_no, source, exc
                )
        return entries

    def serialize_entry(self, entry: TomeEntry) -> dict[str, Any] | None:
        return entry.to_dict()

    def serialize_new_entry(
        self, entry: TomeEntry, existing: list[TomeEntry]
    ) -> tuple[dict[str, Any], TomeEntry]:
        return entry.to_dict(), entry

    def apply_leaf(
        self,
        header: dict[str, Any],
        entries: list[TomeEntry],
        leaf: TomeEntry,
    ) -> tuple[dict[str, Any], list[TomeEntry]]:
        target = leaf.payload.get("targetId")
        if target:
            header["activeLeafId"] = target
        return header, [*entries, leaf]

    def leaf_id(self, header: dict[str, Any], entries: list[TomeEntry]) -> str | None:
        for entry in reversed(entries):
            if entry.type == TomeEntryType.LEAF:
                target = entry.payload.get("targetId")
                if isinstance(target, str) and target:
                    return target
        active = header.get("activeLeafId")
        return active if isinstance(active, str) and active else None

    def fork(
        self,
        *,
        source: str,
        dest_dir: str,
        new_id: str,
        leaf_id: str | None = None,
        cwd: str | None = None,
    ) -> str:
        # Tome v1 forks stay in TomeHandleFactory.create_branched_tome, which
        # filters the ancestor chain; the codec-level hook is unused.
        raise NotImplementedError(
            "tome-v1 forks go through TomeHandleFactory.create_branched_tome"
        )
