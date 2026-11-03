"""Phase 4 v1 classification: what may run out of process, and what may not.

This module owns the ONE table of hosted-facade methods that both
the runtime and the static audit read:

- ``mvge.runes.host_server.HostRuneAPI`` raises
  ``HostUnsupportedError`` for every method in
  ``HOST_UNSUPPORTED_METHODS``.
- ``mvge.runes.source_audit`` flags hosted source that calls any
  of those methods (``audit_hosted_source``).
- The parity test asserts the two verdicts agree for every method
  in the table. Do not duplicate this table anywhere else: add a
  method here, and both verdicts move together.

Supported methods are the plain-data surface: spell, hook,
command, and skill registrations (metadata only — handlers stay
in the host), session-name and message calls, spell-list queries,
and the audit event. Everything that would carry a live object
across the wire is unsupported in v1:

- realm/provider registration and queries: realm runes are the
  credential path itself (Phase 4b, separate decision).
- ``request_approval``: the approval channel never crosses.
- ``register_spell_gate``: a security-critical gate decision
  never crosses; gates stay in-process.
- ``stream_model_text``: an engine-held model call with a live
  backend; a spell that needs it is in-process-only in v1.
- ``approval_presenter_source``: hands out a live runner object.
- ``sandbox``: a live sandbox object.
- ``on_event`` / ``emit_event``: live event-handler channels.

In-process-only tier (explicit, per the amended Phase 4 scope):

- realm runes (``realm`` capability or ``RealmProvider`` type)
- spell-gateway runes (``spell_gateway`` manifest flag)
- approval-rune (approval channel and spell gates)
- codec runes (a session codec is a live object, no wire channel)
- spell-modifier runes (live spell-call mutation, no wire channel)
- any rune whose manifest declares ``hosting: "in-process"``
- any rune whose manifest ``hosting`` value is unrecognized
  (fail-closed: an unknown value is treated as in-process)

Their protection in v1 is Phase 3 pinning, stated as such in
code and docs. Realm hosting is Phase 4b, out of scope.

A manifest that declares nothing is eligible, subject to the
tier rules above. Eligible is not proven: a first-party rune
that is eligible still needs its own hosted verification before
it is hosted by default (see the marketplace hosting table).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mvge.runes.types import RuneManifest

# RuneAPI-facing names. The runtime (HostRuneAPI) and the audit
# (source_audit, hosted mode) both import this table; neither
# invents its own list.
HOST_UNSUPPORTED_METHODS: dict[str, str] = {
    "register_realm_factory": (
        "realm registration is in-process-only in v1; realm hosting is Phase 4b"
    ),
    "register_provider": (
        "provider registration is in-process-only in v1; realm hosting is Phase 4b"
    ),
    "get_registered_providers": (
        "provider queries are in-process-only in v1; realm hosting is Phase 4b"
    ),
    "get_registered_realm_factories": (
        "realm factory queries are in-process-only in v1; realm hosting is Phase 4b"
    ),
    "request_approval": "the approval channel never crosses the host wire",
    "register_spell_gate": (
        "spell-gate decisions are security-critical and stay in-process in v1"
    ),
    "stream_model_text": (
        "engine-held model calls require a live backend and are in-process-only in v1"
    ),
    "approval_presenter_source": (
        "the approval-presenter source hands out a live runner object "
        "and never crosses the host wire"
    ),
    "sandbox": "the sandbox is a live object and never crosses the host wire",
    "on_event": "live event-handler channels are in-process-only in v1",
    "emit_event": "live event-handler channels are in-process-only in v1",
}

HOST_SUPPORTED_METHODS: frozenset[str] = frozenset(
    {
        "register_spell",
        "unregister_spell",
        "get_spell",
        "get_all_spells",
        "get_discoverable_spells",
        "register_command",
        "register_shortcut",
        "get_shortcuts",
        "on",
        "get_active_spells",
        "set_active_spells",
        "get_global_spell_allowlist",
        "widen_global_allowlist",
        "narrow_global_allowlist",
        "has_spell_gateway",
        "gateway_spell_names",
        "get_skills",
        "register_skill",
        "register_skill_path",
        "clear_skills_for_rune",
        "send_message",
        "set_session_name",
        "get_session_name",
        "context",
        "install_id",
        "scope",
        "on_dispose",
        "sanitize_no_proxy_env",
        "audit",
    }
)

IN_PROCESS_ONLY_RUNE_NAMES: frozenset[str] = frozenset({"approval-rune"})


@dataclass(frozen=True)
class HostClassification:
    """The hosting verdict for one manifest."""

    hostable: bool
    reason: str
    tier: str  # "hosted" or "in-process-only"


def classify_manifest(manifest: RuneManifest) -> HostClassification:
    """Classify a manifest for Phase 4 v1 hosting, fail-closed."""
    hosting = getattr(manifest, "hosting", None)
    if hosting == "in-process":
        return HostClassification(
            hostable=False,
            reason=(
                f"Rune '{manifest.name}' declares hosting: in-process in its manifest"
            ),
            tier="in-process-only",
        )
    if hosting is not None and hosting != "hosted":
        return HostClassification(
            hostable=False,
            reason=(
                f"Rune '{manifest.name}' declares an unrecognized "
                f"hosting value {hosting!r}; fail-closed to in-process"
            ),
            tier="in-process-only",
        )
    if manifest.name in IN_PROCESS_ONLY_RUNE_NAMES:
        return HostClassification(
            hostable=False,
            reason=(
                f"Rune '{manifest.name}' is the approval rune: its "
                "approval channel and spell gates stay in-process in v1"
            ),
            tier="in-process-only",
        )
    if manifest.spell_gateway:
        return HostClassification(
            hostable=False,
            reason=(
                f"Rune '{manifest.name}' is a spell-gateway rune: "
                "gateway runes stay in-process in v1"
            ),
            tier="in-process-only",
        )
    capabilities = manifest.capabilities or []
    if "realm" in capabilities or "RealmProvider" in manifest.types:
        return HostClassification(
            hostable=False,
            reason=(
                f"Rune '{manifest.name}' is a realm rune: realm hosting "
                "is Phase 4b, not Phase 4 v1"
            ),
            tier="in-process-only",
        )
    if "Codec" in manifest.types or manifest.session_codecs:
        return HostClassification(
            hostable=False,
            reason=(
                f"Rune '{manifest.name}' is a codec rune: a session "
                "codec is a live object with no wire channel in v1"
            ),
            tier="in-process-only",
        )
    if "Spell Gate" in manifest.types:
        return HostClassification(
            hostable=False,
            reason=(
                f"Rune '{manifest.name}' registers spell gates: gate "
                "decisions stay in-process in v1"
            ),
            tier="in-process-only",
        )
    if "Spell Modifier" in manifest.types:
        return HostClassification(
            hostable=False,
            reason=(
                f"Rune '{manifest.name}' registers spell modifiers: "
                "live spell-call mutation has no wire channel in v1"
            ),
            tier="in-process-only",
        )
    return HostClassification(
        hostable=True,
        reason=(
            f"Rune '{manifest.name}' is a spell, hook, command, or "
            "skill rune: hostable in v1"
        ),
        tier="hosted",
    )
