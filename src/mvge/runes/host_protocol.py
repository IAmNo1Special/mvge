"""Phase 4 wire protocol v1 for the out-of-process rune host.

The wire is newline-delimited JSON (one message per line) over the
host process stdio. Stdout is the wire: the host must never print
anything else to stdout; diagnostics go to stderr, which the engine
captures to a log file for debugging.

Envelope (every message):

    {"protocol": "mvge-rune-host/1", "kind": ..., ...}

- ``kind: "request"`` carries ``id``, ``type``, ``payload`` and
  expects a ``kind: "response"`` with the same ``id``.
- ``kind: "response"`` carries ``id``, ``ok``, and either
  ``payload`` or ``error`` (``{"kind": ..., "message": ...}``).
- ``kind: "event"`` carries ``type`` and ``payload`` and expects
  no response. Hello, ready, abort, and streaming spell updates
  are events.

Versioning: the host's first message must be a ``hello`` event
with ``protocol_version`` 1. Any other version is a mismatch:
the engine kills the host and refuses the rune. There is no
version negotiation in v1 — a host that speaks another version
is not a host this engine can run.

Limits, all fail-closed:

- ``MAX_MESSAGE_BYTES`` (1 MiB): a single line longer than this
  is rejected and the host is killed. Neither side sends a
  message longer than this.
- Timeouts are per call on the engine side; a timed-out call
  kills the host, because a host that stopped answering cannot
  be trusted to have stopped working.
- Restart is a fresh process: the host re-runs its factory and
  re-registers, and the engine re-applies those registrations
  after clearing the old ones.

Env scrubbing: the engine spawns the host with an environment
from which credential-shaped names have been removed, and the
host removes them again at startup (defense in depth). Scrubbing
is by variable *name* only: values are never inspected, logged,
or printed, here or anywhere in the host code.
"""

from __future__ import annotations

import json
from typing import Any

PROTOCOL_NAME = "mvge-rune-host/1"
PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 1024 * 1024
DEFAULT_START_TIMEOUT_SECONDS = 10.0
DEFAULT_CALL_TIMEOUT_SECONDS = 30.0

# Credential-shaped names are removed from the host environment.
# Exact names first, then substrings: a variable whose name contains
# any of these tokens is treated as a credential carrier. This is
# deliberately broad — a missed credential is worse than a dropped
# harmless variable — and it never looks at values.
_CREDENTIAL_EXACT: frozenset[str] = frozenset(
    {
        "API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "MVGE_MODEL_KEY",
        "MODEL_KEY",
    }
)
_CREDENTIAL_TOKENS: tuple[str, ...] = (
    "API_KEY",
    "APIKEY",
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "CREDENTIAL",
    "PRIVATE_KEY",
)


def is_credential_env_name(name: str) -> bool:
    """True when an environment variable name looks credential-shaped."""
    upper = name.upper()
    if upper in _CREDENTIAL_EXACT:
        return True
    return any(token in upper for token in _CREDENTIAL_TOKENS)


def scrub_env(env: dict[str, str]) -> dict[str, str]:
    """Return a copy of ``env`` with credential-shaped names removed.

    Non-credential variables pass through unchanged, including
    ``PATH`` (the host needs it to run) and ``PYTHONPATH`` (an
    editable engine install needs it; it is set by the engine,
    never by rune code, and it is not a credential).
    """
    return {k: v for k, v in env.items() if not is_credential_env_name(k)}


def encode_message(message: dict[str, Any]) -> bytes:
    """Encode one wire message, enforcing the size limit."""
    raw = json.dumps(message, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_MESSAGE_BYTES:
        raise OversizeMessageError(
            f"Message of {len(raw)} bytes exceeds the "
            f"{MAX_MESSAGE_BYTES}-byte wire limit"
        )
    return raw + b"\n"


def decode_message(raw: bytes) -> dict[str, Any]:
    """Decode one wire line, enforcing the size limit and envelope."""
    if len(raw) > MAX_MESSAGE_BYTES:
        raise OversizeMessageError(
            f"Message of {len(raw)} bytes exceeds the "
            f"{MAX_MESSAGE_BYTES}-byte wire limit"
        )
    try:
        message = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HostProtocolError(f"Malformed wire message: {exc}") from exc
    if not isinstance(message, dict):
        raise HostProtocolError("Wire message is not a JSON object")
    if message.get("protocol") != PROTOCOL_NAME:
        raise HostProtocolError(
            "Wire message has an unexpected protocol envelope: "
            f"{message.get('protocol')!r}"
        )
    if message.get("kind") not in ("request", "response", "event"):
        raise HostProtocolError(
            f"Wire message has an unknown kind: {message.get('kind')!r}"
        )
    return message


class HostError(Exception):
    """Base error for out-of-process host failures."""


class HostCrashError(HostError):
    """The host process died (exit, signal, or closed stdio)."""


class HostTimeoutError(HostError):
    """A host call did not answer within its timeout; the host was killed."""


class HostProtocolError(HostError):
    """The wire itself is broken: malformed, oversized, or wrong envelope."""


class OversizeMessageError(HostProtocolError):
    """A single wire message exceeded MAX_MESSAGE_BYTES."""


class VersionMismatchError(HostProtocolError):
    """The host speaks a protocol version this engine does not speak."""


class HostUnsupportedError(HostError):
    """A hosted rune asked for a facade method the wire does not carry.

    Fail-closed with a stated reason: the method is named in the
    one table in ``mvge.runes.host_classification`` that the
    Phase 2B audit also reads.
    """


class HostInProcessOnlyError(HostError):
    """The rune, or this particular call, must run in-process in v1.

    Raised for the in-process-only tier (realm, gateway, approval),
    for a spell cast that carries a live backend, and for a context
    object that cannot cross as plain JSON.
    """


class HostAbortedError(HostError):
    """A hosted spell reported abort, not success, after an abort crossed."""


class HostRemoteError(HostError):
    """The host answered a request with an error payload."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.remote_message = message
