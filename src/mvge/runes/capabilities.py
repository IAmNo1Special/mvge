"""Phase 2B capability model for runes.

A rune declares the gates it wants in its manifest ``capabilities``
field; the engine grants them from engine-owned, user-owned storage
— never from the rune code directory. This module owns the ONE
declaration table that both the runtime gate (``CapabilityGate``)
and the static audit (``mvge.runes.source_audit``) read: a
capability maps to the RuneAPI methods it governs, so an audit
verdict and a runtime verdict for the same method cannot drift.

Only gates that exist are minted:

- ``spell`` — register a spell
- ``hook`` — register a sigil handler or spell gate
- ``command`` — register a command or shortcut
- ``realm`` — register a realm factory or provider
- ``skill`` — register a skill or skill path
- ``filesystem`` — write files under declared roots, enforced
  by the Phase 5a subprocess ladder
  (``mvge.runes.subprocess_policy``). It governs no RuneAPI
  method, so its entry in ``CAPABILITY_GATES`` is an empty
  method tuple by design: its gate is the ladder, not a facade
  method. A rune declaring ``filesystem`` without write roots
  is granted nothing writable.

Network capabilities are deliberately NOT minted: no network
gate exists yet. Unknown capability ids are dropped at parse
time, never granted — fail-closed.

Legacy rule, stated plainly: a manifest with no ``capabilities``
field at all (pre-2B) is not gated — its registrations are
allowed, exactly as before 2B. A manifest that declares the
field, even as an empty list, is gated strictly: only consented
capabilities are granted. Deny-by-default for undeclared
manifests is a follow-up once every first-party manifest
declares; flipping it now would break every pre-2B rune.

Consent model: the store records, per rune, the capabilities a
host has consented to and the declaration (and its hash) seen
at record time. A granted capability is always
``consented ∩ declared``: a declaration change can never grant
a capability that was not consented to, so a new capability
requires re-consent. The stored declaration hash lets a host
detect that the declaration changed since consent.

This is narrowing plus an admission audit, not a sandbox:
in-process code can still reach objects through introspection,
and the docs must never claim otherwise.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from mvge.core.layers import extensions_dir

# The single declaration table. Runtime gate and source audit
# both import this; neither invents its own mapping.
# ``filesystem`` maps to no RuneAPI method: its gate is the
# Phase 5a subprocess ladder, not a facade call (see the module
# docstring). It is listed here so declaration, consent, and
# re-consent treat it exactly like every other capability.
CAPABILITY_GATES: dict[str, tuple[str, ...]] = {
    "spell": ("register_spell",),
    # Method names are the RuneAPI-facing names (the API's hook
    # registration method is ``on``, not the facade's
    # ``register_handler``).
    "hook": ("on", "register_spell_gate"),
    "command": ("register_command", "register_shortcut"),
    "realm": ("register_realm_factory", "register_provider"),
    "skill": ("register_skill", "register_skill_path"),
    "filesystem": (),
}

METHOD_TO_CAPABILITY: dict[str, str] = {
    method: capability
    for capability, methods in CAPABILITY_GATES.items()
    for method in methods
}

CAPABILITY_IDS: frozenset[str] = frozenset(CAPABILITY_GATES)

CONSENTS_FILENAME = "capability-consents.json"


def parse_capabilities(raw: Any) -> tuple[list[str], list[str]]:
    """Split a manifest ``capabilities`` value into known and dropped ids.

    Unknown ids are dropped, never granted. Order is preserved and
    duplicates removed; a non-list value yields an empty declaration
    (fail-closed: a malformed field grants nothing).
    """
    if not isinstance(raw, list):
        return [], []
    known: list[str] = []
    dropped: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            dropped.append(str(item))
        elif item in CAPABILITY_IDS:
            if item not in known:
                known.append(item)
        else:
            dropped.append(item)
    return known, dropped


def parse_filesystem_write_roots(raw: Any) -> tuple[list[str], list[str]]:
    """Split a manifest ``filesystem_write_roots`` value into kept/dropped.

    Roots are relative to the spell workspace root; ``"."`` means
    the workspace itself. Absolute paths, ``..`` escapes, empty
    strings, and non-strings are dropped, never granted —
    fail-closed. Duplicates are removed, order preserved.
    """
    if not isinstance(raw, list):
        return [], []
    kept: list[str] = []
    dropped: list[str] = []
    for item in raw:
        if not isinstance(item, str) or item == "":
            dropped.append(str(item))
            continue
        candidate = Path(item)
        if candidate.is_absolute() or ".." in candidate.parts:
            dropped.append(item)
            continue
        if item not in kept:
            kept.append(item)
    return kept, dropped


def declaration_hash(capabilities: list[str] | None) -> str:
    """Canonical hash of a declaration, order-independent."""
    canonical = json.dumps(sorted(set(capabilities or [])), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def default_capability_store_dir() -> Path:
    """User-scope ``.agents`` dir + ``extensions``.

    The same global layer the rune-op audit log uses, so grants
    cannot land in a different layer than the runes they govern.
    Hosts opt into the file-backed store explicitly; the runner
    default is an in-memory store (see ``CapabilityStore``).
    """
    return extensions_dir()


class CapabilityStore:
    """Engine-owned grant storage, separate from any rune code dir.

    With ``data_dir=None`` the store is in-memory only (per
    session). With a directory it persists as JSON, written
    atomically (temp file + rename) so a torn write never
    leaves a half-grant behind.
    """

    def __init__(self, data_dir: Path | None = None) -> None:
        self._data_dir = Path(data_dir) if data_dir is not None else None
        self._records: dict[str, dict[str, Any]] = {}
        if self._data_dir is not None:
            self._load()

    @property
    def data_dir(self) -> Path | None:
        return self._data_dir

    def _path(self) -> Path:
        assert self._data_dir is not None
        return self._data_dir / CONSENTS_FILENAME

    def _load(self) -> None:
        assert self._data_dir is not None
        try:
            data = json.loads(self._path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(data, dict):
            self._records = {
                str(name): record
                for name, record in data.items()
                if isinstance(record, dict)
            }

    def _save(self) -> None:
        if self._data_dir is None:
            return
        self._data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path().with_suffix(".tmp")
        tmp.write_text(json.dumps(self._records, indent=2, sort_keys=True))
        os.replace(tmp, self._path())

    def _record(self, rune_name: str) -> dict[str, Any]:
        return self._records.setdefault(
            rune_name, {"declared": [], "declaration_hash": "", "consented": []}
        )

    def consent(self, rune_name: str, capabilities: list[str]) -> None:
        """Record host consent for capabilities (known ids only)."""
        record = self._record(rune_name)
        consented = set(record.get("consented", []))
        consented.update(c for c in capabilities if c in CAPABILITY_IDS)
        record["consented"] = sorted(consented)
        self._save()

    def record_declaration(self, rune_name: str, declared: list[str]) -> list[str]:
        """Record the declaration seen for a rune; return the previous one."""
        record = self._record(rune_name)
        previous = list(record.get("declared", []))
        record["declared"] = sorted(set(declared))
        record["declaration_hash"] = declaration_hash(declared)
        self._save()
        return previous

    def ensure_declaration(self, rune_name: str, declared: list[str]) -> None:
        """Record a first-seen declaration without overwriting a later one.

        The runner calls this on load: the first declaration is
        recorded, and a later, changed declaration stays detectable
        via ``declaration_changed`` instead of being silently
        overwritten on every load.
        """
        if rune_name not in self._records:
            self.record_declaration(rune_name, declared)

    def declaration_changed(self, rune_name: str, declared: list[str]) -> bool:
        """True when the declaration differs from the stored one."""
        record = self._records.get(rune_name)
        if record is None:
            return False
        return sorted(set(declared)) != sorted(set(record.get("declared", [])))

    def granted(self, rune_name: str, declared: list[str]) -> set[str]:
        """Granted capabilities: consented ∩ declared, nothing more."""
        record = self._records.get(rune_name)
        if record is None:
            return set()
        consented = set(record.get("consented", []))
        return {c for c in declared if c in consented and c in CAPABILITY_IDS}


class CapabilityGate:
    """Runtime enforcement for one runner's loaded manifests.

    ``declared_by_rune`` maps a rune name to its manifest
    declaration, or ``None`` for a legacy manifest with no field.
    A rune with no entry (ad-hoc API use, no manifest) and a
    legacy manifest are both allowed, per the legacy rule in
    this module's docstring. A declared rune is allowed exactly
    its granted capabilities.
    """

    def __init__(
        self,
        store: CapabilityStore,
        declared_by_rune: dict[str, list[str] | None],
    ) -> None:
        self._store = store
        self._declared_by_rune = declared_by_rune

    def allows(self, rune_name: str | None, capability: str) -> bool:
        if rune_name is None:
            return True
        if rune_name not in self._declared_by_rune:
            return True
        declared = self._declared_by_rune[rune_name]
        if declared is None:
            return True
        return capability in self._store.granted(rune_name, declared)

    def granted_capabilities(self, rune_name: str | None) -> set[str] | None:
        """Granted set for a declared rune; None means ungated."""
        if rune_name is None or rune_name not in self._declared_by_rune:
            return None
        declared = self._declared_by_rune[rune_name]
        if declared is None:
            return None
        return self._store.granted(rune_name, declared)
