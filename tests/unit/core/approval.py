from __future__ import annotations

import dataclasses
import hashlib
import json
from types import MappingProxyType
from typing import Any

import pytest

from mvge.core.approval import (
    ApprovalDecision,
    ApprovalOutcome,
    ApprovalReasonCode,
    ApprovalRequest,
    ApprovalScope,
    normalize_arguments,
)


def _request(**overrides: Any) -> ApprovalRequest:
    base: dict[str, Any] = {
        "cast_id": "call_1",
        "spell_name": "write",
        "spell_identity": MappingProxyType({"name": "write", "origin": "builtin"}),
        "arguments": MappingProxyType({"path": "a.txt"}),
        "argument_digest": "sha256:abc",
    }
    base.update(overrides)
    return ApprovalRequest(**base)


def test_request_is_frozen() -> None:
    req = _request()
    with pytest.raises(dataclasses.FrozenInstanceError):
        req.cast_id = "other"  # type: ignore[misc]


def test_decision_is_frozen() -> None:
    decision = ApprovalDecision(outcome=ApprovalOutcome.ALLOW)
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.outcome = ApprovalOutcome.DENY  # type: ignore[misc]


def test_decision_accepts_plain_strings() -> None:
    decision = ApprovalDecision(
        outcome="allow", scope="session", reason_code="user", request_digest="sha256:x"
    )
    assert decision.outcome is ApprovalOutcome.ALLOW
    assert decision.scope is ApprovalScope.SESSION
    assert decision.reason_code is ApprovalReasonCode.USER


def test_decision_rejects_unknown_outcome() -> None:
    with pytest.raises(ValueError, match="Unknown approval outcome"):
        ApprovalDecision(outcome="maybe")  # type: ignore[arg-type]


def test_decision_rejects_unknown_scope() -> None:
    with pytest.raises(ValueError, match="Unknown approval scope"):
        ApprovalDecision(outcome="deny", scope="forever")  # type: ignore[arg-type]


def test_decision_rejects_unknown_reason_code() -> None:
    with pytest.raises(ValueError, match="Unknown approval reason code"):
        ApprovalDecision(outcome="deny", reason_code="vibes")  # type: ignore[arg-type]


def test_normalize_returns_canonical_form_and_digest() -> None:
    args = {"b": 2, "a": [1, 2]}
    proxy, digest = normalize_arguments(args)
    assert isinstance(proxy, MappingProxyType)
    canonical = json.dumps(
        args, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    assert digest == "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    # Key order in the input must not change the digest.
    _, digest2 = normalize_arguments({"a": [1, 2], "b": 2})
    assert digest == digest2


def test_normalize_rejects_non_dict() -> None:
    with pytest.raises(ValueError, match="dict"):
        normalize_arguments(["not", "a", "dict"])


def test_normalize_rejects_non_serializable() -> None:
    with pytest.raises(ValueError, match="serializable"):
        normalize_arguments({"fn": object()})


def test_normalize_deep_freezes_nested_values() -> None:
    args: dict[str, Any] = {"nested": {"x": 1}, "items": [{"y": 2}]}
    frozen, digest = normalize_arguments(args)
    assert isinstance(frozen, MappingProxyType)
    nested = frozen["nested"]
    assert isinstance(nested, MappingProxyType)
    with pytest.raises(TypeError):
        nested["x"] = 99  # type: ignore[index]
    items = frozen["items"]
    assert isinstance(items, tuple)
    assert isinstance(items[0], MappingProxyType)
    with pytest.raises(TypeError):
        items[0]["y"] = 99  # type: ignore[index]
    # The frozen view shares no mutable state with the input.
    args["nested"]["x"] = 99
    assert frozen["nested"]["x"] == 1
    # Digest is unaffected by key order or the freeze.
    _, digest2 = normalize_arguments({"items": [{"y": 2}], "nested": {"x": 1}})
    assert digest == digest2


def test_request_schema_validated_defaults_true() -> None:
    assert _request().schema_validated is True


def test_request_schema_validated_can_be_false() -> None:
    assert _request(schema_validated=False).schema_validated is False
