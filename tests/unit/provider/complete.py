from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from mock_realm import MockStreamingRealm

from mvge.core.channel import (
    ChannelConfig,
    Model,
)


def _model() -> Model:
    return Model(
        id="openrouter/test-model",
        name="Test Model",
        realm="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
    )


class _Response:
    """Minimal stand-in for an httpx non-streaming response."""

    def __init__(
        self,
        status_code: int = 200,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {"content-type": "application/json"}

    def json(self) -> dict[str, Any]:
        return self._payload


def _completion(text: str = "A summary.", usage: dict[str, int] | None = None) -> dict:
    return {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "model": "openrouter/test-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": text},
            }
        ],
        "usage": usage
        or {
            "prompt_tokens": 25,
            "completion_tokens": 10,
            "total_tokens": 35,
        },
    }


class TestRealmProtocol:
    def test_realm_declares_complete(self) -> None:
        from mvge.provider.base import Realm

        assert hasattr(Realm, "complete")

    @pytest.mark.asyncio
    async def test_base_complete_raises_not_implemented(self) -> None:
        from mvge.provider.base import Realm

        with pytest.raises(NotImplementedError):
            await Realm().complete(_model(), [], ChannelConfig(model=_model()))


class TestOpenRouterComplete:
    @pytest.mark.asyncio
    async def test_returns_message_text(self) -> None:
        realm = MockStreamingRealm(api_key="test-key")
        realm._client.post = AsyncMock(return_value=_Response(payload=_completion()))

        result = await realm.complete(
            _model(),
            [{"role": "user", "content": "Summarize"}],
            ChannelConfig(model=_model()),
        )

        assert result.invocation is not None
        assert result.invocation.content[0]["text"] == "A summary."

    @pytest.mark.asyncio
    async def test_sends_stream_false(self) -> None:
        realm = MockStreamingRealm(api_key="test-key")
        post = AsyncMock(return_value=_Response(payload=_completion()))
        realm._client.post = post

        await realm.complete(
            _model(),
            [{"role": "user", "content": "Summarize"}],
            ChannelConfig(model=_model()),
        )

        payload = post.await_args.kwargs["json"]
        assert payload["stream"] is False

    @pytest.mark.asyncio
    async def test_reports_mana_usage(self) -> None:
        realm = MockStreamingRealm(api_key="test-key")
        realm._client.post = AsyncMock(return_value=_Response(payload=_completion()))

        result = await realm.complete(
            _model(),
            [{"role": "user", "content": "Summarize"}],
            ChannelConfig(model=_model()),
        )

        assert result.mana_used == 35
        assert result.invocation is not None
        assert result.invocation.mana_usage["input"] == 25
        assert result.invocation.mana_usage["output"] == 10

    @pytest.mark.asyncio
    async def test_does_not_send_tools(self) -> None:
        # Summarization is a standalone call; Spells must not leak into it.
        realm = MockStreamingRealm(api_key="test-key")
        post = AsyncMock(return_value=_Response(payload=_completion()))
        realm._client.post = post

        config = ChannelConfig(
            model=_model(),
            tools=[{"type": "function", "function": {"name": "bash"}}],
        )
        await realm.complete(_model(), [{"role": "user", "content": "x"}], config)

        assert "tools" not in post.await_args.kwargs["json"]

    @pytest.mark.asyncio
    async def test_error_status_returns_error_response(self) -> None:
        realm = MockStreamingRealm(api_key="test-key")
        realm._client.post = AsyncMock(
            return_value=_Response(
                status_code=401,
                payload={"error": {"message": "Missing Authentication header"}},
            )
        )

        result = await realm.complete(
            _model(),
            [{"role": "user", "content": "x"}],
            ChannelConfig(model=_model()),
        )

        assert result.error_message is not None
        assert result.error_code == "auth_failed"
        assert result.invocation is None

    @pytest.mark.asyncio
    async def test_rate_limit_maps_to_retryable_code(self) -> None:
        realm = MockStreamingRealm(api_key="test-key")
        realm._client.post = AsyncMock(
            return_value=_Response(
                status_code=429,
                payload={"error": {"message": "Rate limit exceeded"}},
            )
        )

        result = await realm.complete(
            _model(),
            [{"role": "user", "content": "x"}],
            ChannelConfig(model=_model()),
        )

        assert result.error_code == "rate_limited"

    @pytest.mark.asyncio
    async def test_empty_choices_yields_error(self) -> None:
        realm = MockStreamingRealm(api_key="test-key")
        realm._client.post = AsyncMock(return_value=_Response(payload={"choices": []}))

        result = await realm.complete(
            _model(),
            [{"role": "user", "content": "x"}],
            ChannelConfig(model=_model()),
        )

        assert result.error_message is not None

    @pytest.mark.asyncio
    async def test_is_awaitable_not_generator(self) -> None:
        realm = MockStreamingRealm(api_key="test-key")
        realm._client.post = AsyncMock(return_value=_Response(payload=_completion()))

        coro = realm.complete(
            _model(),
            [{"role": "user", "content": "x"}],
            ChannelConfig(model=_model()),
        )
        assert asyncio.iscoroutine(coro)
        await coro

    @pytest.mark.asyncio
    async def test_prepare_request_injects_system_prompt(self) -> None:
        realm = MockStreamingRealm(api_key="test-key")
        model = _model()
        config = ChannelConfig(model=model, system_prompt="You are a Mvge.")

        class DummyUserMsg:
            role = "user"
            content = "hello"

        _url, _headers, payload = realm._prepare_request(
            model, [DummyUserMsg()], config
        )
        assert len(payload["messages"]) == 2
        assert payload["messages"][0] == {
            "role": "system",
            "content": "You are a Mvge.",
        }
        assert payload["messages"][1] == {"role": "user", "content": "hello"}

    @pytest.mark.asyncio
    async def test_prepare_request_does_not_duplicate_existing_system_message(
        self,
    ) -> None:
        realm = MockStreamingRealm(api_key="test-key")
        model = _model()
        config = ChannelConfig(model=model, system_prompt="You are a Mvge.")

        class DummySysMsg:
            role = "system"
            content = "Existing system."

        class DummyUserMsg:
            role = "user"
            content = "hello"

        _url, _headers, payload = realm._prepare_request(
            model, [DummySysMsg(), DummyUserMsg()], config
        )
        assert len(payload["messages"]) == 2
        assert payload["messages"][0] == {
            "role": "system",
            "content": "Existing system.",
        }
        assert payload["messages"][1] == {"role": "user", "content": "hello"}

    @pytest.mark.asyncio
    async def test_complete_injects_system_prompt_when_missing(self) -> None:
        realm = MockStreamingRealm(api_key="test-key")
        realm._client.post = AsyncMock(return_value=_Response(payload=_completion()))
        config = ChannelConfig(model=_model(), system_prompt="Base sys prompt.")

        await realm.complete(
            _model(),
            [{"role": "user", "content": "hello"}],
            config,
        )

        sent_payload = realm._client.post.call_args.kwargs["json"]
        assert sent_payload["messages"][0] == {
            "role": "system",
            "content": "Base sys prompt.",
        }
        assert sent_payload["messages"][1] == {"role": "user", "content": "hello"}

    @pytest.mark.asyncio
    async def test_fallback_realm_is_router_and_stream_error_after_emit_and_close(
        self,
    ) -> None:
        from mvge.provider.base import FallbackRealm

        primary = MockStreamingRealm(api_key="test-key")
        fallback = MockStreamingRealm(api_key="test-key")
        primary._is_router = True
        fb_realm = FallbackRealm(primary, fallback)
        assert fb_realm.is_router is True

        async def failing_stream(*args: Any, **kwargs: Any) -> Any:
            yield "chunk1"
            raise RuntimeError("stream broke midway")

        primary.stream = failing_stream  # type: ignore[assignment]
        with pytest.raises(RuntimeError, match="stream broke midway"):
            async for _ in fb_realm.stream(_model(), [], ChannelConfig(model=_model())):
                pass

        await fb_realm.close()
