from typing import Any

from mvgeos_core.channel import (
    MvgeResponse,
    StopReason,
)
from mvgeos_core.events import (
    ContentType,
    QueueMode,
)
from mvgeos_core.invocations import SummonerRequest
from mvgeos_core.spells import MvgeSpell

from mvgeos_agent.harness._state import MvgeState


def test_content_type_values() -> None:
    assert ContentType.TEXT.value == "text"
    assert ContentType.SPELL_CAST.value == "spell_cast"
    assert ContentType.CONTEMPLATION.value == "contemplation"
    assert not hasattr(ContentType, "TOOL_CALL")


def test_summoner_request_defaults() -> None:
    request = SummonerRequest()
    assert request.role == "user"
    assert request.content is None


def test_mvge_response_defaults() -> None:
    response = MvgeResponse()
    assert response.role == "assistant"
    assert response.content == []
    assert response.stop_reason == StopReason.PENDING


def test_mvge_state_default_spells() -> None:
    state = MvgeState()
    assert state.spells == []


def test_mvge_state_default_events() -> None:
    state = MvgeState()
    assert state.events == []


def test_mvge_state_default_queue_mode() -> None:
    state = MvgeState()
    assert state.queue_mode == QueueMode.ONE_AT_A_TIME


def test_mvge_state_queue_mode_can_be_set() -> None:
    state = MvgeState(queue_mode=QueueMode.ALL)
    assert state.queue_mode == QueueMode.ALL


def test_queue_mode_values() -> None:
    assert QueueMode.ALL.value == "all"
    assert QueueMode.ONE_AT_A_TIME.value == "one-at-a-time"


def test_mvge_spell_has_required_fields() -> None:
    spell = MvgeSpell(name="test_spell", description="A test spell", parameters={})
    assert spell.name == "test_spell"
    assert spell.description == "A test spell"
    assert spell.parameters == {}


def test_mvge_spell_execute_raises_not_implemented() -> None:
    import asyncio

    spell = MvgeSpell(name="test_spell", description="A test spell", parameters={})
    try:
        asyncio.run(spell.execute("cast-1", {}))
        raise AssertionError("Expected NotImplementedError")
    except NotImplementedError:
        pass


def test_mvge_spell_prepare_arguments_no_schema() -> None:
    spell = MvgeSpell(name="test", description="", parameters={})
    args = {"key": "value"}
    result = spell.prepare_arguments(args)
    assert result == args


def test_mvge_spell_prepare_arguments_with_schema() -> None:
    spell = MvgeSpell(
        name="test",
        description="",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "count": {"type": "integer"},
            },
            "required": ["name"],
        },
    )
    result = spell.prepare_arguments({"name": "test", "count": 5})
    assert result == {"name": "test", "count": 5}


def test_mvge_spell_prepare_arguments_invalid() -> None:
    import pytest

    spell = MvgeSpell(
        name="test",
        description="",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
        },
    )
    with pytest.raises(ValueError, match="Invalid arguments"):
        spell.prepare_arguments({})


def test_mvge_spell_json_type_to_python_number() -> None:
    spell = MvgeSpell(name="test", description="", parameters={})
    result = spell._json_type_to_python({"type": "number"})
    assert result is float


def test_mvge_spell_json_type_to_python_boolean() -> None:
    spell = MvgeSpell(name="test", description="", parameters={})
    result = spell._json_type_to_python({"type": "boolean"})
    assert result is bool


def test_mvge_spell_json_type_to_python_array_with_items() -> None:
    spell = MvgeSpell(name="test", description="", parameters={})
    result = spell._json_type_to_python({"type": "array", "items": {"type": "string"}})
    assert result == list[Any]


def test_mvge_spell_json_type_to_python_array_without_items() -> None:
    spell = MvgeSpell(name="test", description="", parameters={})
    result = spell._json_type_to_python({"type": "array"})
    assert result == list[Any]


def test_mvge_spell_json_type_to_python_object() -> None:
    spell = MvgeSpell(name="test", description="", parameters={})
    result = spell._json_type_to_python({"type": "object"})
    assert result == dict[str, Any]


def test_mvge_spell_json_type_to_python_unknown() -> None:
    spell = MvgeSpell(name="test", description="", parameters={})
    result = spell._json_type_to_python({"type": "unknown"})
    assert result is Any
