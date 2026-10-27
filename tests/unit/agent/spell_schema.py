from typing import Any

from mvge.core.spell_schema import generate_spell_schema


def test_generate_empty_params() -> None:
    def no_params(spell_cast_id: str) -> str:
        return "ok"

    schema = generate_spell_schema(no_params)
    assert set(schema["properties"].keys()) == {"spell_action", "spell_summary"}
    assert schema["required"] == ["spell_action", "spell_summary"]


def test_generate_with_all_param_types() -> None:
    def spell(
        spell_cast_id: str,
        name: str,
        count: int,
        ratio: float,
        flag: bool,
        items: list[str],
        meta: dict[str, Any],
        optional: str = "default",
    ) -> str:
        return "ok"

    schema = generate_spell_schema(spell)
    props = schema["properties"]
    assert props["name"]["type"] == "string"
    assert props["count"]["type"] == "integer"
    assert props["ratio"]["type"] == "number"
    assert props["flag"]["type"] == "boolean"
    assert props["items"]["type"] == "array"
    assert props["meta"]["type"] == "object"
    # required excludes optional which has default
    assert "name" in schema["required"]
    assert "optional" not in schema.get("required", [])


def test_generate_excludes_internal_params() -> None:
    def spell(
        spell_cast_id: str,
        params: dict[str, Any],
        signal: Any | None,
        on_update: Any | None,
        real_arg: str,
    ) -> str:
        return real_arg

    schema = generate_spell_schema(spell)
    assert "real_arg" in schema["properties"]
    assert "spell_cast_id" not in schema["properties"]
    assert "params" not in schema["properties"]
    assert "signal" not in schema["properties"]
    assert "on_update" not in schema["properties"]


def test_generate_with_any_type_fallback() -> None:
    def spell(spell_cast_id: str, data: Any) -> str:  # noqa: ANN401
        return str(data)

    schema = generate_spell_schema(spell)
    # Any without explicit type still produces a property
    assert "data" in schema["properties"]
