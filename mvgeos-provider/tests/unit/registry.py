from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mock_realm import MockStreamingRealm
from mvgeos_core.channel import (
    ChannelConfig,
    Model,
    RealmResponse,
)

from mvgeos_provider.base import (
    NoRealmRegisteredError,
    Realm,
    RealmFactory,
)
from mvgeos_provider.model_registry import ModelRegistry
from mvgeos_provider.registry import RealmRegistry


def test_register_provider() -> None:
    reg = RealmRegistry()
    reg.register_provider(
        "custom", {"apiKey": "sekret", "baseUrl": "https://custom.ai"}
    )
    config = reg.get_provider_config("custom")
    assert config is not None
    assert config["apiKey"] == "sekret"


def test_register_provider_overwrites_merges() -> None:
    reg = RealmRegistry()
    reg.register_provider("custom", {"apiKey": "sekret"})
    reg.register_provider("custom", {"baseUrl": "https://custom.ai"})
    config = reg.get_provider_config("custom")
    assert config is not None
    assert config["apiKey"] == "sekret"
    assert config["baseUrl"] == "https://custom.ai"


def test_get_provider_config_nonexistent() -> None:
    reg = RealmRegistry()
    assert reg.get_provider_config("nonexistent") is None


def test_unregister_provider() -> None:
    reg = RealmRegistry()
    reg.register_provider("custom", {"apiKey": "sekret"})
    reg.unregister_provider("custom")
    assert reg.get_provider_config("custom") is None
    assert "custom" not in reg.get_registered_providers()


def test_unregister_provider_nonexistent_is_noop() -> None:
    reg = RealmRegistry()
    reg.unregister_provider("never-registered")


def test_get_registered_providers() -> None:
    reg = RealmRegistry()
    assert reg.get_registered_providers() == []
    reg.register_provider("a", {})
    reg.register_provider("b", {})
    providers = reg.get_registered_providers()
    assert "a" in providers
    assert "b" in providers


def test_has_provider_builtin() -> None:
    reg = RealmRegistry()
    assert not reg.has_provider("openrouter")
    reg.register_realm_factory(
        "openrouter",
        lambda api_key="", base_url="", **kw: MockStreamingRealm(
            api_key=api_key, base_url=base_url
        ),
    )
    assert reg.has_provider("openrouter")


def test_has_provider_extension() -> None:
    reg = RealmRegistry()
    reg.register_provider("custom", {})
    assert reg.has_provider("custom")


def test_has_provider_nonexistent() -> None:
    reg = RealmRegistry()
    assert not reg.has_provider("nonexistent")


def test_compose_model_no_extension() -> None:
    reg = RealmRegistry()
    model = reg.compose_model("openai/gpt-oss-20b:free", api_key="test-key")
    assert model is not None
    assert model.api_key == "test-key"


def test_compose_model_unknown() -> None:
    reg = RealmRegistry()
    model = reg.compose_model("nonexistent/model", api_key="test-key")
    assert model is None


def test_compose_model_with_extension_override() -> None:
    reg = RealmRegistry()
    reg.register_provider(
        "openai", {"baseUrl": "https://custom.openai.com", "apiKey": "ext-key"}
    )
    model = reg.compose_model("openrouter/openai/gpt-4", api_key="cli-key")
    if model is not None and model.provider == "openai":
        assert model.api_key == "ext-key"
        assert model.base_url == "https://custom.openai.com"


def test_create_realm_default_openrouter() -> None:
    reg = RealmRegistry()
    model = Model(
        id="test-model",
        name="Test",
        realm="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
    )
    with pytest.raises(NoRealmRegisteredError) as exc_info:
        reg.create_realm(model, api_key="test-key")
    msg = str(exc_info.value)
    assert "No Realm factory registered for model 'test-model'" in msg
    assert "Run 'mvgeos rune install openrouter-realm'" in msg


def test_compose_model_prioritizes_realm_over_provider() -> None:
    reg = RealmRegistry()
    reg.register_provider(
        "openrouter",
        {"apiKey": "openrouter-key", "baseUrl": "https://openrouter.ai/api/v1"},
    )
    model = reg.compose_model("nvidia/nemotron-3.5-lightning:free", api_key="cli-key")
    assert model is not None
    assert model.api_key == "openrouter-key"
    assert model.base_url == "https://openrouter.ai/api/v1"


