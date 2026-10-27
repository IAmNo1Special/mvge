"""Approval Rune contract types for the CLI host adapter.

Source of truth: ``~/workspace/your_files/approval-rune-spec/approval-rune-spec.pdf``
(Host-behavior and Interaction-design sections).

The engine owns the canonical gate vocabulary in ``mvge.core.approval``
(``ApprovalRequest`` / ``ApprovalDecision`` / ``ApprovalPresenter`` and the
outcome/scope/reason-code enums); this module re-exports it and adds the
CLI-only pieces:

* ``ApprovalMode`` — the one-run ``--approval-mode`` flag. Never persisted;
  environment variables never select it.
* ``ApprovalBindingError`` — raised when the presenter cannot be bound.
* ``PersistentGrantInfo`` — the CLI's display model for the persistent-grant
  confirmation screen, derived from the canonical request. The engine's
  request carries no grant metadata (the rune owns policy); the presenter
  describes the grant from the engine-derived spell identity and the
  current cast's arguments.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from mvge.core.approval import (
    ApprovalDecision,
    ApprovalOutcome,
    ApprovalPresenter,
    ApprovalReasonCode,
    ApprovalRequest,
    ApprovalScope,
    allow,
    deny,
)

__all__ = [
    "ApprovalBindingError",
    "ApprovalDecision",
    "ApprovalMode",
    "ApprovalOutcome",
    "ApprovalPresenter",
    "ApprovalReasonCode",
    "ApprovalRequest",
    "ApprovalScope",
    "PersistentGrantInfo",
    "allow",
    "deny",
]

#: One-run automation mode. Never persisted; chosen per CLI invocation.
ApprovalMode = Literal["deny", "prompt", "allow-all"]


class ApprovalBindingError(Exception):
    """Raised when the approval presenter cannot be bound to the runner."""


@dataclass(frozen=True)
class PersistentGrantInfo:
    """Scope and constraint data for the persistent-grant confirmation screen.

    Derived by the presenter from the canonical request (the rune owns
    policy and supplies no grant metadata on the request).
    ``unconstrained_warning`` is set for mutating spells with no
    constraints, where the grant authorizes every future argument.
    """

    kind: Literal["spell", "project", "deny-spell"]
    scope_label: str
    constraint_lines: tuple[str, ...] = ()
    unconstrained_warning: bool = False
