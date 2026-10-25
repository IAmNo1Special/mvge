"""Durable, append-only audit log for rune ops.

One JSON Lines record per mutating rune op, written by the engine (never
by runes directly): runes emit events through ``RuneAPI.audit``, which
stamps the rune's identity and the timestamp — a rune cannot forge another
rune's entries or rewrite history, because the log exposes no read/rewrite
API, only append.

Storage follows the approval rune's durable audit-trail precedent:
append-only ``audit.jsonl`` with an exclusive lock held across the
write, ``fsync`` before release, restrictive permissions, and
size/age rotation that archives (never deletes) old records.

Location is the user-scope ``.agents`` directory — the global per-user
layer — plus ``extensions``: ``$MVGEOS_GLOBAL_DIR`` is the ``.agents`` dir
itself when set, else ``~/.agents`` (the skills-bridge scope convention).
Resolved log path: ``$MVGEOS_GLOBAL_DIR/extensions/audit.jsonl`` when
``$MVGEOS_GLOBAL_DIR`` is set, else ``~/.agents/extensions/audit.jsonl``.
Rune-op audit records are agent-level mutations that outlive any single
session, so session-scoped tome storage is the wrong home.

The ``rune`` field of each record names the emitter: a rune's manifest
name for rune-op events (stamped by ``RuneAPI.audit``), or ``"engine"``
for harness-initiated events such as mid-session reloads (``Mvge.reload``),
which no rune performs.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

from mvge.core.layers import extensions_dir


class _Fcntl(Protocol):
    """The slice of the POSIX ``fcntl`` module this log depends on."""

    LOCK_EX: int

    def flock(self, fd: int, operation: int) -> None: ...


_fcntl_module: Any
try:
    import fcntl as _mod
except ImportError:  # pragma: no cover - Windows
    _fcntl_module = None
else:
    _fcntl_module = _mod

# Typed through a Protocol so the Windows None fallback and the real module
# both satisfy mypy strict on either platform without a platform-conditional
# ignore comment (which strict mode flags as unused on the other one).
_FCNTL: _Fcntl | None = cast("_Fcntl | None", _fcntl_module)

AUDIT_FILENAME = "audit.jsonl"
DEFAULT_MAX_BYTES = 25 * 1024 * 1024
DEFAULT_MAX_AGE_DAYS = 30


class AuditError(Exception):
    """The audit append failed.

    Callers must surface this loudly: an op that cannot prove it happened
    must never report silent success.
    """


def utcnow() -> str:
    """Current UTC time as ``YYYY-MM-DDTHH:MM:SSZ`` (approval format)."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_rune_ops_dir() -> Path:
    """User-scope ``.agents`` dir + ``extensions``.

    Delegates to the shared resolver so the audit log cannot land in a
    different global layer than the runes it is auditing.
    """
    return extensions_dir()


class RuneAuditLog:
    """Append-only JSONL audit log with rotation, owned by the engine."""

    def __init__(
        self,
        data_dir: Path,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    ) -> None:
        """Initialize the instance."""
        self.data_dir = Path(data_dir)
        self.max_bytes = max_bytes
        self.max_age_days = max_age_days

    @property
    def audit_path(self) -> Path:
        """Audit path."""
        return self.data_dir / AUDIT_FILENAME

    def append_event(self, record: dict[str, Any]) -> None:
        """Durably append one audit record.

        Raises:
            AuditError: If the record could not be written durably.
        """
        self._append(dict(record))

    def _append(self, record: dict[str, Any]) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(self.data_dir, 0o700)
            self._maybe_rotate()
            line = json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n"
            with open(self.audit_path, "a", encoding="utf-8") as handle:
                if _FCNTL is not None:
                    _FCNTL.flock(handle.fileno(), _FCNTL.LOCK_EX)
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(self.audit_path, 0o600)
        except OSError as exc:
            raise AuditError(f"audit append failed: {exc}") from exc

    def _maybe_rotate(self) -> None:
        path = self.audit_path
        if not path.exists():
            return
        try:
            stat_result = path.stat()
        except OSError:
            return
        too_big = self.max_bytes > 0 and stat_result.st_size >= self.max_bytes
        too_old = (
            self.max_age_days >= 0
            and (time.time() - stat_result.st_mtime) > self.max_age_days * 86400
        )
        if too_big or too_old:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            archive = self.data_dir / f"audit-{stamp}.jsonl"
            suffix = 0
            while archive.exists():
                suffix += 1
                archive = self.data_dir / f"audit-{stamp}-{suffix}.jsonl"
            try:
                os.replace(path, archive)
                os.chmod(archive, 0o600)
            except OSError:
                pass

    def recent(self, limit: int) -> list[dict[str, Any]]:
        """Newest-last slice of recent records; corrupt lines are skipped."""
        path = self.audit_path
        if not path.exists():
            return []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        records: list[dict[str, Any]] = []
        for line in lines[-limit:]:
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
        return records