def test_create_realm_prioritizes_realm_over_provider() -> None:
    reg = RealmRegistry()
    reg.register_realm_factory(
        "openrouter",
        lambda api_key="", base_url="", **kw: MockStreamingRealm(
            api_key=api_key, base_url=base_url
        ),
    )
    reg.register_provider(
        "openrouter",
        {"apiKey": "openrouter-key", "baseUrl": "https://openrouter.ai/api/v1"},
    )
    model = Model(
        id="nvidia/nemotron-3.5-lightning:free",
        name="Nemotron",
        realm="openrouter",
        base_url="https://original.url",
        api_key="cli-key",
    )
    realm = reg.create_realm(model, api_key="cli-key")
    assert isinstance(realm, MockStreamingRealm)
    assert realm._api_key == "openrouter-key"
    assert realm._base_url == "https://openrouter.ai/api/v1"


def test_compose_model_fallback_for_unlisted_model() -> None:
    reg = RealmRegistry()
    reg.register_provider(
        "ollama", {"baseUrl": "http://localhost:11434", "apiKey": "ollama-key"}
    )
    model = reg.compose_model("ollama/llama3", api_key="cli-key")
    assert model is not None
    assert model.id == "ollama/llama3"
    assert model.realm == "ollama"
    assert model.api_key == "ollama-key"
    assert model.base_url == "http://localhost:11434"


def test_compose_model_fallback_with_explicit_provider_name() -> None:
    reg = RealmRegistry()
    reg.register_provider("vllm", {"baseUrl": "http://localhost:8000"})
    model = reg.compose_model(
        "custom-model-id", api_key="cli-key", provider_name="vllm"
    )
    assert model is not None
    assert model.id == "custom-model-id"
    assert model.realm == "vllm"
    assert model.base_url == "http://localhost:8000"


def test_resolve_returns_model_and_realm() -> None:
    reg = RealmRegistry()
    reg.register_realm_factory(
        "openrouter",
        lambda api_key="", base_url="", **kw: MockStreamingRealm(
            api_key=api_key, base_url=base_url
        ),
    )
    model, realm = reg.resolve("openai/gpt-oss-20b:free", api_key="test-key")
    assert isinstance(model, Model)
    assert model.id == "openai/gpt-oss-20b:free"
    assert model.api_key == "test-key"
    assert isinstance(realm, MockStreamingRealm)


def test_resolve_unknown_model_raises() -> None:
    reg = RealmRegistry()
    with pytest.raises(ValueError, match="Unknown model: nonexistent/model"):
        reg.resolve("nonexistent/model", api_key="test-key")


def test_resolve_unlisted_model_with_registered_prefix() -> None:
    reg = RealmRegistry()
    reg.register_realm_factory("ollama", DummyCustomRealm)
    reg.register_provider(
        "ollama", {"baseUrl": "http://localhost:11434", "apiKey": "ollama-key"}
    )
    model, realm = reg.resolve("ollama/llama3", api_key="cli-key")
    assert model.id == "ollama/llama3"
    assert model.realm == "ollama"
    assert model.api_key == "ollama-key"
    assert model.base_url == "http://localhost:11434"
    assert isinstance(realm, Realm)


def test_resolve_with_explicit_provider_name() -> None:
    reg = RealmRegistry()
    reg.register_realm_factory("vllm", DummyCustomRealm)
    reg.register_provider("vllm", {"baseUrl": "http://localhost:8000"})
    model, realm = reg.resolve(
        "custom-model-id", api_key="cli-key", provider_name="vllm"
    )
    assert model.id == "custom-model-id"
    assert model.realm == "vllm"
    assert model.base_url == "http://localhost:8000"
    assert isinstance(realm, Realm)


def test_resolve_extension_overrides_apply_to_model_and_realm() -> None:
    reg = RealmRegistry()
    reg.register_realm_factory(
        "openrouter",
        lambda api_key="", base_url="", **kw: MockStreamingRealm(
            api_key=api_key, base_url=base_url
        ),
    )
    reg.register_provider(
        "openai",
        {"baseUrl": "https://custom.openai.com", "apiKey": "ext-key"},
    )
    model, realm = reg.resolve("openai/gpt-4", api_key="cli-key")
    assert model.api_key == "ext-key"
    assert model.base_url == "https://custom.openai.com"
    assert isinstance(realm, MockStreamingRealm)


