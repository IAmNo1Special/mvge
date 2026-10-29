import json
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mvge.core.channel import Model
from mvge.provider.model_registry import (
    CACHE_TTL_SECONDS,
    ModelRegistry,
    RefreshStatus,
    _create_openrouter_model,
    _is_free_entry,
)


def test_is_free_entry_free_suffix() -> None:
    assert _is_free_entry({"id": "openai/gpt-4:free"}) is True
    assert _is_free_entry({"id": "openrouter/free"}) is True


def test_is_free_entry_pricing_zero() -> None:
    assert (
        _is_free_entry({"id": "x", "pricing": {"prompt": "0", "completion": "0"}})
        is True
    )
    assert (
        _is_free_entry({"id": "x", "pricing": {"prompt": "1", "completion": "0"}})
        is False
    )


def test_is_free_entry_no_pricing() -> None:
    assert _is_free_entry({"id": "x"}) is False
    assert _is_free_entry({"id": "x", "pricing": "bad"}) is False
    assert (
        _is_free_entry({"id": "x", "pricing": {"prompt": "bad", "completion": "bad"}})
        is False
    )


def test_load_cache_missing(tmp_path: Path) -> None:
    reg = ModelRegistry(cache_path=tmp_path / "cache.json")
    assert reg.load_cache() is False


def test_load_cache_valid(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(
        json.dumps(
            {"_cached_at": time.time(), "models": [{"id": "a/b", "name": "A B"}]}
        ),
        encoding="utf-8",
    )
    reg = ModelRegistry(cache_path=cache_path)
    assert reg.load_cache() is True
    assert reg.get("a/b") is not None
    assert reg.needs_refresh() is False


def test_load_cache_expired(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(
        json.dumps({"_cached_at": time.time() - CACHE_TTL_SECONDS - 10, "models": []}),
        encoding="utf-8",
    )
    reg = ModelRegistry(cache_path=cache_path)
    assert reg.load_cache() is False


def test_load_cache_corrupt(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    cache_path.write_text("not json", encoding="utf-8")
    reg = ModelRegistry(cache_path=cache_path)
    assert reg.load_cache() is False


def test_needs_refresh_initial(tmp_path: Path) -> None:
    reg = ModelRegistry(cache_path=tmp_path / "c.json")
    assert reg.needs_refresh() is True


@pytest.mark.asyncio
async def test_auto_refresh_when_fresh(tmp_path: Path) -> None:
    reg = ModelRegistry(cache_path=tmp_path / "c.json")
    reg._refreshed = True
    assert await reg.auto_refresh() == 0


@pytest.mark.asyncio
async def test_refresh_success(tmp_path: Path) -> None:
    reg = ModelRegistry(cache_path=tmp_path / "c.json")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": [
            {
                "id": "a/b",
                "name": "A B",
                "context_length": 4096,
                "pricing": {"prompt": "0", "completion": "0"},
            },
            {"id": "c/d", "name": "C D", "pricing": {"prompt": "1", "completion": "1"}},
            {"id": "", "name": "bad"},
        ]
    }
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)
    with patch(
        "mvge.provider.model_registry.httpx.AsyncClient", return_value=mock_client
    ):
        count = await reg.refresh()
    assert count == 2
    assert reg.get("a/b").is_free is True
    assert reg.get("c/d").is_free is False


@pytest.mark.asyncio
async def test_refresh_handles_exception(tmp_path: Path) -> None:
    reg = ModelRegistry(cache_path=tmp_path / "c.json")
    with patch(
        "mvge.provider.model_registry.httpx.AsyncClient",
        side_effect=Exception("boom"),
    ):
        assert await reg.refresh() == 0


def test_save_cache_creates_file(tmp_path: Path) -> None:
    reg = ModelRegistry(cache_path=tmp_path / "sub" / "c.json")
    reg._save_cache([{"id": "x/y", "name": "X Y"}])
    assert (tmp_path / "sub" / "c.json").exists()


