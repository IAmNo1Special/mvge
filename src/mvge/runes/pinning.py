"""Rune hash pinning: provenance recorded at install, verified at load.

A pinned rune is unmodified, not benign. The sidecar (``.rune-pin.json``)
records, at install time, where a rune came from and the SHA-256 tree hash
of its staged source tree. Every load re-hashes the installed tree and
refuses the rune when the hash differs, when the sidecar is missing, or
when the rune is revoked. Changed code is never silently loaded.

The sidecar is deliberately a new file, not a mutation of ``.install-id``:
that id is policy identity, preserved across updates, and changing its
semantics would invalidate approval policies stamped with it.

Hash exclusions mirror the workspace-trust digest: ``__pycache__`` and
``*.pyc``/``*.pyo`` (importing a rune writes bytecode next to its source,
so hashing it would make a first load invalidate its own pin), ``.venv``/
``venv`` site-packages (installed after staging), ``.git`` (installer
metadata, not rune code), ``.install-id`` (policy identity), and the
sidecar itself (the hash must not cover its own record).

Index integrity limit: index ``commit``/``tree_hash`` fields are only as
trustworthy as the index fetch (HTTPS plus repo access control). This is
transport trust, not publisher signing (Phase 6). A version later found
malicious is pulled by revocation, not by pinning: an index entry marked
``revoked: true`` refuses installation, and a local blocklist entry refuses
a load even when its hash matches.

The manifest ``RealmProvider`` gate is a declaration, not authentication:
pinning makes that declaration reviewable (the recorded tree is the
reviewed tree), it does not authenticate the declarer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from mvge.core.layers import approval_dir

#: Sidecar file written into every installer-managed rune directory.
PIN_FILENAME = ".rune-pin.json"

#: Local revocation blocklist, in user-owned storage beside approvals.
BLOCKLIST_FILENAME = "rune-blocklist.json"

#: Directory names never hashed: caches, dependency venvs, VCS metadata.
_TREE_HASH_EXCLUDED_DIRS = frozenset({"__pycache__", ".venv", "venv", ".git"})

#: File names never hashed: policy identity and the sidecar itself.
_TREE_HASH_EXCLUDED_FILES = frozenset({".install-id", PIN_FILENAME})

#: File suffixes never hashed: bytecode caches.
_TREE_HASH_EXCLUDED_SUFFIXES = frozenset({".pyc", ".pyo"})

#: Source kinds a sidecar can record. ``manual`` is the adopt kind: a
#: manually placed rune whose current tree an operator explicitly reviewed
#: with ``mvge rune trust --adopt``. Install-time kinds are the other three.
SOURCE_KINDS = frozenset({"git", "catalog", "local", "manual"})

PinStatus = Literal["ok", "missing", "mismatch", "revoked", "corrupt"]


def compute_rune_tree_hash(rune_dir: Path) -> str:
    """Deterministic SHA-256 tree digest of a rune directory.

    Walks files in sorted relative-posix-path order, hashing each path
    plus its bytes, with the exclusion list above. Returns ``sha256:empty``
    when no hashable file exists, matching the workspace-trust convention.
    """
    import hashlib

    files_to_hash: list[tuple[str, Path]] = []
    for child in rune_dir.rglob("*"):
        if not child.is_file():
            continue
        rel_parts = child.relative_to(rune_dir).parts
        if any(part in _TREE_HASH_EXCLUDED_DIRS for part in rel_parts):
            continue
        if child.name in _TREE_HASH_EXCLUDED_FILES:
            continue
        if child.suffix in _TREE_HASH_EXCLUDED_SUFFIXES:
            continue
        files_to_hash.append((child.relative_to(rune_dir).as_posix(), child))
    if not files_to_hash:
        return "sha256:empty"
    files_to_hash.sort(key=lambda item: item[0])
    hasher = hashlib.sha256()
    for rel_posix, file_path in files_to_hash:
        hasher.update(rel_posix.encode("utf-8") + b"\0")
        try:
            hasher.update(file_path.read_bytes() + b"\0")
        except OSError:
            continue
    return "sha256:" + hasher.hexdigest()


@dataclass
class RunePin:
    """One recorded provenance decision for an installed rune."""

    source_kind: str
    tree_hash: str
    git_url: str | None = None
    commit: str | None = None
    local_path: str | None = None
    adopted: bool = False
    reviewed_at: str | None = None
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "source_kind": self.source_kind,
            "git_url": self.git_url,
            "commit": self.commit,
            "tree_hash": self.tree_hash,
            "local_path": self.local_path,
            "adopted": self.adopted,
            "reviewed_at": self.reviewed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunePin:
        if not isinstance(data, dict):
            raise ValueError("Rune pin must be a JSON object.")
        source_kind = data.get("source_kind")
        tree_hash = data.get("tree_hash")
        if source_kind not in SOURCE_KINDS:
            raise ValueError(f"Rune pin has unknown source_kind {source_kind!r}.")
        if not isinstance(tree_hash, str) or not tree_hash:
            raise ValueError("Rune pin is missing tree_hash.")
        git_url = data.get("git_url")
        commit = data.get("commit")
        local_path = data.get("local_path")
        for field_name, value in (
            ("git_url", git_url),
            ("commit", commit),
            ("local_path", local_path),
        ):
            if value is not None and not isinstance(value, str):
                raise ValueError(f"Rune pin field {field_name!r} must be a string.")
        return cls(
            source_kind=str(source_kind),
            tree_hash=tree_hash,
            git_url=git_url,
            commit=commit,
            local_path=local_path,
            adopted=bool(data.get("adopted", False)),
            reviewed_at=data.get("reviewed_at"),
            version=int(data.get("version", 1)),
        )


def write_pin(rune_dir: Path, pin: RunePin) -> Path:
    """Write a sidecar into a rune directory, returning its path."""
    pin_path = Path(rune_dir) / PIN_FILENAME
    pin_path.write_text(
        json.dumps(pin.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return pin_path


def read_pin(rune_dir: Path) -> RunePin | None:
    """Read a rune's sidecar, or None when the rune has none.

    Raises:
        ValueError: If the sidecar exists but is unreadable or malformed.
            A corrupt pin is not a missing pin: the loader must refuse
            with a corruption diagnostic, not an adopt diagnostic.
    """
    pin_path = Path(rune_dir) / PIN_FILENAME
    if not pin_path.is_file():
        return None
    try:
        data = json.loads(pin_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Rune pin {pin_path} is unreadable: {exc}") from exc
    return RunePin.from_dict(data)


def default_blocklist_path() -> Path:
    """User-owned revocation blocklist path, resolved on every call."""
    return approval_dir() / BLOCKLIST_FILENAME


def read_blocklist(blocklist_path: Path | None = None) -> set[str]:
    """Revoked rune names from the local blocklist, empty when absent.

    Accepts ``{"version": 1, "revoked": [...]}`` or a bare JSON list, so
    an operator can maintain the file by hand. Anything else unreadable
    reads as empty rather than refusing every load for a typo.
    """
    path = (
        Path(blocklist_path) if blocklist_path is not None else default_blocklist_path()
    )
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if isinstance(data, list):
        return {str(name) for name in data if name}
    if isinstance(data, dict):
        revoked = data.get("revoked")
        if isinstance(revoked, list):
            return {str(name) for name in revoked if name}
    return set()


def is_revoked(rune_name: str, blocklist_path: Path | None = None) -> bool:
    """Whether a rune name is on the local revocation blocklist."""
    return rune_name in read_blocklist(blocklist_path)


@dataclass
class PinVerdict:
    """The outcome of verifying one installed rune against its sidecar."""

    status: PinStatus
    message: str
    remedy: str


def verify_rune_pin(
    rune_dir: Path, rune_name: str, *, blocklist_path: Path | None = None
) -> PinVerdict:
    """Verify an installed rune: revocation, sidecar, then tree hash.

    Revocation is checked first and wins over a matching hash: a version
    later found malicious must stay refused even though it is unmodified.
    """
    if is_revoked(rune_name, blocklist_path):
        return PinVerdict(
            status="revoked",
            message=(
                f"Rune '{rune_name}' is revoked and was refused. Revocation "
                "applies even when the recorded hash matches."
            ),
            remedy=(
                f"Remove '{rune_name}' from the revocation blocklist only if "
                "you have reviewed the revocation, or uninstall the rune."
            ),
        )
    try:
        pin = read_pin(rune_dir)
    except ValueError as exc:
        return PinVerdict(
            status="corrupt",
            message=f"Rune '{rune_name}' was refused: {exc}",
            remedy=(
                f"Reinstall the rune, or run 'mvge rune trust --adopt "
                f"{rune_name}' after reviewing its current contents."
            ),
        )
    if pin is None:
        return PinVerdict(
            status="missing",
            message=(
                f"Rune '{rune_name}' has no provenance sidecar: it was "
                "installed before hash pinning, or placed here by hand, "
                "so its contents are unreviewed."
            ),
            remedy=(
                f"Run 'mvge rune trust --adopt {rune_name}' after reviewing "
                "its contents, or reinstall the rune."
            ),
        )
    current = compute_rune_tree_hash(rune_dir)
    if current != pin.tree_hash:
        return PinVerdict(
            status="mismatch",
            message=(
                f"Rune '{rune_name}' was refused: its contents no longer "
                "match the recorded hash (recorded tree changed on disk)."
            ),
            remedy=(
                "Reinstall the rune to restore the recorded version, or, "
                f"if you made this change deliberately and have reviewed it, "
                f"run 'mvge rune trust --adopt {rune_name}' to record the "
                "new hash."
            ),
        )
    return PinVerdict(
        status="ok",
        message=f"Rune '{rune_name}' matches its recorded hash.",
        remedy="",
    )


def adopt_rune_pin(
    rune_dir: Path, rune_name: str, *, blocklist_path: Path | None = None
) -> RunePin:
    """Record an operator review of a rune's current tree.

    Writes a ``manual`` sidecar for the tree as it stands. This is the
    remedy for legacy sidecar-less runes and for deliberate local edits:
    it asserts a human reviewed this exact tree, nothing about whether
    the tree is benign.

    Raises:
        ValueError: If the rune is revoked: adopting a revoked rune
            would record review for a version that must stay refused.
    """
    if is_revoked(rune_name, blocklist_path):
        raise ValueError(
            f"Rune '{rune_name}' is revoked and cannot be adopted. Remove "
            "it from the revocation blocklist only if you have reviewed "
            "the revocation."
        )
    pin = RunePin(
        source_kind="manual",
        tree_hash=compute_rune_tree_hash(rune_dir),
        adopted=True,
        reviewed_at=datetime.now(UTC).isoformat(),
    )
    write_pin(rune_dir, pin)
    return pin


__all__ = [
    "BLOCKLIST_FILENAME",
    "PIN_FILENAME",
    "PinStatus",
    "PinVerdict",
    "RunePin",
    "SOURCE_KINDS",
    "adopt_rune_pin",
    "compute_rune_tree_hash",
    "default_blocklist_path",
    "is_revoked",
    "read_blocklist",
    "read_pin",
    "verify_rune_pin",
    "write_pin",
]