def test_resolve_cached_model(tmp_path: Path) -> None:
    cache_file = tmp_path / "models.json"
    cache_payload = {
        "_cached_at": 1000.0,
        "models": [
            {
                "id": "community/cached-model-99b",
                "name": "Cached 99B Model",
                "context_length": 32768,
                "supported_parameters": ["tools", "temperature"],
                "is_free": False,
            }
        ],
    }
    cache_file.write_text(json.dumps(cache_payload), encoding="utf-8")

    model_reg = ModelRegistry(cache_path=cache_file)
    with patch("time.time", return_value=1050.0):
        reg = RealmRegistry(model_registry=model_reg)
        reg.register_realm_factory(
            "openrouter",
            lambda api_key="", base_url="", **kw: MockStreamingRealm(
                api_key=api_key, base_url=base_url
            ),
        )
        model, realm = reg.resolve("community/cached-model-99b", api_key="test-key")

    assert model.id == "community/cached-model-99b"
    assert model.name == "Cached 99B Model"
    assert model.context_window == 32768
    assert model.supported_parameters == ["tools", "temperature"]
    assert model.api_key == "test-key"
    assert isinstance(realm, MockStreamingRealm)


def test_realm_registry_get_model_options_and_flat_ids() -> None:
    from mvgeos_provider.registry import get_flat_model_ids, get_model_options

    reg = RealmRegistry()
    options = reg.get_model_options()
    assert isinstance(options, dict)
    assert len(options) > 0

    flat_ids = reg.get_flat_model_ids()
    assert isinstance(flat_ids, list)
    assert len(flat_ids) > 0

    # Top-level helper functions
    top_options = get_model_options()
    top_flat_ids = get_flat_model_ids()
    assert top_options == options
    assert top_flat_ids == flat_ids


def test_realm_registry_custom_ttl() -> None:
    reg = RealmRegistry(cache_ttl_seconds=1234)
    assert reg.cache_ttl_seconds == 1234
    assert reg.model_registry.cache_ttl_seconds == 1234

    default_reg = RealmRegistry()
    assert default_reg.cache_ttl_seconds == 86400


@pytest.mark.asyncio
async def test_realm_registry_refresh_models_cached_vs_force(tmp_path: Path) -> None:
    cache_path = tmp_path / "models.json"
    cache_payload = {
        "_cached_at": 1000.0,
        "models": [
            {
                "id": "community/cached-model",
                "name": "Cached Model",
                "context_length": 4096,
                "supported_parameters": [],
                "is_free": False,
            }
        ],
    }
    cache_path.write_text(json.dumps(cache_payload), encoding="utf-8")

    with patch("time.time", return_value=1050.0):
        model_reg = ModelRegistry(cache_path=cache_path, cache_ttl_seconds=3600)
        reg = RealmRegistry(model_registry=model_reg)

        # Non-force refresh hits valid cache and returns 0 without network call
        with patch(
            "mvgeos_provider.model_registry.httpx.AsyncClient"
        ) as mock_client_cls:
            count = await reg.refresh_models(force_refresh=False)
            assert count == 0
            mock_client_cls.assert_not_called()

        # Force refresh bypasses valid cache and fetches live models
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {"id": "live/new-model", "name": "Live New Model"},
            ]
        }
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch(
            "mvgeos_provider.model_registry.httpx.AsyncClient", return_value=mock_client
        ):
            count = await reg.refresh_models(force_refresh=True)

        assert count == 1
        assert reg.model_registry.get("live/new-model") is not None


@pytest.mark.asyncio
async def test_top_level_refresh_models() -> None:
    from mvgeos_provider.registry import refresh_models

    with patch(
        "mvgeos_provider.registry.get_default_realm_registry"
    ) as mock_get_default:
        mock_reg = MagicMock()
        mock_reg.refresh_models = AsyncMock(return_value=42)
        mock_get_default.return_value = mock_reg

        result = await refresh_models(force_refresh=True)
        assert result == 42
        mock_reg.refresh_models.assert_awaited_once_with(force_refresh=True)


class DummyCustomRealm(Realm):
    def __init__(self, api_key: str = "", base_url: str = "", **kwargs: object) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.kwargs = kwargs

    async def stream(
        self,
        model: Model,
        invocations: list[object],
        config: ChannelConfig,
        signal: object | None = None,
    ):
        yield RealmResponse(
            model=model,
            mana_used=42,
            stop_reason="stop",
        )


def test_realm_factory_protocol_runtime_check() -> None:
    def factory_func(api_key: str = "", base_url: str = "", **kwargs: object) -> Realm:
        return DummyCustomRealm(api_key, base_url, **kwargs)

    assert isinstance(factory_func, RealmFactory)

    class FactoryClass:
        def __call__(
            self, api_key: str = "", base_url: str = "", **kwargs: object
        ) -> Realm:
            return DummyCustomRealm(api_key, base_url, **kwargs)

    assert isinstance(FactoryClass(), RealmFactory)
    assert not isinstance("not_a_factory", RealmFactory)


