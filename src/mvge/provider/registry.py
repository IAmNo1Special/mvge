from __future__ import annotations

import asyncio
from typing import Any

import httpx

from mvge.core.channel import Model
from mvge.core.proxy import sanitize_no_proxy_env
from mvge.provider.base import (
    FallbackRealm,
    NoRealmRegisteredError,
    Realm,
    RealmFactory,
)
from mvge.provider.model_registry import CatalogRefresh, ModelRegistry
from mvge.provider.realms import (
    REALM_RUNES,
    realm_for_model_id,
    rune_for_realm,
)


def no_realm_registered(
    model: Model | str, provider_name: str | None = None
) -> NoRealmRegisteredError:
    """Build the error for a model whose Realm has no registered factory.

    The Rune to install is derived from the model's own Realm rather than
    hardcoded. That is the whole reason this is a function: the message is the
    only remedy a Summoner gets when the engine cannot resolve a model, and a
    hardcoded string in it is a string that goes stale the moment a second Realm
    exists -- which is how a model that needs ``opencode-realm`` gets told to
    install ``openrouter-realm``.

    The exception carries ``realm`` and ``rune_name`` as attributes so callers
    can act on them. Both the CLI prompt and the GUI badge used to recover the
    name by splitting the message on ``"install "``, which breaks the moment the
    wording changes.
    """
    model_id = model if isinstance(model, str) else model.id
    realm = (
        provider_name
        or ("" if isinstance(model, str) else model.realm)
        or realm_for_model_id(model_id)
    )
    rune_name = rune_for_realm(realm)
    return NoRealmRegisteredError(
        f"No Realm factory registered for model '{model_id}'. "
        f"Run 'mvge rune install {rune_name}' to install it from "
        "the central marketplace.",
        realm=realm,
        rune_name=rune_name,
    )


