from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx

from mvge.core.channel import Model
from mvge.core.layers import models_file
from mvge.provider.realms import (
    REALM_BASE_URLS,
    REALMS_WITH_ANONYMOUS_FREE_TIER,
    REALMS_WITHOUT_CREDENTIAL,
    realm_for_model_id,
)

logger = logging.getLogger(__name__)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
CACHE_TTL_SECONDS = 86400

_MODELS_PATH = Path(__file__).parent / "models.json"


_DEFAULT_CONTEMPLATION_LEVELS: list[str] = [
    "none",
    "low",
    "medium",
    "high",
    "x-high",
]


def _create_catalog_model(
    id: str,
    name: str,
    realm: str,
    base_url: str,
    context_window: int = 4096,
    supported_parameters: list[str] | None = None,
    is_free: bool = False,
    supported_contemplation_levels: list[str] | None = None,
) -> Model:
    """Build one catalog entry for the Realm that serves it.

    The Realm and its base URL are arguments rather than constants because the
    baseline catalog is no longer exclusively OpenRouter's. Hardcoding
    ``realm="openrouter"`` here meant a non-OpenRouter entry would have been
    built as an OpenRouter model -- and would have been sent to
    openrouter.ai, where it does not exist.
    """
    params = supported_parameters or []
    if supported_contemplation_levels is not None:
        levels = list(supported_contemplation_levels)
    elif any(
        p in params
        for p in ("reasoning", "thinking", "include_reasoning", "reasoning_effort")
    ) or any(m in id for m in ("openai/o1", "openai/o3")):
        levels = list(_DEFAULT_CONTEMPLATION_LEVELS)
    else:
        levels = []

    return Model(
        id=id,
        name=name,
        realm=realm,
        base_url=base_url,
        api_key="",
        max_completion_mana=0,
        context_window=context_window,
        max_tokens=4096,
        supported_parameters=params,
        supported_contemplation_levels=levels,
        is_free=is_free,
    )


def _create_openrouter_model(
    id: str,
    name: str,
    context_window: int = 4096,
    supported_parameters: list[str] | None = None,
    is_free: bool = False,
    supported_contemplation_levels: list[str] | None = None,
) -> Model:
    """Build one OpenRouter catalog entry.

    The live refresh at ``OPENROUTER_MODELS_URL`` only ever returns OpenRouter
    models, so those two call sites keep this name rather than passing a Realm
    they already know.
    """
    return _create_catalog_model(
        id=id,
        name=name,
        realm="openrouter",
        base_url=OPENROUTER_BASE_URL,
        context_window=context_window,
        supported_parameters=supported_parameters,
        is_free=is_free,
        supported_contemplation_levels=supported_contemplation_levels,
    )


def _load_models_json() -> list[tuple[str, str, str, int, list[str], bool]]:
    """Read the shipped baseline catalog.

    Entries are positional ``[id, name, context_window, params, realm]``. The
    Realm is optional and defaults to ``openrouter``, which is what every entry
    written before a second Realm existed means -- so the default keeps the
    existing file valid instead of forcing a rewrite of ~200 entries to say
    something already implied by their shape.
    """
    try:
        data = json.loads(_MODELS_PATH.read_text(encoding="utf-8"))
        result: list[tuple[str, str, str, int, list[str], bool]] = []
        for is_free, key in ((True, "free"), (False, "paid")):
            for entry in data.get(key, []):
                mid = entry[0]
                name = entry[1]
                ctx = entry[2]
                params = entry[3] if len(entry) > 3 else []
                realm = entry[4] if len(entry) > 4 else "openrouter"
                result.append((mid, name, realm, ctx, params, is_free))
        return result
    except (json.JSONDecodeError, OSError):
        return []


def _load_baseline_models() -> dict[str, Model]:
    return {
        mid: _create_catalog_model(
            id=mid,
            name=name,
            realm=realm,
            base_url=REALM_BASE_URLS.get(realm, ""),
            context_window=ctx,
            supported_parameters=params,
            is_free=is_free,
        )
        for mid, name, realm, ctx, params, is_free in _load_models_json()
    }


def list_models() -> list[Model]:
    return list(_load_baseline_models().values())