def test_register_realm_factory_validation() -> None:
    reg = RealmRegistry()
    with pytest.raises(ValueError, match="Prefix must be a non-empty string"):
        reg.register_realm_factory("", DummyCustomRealm)

    with pytest.raises(ValueError, match="Prefix must be a non-empty string"):
        reg.register_realm_factory("   ", DummyCustomRealm)

    with pytest.raises(TypeError, match="must be callable"):
        reg.register_realm_factory("dummy", "not-callable")  # type: ignore[arg-type]


def test_register_and_get_realm_factory() -> None:
    reg = RealmRegistry()
    assert not reg.has_realm_factory("custom")
    assert reg.get_realm_factory("custom") is None

    reg.register_realm_factory("custom", DummyCustomRealm)
    assert reg.has_realm_factory("custom")
    assert reg.get_realm_factory("custom") is DummyCustomRealm
    assert "custom" in reg.get_registered_realm_factories()
    assert reg.has_provider("custom")


def test_create_realm_resolves_registered_factory_by_provider_name() -> None:
    reg = RealmRegistry()
    reg.register_realm_factory("my_custom", DummyCustomRealm)

    model = Model(
        id="generic-model",
        name="Generic",
        realm="openrouter",
        base_url="https://api.example.com",
        api_key="secret-key",
    )
    realm = reg.create_realm(model, api_key="secret-key", provider_name="my_custom")
    assert isinstance(realm, DummyCustomRealm)
    assert realm.api_key == "secret-key"
    assert realm.base_url == "https://api.example.com"


def test_create_realm_resolves_registered_factory_by_model_realm() -> None:
    reg = RealmRegistry()
    reg.register_realm_factory("custom_backend", DummyCustomRealm)

    model = Model(
        id="model-without-slash",
        name="Custom",
        realm="custom_backend",
        base_url="https://custom.backend.internal",
        api_key="internal-key",
    )
    realm = reg.create_realm(model, api_key="internal-key")
    assert isinstance(realm, DummyCustomRealm)
    assert realm.api_key == "internal-key"
    assert realm.base_url == "https://custom.backend.internal"


def test_create_realm_resolves_registered_factory_by_model_provider() -> None:
    reg = RealmRegistry()
    reg.register_realm_factory("anthropic", DummyCustomRealm)

    model = Model(
        id="anthropic/claude-3-5-sonnet",
        name="Claude 3.5 Sonnet",
        realm="openrouter",
        base_url="https://anthropic.direct",
        api_key="anthropic-key",
    )
    realm = reg.create_realm(model, api_key="anthropic-key")
    assert isinstance(realm, DummyCustomRealm)
    assert realm.api_key == "anthropic-key"
    assert realm.base_url == "https://anthropic.direct"


def test_create_realm_with_extension_config_and_registered_factory() -> None:
    reg = RealmRegistry()
    reg.register_realm_factory("ollama", DummyCustomRealm)
    reg.register_provider(
        "ollama", {"baseUrl": "http://127.0.0.1:11434", "apiKey": "ollama-token"}
    )

    model = Model(
        id="ollama/llama3.1",
        name="Llama 3.1",
        realm="ollama",
        base_url="",
        api_key="",
    )
    realm = reg.create_realm(model, api_key="")
    assert isinstance(realm, DummyCustomRealm)
    assert realm.api_key == "ollama-token"
    assert realm.base_url == "http://127.0.0.1:11434"


def test_resolve_with_registered_realm_factory() -> None:
    reg = RealmRegistry()
    reg.register_realm_factory("local_provider", DummyCustomRealm)
    reg.register_provider("local_provider", {"baseUrl": "http://localhost:8080"})

    model, realm = reg.resolve("local_provider/my-model", api_key="resolve-key")
    assert model.id == "local_provider/my-model"
    assert model.realm == "local_provider"
    assert model.base_url == "http://localhost:8080"
    assert isinstance(realm, DummyCustomRealm)
    assert realm.api_key == "resolve-key"
    assert realm.base_url == "http://localhost:8080"


@pytest.mark.asyncio
async def test_custom_realm_streaming_and_token_counting() -> None:
    reg = RealmRegistry()
    reg.register_realm_factory("custom_math", DummyCustomRealm)

    model = Model(
        id="custom_math/solver-v1",
        name="Solver",
        realm="custom_math",
        base_url="https://math.test",
        api_key="key",
    )
    realm = reg.create_realm(model, api_key="key")
    assert isinstance(realm, DummyCustomRealm)

    config = ChannelConfig(model=model)
    responses: list[RealmResponse] = []
    async for resp in realm.stream(model, [], config):
        responses.append(resp)

    assert len(responses) == 1
    assert responses[0].mana_used == 42
    assert responses[0].stop_reason == "stop"