def test_get_model_options_sorting_and_filtering(tmp_path: Path) -> None:
    reg = ModelRegistry(cache_path=tmp_path / "c.json")
    reg._models.clear()
    reg._models["beta/paid"] = Model(
        id="beta/paid",
        name="Beta Paid",
        realm="openrouter",
        base_url="",
        api_key="",
        is_free=False,
    )
    reg._models["alpha/free"] = Model(
        id="alpha/free",
        name="Alpha Free",
        realm="openrouter",
        base_url="",
        api_key="",
        is_free=True,
    )
    reg._models["zeta/free"] = Model(
        id="zeta/free",
        name="Zeta Free",
        realm="openrouter",
        base_url="",
        api_key="",
        is_free=True,
    )
    reg._models["alpha/paid"] = Model(
        id="alpha/paid",
        name="Alpha Paid",
        realm="openrouter",
        base_url="",
        api_key="",
        is_free=False,
    )
    reg._models["~internal/hidden"] = Model(
        id="~internal/hidden",
        name="Hidden",
        realm="openrouter",
        base_url="",
        api_key="",
        is_free=True,
    )

    options = reg.get_model_options()
    keys = list(options.keys())

    # Free models come first, sorted by id
    assert keys[:2] == ["alpha/free", "zeta/free"]
    # Paid models come next, sorted by id
    assert keys[2:] == ["alpha/paid", "beta/paid"]
    # Internal ~ models are excluded
    assert "~internal/hidden" not in options


def test_get_flat_model_ids(tmp_path: Path) -> None:
    reg = ModelRegistry(cache_path=tmp_path / "c.json")
    reg._models.clear()
    reg._models["a/model"] = Model(
        id="a/model", name="A Model", realm="openrouter", base_url="", api_key=""
    )
    reg._models["b/model"] = Model(
        id="b/model", name="B Model", realm="openrouter", base_url="", api_key=""
    )
    reg._models["~hidden"] = Model(
        id="~hidden", name="Hidden", realm="openrouter", base_url="", api_key=""
    )

    ids = reg.get_flat_model_ids()
    assert "a/model" in ids
    assert "b/model" in ids
    assert "~hidden" not in ids


def test_init_cache_ttl_configurable(tmp_path: Path) -> None:
    reg = ModelRegistry(cache_path=tmp_path / "c.json", cache_ttl_seconds=3600)
    assert reg.cache_ttl_seconds == 3600
    default_reg = ModelRegistry(cache_path=tmp_path / "c2.json")
    assert default_reg.cache_ttl_seconds == CACHE_TTL_SECONDS


def test_load_cache_custom_ttl_hit(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(
        json.dumps(
            {"_cached_at": time.time() - 500, "models": [{"id": "a/b", "name": "A B"}]}
        ),
        encoding="utf-8",
    )
    reg = ModelRegistry(cache_path=cache_path, cache_ttl_seconds=1000)
    assert reg.load_cache() is True
    assert reg.get("a/b") is not None


def test_load_cache_custom_ttl_expired(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(
        json.dumps(
            {"_cached_at": time.time() - 500, "models": [{"id": "a/b", "name": "A B"}]}
        ),
        encoding="utf-8",
    )
    reg = ModelRegistry(cache_path=cache_path, cache_ttl_seconds=300)
    assert reg.load_cache() is False


def test_load_cache_force_refresh_bypasses_cache(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(
        json.dumps(
            {"_cached_at": time.time(), "models": [{"id": "a/b", "name": "A B"}]}
        ),
        encoding="utf-8",
    )
    reg = ModelRegistry(cache_path=cache_path)
    assert reg.load_cache(force_refresh=True) is False


@pytest.mark.asyncio
async def test_refresh_with_valid_cache_returns_zero_without_network(
    tmp_path: Path,
) -> None:
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(
        json.dumps(
            {"_cached_at": time.time(), "models": [{"id": "a/b", "name": "A B"}]}
        ),
        encoding="utf-8",
    )
    reg = ModelRegistry(cache_path=cache_path)
    assert reg.load_cache() is True

    with patch("mvge.provider.model_registry.httpx.AsyncClient") as mock_client_cls:
        count = await reg.refresh(force_refresh=False)
        assert count == 0
        mock_client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_refresh_force_refresh_bypasses_valid_cache(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(
        json.dumps(
            {"_cached_at": time.time(), "models": [{"id": "a/b", "name": "A B"}]}
        ),
        encoding="utf-8",
    )
    reg = ModelRegistry(cache_path=cache_path)
    assert reg.load_cache() is True

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": [
            {"id": "new/model", "name": "New Model"},
        ]
    }
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch(
        "mvge.provider.model_registry.httpx.AsyncClient", return_value=mock_client
    ):
        count = await reg.refresh(force_refresh=True)

    assert count == 1
    assert reg.get("new/model") is not None


@pytest.mark.asyncio
async def test_auto_refresh_force_refresh(tmp_path: Path) -> None:
    reg = ModelRegistry(cache_path=tmp_path / "c.json")
    reg._refreshed = True

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": [
            {"id": "live/model", "name": "Live Model"},
        ]
    }
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch(
        "mvge.provider.model_registry.httpx.AsyncClient", return_value=mock_client
    ):
        count = await reg.auto_refresh(force_refresh=True)

    assert count == 1
    assert reg.get("live/model") is not None


