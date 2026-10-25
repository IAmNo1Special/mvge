"""Unit tests for the CLI Approval Rune contract types.

The engine owns the canonical gate vocabulary (``mvge.core.approval``);
``mvge.cli.approval_types`` re-exports it and adds the CLI-only pieces
(``ApprovalMode``, ``ApprovalBindingError``, ``PersistentGrantInfo``).
"""

from __future__ import annotations

import dataclasses

import pytest

from mvge.cli.approval_types import (
    ApprovalBindingError,
    ApprovalDecision,
    ApprovalMode,
    ApprovalOutcome,
    ApprovalPresenter,
    ApprovalReasonCode,
    ApprovalRequest,
    ApprovalScope,
    PersistentGrantInfo,
    allow,
    deny,
)
from mvge.core.approval import (
    ApprovalDecision as CoreDecision,
)
from mvge.core.approval import (
    ApprovalRequest as CoreRequest,
)


def _request() -> ApprovalRequest:
    return ApprovalRequest(
        cast_id="call_8b17",
        spell_name="write",
        spell_identity={
            "name": "write",
            "source_kind": "builtin",
            "source_id": "",
            "runner_origin": "false",
            "read_only": "false",
        },
        arguments={"path": "/workspace/mvgeos/notes.txt"},
        argument_digest="sha256:deadbeef",
        project_root="/workspace/mvgeos",
        tome_id="7f24c1",
        agent_name="coding_mvge",
    )


def test_reexports_are_the_canonical_engine_types() -> None:
    assert ApprovalRequest is CoreRequest
    assert ApprovalDecision is CoreDecision


def test_request_is_frozen() -> None:
    request = _request()
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.cast_id = "other"  # type: ignore[misc]


def test_decision_is_frozen() -> None:
    decision = ApprovalDecision(
        outcome="allow", scope="once", reason_code="user", request_digest="sha256:x"
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.outcome = "deny"  # type: ignore[misc]


def test_decision_coerces_strings_to_canonical_enums() -> None:
    decision = ApprovalDecision(
        outcome="deny",
        scope="spell",
        reason_code="user",
        request_digest="sha256:deadbeef",
    )
    assert decision.outcome is ApprovalOutcome.DENY
    assert decision.scope is ApprovalScope.SPELL
    assert decision.reason_code is ApprovalReasonCode.USER
    assert decision.request_digest == "sha256:deadbeef"


def test_deny_helper_binds_user_reason_to_request_digest() -> None:
    request = _request()
    decision = deny(request, ApprovalReasonCode.USER, ApprovalScope.ONCE)
    assert decision.outcome is ApprovalOutcome.DENY
    assert decision.reason_code is ApprovalReasonCode.USER
    assert decision.request_digest == "sha256:deadbeef"


def test_allow_helper_binds_user_reason_to_request_digest() -> None:
    request = _request()
    decision = allow(request, ApprovalReasonCode.USER, ApprovalScope.SESSION)
    assert decision.outcome is ApprovalOutcome.ALLOW
    assert decision.scope is ApprovalScope.SESSION
    assert decision.request_digest == "sha256:deadbeef"


def test_persistent_grant_info_carries_scope_and_constraints() -> None:
    grant = PersistentGrantInfo(
        kind="spell",
        scope_label="global (all projects)",
        constraint_lines=("path: path_within_project",),
        unconstrained_warning=False,
    )
    assert grant.scope_label == "global (all projects)"
    assert grant.constraint_lines == ("path: path_within_project",)
    with pytest.raises(dataclasses.FrozenInstanceError):
        grant.scope_label = "other"  # type: ignore[misc]


def test_approval_mode_is_the_cli_flag_vocabulary() -> None:
    mode: ApprovalMode = "allow-all"
    assert mode == "allow-all"


def test_presenter_contract_is_a_callable_alias() -> None:
    async def fake_presenter(request: ApprovalRequest) -> ApprovalDecision:
        return deny(request, ApprovalReasonCode.USER, ApprovalScope.ONCE)

    presenter: ApprovalPresenter = fake_presenter
    assert callable(presenter)


def test_binding_error_is_exception() -> None:
    assert issubclass(ApprovalBindingError, Exception)