def test_factory_instantiation_error_handling() -> None:
    def failing_factory(
        api_key: str = "", base_url: str = "", **kwargs: object
    ) -> Realm:
        raise RuntimeError("Failed to initialize custom realm connection")

    reg = RealmRegistry()
    reg.register_realm_factory("failing_prov", failing_factory)

    model = Model(
        id="failing_prov/broken-model",
        name="Broken",
        realm="failing_prov",
        base_url="https://broken.test",
        api_key="key",
    )
    with pytest.raises(
        RuntimeError, match="Failed to initialize custom realm connection"
    ):
        reg.create_realm(model, api_key="key")


@pytest.mark.asyncio
async def test_prewarm_client() -> None:
    reg = RealmRegistry()
    client = await reg.prewarm_client()
    assert client is not None
    assert not client.is_closed
    # Repeat call returns same client
    client2 = await reg.prewarm_client()
    assert client2 is client
    assert reg.get_shared_client() is client
    await reg.close()


def test_realm_is_router_property() -> None:
    base_realm = Realm()
    assert base_realm.is_router is False

    mock_realm = MockStreamingRealm(api_key="test-key")
    assert mock_realm.is_router is True


def test_create_realm_raises_no_realm_registered_error() -> None:
    reg = RealmRegistry()

    model = Model(
        id="anthropic/claude-3-5-sonnet",
        name="Claude",
        realm="",
        base_url="https://api.anthropic.com",
        api_key="key",
    )
    with pytest.raises(NoRealmRegisteredError) as exc_info:
        reg.create_realm(model, api_key="test-key")

    msg = str(exc_info.value)
    assert "No Realm factory registered for model 'anthropic/claude-3-5-sonnet'" in msg
    # The Rune to install is derived from the model's own Realm. This model has
    # none set, so the id prefix is the only evidence available and the message
    # follows it. It used to say openrouter-realm unconditionally, which sent a
    # Summoner after a Rune that cannot serve the model they asked for.
    assert "Run 'mvgeos rune install anthropic-realm'" in msg
    assert exc_info.value.rune_name == "anthropic-realm"
    assert exc_info.value.realm == "anthropic"


def test_clear_realm_factories() -> None:
    reg = RealmRegistry()
    reg.register_realm_factory(
        "openrouter",
        lambda api_key="", base_url="", **kw: MockStreamingRealm(
            api_key=api_key, base_url=base_url
        ),
    )
    assert "openrouter" in reg.get_registered_realm_factories()

    reg.clear_realm_factories()
    assert reg.get_registered_realm_factories() == []

    model = Model(
        id="openai/gpt-4o",
        name="GPT-4o",
        realm="",
        base_url="https://api.openai.com",
        api_key="key",
    )
    with pytest.raises(NoRealmRegisteredError):
        reg.create_realm(model, api_key="test-key")


def test_fallback_chain_unregistered_primary() -> None:
    reg = RealmRegistry()
    mock_openrouter = MockStreamingRealm(api_key="or-key")
    reg.register_realm_factory("openrouter", lambda **kw: mock_openrouter)
    reg.set_fallback_chain("anthropic", ["openrouter"])

    model = Model(
        id="anthropic/claude-3-5-sonnet",
        name="Claude",
        realm="anthropic",
        base_url="https://api.anthropic.com",
        api_key="key",
    )
    # Primary "anthropic" has no factory, so it falls back to "openrouter"
    realm = reg.create_realm(model, api_key="test-key")
    assert realm is mock_openrouter


@pytest.mark.asyncio
async def test_fallback_realm_runtime_failover() -> None:
    from mvgeos_provider.base import FallbackRealm

    class FailingRealm(Realm):
        async def complete(self, *args: Any, **kwargs: Any) -> Any:
            raise ConnectionError("Primary upstream down")

        async def stream(self, *args: Any, **kwargs: Any) -> Any:
            raise ConnectionError("Primary stream failed")
            yield  # type: ignore[unreachable]

    class WorkingRealm(Realm):
        async def complete(self, *args: Any, **kwargs: Any) -> Any:
            return "fallback-response"

        async def stream(self, *args: Any, **kwargs: Any) -> Any:
            yield "chunk-from-fallback"

    composite = FallbackRealm(primary=FailingRealm(), fallback=WorkingRealm())
    model = Model(id="test/model", name="test", realm="test", base_url="", api_key="")
    cfg = ChannelConfig(model=model)

    # Test complete failover
    result = await composite.complete(model, [], cfg)
    assert result == "fallback-response"

    # Test stream failover
    chunks = []
    async for chunk in composite.stream(model, [], cfg):
        chunks.append(chunk)
    assert chunks == ["chunk-from-fallback"]
