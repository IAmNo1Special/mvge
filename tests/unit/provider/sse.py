from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from mvge.core.abort import (
    AbortController,
    AbortError,
)
from mvge.core.channel import (
    ChannelConfig,
    Model,
    RealmResponse,
    StopReason,
)
from mvge.provider.retry import CAPACITY_RETRY_BUDGET
from mvge.provider.sse import SSEChunk, SSEStreamingRealm


class MockStreamResponse:
    def __init__(
        self,
        lines: list[bytes],
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> None:
        self._lines = lines
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body or b""
        self.aclose = AsyncMock()

    async def aiter_lines(self) -> AsyncIterator[bytes]:
        for line in self._lines:
            yield line

    def read(self) -> bytes:
        return self._body

    async def aread(self) -> bytes:
        return self._body


def _make_client(
    lines: list[bytes],
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    sink: dict[str, Any] | None = None,
) -> httpx.AsyncClient:
    class _MockStreamCM:
        def __init__(self, method: str, url: str, **kwargs: Any) -> None:
            if sink is not None:
                sink.update(kwargs)
                sink["method"] = method
                sink["url"] = url

        async def __aenter__(self) -> MockStreamResponse:
            return MockStreamResponse(
                lines=lines,
                status_code=status_code,
                headers=headers,
                body=body,
            )

        async def __aexit__(self, *args: Any) -> None:
            pass

    client = AsyncMock(spec=httpx.AsyncClient)
    client.stream = _MockStreamCM
    client.is_closed = False
    return client


def _make_sequence_client(
    entries: list[tuple[int, dict[str, str], list[bytes], bytes]],
    sink: dict[str, Any] | None = None,
) -> httpx.AsyncClient:
    class _SeqCM:
        _calls = 0

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> MockStreamResponse:
            idx = min(_SeqCM._calls, len(entries) - 1)
            _SeqCM._calls += 1
            if sink is not None:
                sink["calls"] = _SeqCM._calls
            status, headers, lines, body = entries[idx]
            return MockStreamResponse(
                lines=lines,
                status_code=status,
                headers=headers,
                body=body,
            )

        async def __aexit__(self, *args: Any) -> None:
            pass

    client = AsyncMock(spec=httpx.AsyncClient)
    client.stream = _SeqCM
    client.is_closed = False
    return client


class _SleepRecorder:
    """Stands in for the retry sleep and records what it was asked to wait."""

    def __init__(self) -> None:
        self.requested_ms: list[float] = []

    async def __call__(self, delay_ms: float, signal: Any = None) -> None:
        self.requested_ms.append(delay_ms)

    @property
    def total_ms(self) -> float:
        return sum(self.requested_ms)


#: The free-endpoint saturation this retry budget exists for, verbatim.
_OVERLOADED_CHUNK = (
    b'data: {"error":{"code":503,"message":"Upstream error from Nvidia: '
    b'Service temporarily overloaded"}}\n\n'
)


class DummySSERealm(SSEStreamingRealm):
    def _prepare_request(
        self,
        model: Model,
        invocations: list[Any],
        config: ChannelConfig,
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        url = f"{self._base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        payload: dict[str, Any] = {
            "model": model.id,
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        }
        if config.tools:
            payload["tools"] = config.tools
        return url, headers, payload

    def _parse_sse_chunk(self, chunk: dict[str, Any]) -> SSEChunk | None:
        usage = chunk.get("usage")
        choices = chunk.get("choices") or []
        if not choices:
            if usage:
                return SSEChunk(usage=usage)
            return None

        choice = choices[0]
        delta = choice.get("delta", {})
        return SSEChunk(
            content=delta.get("content"),
            contemplation=delta.get("reasoning"),
            tool_calls=delta.get("tool_calls"),
            finish_reason=choice.get("finish_reason"),
            usage=usage,
        )


def _test_model() -> Model:
    return Model(
        id="test-provider/test-model",
        name="Test Model",
        realm="test-realm",
        base_url="https://api.example.com/v1",
        api_key="test-key",
    )


async def _collect(gen: AsyncIterator[RealmResponse]) -> list[RealmResponse]:
    return [item async for item in gen]


class _ConcreteRealm(SSEStreamingRealm):
    """The smallest thing that can be constructed, for header assertions."""

    def _prepare_request(self, model, invocations, config):  # noqa: ANN001, ANN201
        raise NotImplementedError

    def _parse_sse_chunk(self, chunk):  # noqa: ANN001, ANN201
        return None


def _concrete_realm(api_key: str) -> SSEStreamingRealm:
    """Build a realm that owns a real httpx client, headers and all.

    The owned client is the point: an injected mock would accept a malformed
    header, which is how this defect survived a green suite.
    """
    return _ConcreteRealm(api_key=api_key, base_url="https://api.example.com/v1")


@pytest.mark.asyncio
async def test_stream_text_deltas_and_final_invocation() -> None:
    lines = [
        b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":" world"}}]}\n\n',
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    realm = DummySSERealm(client=_make_client(lines))
    model = _test_model()
    config = ChannelConfig(model=model)

    responses = await _collect(realm.stream(model, [], config))

    assert len(responses) == 3
    # Two partial deltas
    assert responses[0].stop_reason == "pending"
    assert responses[0].invocation is not None
    assert responses[0].invocation.content == [{"type": "text", "text": "Hello"}]

    assert responses[1].stop_reason == "pending"
    assert responses[1].invocation is not None
    assert responses[1].invocation.content == [{"type": "text", "text": " world"}]

    # Final response
    assert responses[2].stop_reason == "stop"
    assert responses[2].invocation is not None
    assert responses[2].invocation.content == [{"type": "text", "text": "Hello world"}]
    assert responses[2].invocation.stop_reason == StopReason.STOP
    assert responses[2].invocation.realm == "test-realm"


@pytest.mark.asyncio
async def test_stream_contemplation_deltas() -> None:
    lines = [
        b'data: {"choices":[{"delta":{"reasoning":"Thinking deep"}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":"Answer"}}]}\n\n',
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    realm = DummySSERealm(client=_make_client(lines))
    model = _test_model()
    config = ChannelConfig(model=model)

    responses = await _collect(realm.stream(model, [], config))

    assert len(responses) == 3
    assert responses[0].invocation is not None
    assert responses[0].invocation.content == [
        {"type": "contemplation", "text": "Thinking deep"}
    ]

    final = responses[-1]
    assert final.invocation is not None
    assert final.invocation.content == [
        {"type": "contemplation", "text": "Thinking deep"},
        {"type": "text", "text": "Answer"},
    ]


@pytest.mark.asyncio
async def test_stream_accumulates_tool_calls() -> None:
    lines = [
        (
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1",'
            b'"type":"function","function":{"name":"bash","arguments":"{\\"cmd\\":'
            b' \\"ls\\"}"}}]}}]}\n\n'
        ),
        b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    realm = DummySSERealm(client=_make_client(lines))
    model = _test_model()
    config = ChannelConfig(model=model)

    responses = await _collect(realm.stream(model, [], config))

    assert len(responses) == 1
    final = responses[0]
    assert final.stop_reason == StopReason.SPELL_USE.value
    assert final.invocation is not None
    assert final.invocation.stop_reason == StopReason.SPELL_USE
    assert final.invocation.content[0]["type"] == "spell_cast"
    spell_cast = final.invocation.content[0]["spell_cast"]
    assert spell_cast["id"] == "call_1"
    assert spell_cast["name"] == "bash"
    assert spell_cast["arguments"] == {"cmd": "ls"}


@pytest.mark.asyncio
async def test_stream_mana_usage_breakdown() -> None:
    lines = [
        b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n',
        (
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
            b'"usage":{"prompt_tokens":10,"completion_tokens":4,"total_tokens":14,'
            b'"completion_tokens_details":{"reasoning_tokens":2}}}\n\n'
        ),
        b"data: [DONE]\n\n",
    ]
    realm = DummySSERealm(client=_make_client(lines))
    model = _test_model()
    config = ChannelConfig(model=model)

    responses = await _collect(realm.stream(model, [], config))

    final = responses[-1]
    assert final.mana_used == 14
    assert final.invocation is not None
    assert final.invocation.mana_usage == {
        "input": 10.0,
        "output": 4.0,
        "total": 14.0,
        "contemplation": 2.0,
    }


@pytest.mark.asyncio
async def test_stream_usage_chunk_after_finish_reason() -> None:
    """OpenRouter sends the usage chunk AFTER the finish_reason chunk.

    The final response must still carry the mana breakdown instead of
    dropping the usage that arrives after finish_reason.
    """
    lines = [
        b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n',
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
        (
            b'data: {"usage":{"prompt_tokens":10,"completion_tokens":4,'
            b'"total_tokens":14}}\n\n'
        ),
        b"data: [DONE]\n\n",
    ]
    realm = DummySSERealm(client=_make_client(lines))
    model = _test_model()
    config = ChannelConfig(model=model)

    responses = await _collect(realm.stream(model, [], config))

    final = responses[-1]
    assert final.invocation is not None
    assert final.invocation.mana_usage == {
        "input": 10.0,
        "output": 4.0,
        "total": 14.0,
    }


@pytest.mark.asyncio
async def test_stream_requests_usage_in_stream_options() -> None:
    lines = [
        b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n',
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    sink: dict[str, Any] = {}
    realm = DummySSERealm(client=_make_client(lines, sink=sink))
    model = _test_model()
    config = ChannelConfig(model=model)

    await _collect(realm.stream(model, [], config))

    payload = sink["json"]
    assert payload["stream"] is True
    assert payload["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
async def test_stream_preserves_realm_stream_options() -> None:
    lines = [
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
        b"data: [DONE]\n\n",
    ]

    class OptionsRealm(DummySSERealm):
        def _prepare_request(
            self,
            model: Model,
            invocations: list[Any],
            config: ChannelConfig,
        ) -> tuple[str, dict[str, str], dict[str, Any]]:
            url, headers, payload = super()._prepare_request(model, invocations, config)
            payload["stream_options"] = {"include_usage": False}
            return url, headers, payload

    sink: dict[str, Any] = {}
    realm = OptionsRealm(client=_make_client(lines, sink=sink))

    await _collect(realm.stream(_test_model(), [], ChannelConfig(model=_test_model())))

    assert sink["json"]["stream_options"] == {"include_usage": False}


@pytest.mark.asyncio
async def test_stream_handles_empty_choices_and_done() -> None:
    lines = [
        b'data: {"choices":[]}\n\n',
        b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n',
        (
            b'data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":1,'
            b'"total_tokens":4}}\n\n'
        ),
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    realm = DummySSERealm(client=_make_client(lines))
    model = _test_model()
    config = ChannelConfig(model=model)

    responses = await _collect(realm.stream(model, [], config))

    assert len(responses) == 2
    final = responses[-1]
    assert final.mana_used == 4
    assert final.invocation is not None
    assert final.invocation.content == [{"type": "text", "text": "ok"}]


@pytest.mark.asyncio
async def test_stream_finish_reason_length_maps_to_length() -> None:
    lines = [
        b'data: {"choices":[{"delta":{"content":"truncated"}}]}\n\n',
        b'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    realm = DummySSERealm(client=_make_client(lines))
    model = _test_model()
    config = ChannelConfig(model=model)

    responses = await _collect(realm.stream(model, [], config))

    final = responses[-1]
    assert final.stop_reason == "length"
    assert final.invocation is not None
    assert final.invocation.stop_reason == StopReason.LENGTH


@pytest.mark.asyncio
async def test_stream_cancelled_before_request_raises_abort_error() -> None:
    realm = DummySSERealm()
    model = _test_model()
    config = ChannelConfig(model=model)

    controller = AbortController()
    controller.abort()

    with pytest.raises(AbortError, match="Operation aborted"):
        async for _ in realm.stream(model, [], config, signal=controller.signal):
            pass


@pytest.mark.asyncio
async def test_stream_cancelled_mid_stream_raises_abort_error() -> None:
    controller = AbortController()

    class AbortingStreamCM:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> MockStreamResponse:
            resp = MockStreamResponse(
                lines=[b'data: {"choices":[{"delta":{"content":"part1"}}]}\n\n']
            )

            async def aborting_iter() -> AsyncIterator[bytes]:
                yield b'data: {"choices":[{"delta":{"content":"part1"}}]}\n\n'
                controller.abort()
                yield b'data: {"choices":[{"delta":{"content":"part2"}}]}\n\n'

            resp.aiter_lines = aborting_iter  # type: ignore[method-assign]
            return resp

        async def __aexit__(self, *args: Any) -> None:
            pass

    client = AsyncMock(spec=httpx.AsyncClient)
    client.stream = AbortingStreamCM
    realm = DummySSERealm(client=client)
    model = _test_model()
    config = ChannelConfig(model=model)

    with pytest.raises(AbortError, match="Operation aborted"):
        await _collect(realm.stream(model, [], config, signal=controller.signal))


@pytest.mark.asyncio
async def test_stream_error_translation_401_auth_failed() -> None:
    client = _make_client(
        lines=[],
        status_code=401,
        body=b'{"error": {"message": "Invalid API key"}}',
    )
    realm = DummySSERealm(client=client)
    model = _test_model()
    config = ChannelConfig(model=model)

    responses = await _collect(realm.stream(model, [], config))

    assert len(responses) == 1
    assert responses[0].error_code == "auth_failed"
    assert responses[0].error_message == "Invalid API key"


@pytest.mark.asyncio
async def test_stream_error_translation_429_rate_limited() -> None:
    client = _make_client(
        lines=[],
        status_code=429,
        body=b'{"error": {"message": "Rate limit exceeded"}}',
    )
    realm = DummySSERealm(client=client)
    model = _test_model()
    config = ChannelConfig(model=model, max_retries=1)

    responses = await _collect(realm.stream(model, [], config))

    assert len(responses) == 1
    assert responses[0].error_code == "rate_limited"
    assert responses[0].error_message == "Rate limit exceeded"


@pytest.mark.asyncio
async def test_stream_retries_transient_503_then_succeeds() -> None:
    client = _make_sequence_client(
        [
            (503, {}, [], b'{"error":{"message":"Temporarily unavailable"}}'),
            (
                200,
                {},
                [
                    b'data: {"choices":[{"delta":{"content":"recovered"}}]}\n\n',
                    b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
                    b"data: [DONE]\n\n",
                ],
                b"",
            ),
        ]
    )
    realm = DummySSERealm(client=client)
    model = _test_model()
    config = ChannelConfig(model=model, max_retries=3)

    with patch("asyncio.sleep", new=AsyncMock()):
        responses = await _collect(realm.stream(model, [], config))

    assert len(responses) == 2
    assert responses[-1].stop_reason == "stop"
    assert responses[-1].invocation is not None
    assert responses[-1].invocation.content == [{"type": "text", "text": "recovered"}]


@pytest.mark.asyncio
async def test_stream_server_retry_delay_too_long() -> None:
    client = _make_client(
        lines=[],
        status_code=429,
        headers={"retry-after": "300"},
        body=b'{"error":{"message":"Slow down"}}',
    )
    realm = DummySSERealm(client=client)
    model = _test_model()
    config = ChannelConfig(model=model, max_retries=2)

    responses = await _collect(realm.stream(model, [], config))

    assert len(responses) == 1
    assert responses[0].error_message is not None
    assert "retry delay" in responses[0].error_message


@pytest.mark.asyncio
async def test_close_realm() -> None:
    client = httpx.AsyncClient()
    realm = DummySSERealm(client=client)
    await realm.close()
    assert not client.is_closed
    await client.aclose()

    owned_realm = DummySSERealm()
    await owned_realm.close()


@pytest.mark.asyncio
async def test_stream_chunk_error_surfaces_realm_response() -> None:
    lines = [
        b'data: {"id":"gen-1","choices":[],"error":'
        b'{"code":400,"message":"Invalid tool name"}}\n\n',
    ]
    realm = DummySSERealm(client=_make_client(lines))
    model = _test_model()
    config = ChannelConfig(model=model)

    responses = await _collect(realm.stream(model, [], config))

    assert len(responses) == 1
    assert responses[0].error_message == "Invalid tool name"
    assert responses[0].error_code == "400"


@pytest.mark.asyncio
async def test_stream_chunk_error_retryable_recovers() -> None:
    rec_chunk = (
        b'data: {"choices":[{"delta":{"content":"recovered"},'
        b'"finish_reason":"stop"}]}\n\n'
    )
    entries = [
        (200, {}, [_OVERLOADED_CHUNK], b""),
        (200, {}, [rec_chunk, b"data: [DONE]\n\n"], b""),
    ]
    client = _make_sequence_client(entries)
    realm = DummySSERealm(client=client)
    model = _test_model()
    config = ChannelConfig(model=model, max_retries=2)

    with patch("mvge.provider.sse.realm_request_delay_ms", return_value=0.0):
        responses = await _collect(realm.stream(model, [], config))

    assert len(responses) > 0
    assert any(
        r.invocation is not None
        and any(c.get("text") == "recovered" for c in r.invocation.content)
        for r in responses
    )


@pytest.mark.asyncio
async def test_stream_mid_stream_retryable_error_classified() -> None:
    lines = [
        b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n',
        b'data: {"error":{"code":503,"message":"Upstream idle timeout exceeded"}}\n\n',
    ]
    realm = DummySSERealm(client=_make_client(lines))
    model = _test_model()
    config = ChannelConfig(model=model, max_retries=2)

    with patch("mvge.provider.sse.realm_request_delay_ms", return_value=0.0):
        responses = await _collect(realm.stream(model, [], config))

    assert len(responses) == 2
    assert responses[0].stop_reason == "pending"
    assert responses[1].error_message == "Upstream idle timeout exceeded"
    assert responses[1].error_code == "upstream_idle_timeout"


@pytest.mark.asyncio
async def test_stream_mid_stream_non_retryable_error_keeps_code() -> None:
    lines = [
        b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n',
        b'data: {"id":"gen-1","choices":[],"error":'
        b'{"code":400,"message":"Invalid tool name"}}\n\n',
    ]
    realm = DummySSERealm(client=_make_client(lines))
    model = _test_model()
    config = ChannelConfig(model=model, max_retries=2)

    responses = await _collect(realm.stream(model, [], config))

    assert len(responses) == 2
    assert responses[1].error_message == "Invalid tool name"
    assert responses[1].error_code == "400"


@pytest.mark.asyncio
async def test_stream_chunk_error_retryable_exhausts_retries() -> None:
    entries = [
        (200, {}, [_OVERLOADED_CHUNK], b""),
        (200, {}, [_OVERLOADED_CHUNK], b""),
    ]
    client = _make_sequence_client(entries)
    realm = DummySSERealm(client=client)
    model = _test_model()
    config = ChannelConfig(model=model, max_retries=2)

    with patch("mvge.provider.sse.realm_request_delay_ms", return_value=0.0):
        responses = await _collect(realm.stream(model, [], config))

    assert len(responses) == 1
    assert "temporarily overloaded" in (responses[0].error_message or "")


@pytest.mark.asyncio
async def test_stream_capacity_saturation_outlasts_a_saturation_window() -> None:
    # A saturated free endpoint fails in windows, not at random, so the fix has
    # to spread attempts across tens of seconds rather than add a fourth try.
    sink: dict[str, Any] = {}
    realm = DummySSERealm(
        client=_make_sequence_client([(200, {}, [_OVERLOADED_CHUNK], b"")], sink)
    )
    model = _test_model()
    config = ChannelConfig(model=model)
    sleep = _SleepRecorder()

    with patch("mvge.provider.sse._sleep_ms", sleep):
        responses = await _collect(realm.stream(model, [], config))

    assert len(responses) == 1
    assert "temporarily overloaded" in (responses[0].error_message or "")
    assert sink["calls"] >= 5
    assert sleep.total_ms >= 30_000


@pytest.mark.asyncio
async def test_stream_transient_failure_keeps_the_short_budget() -> None:
    sink: dict[str, Any] = {}
    err_chunk = (
        b'data: {"error":{"code":503,"message":"Upstream error: '
        b'connection reset before headers"}}\n\n'
    )
    realm = DummySSERealm(
        client=_make_sequence_client([(200, {}, [err_chunk], b"")], sink)
    )
    model = _test_model()
    config = ChannelConfig(model=model)
    sleep = _SleepRecorder()

    with patch("mvge.provider.sse._sleep_ms", sleep):
        await _collect(realm.stream(model, [], config))

    assert sink["calls"] == 3
    assert sleep.total_ms < 5_000


@pytest.mark.asyncio
async def test_stream_capacity_retry_budget_is_bounded() -> None:
    # A genuinely dead Realm must still fail, and must not have converted a
    # four-second failure into an open-ended hang.
    sink: dict[str, Any] = {}
    realm = DummySSERealm(
        client=_make_sequence_client([(200, {}, [_OVERLOADED_CHUNK], b"")], sink)
    )
    model = _test_model()
    config = ChannelConfig(model=model)
    sleep = _SleepRecorder()

    with patch("mvge.provider.sse._sleep_ms", sleep):
        responses = await _collect(realm.stream(model, [], config))

    assert len(responses) == 1
    assert responses[0].error_message is not None
    assert sleep.total_ms <= CAPACITY_RETRY_BUDGET.max_total_wait_ms
    assert sink["calls"] <= CAPACITY_RETRY_BUDGET.max_attempts


@pytest.mark.asyncio
async def test_stream_capacity_wait_ceiling_bounds_a_large_attempt_request() -> None:
    # Even a caller that asks for far more retries than the ceiling can spend
    # stops at the ceiling instead of stalling on the backoff schedule.
    sink: dict[str, Any] = {}
    realm = DummySSERealm(
        client=_make_sequence_client([(200, {}, [_OVERLOADED_CHUNK], b"")], sink)
    )
    model = _test_model()
    config = ChannelConfig(model=model, max_retries=200)
    sleep = _SleepRecorder()

    with patch("mvge.provider.sse._sleep_ms", sleep):
        responses = await _collect(realm.stream(model, [], config))

    assert len(responses) == 1
    assert sleep.total_ms <= CAPACITY_RETRY_BUDGET.max_total_wait_ms
    assert sink["calls"] < 200


@pytest.mark.asyncio
async def test_stream_capacity_error_on_a_non_200_gets_the_long_budget() -> None:
    sink: dict[str, Any] = {}
    entries = [
        (
            503,
            {},
            [],
            b'{"error":{"message":"No capacity available for this model"}}',
        )
    ]
    realm = DummySSERealm(client=_make_sequence_client(entries, sink))
    model = _test_model()
    config = ChannelConfig(model=model)
    sleep = _SleepRecorder()

    with patch("mvge.provider.sse._sleep_ms", sleep):
        responses = await _collect(realm.stream(model, [], config))

    assert len(responses) == 1
    assert "No capacity available" in (responses[0].error_message or "")
    assert sleep.total_ms >= 30_000


@pytest.mark.asyncio
async def test_stream_quota_error_is_not_retried_under_a_capacity_budget() -> None:
    # Billing and quota are deterministic. The capacity patterns must never
    # outrank them, however many attempts the caller allowed.
    sink: dict[str, Any] = {}
    err_chunk = (
        b'data: {"error":{"code":429,"message":"Upstream overloaded: '
        b'insufficient_quota for this key"}}\n\n'
    )
    realm = DummySSERealm(
        client=_make_sequence_client([(200, {}, [err_chunk], b"")], sink)
    )
    model = _test_model()
    config = ChannelConfig(model=model)
    sleep = _SleepRecorder()

    with patch("mvge.provider.sse._sleep_ms", sleep):
        responses = await _collect(realm.stream(model, [], config))

    assert sink["calls"] == 1
    assert sleep.requested_ms == []
    assert "insufficient_quota" in (responses[0].error_message or "")


@pytest.mark.asyncio
async def test_stream_capacity_error_after_content_is_not_replayed() -> None:
    # The capacity budget must not weaken the rule that stops a retry from
    # duplicating transcript text: once anything has streamed, no replay.
    lines = [
        b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n',
        _OVERLOADED_CHUNK,
    ]
    realm = DummySSERealm(client=_make_client(lines))
    model = _test_model()
    config = ChannelConfig(model=model)
    sleep = _SleepRecorder()

    with patch("mvge.provider.sse._sleep_ms", sleep):
        responses = await _collect(realm.stream(model, [], config))

    assert len(responses) == 2
    assert responses[0].stop_reason == "pending"
    assert responses[1].error_message is not None
    assert "temporarily overloaded" in responses[1].error_message
    assert sleep.requested_ms == []


@pytest.mark.asyncio
async def test_stream_capacity_retry_recovers_once_the_window_clears() -> None:
    ok_chunk = b'data: {"choices":[{"delta":{"content":"recovered"}}]}\n\n'
    entries = [
        (200, {}, [_OVERLOADED_CHUNK], b""),
        (
            200,
            {},
            [ok_chunk, b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'],
            b"",
        ),
    ]
    realm = DummySSERealm(client=_make_sequence_client(entries))
    model = _test_model()
    config = ChannelConfig(model=model)

    with patch("mvge.provider.sse._sleep_ms", _SleepRecorder()):
        responses = await _collect(realm.stream(model, [], config))

    assert any(
        r.invocation is not None
        and any(c.get("text") == "recovered" for c in r.invocation.content)
        for r in responses
    )


# 2100-01-01T00:00:00Z, far enough out that no retry budget can wait it out.
_FAR_FUTURE_RESET_MS = "4102444800000"

#: The exact 429 a drained OpenRouter free-tier daily allowance produces.
_SPENT_DAILY_ALLOWANCE = (
    429,
    {
        "x-ratelimit-limit": "50",
        "x-ratelimit-remaining": "0",
        "x-ratelimit-reset": _FAR_FUTURE_RESET_MS,
    },
    [],
    b'{"error":{"message":"Rate limit exceeded: free-models-per-day. Add 10 '
    b'credits to unlock 1000 free model requests per day"}}',
)


@pytest.mark.asyncio
async def test_stream_spent_daily_allowance_is_not_retried() -> None:
    # The failure this change exists for. A Summoner waiting 3s to be told to
    # come back in 14 hours is the bug; one request and no waits is the fix.
    sink: dict[str, Any] = {}
    realm = DummySSERealm(client=_make_sequence_client([_SPENT_DAILY_ALLOWANCE], sink))
    model = _test_model()
    config = ChannelConfig(model=model)
    sleep = _SleepRecorder()

    with patch("mvge.provider.sse._sleep_ms", sleep):
        responses = await _collect(realm.stream(model, [], config))

    assert sink["calls"] == 1
    assert sleep.requested_ms == []
    assert len(responses) == 1
    assert "free-models-per-day" in (responses[0].error_message or "")
    assert responses[0].error_code == "rate_limited"


@pytest.mark.asyncio
async def test_stream_spent_daily_allowance_still_reports_its_reset_time() -> None:
    # Not retrying must not cost the Summoner the reset time. The loop turns
    # this response into a RateLimitError and the renderer names the window
    # from these fields, so they have to survive the no-retry path intact.
    realm = DummySSERealm(client=_make_sequence_client([_SPENT_DAILY_ALLOWANCE]))
    model = _test_model()
    config = ChannelConfig(model=model)

    with patch("mvge.provider.sse._sleep_ms", _SleepRecorder()):
        responses = await _collect(realm.stream(model, [], config))

    assert responses[0].reset_at == 4102444800.0
    assert responses[0].quota_limit == 50
    assert responses[0].quota_remaining == 0


@pytest.mark.asyncio
async def test_stream_short_rate_limit_window_is_still_retried() -> None:
    # The structured rule keys off how far out the window is, not off the fact
    # that one was reported. A per-minute window reopens well inside the
    # budget, so waiting it out is still the right move.
    sink: dict[str, Any] = {}
    soon_ms = str(int((time.time() + 2) * 1000))
    entries = [
        (
            429,
            {"x-ratelimit-reset": soon_ms},
            [],
            b'{"error":{"message":"Rate limit exceeded"}}',
        )
    ]
    realm = DummySSERealm(client=_make_sequence_client(entries, sink))
    model = _test_model()
    config = ChannelConfig(model=model)
    sleep = _SleepRecorder()

    with patch("mvge.provider.sse._sleep_ms", sleep):
        await _collect(realm.stream(model, [], config))

    assert sink["calls"] == 3
    assert len(sleep.requested_ms) == 2


def test_error_from_response_parses_openrouter_rate_limit_metadata() -> None:
    from mvge.provider.sse import _error_from_response

    class FakeResponse:
        status_code = 429
        headers = {
            "x-ratelimit-limit": "50",
            "x-ratelimit-remaining": "0",
            "x-ratelimit-reset": "1788566400000",
            "retry-after": "60",
        }

        def read(self) -> bytes:
            body = {
                "error": {
                    "code": 429,
                    "message": "Rate limit exceeded: free-models-per-day.",
                    "metadata": {
                        "limit_source": "openrouter_free_tier_daily",
                        "remedy_hint": "Wait for daily reset or purchase credits.",
                    },
                }
            }
            return json.dumps(body).encode("utf-8")

    err = _error_from_response(FakeResponse())
    assert err.message == "Rate limit exceeded: free-models-per-day."
    assert err.error_code == "rate_limited"
    assert err.limit_source == "openrouter_free_tier_daily"
    assert err.remedy_hint == "Wait for daily reset or purchase credits."
    assert err.quota_limit == 50
    assert err.quota_remaining == 0
    assert err.reset_at == 1788566400.0
    assert err.retry_after == 60.0

    # Unpack as tuple
    msg, code = err
    assert msg == "Rate limit exceeded: free-models-per-day."
    assert code == "rate_limited"


class _HeaderOnlyResponse:
    """A 429 the way OpenRouter sends it: the limit is in headers only."""

    status_code = 429

    def __init__(
        self, headers: dict[str, str], message: str = "Provider returned error"
    ):
        self.headers = headers
        self._message = message

    def read(self) -> bytes:
        body = {"error": {"code": 429, "message": self._message}}
        return json.dumps(body).encode("utf-8")


def test_error_from_response_names_free_tier_from_quota_headers() -> None:
    from mvge.provider.sse import _error_from_response

    err = _error_from_response(
        _HeaderOnlyResponse(
            {
                "x-ratelimit-limit": "50",
                "x-ratelimit-remaining": "0",
                "x-ratelimit-reset": "1791244800000",
                "retry-after": "60",
            }
        )
    )

    assert err.error_code == "rate_limited"
    assert err.limit_source == "openrouter_free_tier_daily"
    assert err.remedy_hint
    assert err.reset_at == 1791244800.0


def test_error_from_response_names_unreported_quota_upstream() -> None:
    from mvge.provider.sse import _error_from_response

    drained_large_window = _error_from_response(
        _HeaderOnlyResponse({"x-ratelimit-limit": "1000", "x-ratelimit-remaining": "0"})
    )
    assert drained_large_window.limit_source == "upstream_rate_limit"
    assert drained_large_window.remedy_hint is None

    quota_left = _error_from_response(
        _HeaderOnlyResponse({"x-ratelimit-limit": "50", "x-ratelimit-remaining": "7"})
    )
    assert quota_left.limit_source == "upstream_rate_limit"


def test_error_from_response_leaves_source_unnamed_without_quota() -> None:
    from mvge.provider.sse import _error_from_response

    err = _error_from_response(_HeaderOnlyResponse({"retry-after": "60"}))

    assert err.limit_source is None
    assert err.remedy_hint is None


# Captured 2026-10-05 from `openrouter/free` with the account's daily free
# allowance spent. The router walked free endpoints, each refusing on the
# account-wide daily cap, then fell through to a BYOK provider whose credits are
# separately depleted and answered 402. `X-RateLimit-*` is absent from the 402
# and from every `previous_errors` entry, so nothing here is readable as a
# quota -- only the shape is. `raw` and the account `user_id` are omitted
# because the parser reads neither and an account identifier should not be
# committed.
_FREE_MODEL_DAILY_REFUSAL: dict[str, Any] = {
    "code": 429,
    "message": (
        "Rate limit exceeded: free-models-per-day. Add 10 credits to unlock "
        "1000 free model requests per day"
    ),
}

ROUTER_SPENT_ALLOWANCE_BODY_402: dict[str, Any] = {
    "error": {
        "message": "Provider returned error",
        "code": 402,
        "metadata": {
            "provider_name": "Google AI Studio",
            "is_byok": True,
            "provider_error_code": "402",
            "previous_errors": [
                _FREE_MODEL_DAILY_REFUSAL,
                _FREE_MODEL_DAILY_REFUSAL,
                _FREE_MODEL_DAILY_REFUSAL,
                _FREE_MODEL_DAILY_REFUSAL,
                {
                    "code": 402,
                    "message": "Provider returned error",
                    "provider_name": "Google AI Studio",
                },
                _FREE_MODEL_DAILY_REFUSAL,
                _FREE_MODEL_DAILY_REFUSAL,
                _FREE_MODEL_DAILY_REFUSAL,
                _FREE_MODEL_DAILY_REFUSAL,
            ],
        },
    },
}


class _RouterSpentAllowance402:
    """The router's 402, with the real header set that carries no quota."""

    status_code = 402

    def __init__(self, body: dict[str, Any]):
        self.headers = httpx.Headers(
            {
                "date": "Mon, 05 Oct 2026 10:39:26 GMT",
                "content-type": "application/json",
                "x-generation-id": "gen-1791196765-6HfulTo41tSFW22c6Fcr",
                "cf-ray": "a45bcd6b2b9138c8-IAD",
            }
        )
        self._body = body

    def read(self) -> bytes:
        return json.dumps(self._body).encode("utf-8")


def test_error_from_response_names_spent_allowance_behind_router_402() -> None:
    from mvge.provider.sse import _error_from_response

    err = _error_from_response(
        _RouterSpentAllowance402(ROUTER_SPENT_ALLOWANCE_BODY_402)
    )

    assert err.limit_source == "openrouter_free_tier_daily"
    assert err.remedy_hint
    # No window was reported, so none is claimed. A reset time invented here
    # would be a lie the Summoner waits out.
    assert err.reset_at is None
    assert err.quota_limit is None
    assert err.quota_remaining is None
    assert err.retry_after is None
    # The turn only reaches the formatter through a rate-limit error; a 402
    # with no error code surfaces as an opaque `Provider returned error`.
    assert err.error_code == "rate_limited"


def test_error_from_response_leaves_402_alone_without_previous_errors() -> None:
    """A BYOK billing 402 is still a billing 402."""
    from mvge.provider.sse import _error_from_response

    err = _error_from_response(
        _RouterSpentAllowance402(
            {
                "error": {
                    "message": "Provider returned error",
                    "code": 402,
                    "metadata": {"is_byok": True, "provider_name": "Google AI Studio"},
                }
            }
        )
    )

    assert err.limit_source is None
    assert err.remedy_hint is None
    assert err.error_code is None


def test_error_from_response_keeps_declared_source_over_classified_one() -> None:
    from mvge.provider.sse import _error_from_response

    class DeclaredResponse(_HeaderOnlyResponse):
        def read(self) -> bytes:
            body = {
                "error": {
                    "code": 429,
                    "message": "Rate limit exceeded: free-models-per-day.",
                    "metadata": {
                        "limit_source": "upstream_rate_limit",
                        "remedy_hint": "Realm knows better.",
                    },
                }
            }
            return json.dumps(body).encode("utf-8")

    err = _error_from_response(
        DeclaredResponse({"x-ratelimit-limit": "50", "x-ratelimit-remaining": "0"})
    )

    assert err.limit_source == "upstream_rate_limit"
    assert err.remedy_hint == "Realm knows better."


@pytest.mark.asyncio
async def test_stream_exhausts_retries_surfaces_diagnostic_fields() -> None:
    err_body = json.dumps(
        {
            "error": {
                "code": 429,
                "message": "Rate limit exceeded: free-models-per-day.",
                "metadata": {
                    "limit_source": "openrouter_free_tier_daily",
                    "remedy_hint": "Add credits",
                },
            }
        }
    ).encode("utf-8")

    entries = [
        (429, {"x-ratelimit-limit": "50", "x-ratelimit-remaining": "0"}, [], err_body),
    ]
    client = _make_sequence_client(entries)
    realm = DummySSERealm(client=client)
    model = _test_model()
    config = ChannelConfig(model=model, max_retries=1)

    responses = await _collect(realm.stream(model, [], config))

    assert len(responses) == 1
    resp = responses[0]
    assert resp.error_code == "rate_limited"
    assert resp.limit_source == "openrouter_free_tier_daily"
    assert resp.remedy_hint == "Add credits"
    assert resp.quota_limit == 50
    assert resp.quota_remaining == 0


@pytest.mark.asyncio
async def test_error_from_response_async_uses_aread() -> None:
    from mvge.provider.sse import _error_from_response_async

    class AsyncOnlyResponse:
        status_code = 400
        headers: dict[str, str] = {}

        def read(self) -> bytes:
            raise RuntimeError("sync read() in async streaming context")

        async def aread(self) -> bytes:
            return b'{"error": {"message": "context length exceeded"}}'

    err = await _error_from_response_async(AsyncOnlyResponse())
    assert err.message == "context length exceeded"


@pytest.mark.asyncio
async def test_error_from_response_async_falls_back_to_read() -> None:
    from mvge.provider.sse import _error_from_response_async

    class SyncOnlyResponse:
        status_code = 400
        headers: dict[str, str] = {}

        def read(self) -> bytes:
            return b'{"error": {"message": "bad request"}}'

    err = await _error_from_response_async(SyncOnlyResponse())
    assert err.message == "bad request"


@pytest.mark.asyncio
async def test_error_from_response_async_unreadable_body() -> None:
    from mvge.provider.sse import _error_from_response_async

    class BrokenResponse:
        status_code = 400
        headers: dict[str, str] = {}

        def read(self) -> bytes:
            raise RuntimeError("nope")

        async def aread(self) -> bytes:
            raise RuntimeError("nope")

    err = await _error_from_response_async(BrokenResponse())
    assert err.message == "HTTP 400"


@pytest.mark.asyncio
async def test_stream_400_surfaces_provider_message() -> None:
    client = _make_client(
        lines=[],
        status_code=400,
        body=b'{"error": {"message": "prompt is too long"}}',
    )
    realm = DummySSERealm(client=client)
    model = _test_model()
    config = ChannelConfig(model=model, max_retries=1)

    responses = await _collect(realm.stream(model, [], config))

    assert len(responses) == 1
    assert responses[0].error_message == "prompt is too long"


def test_error_from_response_string_error_body() -> None:
    from mvge.provider.sse import _error_from_response

    class StringErrorResponse:
        status_code = 400
        headers: dict[str, str] = {}

        def read(self) -> bytes:
            return b'{"error": "boom"}'

    err = _error_from_response(StringErrorResponse())
    assert err.message == "boom"


@pytest.mark.asyncio
async def test_error_from_response_async_log_level_by_attempt(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify _error_from_response_async log level by attempt number."""
    import logging

    from mvge.provider.sse import _error_from_response_async

    class AsyncErrorResponse:
        status_code = 429
        headers: dict[str, str] = {}

        def read(self) -> bytes:
            raise RuntimeError("sync read not available")

        async def aread(self) -> bytes:
            return b'{"error": {"message": "Rate limit exceeded"}}'

    # Test intermediate attempt (not final) -> DEBUG
    with caplog.at_level(logging.DEBUG, logger="mvge.provider.sse"):
        await _error_from_response_async(
            AsyncErrorResponse(), attempt=0, max_attempts=3
        )

    debug_logs = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert len(debug_logs) == 1
    assert "attempt 1/3" in debug_logs[0].message
    assert "Rate limit exceeded" in debug_logs[0].message

    # Clear caplog
    caplog.clear()

    # Test final attempt -> WARNING
    with caplog.at_level(logging.WARNING, logger="mvge.provider.sse"):
        await _error_from_response_async(
            AsyncErrorResponse(), attempt=2, max_attempts=3
        )

    warning_logs = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_logs) == 1
    assert "attempt 3/3" in warning_logs[0].message
    assert "Rate limit exceeded" in warning_logs[0].message


@pytest.mark.asyncio
async def test_a_keyless_realm_sends_no_authorization_header() -> None:
    """`Bearer ` with nothing after it is an illegal header, and httpx refuses it.

    Zen's free tier needs no key, so an empty credential is the normal case for
    a Realm serving it -- not a misconfiguration. The client default headers
    used to send the empty bearer anyway, which raised LocalProtocolError before
    the request left the process. A mock client accepts the malformed value, so
    this is only observable against a real transport or an httpx-built client;
    the assertion is on the headers the client was constructed with, which is
    the fact that caused it.
    """
    realm = _concrete_realm(api_key="")
    try:
        assert "Authorization" not in realm._client.headers
        assert realm._client.headers["Content-Type"] == "application/json"
    finally:
        await realm.close()


@pytest.mark.asyncio
async def test_a_keyed_realm_still_sends_its_bearer_header() -> None:
    realm = _concrete_realm(api_key="secret-key")
    try:
        assert realm._client.headers["Authorization"] == "Bearer secret-key"
    finally:
        await realm.close()
