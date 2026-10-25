"""Gate vocabulary for the critical spell-approval path.

These types are the contract between the spell dispatcher (which builds the
request), the critical gate handlers registered by runes (which decide), and
the host-bound presenter (which surfaces the decision to the Summoner).

The gate is fail-closed: any exception, cancellation, malformed response, or
stale digest denies the cast. This module carries no I/O and no first-party
dependencies.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class ApprovalOutcome(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class ApprovalScope(StrEnum):
    ONCE = "once"
    SPELL = "spell"
    SESSION = "session"
    PROJECT = "project"


class ApprovalReasonCode(StrEnum):
    USER = "user"
    RULE = "rule"
    READ_ONLY = "read_only"
    FAILURE = "failure"


def _coerce_outcome(value: ApprovalOutcome | str) -> ApprovalOutcome:
    try:
        return ApprovalOutcome(value)
    except ValueError as exc:
        raise ValueError(f"Unknown approval outcome: {value!r}") from exc


def _coerce_scope(value: ApprovalScope | str) -> ApprovalScope:
    try:
        return ApprovalScope(value)
    except ValueError as exc:
        raise ValueError(f"Unknown approval scope: {value!r}") from exc


def _coerce_reason_code(value: ApprovalReasonCode | str) -> ApprovalReasonCode:
    try:
        return ApprovalReasonCode(value)
    except ValueError as exc:
        raise ValueError(f"Unknown approval reason code: {value!r}") from exc


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(value, MappingProxyType):
        return value
    return MappingProxyType(dict(value))


@dataclass(frozen=True)
class ApprovalRequest:
    """One immutable approval question for a single spell cast.

    The dispatcher builds this from the validated, normalized argument copy
    and the gate approves exactly this data; the dispatcher then executes the
    approved copy. ``spell_identity`` is engine-derived (never rune-chosen)
    attribution the policy layer matches rules against.
    """

    cast_id: str
    spell_name: str
    spell_identity: Mapping[str, str] = field(default_factory=dict)
    arguments: Mapping[str, Any] = field(default_factory=dict)
    argument_digest: str = ""
    project_root: str = ""
    tome_id: str = ""
    agent_name: str = ""
    # True when the spell carried a parameter schema and the dispatcher
    # validated/normalized arguments against it before building this
    # request. False marks a schemaless cast: policy may never match an
    # allow rule against it, and presenters must show an "unvalidated
    # arguments" warning.
    schema_validated: bool = True
    spell_action: str = ""
    spell_summary: str = ""
    sub_mvge_id: str | None = None
    parent_spell_cast_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "spell_identity", _freeze_mapping(self.spell_identity))
        object.__setattr__(self, "arguments", _freeze_mapping(self.arguments))


@dataclass(frozen=True)
class ApprovalDecision:
    """The verdict for one :class:`ApprovalRequest`.

    ``request_digest`` must equal the request's ``argument_digest``; a
    decision bound to any other digest is stale and denies the cast.
    """

    outcome: ApprovalOutcome
    scope: ApprovalScope = ApprovalScope.ONCE
    reason_code: ApprovalReasonCode = ApprovalReasonCode.RULE
    request_digest: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcome", _coerce_outcome(self.outcome))
        object.__setattr__(self, "scope", _coerce_scope(self.scope))
        object.__setattr__(self, "reason_code", _coerce_reason_code(self.reason_code))


def deny(
    request: ApprovalRequest,
    reason_code: ApprovalReasonCode | str = ApprovalReasonCode.FAILURE,
    scope: ApprovalScope | str = ApprovalScope.ONCE,
) -> ApprovalDecision:
    """Build a denial decision already bound to ``request``."""
    return ApprovalDecision(
        outcome=ApprovalOutcome.DENY,
        scope=_coerce_scope(scope),
        reason_code=_coerce_reason_code(reason_code),
        request_digest=request.argument_digest,
    )


def allow(
    request: ApprovalRequest,
    reason_code: ApprovalReasonCode | str = ApprovalReasonCode.RULE,
    scope: ApprovalScope | str = ApprovalScope.ONCE,
) -> ApprovalDecision:
    """Build an allow decision already bound to ``request``."""
    return ApprovalDecision(
        outcome=ApprovalOutcome.ALLOW,
        scope=_coerce_scope(scope),
        reason_code=_coerce_reason_code(reason_code),
        request_digest=request.argument_digest,
    )


def _deep_freeze(value: Any) -> Any:
    """Recursively freeze JSON-like data into immutable containers.

    dicts become ``MappingProxyType``, lists/tuples become tuples; scalars
    pass through. The frozen tree shares no mutable state with the input.
    """
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _deep_freeze(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    return value


def normalize_arguments(
    arguments: Any,
) -> tuple[Mapping[str, Any], str]:
    """Freeze arguments to canonical JSON and digest them.

    Args:
        arguments: The validated argument mapping for one cast.

    Returns:
        A tuple of (recursively immutable argument view, ``"sha256:<hex>"``
        digest) where the digest is computed over ``json.dumps`` with
        ``sort_keys=True, separators=(",", ":"), ensure_ascii=True``.

    Raises:
        ValueError: If ``arguments`` is not a dict or is not JSON-serializable.
    """
    if not isinstance(arguments, dict):
        raise ValueError(
            "Spell arguments must be a JSON object (dict); "
            f"got {type(arguments).__name__}"
        )
    try:
        canonical = json.dumps(
            arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Spell arguments are not JSON-serializable: {exc}") from exc
    digest = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    # Freeze after dumping: the frozen tree shares no mutable state with the
    # input, so a gate cannot mutate nested values after display and change
    # what the dispatcher executes.
    frozen = _deep_freeze(arguments)
    # `arguments` is a dict, so `_deep_freeze` returned a MappingProxyType.
    return frozen, digest


SpellGateHandler = Callable[
    [ApprovalRequest], "ApprovalDecision | Awaitable[ApprovalDecision]"
]
"""A critical gate handler: receives the request, returns or awaits a decision."""

ApprovalPresenter = Callable[[ApprovalRequest], Awaitable[ApprovalDecision]]
"""Host-bound UI surface: awaits the Summoner's decision for one request."""


__all__ = [
    "ApprovalDecision",
    "ApprovalOutcome",
    "ApprovalPresenter",
    "ApprovalReasonCode",
    "ApprovalRequest",
    "ApprovalScope",
    "SpellGateHandler",
    "allow",
    "deny",
    "normalize_arguments",
]
