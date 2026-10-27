from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from mock_realm import MockStreamingRealm

from mvge.core.channel import Model
from mvge.provider.registry import RealmRegistry


@pytest.mark.asyncio
async def test_realm_registry_shared_client() -> None:
    """Verify RealmRegistry shares an httpx.AsyncClient instance across realms."""
    registry = RealmRegistry()
    client1 = registry.get_shared_client()
    client2 = registry.get_shared_client()

    assert client1 is client2
    assert not client1.is_closed

    await registry.close()
    assert client1.is_closed


@pytest.mark.asyncio
async def test_mock_streaming_realm_shared_client_lifecycle() -> None:
    """Verify MockStreamingRealm does not close unowned shared client."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False

    realm = MockStreamingRealm(
        api_key="test_key",
        base_url="https://openrouter.ai/api/v1",
        client=mock_client,
    )

    await realm.close()
    # Unowned client should NOT have aclose() called by realm.close()
    mock_client.aclose.assert_not_called()


@pytest.mark.asyncio
async def test_realm_registry_create_realm_reuses_client() -> None:
    """Verify create_realm passes shared client to MockStreamingRealm."""
    registry = RealmRegistry()
    registry.register_realm_factory(
        "openrouter",
        lambda api_key="", base_url="", **kw: MockStreamingRealm(
            api_key=api_key,
            base_url=base_url,
            client=registry.get_shared_client(),
        ),
    )
    model = Model(
        id="nvidia/nemotron-3-ultra-550b-a55b:free",
        name="Nemotron",
        realm="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test_key",
    )

    realm = registry.create_realm(model, api_key="test_key")
    assert isinstance(realm, MockStreamingRealm)
    assert realm._client is registry.get_shared_client()

    await registry.close()


@pytest.mark.asyncio
async def test_mock_streaming_realm_request_url_and_headers() -> None:
    """Verify stream and complete send absolute HTTPS URLs and auth headers."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "ok"}}],
        "usage": {},
    }
    mock_client.post.return_value = mock_resp

    realm = MockStreamingRealm(
        api_key="test_key",
        base_url="https://openrouter.ai/api/v1",
        client=mock_client,
    )
    model = Model(
        id="nvidia/nemotron-3-ultra-550b-a55b:free",
        name="Nemotron",
        realm="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test_key",
    )
    from mvge.core.channel import ChannelConfig

    config = ChannelConfig(model=model)

    # Test complete() passes full URL and headers
    await realm.complete(model, [{"role": "user", "content": "hi"}], config)
    assert mock_client.post.called
    call_args, call_kwargs = mock_client.post.call_args
    assert call_args[0] == "https://openrouter.ai/api/v1/chat/completions"
    assert call_kwargs.get("headers", {}).get("Authorization") == "Bearer test_key"

    # Test stream() passes full URL and headers
    mock_stream_resp = MagicMock()
    mock_stream_resp.status_code = 400
    mock_stream_resp.headers = {}
    mock_stream_cm = AsyncMock()
    mock_stream_cm.__aenter__.return_value = mock_stream_resp
    mock_stream_cm.__aexit__.return_value = None
    mock_client.stream.return_value = mock_stream_cm

    _ = [r async for r in realm.stream(model, [], config)]
    assert mock_client.stream.called
    stream_call_args, stream_call_kwargs = mock_client.stream.call_args
    assert stream_call_args[1] == "https://openrouter.ai/api/v1/chat/completions"
    assert (
        stream_call_kwargs.get("headers", {}).get("Authorization") == "Bearer test_key"
    )