class RealmRegistry:
    def __init__(
        self,
        shared_client: httpx.AsyncClient | None = None,
        model_registry: ModelRegistry | None = None,
        cache_ttl_seconds: int = 86400,
    ) -> None:
        self._shared_client = shared_client
        self._cache_ttl_seconds = cache_ttl_seconds
        self._model_registry = model_registry or ModelRegistry(
            cache_ttl_seconds=cache_ttl_seconds
        )
        self._model_registry.load_cache()
        self._extension_providers: dict[str, dict[str, Any]] = {}
        self._realm_factories: dict[str, RealmFactory] = {}
        self._fallback_chains: dict[str, list[str]] = {}

    @property
    def cache_ttl_seconds(self) -> int:
        return self._model_registry.cache_ttl_seconds

    @property
    def model_registry(self) -> ModelRegistry:
        return self._model_registry

    async def refresh_models(self, force_refresh: bool = False) -> int:
        """Refresh model catalog from provider APIs or cache.

        Args:
            force_refresh: If True, bypass disk cache and fetch live models.

        Returns:
            Number of models loaded from the API, or 0 if cache was used.
        """
        return await self._model_registry.refresh(force_refresh=force_refresh)

    def get_model_options(self) -> dict[str, str]:
        """Return model display options with free models first, sorted by id."""
        return self._model_registry.get_model_options()

    def get_flat_model_ids(self) -> list[str]:
        """Return all model IDs, filtering out internal ~ prefixes."""
        return self._model_registry.get_flat_model_ids()

    def get_providers_for_realm(self, realm: str = "openrouter") -> list[str]:
        """Return sorted unique list of provider prefixes for models in the realm."""
        return self._model_registry.get_providers_for_realm(realm)

    def get_models_for_provider(
        self, provider: str, realm: str = "openrouter"
    ) -> list[Model]:
        """Return list of Model instances matching the given provider and realm."""
        return self._model_registry.get_models_for_provider(provider, realm)

    def get_supported_contemplation_levels(self, model_id: str) -> list[str]:
        """Return supported contemplation levels for model ID, or empty list."""
        return self._model_registry.get_supported_contemplation_levels(model_id)

    def is_realm_router(self, realm: str = "openrouter") -> bool:
        """Return True if the specified realm is a router realm."""
        if realm.lower() == "openrouter":
            return True
        factory = self._realm_factories.get(realm)
        if factory is not None:
            try:
                instance = factory()
                return bool(getattr(instance, "is_router", False))
            except Exception:
                pass
        return False

    def get_shared_client(self) -> httpx.AsyncClient:
        if self._shared_client is None or self._shared_client.is_closed:
            # Direct/library use never ran CLI startup: sanitize here,
            # immediately before the client that parses NO_PROXY is built.
            sanitize_no_proxy_env()
            self._shared_client = httpx.AsyncClient(
                timeout=httpx.Timeout(60.0),
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
            )
        return self._shared_client

    async def prewarm_client(self) -> httpx.AsyncClient:
        """Pre-warm the shared HTTP client in a thread to avoid event-loop blocking."""
        if self._shared_client is None or self._shared_client.is_closed:
            # Sanitize in this thread before the worker thread builds the
            # client: the environment is process-wide, so the worker sees
            # the sanitized value.
            sanitize_no_proxy_env()
            self._shared_client = await asyncio.to_thread(
                httpx.AsyncClient,
                timeout=httpx.Timeout(60.0),
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
            )
        return self._shared_client

    async def close(self) -> None:
        if self._shared_client is not None and not self._shared_client.is_closed:
            await self._shared_client.aclose()
            self._shared_client = None

    def register_provider(self, name: str, config: dict[str, Any]) -> None:
        if name not in self._extension_providers:
            self._extension_providers[name] = {}
        self._extension_providers[name].update(config)

    def unregister_provider(self, name: str) -> None:
        """Unregister a rune-registered provider."""
        self._extension_providers.pop(name, None)

    def get_provider_config(self, name: str) -> dict[str, Any] | None:
        return self._extension_providers.get(name)

    def get_registered_providers(self) -> list[str]:
        providers = list(self._extension_providers.keys())
        for rf in self._realm_factories:
            if rf not in providers:
                providers.append(rf)
        return providers

    def register_realm_factory(self, prefix: str, factory: RealmFactory) -> None:
        """Register a pluggable RealmFactory for a provider prefix."""
        if not prefix or not isinstance(prefix, str) or not prefix.strip():
            raise ValueError("Prefix must be a non-empty string")
        if not callable(factory):
            raise TypeError(f"Realm factory for '{prefix}' must be callable")
        self._realm_factories[prefix.strip()] = factory

    def unregister_realm_factory(self, prefix: str) -> None:
        """Unregister a RealmFactory for a provider prefix."""
        self._realm_factories.pop(prefix, None)

    def clear_realm_factories(self) -> None:
        """Clear all registered realm factories."""
        self._realm_factories.clear()

    def get_realm_factory(self, prefix: str) -> RealmFactory | None:
        """Get the registered RealmFactory for a prefix, or None."""
        return self._realm_factories.get(prefix)

    def has_realm_factory(self, prefix: str) -> bool:
        """Check whether a RealmFactory is registered for a prefix."""
        return prefix in self._realm_factories

    def get_registered_realm_factories(self) -> list[str]:
        """Return a list of all registered realm factory prefixes."""
        return list(self._realm_factories.keys())

    def has_provider(self, name: str) -> bool:
        return name in self._extension_providers or name in self._realm_factories

    def _candidate_keys(
        self, model: Model, provider_name: str | None = None
    ) -> list[str]:
        candidates: list[str] = []
        if provider_name:
            candidates.append(provider_name)
        if model.realm and model.realm not in candidates:
            candidates.append(model.realm)
        if model.provider and model.provider not in candidates:
            candidates.append(model.provider)
        for part in model.id.split("/"):
            if part and part not in candidates:
                candidates.append(part)
        return candidates

    def _get_extension_config(
        self, model: Model, provider_name: str | None = None
    ) -> dict[str, Any] | None:
        for candidate in self._candidate_keys(model, provider_name):
            if candidate in self._extension_providers:
                return self._extension_providers[candidate]
        return None

    def _find_realm_factory(
        self, model: Model, provider_name: str | None = None
    ) -> RealmFactory | None:
        for candidate in self._candidate_keys(model, provider_name):
            if candidate in self._realm_factories:
                return self._realm_factories[candidate]
        return None

    def set_fallback_chain(self, primary_realm: str, fallbacks: list[str]) -> None:
        """Register a list of fallback realm prefixes for a primary realm prefix."""
        self._fallback_chains[primary_realm.strip()] = [f.strip() for f in fallbacks]

    def get_fallback_chain(self, primary_realm: str) -> list[str]:
        """Get the configured fallback realm prefixes for a primary realm prefix."""
        return list(self._fallback_chains.get(primary_realm.strip(), []))

    def create_realm(
        self,
        model: Model | str,
        api_key: str = "",
        provider_name: str | None = None,
        with_fallback: bool = False,
    ) -> Realm:
        if isinstance(model, str):
            model = self.compose_model(
                model, api_key=api_key, provider_name=provider_name
            ) or Model(
                id=model,
                name=model,
                realm=provider_name or (model.split("/")[0] if "/" in model else model),
                base_url="",
                api_key=api_key,
            )
        ext_config = self._get_extension_config(model, provider_name)
        base_url = (ext_config.get("baseUrl") if ext_config else None) or model.base_url
        key = (
            (ext_config.get("apiKey") if ext_config else None)
            or model.api_key
            or api_key
        )

        factory = self._find_realm_factory(model, provider_name)
        if factory is not None:
            primary_realm = factory(api_key=key, base_url=base_url)
            if with_fallback:
                realm_key = provider_name or model.realm
                for fallback_prefix in self.get_fallback_chain(realm_key):
                    fb_factory = self.get_realm_factory(fallback_prefix)
                    if fb_factory is not None:
                        fb_realm = fb_factory(api_key=key, base_url=base_url)
                        return FallbackRealm(primary_realm, fb_realm)
            return primary_realm

        # If primary factory is not registered, try fallback chain
        realm_key = provider_name or model.realm
        for fallback_prefix in self.get_fallback_chain(realm_key):
            fb_factory = self.get_realm_factory(fallback_prefix)
            if fb_factory is not None:
                return fb_factory(api_key=key, base_url=base_url)

        raise no_realm_registered(model, provider_name)

    def create_resilient_realm(
        self,
        model: Model | str,
        api_key: str = "",
        provider_name: str | None = None,
    ) -> Realm:
        """Create a Realm configured with automatic failover to fallback chains."""
        return self.create_realm(
            model, api_key=api_key, provider_name=provider_name, with_fallback=True
        )

    def compose_model(
        self,
        model_id: str,
        api_key: str,
        provider_name: str | None = None,
    ) -> Model | None:
        model_info = self._model_registry.get(model_id)
        if model_info is not None:
            model = Model(
                id=model_info.id,
                name=model_info.name,
                realm=model_info.realm,
                base_url=model_info.base_url,
                api_key=api_key,
                max_completion_mana=model_info.max_completion_mana,
                context_window=model_info.context_window,
                max_tokens=model_info.max_tokens,
                headers=dict(model_info.headers or {}),
                supported_parameters=list(model_info.supported_parameters),
                supported_contemplation_levels=list(
                    model_info.supported_contemplation_levels
                ),
                is_free=model_info.is_free,
            )
        else:
            target_provider = provider_name
            if not target_provider and "/" in model_id:
                target_provider = model_id.split("/")[0]

            if target_provider and self.has_provider(target_provider):
                model = Model(
                    id=model_id,
                    name=model_id,
                    realm=target_provider,
                    base_url="",
                    api_key=api_key,
                )
            else:
                return None

        ext_config = self._get_extension_config(model, provider_name)
        if ext_config is not None:
            if "baseUrl" in ext_config:
                model.base_url = str(ext_config["baseUrl"])
            if "apiKey" in ext_config:
                model.api_key = str(ext_config["apiKey"])
            ext_headers = ext_config.get("headers")
            if isinstance(ext_headers, dict):
                model.headers.update(ext_headers)

        return model

    def resolve(
        self,
        model_id: str,
        api_key: str,
        provider_name: str | None = None,
    ) -> tuple[Model, Realm]:
        """Look up model metadata across static baseline and cached catalog,
        and construct the paired (Model, Realm) in one call.

        Raises:
            ValueError: If the model cannot be resolved from baseline, cache,
                or registered providers.
        """
        model = self.compose_model(model_id, api_key, provider_name)
        if model is None:
            raise self._unknown_model_error(model_id, provider_name)
        realm = self.create_realm(model, api_key, provider_name)
        return model, realm

    def _unknown_model_error(
        self, model_id: str, provider_name: str | None
    ) -> Exception:
        """The error for a slug that is neither catalogued nor servable.

        Two failures look identical from here -- a model that does not exist,
        and a model whose Realm is simply not installed -- and they need
        opposite answers. A slug under a Realm we ship is the second: the model
        is real, the Rune is missing, and the remedy is one command. Reporting
        it as an unknown model would send a Summoner looking for a typo when the
        actual fix is `mvge rune install`.

        Only the membership test is needed. An earlier draft also required the
        prefix to be unregistered, which cannot be true here: `compose_model`
        accepts any slug whose prefix has a registered provider and only returns
        None when it does not. So that clause was unreachable, and worse, it read
        as if it were doing a safety job it never did.

        Note the flip side, which is deliberate and pre-existing: once a Realm
        *is* registered, any slug under it resolves, typo included. A
        Rune-provided Realm serves models no catalog knows about, so refusing
        unknown ids there would refuse the feature.
        """
        prefix = provider_name or realm_for_model_id(model_id)
        if prefix in REALM_RUNES:
            return no_realm_registered(model_id, prefix)
        return ValueError(f"Unknown model: {model_id}")