def serving_realm_for_model(model_id: str) -> str:
    """The Realm that will actually serve ``model_id``.

    Two derivations answer two different questions, and picking the wrong one
    sends a credential to a host that never asked for it:

    ``realm_for_model_id``
        what the id is *spelled* -- its prefix, which for a routed slug like
        ``nvidia/nemotron-3-ultra-550b-a55b:free`` is the provider.
    this
        where the request is *sent*, which is what a credential lookup, a cost
        guard, and a "which Rune do I install" message all need.

    The shipped catalog already records the answer per entry -- that entry is
    why ``DEFAULT_REALM`` has to be a copy at all (see ADR-0015) -- so it is
    consulted first. Falling back to the prefix is not a second answer: a Rune
    may serve a model no catalog carries, which is the feature that makes Runes
    worth having, and refusing to name a Realm there would refuse that model.

    Reads the baseline catalog rather than ``ModelRegistry``, whose cache
    refreshes over the network. This runs on the credential gate, and a prompt
    must not wait on an HTTP call.
    """
    model = _load_baseline_models().get(model_id)
    if model is not None and model.realm:
        return model.realm
    return realm_for_model_id(model_id)


def model_requires_credential(model_id: str | None) -> bool:
    """True unless the engine will serve ``model_id`` without a credential.

    Every credential gate asks this, so a Summoner is prompted by one answer
    rather than by several ad-hoc tests that happened to agree. Three cases, in
    the order they can be decided:

    - a Realm with no credential concept at all is exempt for every model;
    - a Realm whose free tier is anonymous is exempt only for a model the
      catalog records as free;
    - anything else requires one.

    Defaults to required. Both tables record exceptions rather than rules, so a
    newly added Realm works on day one with no engine edit and cannot be made to
    fail by an omission -- and the unknown-model case below is the same
    reasoning: a model the shipped catalog does not carry is required, because a
    Realm wrongly exempted reaches a host that may answer anyway for a while,
    while a model wrongly exempted is the one that costs a Summoner their paid
    access.

    Reads ``is_free`` rather than ``Model.free``. The property also accepts a
    ``-free`` or ``:free`` suffix on the strength of the name, and a credential
    gate must not decide that a model is free from how it is spelled -- the same
    reason ``free_suffix_for_realm`` is deliberately one Realm at a time.

    The baseline catalog is read rather than ``ModelRegistry``, whose cache
    refreshes from the network. This runs on the path that decides whether to
    prompt, and a prompt must not wait on an HTTP call.
    """
    if not model_id:
        return True
    realm = serving_realm_for_model(model_id)
    if realm in REALMS_WITHOUT_CREDENTIAL:
        return False
    if realm not in REALMS_WITH_ANONYMOUS_FREE_TIER:
        return True
    model = _load_baseline_models().get(model_id)
    return model is None or not model.is_free


def _default_cache_path() -> Path:
    return models_file()


class RefreshStatus(StrEnum):
    """Outcome of a catalog refresh."""

    REFRESHED = "refreshed"
    CACHE_FRESH = "cache_fresh"
    UNREACHABLE = "unreachable"


@dataclass(frozen=True)
class CatalogRefresh:
    """What a catalog refresh actually changed.

    ``added``, ``removed`` and ``no_longer_free`` compare the Realm's live
    catalog against everything the registry could resolve beforehand, which is
    the shipped snapshot merged with any on-disk cache.

    The three buckets are disjoint and cover every id either side. ``removed``
    holds ids that retired from the paid catalog; ``no_longer_free`` holds every
    id that used to be free and no longer is -- whether it was repriced or
    withdrawn outright. That second bucket is the one that matters to a Summoner
    on the free tier: an id that stops being free is otherwise discovered at the
    point of a failed request, not at the point of choice.

    The registry is never pruned: a stale id is reported, not dropped, so a
    partial or unreachable Realm cannot take the shipped catalog away.
    """

    status: RefreshStatus
    total: int
    added: tuple[str, ...]
    removed: tuple[str, ...]
    no_longer_free: tuple[str, ...]
    cache_path: Path