_FREE_PRICING = {"prompt": "0", "completion": "0"}
_PAID_PRICING = {"prompt": "3", "completion": "3"}


def _mock_api_client(
    entries: list[dict[str, object]],
) -> tuple[MagicMock, object]:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"data": entries}
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)
    return mock_client, patch(
        "mvge.provider.model_registry.httpx.AsyncClient", return_value=mock_client
    )


@pytest.mark.asyncio
async def test_refresh_catalog_reports_added_removed_and_no_longer_free(
    tmp_path: Path,
) -> None:
    reg = ModelRegistry(cache_path=tmp_path / "c.json")
    reg._models.clear()
    for mid, is_free in (
        ("kept/still-free", True),
        ("retired/free", True),
        ("repriced/free", True),
        ("retired/paid", False),
    ):
        reg._models[mid] = _create_openrouter_model(mid, mid, is_free=is_free)

    client, patched = _mock_api_client(
        [
            {"id": "kept/still-free", "name": "Kept", "pricing": _FREE_PRICING},
            {"id": "repriced/free", "name": "Repriced", "pricing": _PAID_PRICING},
            {"id": "new/free", "name": "New", "pricing": _FREE_PRICING},
        ]
    )
    with patched:
        result = await reg.refresh_catalog()

    assert result.status is RefreshStatus.REFRESHED
    assert result.total == 3
    assert result.added == ("new/free",)
    assert result.removed == ("retired/paid",)
    # Both a repriced free id and a withdrawn one are free-tier losses.
    assert result.no_longer_free == ("repriced/free", "retired/free")
    assert result.cache_path == tmp_path / "c.json"
    # A stale id is reported, never pruned: the shipped catalog stays in use.
    assert reg.get("retired/free") is not None
    assert reg.get("retired/paid") is not None


@pytest.mark.asyncio
async def test_refresh_catalog_unreachable_keeps_shipped_catalog(
    tmp_path: Path,
) -> None:
    reg = ModelRegistry(cache_path=tmp_path / "c.json")
    shipped = set(reg.models)

    with patch(
        "mvge.provider.model_registry.httpx.AsyncClient",
        side_effect=Exception("boom"),
    ):
        result = await reg.refresh_catalog()

    assert result.status is RefreshStatus.UNREACHABLE
    assert result.total == 0
    assert result.added == ()
    assert result.removed == ()
    assert result.no_longer_free == ()
    assert set(reg.models) == shipped
    assert not (tmp_path / "c.json").exists()


@pytest.mark.asyncio
async def test_refresh_catalog_fresh_cache_skips_the_realm(tmp_path: Path) -> None:
    cache_path = tmp_path / "c.json"
    cache_path.write_text(
        json.dumps(
            {"_cached_at": time.time(), "models": [{"id": "a/b", "name": "A B"}]}
        ),
        encoding="utf-8",
    )
    reg = ModelRegistry(cache_path=cache_path)

    with patch("mvge.provider.model_registry.httpx.AsyncClient") as mock_client_cls:
        result = await reg.refresh_catalog()

    assert result.status is RefreshStatus.CACHE_FRESH
    mock_client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_refresh_catalog_force_reaches_the_realm_over_a_fresh_cache(
    tmp_path: Path,
) -> None:
    cache_path = tmp_path / "c.json"
    cache_path.write_text(
        json.dumps(
            {
                "_cached_at": time.time(),
                "models": [{"id": "a/b", "name": "A B", "is_free": True}],
            }
        ),
        encoding="utf-8",
    )
    reg = ModelRegistry(cache_path=cache_path)
    assert reg.load_cache() is True
    shipped = {mid: model.is_free for mid, model in reg.models.items() if mid != "a/b"}

    client, patched = _mock_api_client(
        [{"id": "a/b", "name": "A B", "pricing": _PAID_PRICING}]
    )
    with patched:
        result = await reg.refresh_catalog(force_refresh=True)

    assert result.status is RefreshStatus.REFRESHED
    assert "a/b" not in result.added
    # The cached id was free and is now priced; every shipped id the Realm does
    # not carry is reported, never pruned.
    assert result.no_longer_free == (
        "a/b",
        *sorted(mid for mid, is_free in shipped.items() if is_free),
    )
    assert result.removed == tuple(
        sorted(mid for mid, is_free in shipped.items() if not is_free)
    )
    assert reg.get(next(iter(shipped))) is not None
    client.get.assert_awaited_once()


