"""Table-driven contract test for the single Sigil payload module (issue #163).

For every one of the 23 SigilHook values, `payload_for(hook)` must produce
the dataclass declared in SIGIL_HOOK_DATA_CLASSES. This is the test that was
missing: the interface is provable, not asserted in prose. It needs no Rune,
no runner and no event bus.
"""

from __future__ import annotations

from typing import Any

import pytest

from mvge.agent.sigil_payloads import (
    PayloadBuildError,
    payload_for,
    payload_for_event,
    prepare_next_turn_payload,
)
from mvge.core.events import MvgeEvent, MvgeEventType
from mvge.core.invocations import SummonerRequest
from mvge.core.loop import LoopContext
from mvge.runes.types import (
    SIGIL_HOOK_DATA_CLASSES,
    AfterProviderResponseData,
    PrepareNextTurnData,
    SigilHook,
)


def _invocation() -> Any:
    return SummonerRequest(role="user", content="hello")


# Minimal fields sufficient to build each hook's declared payload.
PAYLOAD_FIELDS: dict[SigilHook, dict[str, Any]] = {
    SigilHook.BEFORE_INVOCATION: {"invocation": _invocation()},
    SigilHook.AFTER_INVOCATION: {
        "invocation": _invocation(),
        "stop_reason": "stop",
    },
    SigilHook.BEFORE_SPELL_CAST: {
        "spell_cast": {"name": "read", "id": "1"},
        "spell_name": "read",
    },
    SigilHook.AFTER_SPELL_RESULT: {
        "spell_name": "read",
        "spell_cast_id": "1",
        "result": "ok",
    },
    SigilHook.BEFORE_PROVIDER_REQUEST: {"model": {"id": "m"}},
    SigilHook.AFTER_PROVIDER_RESPONSE: {"response": "r", "mana_used": 7},
    SigilHook.BEFORE_PROVIDER_HEADERS: {"headers": {"X-A": "b"}},
    SigilHook.TURN_START: {"model": {"id": "m"}},
    SigilHook.TURN_END: {"stop_reason": "stop", "mana_used": 7},
    SigilHook.SESSION_START: {"session_name": "tome-1"},
    SigilHook.SESSION_SHUTDOWN: {"session_name": "tome-1"},
    SigilHook.SESSION_BEFORE_SWITCH: {
        "from_session": "a",
        "to_session": "b",
    },
    SigilHook.SESSION_BEFORE_FORK: {"parent_session": "a"},
    SigilHook.COMPACTION_START: {"session_name": "tome-1", "message_count": 10},
    SigilHook.COMPACTION_END: {
        "session_name": "tome-1",
        "original_count": 10,
        "compacted_count": 3,
    },
    SigilHook.CONTEXT_TRANSFORM: {"invocations": [_invocation()]},
    SigilHook.AGENT_START: {},
    SigilHook.AGENT_END: {"stop_reason": "stop"},
    SigilHook.BEFORE_MVGE_START: {
        "base_prompt": "p",
        "spell_names": ["s"],
        "config_dir": "/c",
        "custom_prompt": "",
        "agent_name": "a",
        "cwd": "/w",
    },
    SigilHook.INPUT: {"content": "hello"},
    SigilHook.SHOULD_STOP_AFTER_TURN: {},
    SigilHook.PREPARE_NEXT_TURN: {},
    SigilHook.RESOURCES_DISCOVER: {"cwd": "/w"},
    SigilHook.SESSION_LEAF_CHANGED: {
        "tome_id": "tome-1",
        "old_leaf_id": "leaf-1",
        "new_leaf_id": "leaf-2",
        "reason": "checkout",
        "workspace_root": "/w",
    },
    SigilHook.WORKSPACE_UNTRUSTED: {
        "workspace_root": "/w",
        "status": "declined",
    },
}


@pytest.mark.parametrize("hook", list(SigilHook))
def test_payload_for_satisfies_declared_dataclass(hook: SigilHook) -> None:
    """The real deliverable: every hook builds its declared payload type."""
    payload = payload_for(hook, **PAYLOAD_FIELDS[hook])
    assert isinstance(payload, SIGIL_HOOK_DATA_CLASSES[hook])


def test_payload_tables_are_total_over_hooks() -> None:
    """Neither the field table nor the declaration may silently drop a hook."""
    assert set(PAYLOAD_FIELDS) == set(SigilHook)
    assert set(SIGIL_HOOK_DATA_CLASSES) == set(SigilHook)


def test_payload_for_fails_loud_on_missing_fields() -> None:
    """A payload that cannot be built is a loud failure, never a raw dict."""
    with pytest.raises(PayloadBuildError):
        payload_for(SigilHook.TURN_END)


def test_payload_for_fails_loud_on_unknown_hook() -> None:
    with pytest.raises(PayloadBuildError):
        payload_for("not-a-hook")  # type: ignore[arg-type]


def test_payload_for_event_selects_declared_fields() -> None:
    """Event-bus keys that are not part of the sigil contract are ignored."""
    event = MvgeEvent(
        type=MvgeEventType.AFTER_PROVIDER_RESPONSE,
        data={"response": "r", "mana_used": 3, "extra": "bus-only"},
    )
    payload = payload_for_event(SigilHook.AFTER_PROVIDER_RESPONSE, event)
    assert isinstance(payload, AfterProviderResponseData)
    assert payload.mana_used == 3
    assert not hasattr(payload, "extra")


def test_payload_for_event_fails_loud_when_event_lacks_required_field() -> None:
    event = MvgeEvent(
        type=MvgeEventType.AFTER_PROVIDER_RESPONSE,
        data={"response": "r"},
    )
    with pytest.raises(PayloadBuildError):
        payload_for_event(SigilHook.AFTER_PROVIDER_RESPONSE, event)


def _loop_context() -> LoopContext:
    return LoopContext(
        system_prompt="prompt",
        invocations=[SummonerRequest(role="user", content="hi")],
    )


def test_prepare_next_turn_payload_builds_from_context() -> None:
    payload = prepare_next_turn_payload(_loop_context())
    assert isinstance(payload, PrepareNextTurnData)
    assert payload.system_prompt == "prompt"
    assert payload.prompt_source == "builtin"
    assert payload.contemplation_level == "medium"
    assert payload.queue_mode == "one-at-a-time"
    assert len(payload.invocations) == 1
