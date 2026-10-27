from __future__ import annotations

from mvge.core.channel import Model


def test_model_supported_contemplation_levels_default() -> None:
    model = Model(
        id="test/model",
        name="Test Model",
        realm="test",
        base_url="https://example.com",
        api_key="key",
    )
    assert model.supported_contemplation_levels == []
    assert model.supports_contemplation is False


def test_model_supports_contemplation_via_levels() -> None:
    model = Model(
        id="test/model",
        name="Test Model",
        realm="test",
        base_url="https://example.com",
        api_key="key",
        supported_contemplation_levels=["none", "low", "medium", "high", "x-high"],
    )
    assert model.supports_contemplation is True
    assert model.supported_contemplation_levels == [
        "none",
        "low",
        "medium",
        "high",
        "x-high",
    ]


def test_model_supports_contemplation_via_parameters() -> None:
    model_reasoning = Model(
        id="test/model-reasoning",
        name="Reasoning Model",
        realm="test",
        base_url="https://example.com",
        api_key="key",
        supported_parameters=["reasoning"],
    )
    assert model_reasoning.supports_contemplation is True

    model_thinking = Model(
        id="test/model-thinking",
        name="Thinking Model",
        realm="test",
        base_url="https://example.com",
        api_key="key",
        supported_parameters=["thinking"],
    )
    assert model_thinking.supports_contemplation is True


def test_model_provider_prefix() -> None:
    # With slash in id
    m1 = Model(
        id="anthropic/claude-3-5-sonnet",
        name="Claude",
        realm="openrouter",
        base_url="https://example.com",
        api_key="key",
    )
    assert m1.provider_prefix == "anthropic"

    # Without slash, with realm
    m2 = Model(
        id="gemini-2.5-flash",
        name="Gemini",
        realm="google",
        base_url="https://example.com",
        api_key="key",
    )
    assert m2.provider_prefix == "google"

    # Without slash, without realm
    m3 = Model(
        id="custom-direct-model",
        name="Custom",
        realm="",
        base_url="https://example.com",
        api_key="key",
    )
    assert m3.provider_prefix == "custom-direct-model"


def _model(id: str, realm: str = "openrouter", *, is_free: bool = False) -> Model:
    return Model(
        id=id,
        name="Test Model",
        realm=realm,
        base_url="",
        api_key="key",
        is_free=is_free,
    )


def test_free_is_declared_by_the_catalog() -> None:
    assert _model("vendor/paid-model", is_free=True).free is True


def test_free_recognises_openrouters_colon_suffix() -> None:
    assert _model("vendor/model:free").free is True


def test_free_recognises_the_free_models_router() -> None:
    """No suffix to read: the id routes to whatever free model is available."""
    assert _model("openrouter/free").free is True


def test_free_recognises_the_dash_suffix_on_the_realm_that_uses_it() -> None:
    """OpenCode Zen spells its free tier ``-free``.

    Before this, every Zen free model was reported as paid, which fed the
    free-first model ordering and any cost guard built on it.
    """
    assert _model("opencode/space-bunny-free", realm="opencode").free is True


def test_the_dash_suffix_does_not_apply_to_other_realms() -> None:
    """A name must not be able to call a paid model free.

    ``-free`` is a weaker signal than ``:free``, so it is scoped to the Realm
    whose convention it is. Reading it globally would let any future model id
    ending in ``-free`` be reported free.
    """
    assert _model("vendor/model-free", realm="openrouter").free is False
    assert _model("vendor/some-free-preview", realm="ollama").free is False


def test_a_paid_model_is_not_free() -> None:
    assert _model("vendor/model", realm="openrouter").free is False
    assert _model("opencode/space-bunny", realm="opencode").free is False