_DEFAULT_REGISTRY: RealmRegistry | None = None


def get_default_realm_registry() -> RealmRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = RealmRegistry()
    return _DEFAULT_REGISTRY


def get_model_options() -> dict[str, str]:
    """Return model display options with free models first, sorted by id."""
    return get_default_realm_registry().get_model_options()


def get_flat_model_ids() -> list[str]:
    """Return all valid model IDs (ignoring internal ~ prefixes)."""
    return get_default_realm_registry().get_flat_model_ids()


async def refresh_models(force_refresh: bool = False) -> int:
    """Refresh model catalog using the default realm registry."""
    return await get_default_realm_registry().refresh_models(
        force_refresh=force_refresh
    )


async def refresh_catalog(force_refresh: bool = False) -> CatalogRefresh:
    """Refresh the model catalog and report what changed against the realm.

    Unlike :func:`refresh_models`, an unreachable realm is reported as such
    rather than collapsing into a count of zero.
    """
    return await get_default_realm_registry().model_registry.refresh_catalog(
        force_refresh=force_refresh
    )


def get_providers_for_realm(realm: str = "openrouter") -> list[str]:
    """Return sorted unique list of provider prefixes for models in the realm."""
    return get_default_realm_registry().get_providers_for_realm(realm)


def get_models_for_provider(provider: str, realm: str = "openrouter") -> list[Model]:
    """Return list of Model instances matching the given provider and realm."""
    return get_default_realm_registry().get_models_for_provider(provider, realm)


def get_supported_contemplation_levels(model_id: str) -> list[str]:
    """Return supported contemplation levels for model ID, or empty list."""
    return get_default_realm_registry().get_supported_contemplation_levels(model_id)


def is_realm_router(realm: str = "openrouter") -> bool:
    """Return True if the realm routes requests across multiple providers."""
    return get_default_realm_registry().is_realm_router(realm)


get_registry = get_default_realm_registry