class ModelRegistry:
    def __init__(
        self,
        cache_path: Path | None = None,
        cache_ttl_seconds: int = CACHE_TTL_SECONDS,
    ) -> None:
        self._models: dict[str, Model] = {}
        self._cache_path = cache_path or _default_cache_path()
        self._cache_ttl_seconds = cache_ttl_seconds
        self._refreshed = False
        self._load_baseline()

    @property
    def cache_ttl_seconds(self) -> int:
        return self._cache_ttl_seconds

    @property
    def models(self) -> dict[str, Model]:
        return dict(self._models)

    def get(self, model_id: str) -> Model | None:
        return self._models.get(model_id)

    def list_all(self) -> list[Model]:
        return list(self._models.values())

    def get_model_options(self) -> dict[str, str]:
        """Return model display options with free models first, sorted by id."""
        models = self.list_all()
        free: dict[str, str] = {}
        paid: dict[str, str] = {}

        for model in models:
            if not model.id or model.id.startswith("~"):
                continue
            display = model.name or model.id
            if model.free:
                free[model.id] = display
            else:
                paid[model.id] = display

        result: dict[str, str] = {}
        for mid in sorted(free.keys()):
            result[mid] = free[mid]
        for mid in sorted(paid.keys()):
            result[mid] = paid[mid]
        return result

    def get_flat_model_ids(self) -> list[str]:
        """Return all model IDs, filtering out internal ~ prefixes."""
        return [m.id for m in self.list_all() if m.id and not m.id.startswith("~")]

    def get_providers_for_realm(self, realm: str = "openrouter") -> list[str]:
        """Return sorted unique list of provider prefixes for models in the realm."""
        providers: set[str] = set()
        for m in self.list_all():
            if m.realm == realm and m.provider_prefix:
                providers.add(m.provider_prefix)
        return sorted(providers)

    def get_models_for_provider(
        self, provider: str, realm: str = "openrouter"
    ) -> list[Model]:
        """Return list of Model instances matching the given provider and realm."""
        return [
            m
            for m in self.list_all()
            if m.realm == realm and m.provider_prefix == provider
        ]

    def get_supported_contemplation_levels(self, model_id: str) -> list[str]:
        """Return supported contemplation levels for model ID, or empty list."""
        model = self.get(model_id)
        if model is None:
            return []
        return list(model.supported_contemplation_levels)

    def _load_baseline(self) -> None:
        self._models.update(_load_baseline_models())

    def load_cache(self, force_refresh: bool = False) -> bool:
        if force_refresh:
            return False
        if not self._cache_path.exists():
            return False
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        timestamp = data.get("_cached_at", 0)
        if time.time() - timestamp > self._cache_ttl_seconds:
            return False
        for entry in data.get("models", []):
            mid = entry["id"]
            self._models[mid] = _create_openrouter_model(
                id=mid,
                name=entry.get("name", mid),
                context_window=entry.get("context_length", 4096),
                supported_parameters=entry.get("supported_parameters", []),
                is_free=entry.get("is_free", False),
                supported_contemplation_levels=entry.get(
                    "supported_contemplation_levels"
                ),
            )
        self._refreshed = True
        logger.info("Loaded %d models from cache", len(data.get("models", [])))
        return True

    def needs_refresh(self) -> bool:
        """Return True if no valid cache has been loaded yet."""
        return not self._refreshed

    async def auto_refresh(self, force_refresh: bool = False) -> int:
        """Refresh only if cache is stale or missing (unless forced).

        Returns the number of models loaded from the API, or 0 if
        the cache was already fresh.
        """
        if not force_refresh and not self.needs_refresh():
            return 0
        return await self.refresh(force_refresh=force_refresh)

    async def refresh(
        self,
        force_refresh: bool = False,
        client: httpx.AsyncClient | None = None,
    ) -> int:
        if not force_refresh and (self._refreshed or self.load_cache()):
            return 0

        api_data = await self._fetch_api_models(client)
        if api_data is None:
            return 0

        landed = self._absorb_api_models(api_data)
        self._save_cache(api_data)
        self._refreshed = True
        logger.info("Refreshed models: %d from API", len(landed))
        return len(landed)

    async def refresh_catalog(
        self,
        force_refresh: bool = False,
        client: httpx.AsyncClient | None = None,
    ) -> CatalogRefresh:
        """Refresh the catalog and report what changed against the Realm.

        Unlike :meth:`refresh`, this distinguishes "the cache was still fresh",
        "the Realm answered" and "the Realm could not be reached", so a failed
        fetch can never be reported as a refresh that updated nothing.
        """
        if not force_refresh and (self._refreshed or self.load_cache()):
            return CatalogRefresh(
                status=RefreshStatus.CACHE_FRESH,
                total=len(self._models),
                added=(),
                removed=(),
                no_longer_free=(),
                cache_path=self._cache_path,
            )

        before: dict[str, bool] = {
            mid: model.is_free for mid, model in self._models.items()
        }

        api_data = await self._fetch_api_models(client)
        if api_data is None:
            return CatalogRefresh(
                status=RefreshStatus.UNREACHABLE,
                total=0,
                added=(),
                removed=(),
                no_longer_free=(),
                cache_path=self._cache_path,
            )

        landed = self._absorb_api_models(api_data)
        self._save_cache(api_data)
        self._refreshed = True

        live = {mid: self._models[mid].is_free for mid in landed}
        return CatalogRefresh(
            status=RefreshStatus.REFRESHED,
            total=len(landed),
            added=tuple(sorted(mid for mid in live if mid not in before)),
            removed=tuple(
                mid for mid in sorted(before) if mid not in live and not before[mid]
            ),
            no_longer_free=tuple(
                sorted(
                    mid for mid in before if before[mid] and not live.get(mid, False)
                )
            ),
            cache_path=self._cache_path,
        )

    async def _fetch_api_models(
        self, client: httpx.AsyncClient | None
    ) -> list[dict[str, Any]] | None:
        """Return the Realm's model entries, or ``None`` when unreachable."""
        try:
            if client is not None:
                response = await client.get(OPENROUTER_MODELS_URL, timeout=30)
            else:
                async with httpx.AsyncClient() as http_client:
                    response = await http_client.get(OPENROUTER_MODELS_URL, timeout=30)

            if response.status_code != 200:
                logger.warning(
                    "OpenRouter models API returned %d",
                    response.status_code,
                )
            res_json = response.json()

            if isinstance(res_json, dict):
                api_data: list[dict[str, Any]] = res_json.get("data", [])
            elif isinstance(res_json, list):
                api_data = res_json
            else:
                api_data = []
        except Exception as exc:
            # Not logger.exception: an unreachable realm is an expected outcome
            # that callers report in their own words, so the traceback would only
            # bury that message under a stack the Summoner cannot act on.
            logger.warning("Failed to fetch OpenRouter models: %s", exc)
            return None
        return api_data

    def _absorb_api_models(self, api_data: list[dict[str, Any]]) -> tuple[str, ...]:
        """Merge the Realm's entries into the catalog, returning the ids landed."""
        landed: list[str] = []
        for entry in api_data:
            mid = entry.get("id", "")
            if not mid:
                continue
            levels = (
                entry.get("supported_contemplation_levels")
                or entry.get("contemplation_levels")
                or entry.get("reasoning_levels")
            )
            self._models[mid] = _create_openrouter_model(
                id=mid,
                name=entry.get("name", mid),
                context_window=entry.get("context_length", 4096),
                supported_parameters=entry.get("supported_parameters", []),
                is_free=_is_free_entry(entry),
                supported_contemplation_levels=levels,
            )
            landed.append(mid)
        return tuple(landed)

    def _save_cache(self, api_data: list[dict[str, Any]]) -> None:
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache: dict[str, Any] = {
                "_cached_at": time.time(),
                "models": [
                    {
                        "id": e.get("id", ""),
                        "name": e.get("name", ""),
                        "context_length": e.get("context_length", 4096),
                        "supported_parameters": e.get("supported_parameters", []),
                        "is_free": _is_free_entry(e),
                        "supported_contemplation_levels": (
                            self._models[e["id"]].supported_contemplation_levels
                            if e.get("id") in self._models
                            else (e.get("supported_contemplation_levels") or [])
                        ),
                    }
                    for e in api_data
                ],
            }
            self._cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")
        except OSError:
            logger.exception("Failed to save models cache")


def _is_free_entry(entry: dict[str, Any]) -> bool:
    """Determine if an API model entry is free."""
    mid = entry.get("id", "")
    if mid.endswith(":free") or mid == "openrouter/free":
        return True
    pricing = entry.get("pricing")
    if not isinstance(pricing, dict):
        return False
    try:
        return (
            float(pricing.get("prompt", "1")) == 0
            and float(pricing.get("completion", "1")) == 0
        )
    except (ValueError, TypeError):
        return False
