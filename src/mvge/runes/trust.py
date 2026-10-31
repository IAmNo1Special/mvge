"""Workspace trust verification and content-hashed crash-safe persistence.

Follows the kernel workspace trust gate specified in ADR 0023.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from mvge.core.layers import approval_dir

logger = logging.getLogger(__name__)


class WorkspaceTrustStatus(StrEnum):
    TRUSTED = "trusted"
    DECLINED = "declined"
    UNSPECIFIED = "unspecified"


@dataclass
class WorkspaceTrustRecord:
    canonical_path: str
    digest: str
    status: WorkspaceTrustStatus | str
    updated_at: str
    trust_parent: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_path": self.canonical_path,
            "digest": self.digest,
            "status": str(self.status),
            "updated_at": self.updated_at,
            "trust_parent": self.trust_parent,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkspaceTrustRecord:
        raw_status = data.get("status", WorkspaceTrustStatus.UNSPECIFIED)
        return cls(
            canonical_path=str(data.get("canonical_path", "")),
            digest=str(data.get("digest", "")),
            status=WorkspaceTrustStatus(raw_status),
            updated_at=str(data.get("updated_at", "")),
            trust_parent=bool(data.get("trust_parent", False)),
        )


def canonicalize_workspace_path(path: Path | str) -> Path:
    """Canonicalize a workspace path.

    Resolves symlinks, queries true on-disk casing via F_GETPATH on macOS to
    prevent case-insensitive APFS alias bypasses, and applies os.path.normcase
    on Windows.
    """
    resolved = Path(path).resolve()

    if sys.platform == "darwin":
        try:
            import fcntl

            # F_GETPATH = 50 on macOS
            f_getpath = getattr(fcntl, "F_GETPATH", 50)
            fd = os.open(str(resolved), os.O_RDONLY)
            try:
                buf = bytearray(1024)
                fcntl.fcntl(fd, f_getpath, buf)
                end = buf.find(b"\0")
                raw_bytes = bytes(buf[:end]) if end != -1 else bytes(buf)
                resolved = Path(os.fsdecode(raw_bytes))
            finally:
                os.close(fd)
        except Exception:
            pass
    elif sys.platform == "win32":
        resolved = Path(os.path.normcase(str(resolved)))

    return resolved


def compute_project_agents_digest(project_root: Path | str) -> str:
    """Compute the deterministic SHA-256 tree digest of project-level .agents.

    Walks {project_root}/.agents/extensions/ and {project_root}/.agents/spells/
    in deterministic sorted relative-path order.
    Returns 'sha256:empty' if no executable files exist in those directories.

    Bytecode caches are excluded: importing a spell or rune writes
    ``__pycache__/*.pyc`` next to its source, so hashing those derived
    artifacts would make a first load invalidate its own trust record on
    the next load. Caches are never executed on their own (discovery loads
    ``.py`` sources), so excluding them does not weaken the check: any
    genuine source edit, addition, or removal still changes the digest.
    """
    root = canonicalize_workspace_path(project_root)
    scanned_dirs = [
        root / ".agents" / "extensions",
        root / ".agents" / "spells",
    ]

    files_to_hash: list[tuple[str, Path]] = []
    for sdir in scanned_dirs:
        if not sdir.is_dir():
            continue
        for child in sdir.rglob("*"):
            if not child.is_file():
                continue
            rel_parts = child.relative_to(root).parts
            if "__pycache__" in rel_parts or child.suffix in {".pyc", ".pyo"}:
                continue
            rel_posix = child.relative_to(root).as_posix()
            files_to_hash.append((rel_posix, child))

    if not files_to_hash:
        return "sha256:empty"

    files_to_hash.sort(key=lambda item: item[0])

    hasher = hashlib.sha256()
    for rel_posix, file_path in files_to_hash:
        hasher.update(rel_posix.encode("utf-8") + b"\0")
        with contextlib.suppress(OSError):
            hasher.update(file_path.read_bytes() + b"\0")

    return "sha256:" + hasher.hexdigest()


class WorkspaceTrustStore:
    """Crash-safe content-hashed store for workspace trust decisions.

    Stores records in ~/.agents/approval/trust.json using a write-ahead journal
    (trust.json.pending) with 0o600 permissions, fsync, and atomic rename.
    """

    def __init__(self, trust_file: Path | str | None = None) -> None:
        if trust_file is None:
            self._trust_file = approval_dir() / "trust.json"
        else:
            self._trust_file = Path(trust_file)
        self._pending_file = self._trust_file.with_name(
            self._trust_file.name + ".pending"
        )
        self._entries: dict[str, WorkspaceTrustRecord] = {}
        self._load()

    @property
    def trust_file(self) -> Path:
        return self._trust_file

    def _load(self) -> None:
        # Replay crash journal if stranded
        if self._pending_file.exists():
            try:
                content = self._pending_file.read_text(encoding="utf-8")
                json.loads(content)
                self._pending_file.replace(self._trust_file)
            except Exception:
                with contextlib.suppress(Exception):
                    self._pending_file.unlink(missing_ok=True)

        if not self._trust_file.exists():
            return

        try:
            content = self._trust_file.read_text(encoding="utf-8")
            data = json.loads(content)
            entries = data.get("entries", {})
            for key, rec_data in entries.items():
                if isinstance(rec_data, dict):
                    self._entries[key] = WorkspaceTrustRecord.from_dict(rec_data)
        except Exception as exc:
            logger.warning(
                "Failed to load workspace trust store from %s: %s",
                self._trust_file,
                exc,
            )

    def _save(self) -> None:
        self._trust_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "entries": {key: rec.to_dict() for key, rec in self._entries.items()},
        }
        serialized = json.dumps(data, indent=2)

        fd = os.open(
            str(self._pending_file),
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        try:
            os.write(fd, serialized.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

        os.replace(str(self._pending_file), str(self._trust_file))

    def check_trust(self, project_root: Path | str) -> WorkspaceTrustStatus:
        """Check whether a workspace is trusted, declined, or unspecified.

        Returns UNSPECIFIED if not recorded or if recorded digest does not match
        current content digest (forcing re-trust on modified code).
        """
        cpath = canonicalize_workspace_path(project_root)
        current_digest = compute_project_agents_digest(cpath)
        key = str(cpath)

        if key in self._entries:
            rec = self._entries[key]
            if rec.digest == current_digest:
                return WorkspaceTrustStatus(rec.status)
            return WorkspaceTrustStatus.UNSPECIFIED

        # Nearest-ancestor check
        for parent in cpath.parents:
            pkey = str(parent)
            if pkey in self._entries:
                prec = self._entries[pkey]
                if prec.trust_parent and prec.status == WorkspaceTrustStatus.TRUSTED:
                    return WorkspaceTrustStatus.TRUSTED

        return WorkspaceTrustStatus.UNSPECIFIED

    def set_trust(
        self,
        project_root: Path | str,
        status: WorkspaceTrustStatus | str,
        *,
        trust_parent: bool = False,
    ) -> WorkspaceTrustRecord:
        """Persist a trust decision for a workspace path."""
        cpath = canonicalize_workspace_path(project_root)
        digest = compute_project_agents_digest(cpath)
        status_enum = WorkspaceTrustStatus(status)
        updated_at = datetime.now(UTC).isoformat()

        record = WorkspaceTrustRecord(
            canonical_path=str(cpath),
            digest=digest,
            status=status_enum,
            updated_at=updated_at,
            trust_parent=trust_parent,
        )
        self._entries[str(cpath)] = record
        self._save()
        return record


__all__ = [
    "WorkspaceTrustRecord",
    "WorkspaceTrustStatus",
    "WorkspaceTrustStore",
    "canonicalize_workspace_path",
    "compute_project_agents_digest",
]