def test_create_openrouter_model_contemplation_levels() -> None:
    # Explicit levels
    m1 = _create_openrouter_model(
        id="test/model1",
        name="Test 1",
        supported_contemplation_levels=["none", "low", "high"],
    )
    assert m1.supported_contemplation_levels == ["none", "low", "high"]
    assert m1.supports_contemplation is True

    # Auto-detected from reasoning in parameters
    m2 = _create_openrouter_model(
        id="test/model2",
        name="Test 2",
        supported_parameters=["reasoning", "temperature"],
    )
    assert m2.supports_contemplation is True
    assert "x-high" in m2.supported_contemplation_levels

    # No reasoning
    m3 = _create_openrouter_model(
        id="test/model3",
        name="Test 3",
        supported_parameters=["temperature"],
    )
    assert m3.supports_contemplation is False
    assert m3.supported_contemplation_levels == []


def test_model_registry_providers_and_models(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(
        json.dumps(
            {
                "_cached_at": time.time(),
                "models": [
                    {
                        "id": "openai/gpt-4o",
                        "name": "GPT-4o",
                        "supported_parameters": ["temperature"],
                    },
                    {
                        "id": "openai/o1",
                        "name": "o1",
                        "supported_parameters": ["reasoning"],
                        "supported_contemplation_levels": ["low", "medium", "high"],
                    },
                    {
                        "id": "anthropic/claude-3-5-sonnet",
                        "name": "Claude Sonnet",
                        "supported_parameters": ["thinking"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    reg = ModelRegistry(cache_path=cache_path)
    reg.load_cache()

    providers = reg.get_providers_for_realm("openrouter")
    assert "anthropic" in providers
    assert "openai" in providers
    assert providers == sorted(providers)

    openai_models = reg.get_models_for_provider("openai", "openrouter")
    openai_ids = [m.id for m in openai_models]
    assert "openai/gpt-4o" in openai_ids
    assert "openai/o1" in openai_ids
    assert "anthropic/claude-3-5-sonnet" not in openai_ids

    # Supported contemplation levels
    levels_o1 = reg.get_supported_contemplation_levels("openai/o1")
    assert levels_o1 == ["low", "medium", "high"]

    levels_sonnet = reg.get_supported_contemplation_levels(
        "anthropic/claude-3-5-sonnet"
    )
    assert len(levels_sonnet) > 0

    levels_gpt4o = reg.get_supported_contemplation_levels("openai/gpt-4o")
    assert levels_gpt4o == []

    levels_unknown = reg.get_supported_contemplation_levels("unknown/model")
    assert levels_unknown == []


# NOTE: Windows environment variables are case-insensitive, so
# NO_PROXY and no_proxy are the SAME slot there. These tests must never
# assume two distinct slots: each case sets exactly one name, after
# clearing both (clearing is a no-op for the second name on Windows).
@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["NO_PROXY", "no_proxy"])
async def test_refresh_sanitizes_bracketed_no_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """A catalog refresh from library use must not carry a bracketed entry
    into client construction."""
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setenv(name, "[::1],localhost")
    reg = ModelRegistry(cache_path=tmp_path / "c.json")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": [
            {
                "id": "a/b",
                "name": "A B",
                "pricing": {"prompt": "0", "completion": "0"},
            },
        ]
    }
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)
    with patch(
        "mvge.provider.model_registry.httpx.AsyncClient", return_value=mock_client
    ):
        count = await reg.refresh()
    assert count == 1
    assert os.environ[name] == "localhost"
