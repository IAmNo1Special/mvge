import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from mock_realm import MockStreamingRealm

from mvge.core.channel import (
    ChannelConfig,
    Model,
)


async def _async_iter(items):
    for item in items:
        yield item


async def collect_responses(iterator):
    return [item async for item in iterator]


def _mock_response(status_code, headers, body):
    import json as _json

    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers
    resp.json.return_value = body
    resp.read.return_value = _json.dumps(body).encode("utf-8")
    return resp


def test_error_from_response_strips_leading_trailing_newlines() -> None:
    from mvge.provider.sse import _error_from_response

    resp = _mock_response(
        400,
        {"content-type": "application/json"},
        {"error": {"message": "\nBad request\n"}},
    )
    message, code = _error_from_response(resp)
    assert "\n" not in message
    assert message == "Bad request"
    assert code is None


def test_error_from_response_normalizes_crlf() -> None:
    from mvge.provider.sse import _error_from_response

    resp = _mock_response(
        400,
        {"content-type": "application/json"},
        {"error": {"message": "line1\r\nline2"}},
    )
    message, code = _error_from_response(resp)
    assert "\r\n" not in message
    assert "\r" not in message
    assert message == "line1\nline2"


def test_error_from_response_preserves_internal_newlines() -> None:
    from mvge.provider.sse import _error_from_response

    resp = _mock_response(
        400,
        {"content-type": "application/json"},
        {"error": {"message": "first\nsecond"}},
    )
    message, code = _error_from_response(resp)
    assert message == "first\nsecond"


def test_openrouter_realm_stream_builds_correct_messages() -> None:
    from mvge.core.invocations import SummonerRequest

    realm = MockStreamingRealm(api_key="test-key")
    model = Model(
        id="openrouter/test-model",
        name="Test Model",
        realm="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
    )
    config = ChannelConfig(model=model)

    invocations: list[SummonerRequest] = [
        SummonerRequest(role="user", content="Hello"),
    ]

    realm._client.stream = _make_stream_factory(  # type: ignore[method-assign]
        [
            b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n',
            (
                b'data: {"choices":[{"delta":{"content":" world"},'
                b'"finish_reason":"stop"}]}\n\n'
            ),
            b"data: [DONE]\n\n",
        ]
    )

    responses = asyncio.run(collect_responses(realm.stream(model, invocations, config)))

    assert len(responses) >= 1
    assert responses[-1].stop_reason == "stop"


def test_openrouter_realm_stream_handles_empty_choices_chunk() -> None:
    from mvge.core.invocations import SummonerRequest

    realm = MockStreamingRealm(api_key="test-key")
    model = Model(
        id="openrouter/test-model",
        name="Test Model",
        realm="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
    )
    config = ChannelConfig(model=model)

    invocations: list[SummonerRequest] = [
        SummonerRequest(role="user", content="Hello"),
    ]

    realm._client.stream = _make_stream_factory(  # type: ignore[method-assign]
        [
            b'data: {"choices":[]}\n\n',
            b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n',
            (
                b'data: {"choices":[],"usage":{"prompt_tokens":5,'
                b'"completion_tokens":2,"total_tokens":7}}\n\n'
            ),
            (
                b'data: {"choices":[{"delta":{"content":" world"},'
                b'"finish_reason":"stop"}]}\n\n'
            ),
            b"data: [DONE]\n\n",
        ]
    )

    responses = asyncio.run(collect_responses(realm.stream(model, invocations, config)))

    assert len(responses) >= 1
    assert responses[-1].stop_reason == "stop"
    assert responses[-1].mana_used == 7


def test_openrouter_realm_stream_handles_tool_calls() -> None:
    from mvge.core.channel import MvgeResponse

    realm = MockStreamingRealm(api_key="test-key")
    model = Model(
        id="openrouter/test-model",
        name="Test Model",
        realm="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
    )
    config = ChannelConfig(model=model)

    invocations: list[MvgeResponse] = [
        MvgeResponse(
            role="assistant",
            content=[
                {
                    "type": "tool_use",
                    "id": "call_123",
                    "name": "bash",
                    "input": {"command": "echo hello"},
                }
            ],
        )
    ]

    mock_response = MagicMock()
    mock_response.aiter_lines.return_value = _async_iter(
        [
            (
                b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_123",'
                b'"type":"function","function":{"name":"bash","arguments":"'
                b'{\\"command\\":\\"echo hello\\"}"}}]}}]}\n\n'
            ),
            b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n',
            b"data: [DONE]\n\n",
        ]
    )
    mock_response.raise_for_status = MagicMock()
    mock_response.headers = {}
    mock_response.text = ""
    mock_response.status_code = 200

    realm._client.post = AsyncMock(return_value=mock_response)

    responses = asyncio.run(collect_responses(realm.stream(model, invocations, config)))

    assert len(responses) >= 1


def test_openrouter_realm_stream_retries_on_429_then_succeeds() -> None:
    from mvge.core.invocations import SummonerRequest

    realm = MockStreamingRealm(api_key="test-key")
    model = Model(
        id="openrouter/test-model",
        name="Test Model",
        realm="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
    )
    config = ChannelConfig(model=model)

    invocations = [SummonerRequest(role="user", content="Hello")]

    factory = _make_sequence_factory(
        [
            (429, {"retry-after": "0"}, []),
            (
                200,
                {},
                [
                    b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n',
                    b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
                    b"data: [DONE]\n\n",
                ],
            ),
        ]
    )
    realm._client.stream = factory  # type: ignore[method-assign]

    responses = asyncio.run(collect_responses(realm.stream(model, invocations, config)))

    assert factory._calls == 2
    assert responses[-1].stop_reason == "stop"


def test_openrouter_realm_stream_retries_on_5xx() -> None:
    from mvge.core.invocations import SummonerRequest

    realm = MockStreamingRealm(api_key="test-key")
    model = _make_model()
    config = ChannelConfig(model=model)
    invocations = [SummonerRequest(role="user", content="Hello")]

    factory = _make_sequence_factory(
        [
            (503, {}, []),
            (
                200,
                {},
                [
                    b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n',
                    b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
                    b"data: [DONE]\n\n",
                ],
            ),
        ]
    )
    realm._client.stream = factory  # type: ignore[method-assign]

    responses = asyncio.run(collect_responses(realm.stream(model, invocations, config)))

    assert factory._calls == 2
    assert responses[-1].stop_reason == "stop"


def test_openrouter_realm_stream_uses_retry_after_header() -> None:
    from unittest.mock import patch

    from mvge.core.invocations import SummonerRequest

    realm = MockStreamingRealm(api_key="test-key")
    model = _make_model()
    config = ChannelConfig(model=model)
    invocations = [SummonerRequest(role="user", content="Hello")]

    factory = _make_sequence_factory(
        [
            (429, {"retry-after": "5"}, []),
            (
                200,
                {},
                [
                    b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
                    b"data: [DONE]\n\n",
                ],
            ),
        ]
    )
    realm._client.stream = factory  # type: ignore[method-assign]

    with patch("asyncio.sleep", new=AsyncMock()) as sleep:
        responses = asyncio.run(
            collect_responses(realm.stream(model, invocations, config))
        )

    # Retry-After is seconds on the wire; the shared policy works in ms.
    sleep.assert_awaited_once_with(5.0)
    assert responses[-1].stop_reason == "stop"


def test_openrouter_realm_stream_rejects_excessive_retry_after() -> None:
    from mvge.core.invocations import SummonerRequest

    realm = MockStreamingRealm(api_key="test-key")
    model = _make_model()
    config = ChannelConfig(model=model)
    invocations = [SummonerRequest(role="user", content="Hello")]

    # 120s exceeds the 60s cap, so the realm reports rather than stalling.
    factory = _make_sequence_factory([(429, {"retry-after": "120"}, [])])
    realm._client.stream = factory  # type: ignore[method-assign]

    responses = asyncio.run(collect_responses(realm.stream(model, invocations, config)))

    assert responses[-1].error_message is not None
    assert "retry delay" in responses[-1].error_message


def test_openrouter_realm_stream_honours_x_should_retry_false() -> None:
    from mvge.core.invocations import SummonerRequest

    realm = MockStreamingRealm(api_key="test-key")
    model = _make_model()
    config = ChannelConfig(model=model)
    invocations = [SummonerRequest(role="user", content="Hello")]

    # 503 would normally retry, but the Realm says not to.
    factory = _make_sequence_factory(
        [
            (503, {"x-should-retry": "false"}, []),
            (200, {}, [b"data: [DONE]\n\n"]),
        ]
    )
    realm._client.stream = factory  # type: ignore[method-assign]

    responses = asyncio.run(collect_responses(realm.stream(model, invocations, config)))

    assert responses[-1].error_message is not None


def test_openrouter_realm_stream_backoff_is_jittered_and_capped() -> None:
    from unittest.mock import patch

    from mvge.core.invocations import SummonerRequest

    realm = MockStreamingRealm(api_key="test-key")
    model = _make_model()
    config = ChannelConfig(model=model)
    invocations = [SummonerRequest(role="user", content="Hello")]

    factory = _make_sequence_factory(
        [
            (500, {}, []),
            (
                200,
                {},
                [
                    b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
                    b"data: [DONE]\n\n",
                ],
            ),
        ]
    )
    realm._client.stream = factory  # type: ignore[method-assign]

    with patch("asyncio.sleep", new=AsyncMock()) as sleep:
        asyncio.run(collect_responses(realm.stream(model, invocations, config)))

    slept = sleep.await_args[0][0]
    # First retry: 0.5s base, jittered down by at most 25%.
    assert 0.375 <= slept <= 0.5


def test_openrouter_realm_stream_exhausts_retries_yields_rate_limited() -> None:
    from unittest.mock import patch

    from mvge.core.invocations import SummonerRequest

    realm = MockStreamingRealm(api_key="test-key")
    model = _make_model()
    config = ChannelConfig(model=model)
    invocations = [SummonerRequest(role="user", content="Hello")]

    factory = _make_sequence_factory(
        [
            (429, {}, []),
            (429, {}, []),
            (429, {}, []),
        ]
    )
    realm._client.stream = factory  # type: ignore[method-assign]

    with patch("asyncio.sleep", new=AsyncMock()):
        responses = asyncio.run(
            collect_responses(realm.stream(model, invocations, config))
        )

    assert factory._calls == 3
    assert len(responses) == 1
    assert responses[0].error_message is not None
    assert responses[0].error_code == "rate_limited"


def test_openrouter_realm_stream_non_retryable_no_retry() -> None:
    from mvge.core.invocations import SummonerRequest

    realm = MockStreamingRealm(api_key="test-key")
    model = _make_model()
    config = ChannelConfig(model=model)
    invocations = [SummonerRequest(role="user", content="Hello")]

    factory = _make_sequence_factory([(401, {}, [])])
    realm._client.stream = factory  # type: ignore[method-assign]

    responses = asyncio.run(collect_responses(realm.stream(model, invocations, config)))

    assert factory._calls == 1
    assert len(responses) == 1
    assert responses[0].error_message is not None
    assert responses[0].error_code == "auth_failed"


def test_openrouter_realm_stream_400_yields_generic_error() -> None:
    from mvge.core.invocations import SummonerRequest

    realm = MockStreamingRealm(api_key="test-key")
    model = _make_model()
    config = ChannelConfig(model=model)
    invocations = [SummonerRequest(role="user", content="Hello")]

    factory = _make_sequence_factory([(400, {}, [])])
    realm._client.stream = factory  # type: ignore[method-assign]

    responses = asyncio.run(collect_responses(realm.stream(model, invocations, config)))

    assert factory._calls == 1
    assert len(responses) == 1
    assert responses[0].error_message is not None
    assert responses[0].error_code is None


def test_openrouter_realm_close() -> None:
    realm = MockStreamingRealm(api_key="test-key")
    asyncio.run(realm.close())
    # Should not raise


def _make_capture_stream(captured_payload: dict[str, Any]) -> type:
    class MockStreamResponse:
        status_code = 200
        headers: dict[str, str] = {}

        def __init__(self) -> None:
            self._lines = [
                b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n',
                b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
                b"data: [DONE]\n\n",
            ]

        async def aiter_lines(self):
            for line in self._lines:
                yield line

    class MockStreamCM:
        def __init__(self, method: str, url: str, **kwargs: Any) -> None:
            captured_payload.update(kwargs.get("json", {}))

        async def __aenter__(self) -> MockStreamResponse:
            return MockStreamResponse()

        async def __aexit__(self, *args: Any) -> None:
            pass

    return MockStreamCM


def test_reasoning_sent_when_contemplation_level_set() -> None:
    from mvge.core.channel import MvgeResponse

    realm = MockStreamingRealm(api_key="test-key")
    model = Model(
        id="openrouter/openai/o1",
        name="Test Model",
        realm="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        supported_parameters=["reasoning", "temperature", "max_tokens"],
    )
    config = ChannelConfig(model=model, contemplation_level="high")

    invocations: list[MvgeResponse] = [
        MvgeResponse(
            role="assistant",
            content=[{"type": "text", "text": "Hello"}],
        )
    ]

    captured_payload: dict[str, Any] = {}
    realm._client.stream = _make_capture_stream(captured_payload)  # type: ignore[method-assign]

    asyncio.run(collect_responses(realm.stream(model, invocations, config)))

    assert "reasoning" in captured_payload
    assert captured_payload["reasoning"]["effort"] == "high"


def test_reasoning_not_sent_when_exclude_contemplation() -> None:
    from mvge.core.channel import MvgeResponse

    realm = MockStreamingRealm(api_key="test-key")
    model = Model(
        id="openrouter/openai/o1",
        name="Test Model",
        realm="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        supported_parameters=["reasoning"],
    )
    config = ChannelConfig(
        model=model, contemplation_level="none", exclude_contemplation=True
    )

    invocations: list[MvgeResponse] = [
        MvgeResponse(
            role="assistant",
            content=[{"type": "text", "text": "Hello"}],
        )
    ]

    captured_payload: dict[str, Any] = {}
    realm._client.stream = _make_capture_stream(captured_payload)  # type: ignore[method-assign]

    asyncio.run(collect_responses(realm.stream(model, invocations, config)))

    assert "reasoning" not in captured_payload


def test_reasoning_not_sent_for_non_reasoning_model() -> None:
    from mvge.core.channel import MvgeResponse

    realm = MockStreamingRealm(api_key="test-key")
    model = Model(
        id="openrouter/anthropic/claude-3.5-sonnet",
        name="Test Model",
        realm="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        supported_parameters=["temperature", "top_p", "max_tokens"],
    )
    config = ChannelConfig(model=model, contemplation_level="high")

    invocations: list[MvgeResponse] = [
        MvgeResponse(
            role="assistant",
            content=[{"type": "text", "text": "Hello"}],
        )
    ]

    captured_payload: dict[str, Any] = {}
    realm._client.stream = _make_capture_stream(captured_payload)  # type: ignore[method-assign]

    asyncio.run(collect_responses(realm.stream(model, invocations, config)))

    assert "reasoning" not in captured_payload


def test_reasoning_sent_for_model_with_supported_parameters() -> None:
    from mvge.core.channel import MvgeResponse

    realm = MockStreamingRealm(api_key="test-key")
    model = Model(
        id="google/gemini-2.5-flash",
        name="Gemini 2.5 Flash",
        realm="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        supported_parameters=["reasoning", "temperature", "max_tokens"],
    )
    config = ChannelConfig(model=model, contemplation_level="high")

    invocations: list[MvgeResponse] = [
        MvgeResponse(
            role="assistant",
            content=[{"type": "text", "text": "Hello"}],
        )
    ]

    captured_payload: dict[str, Any] = {}
    realm._client.stream = _make_capture_stream(captured_payload)  # type: ignore[method-assign]

    asyncio.run(collect_responses(realm.stream(model, invocations, config)))

    assert "reasoning" in captured_payload
    assert captured_payload["reasoning"]["effort"] == "high"


class _StreamRecorder:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines
        self.status_code = 200
        self.headers: dict[str, str] = {}

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _StatusRecorder(_StreamRecorder):
    def __init__(
        self,
        lines: list[bytes],
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(lines)
        self.status_code = status_code
        self.headers = headers or {}


def _make_sequence_factory(
    entries: list[tuple[int, dict[str, str], list[bytes]]],
) -> type:
    """Return a stream factory consuming (status, headers, lines) in order."""

    class _SeqCM:
        _calls = 0

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _StatusRecorder:
            idx = min(_SeqCM._calls, len(entries) - 1)
            _SeqCM._calls += 1
            status, headers, lines = entries[idx]
            return _StatusRecorder(lines, status, headers)

        async def __aexit__(self, *args: Any) -> None:
            pass

    return _SeqCM


def _make_stream_factory(
    lines: list[bytes], sink: dict[str, Any] | None = None
) -> type:
    class _StreamCM:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            if sink is not None:
                sink.update(kwargs.get("json", {}))

        async def __aenter__(self) -> _StreamRecorder:
            return _StreamRecorder(lines)

        async def __aexit__(self, *args: Any) -> None:
            pass

    return _StreamCM


def _make_model(
    model_id: str = "openrouter/test-model",
    supported: list[str] | None = None,
) -> Model:
    return Model(
        id=model_id,
        name="Test Model",
        realm="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        supported_parameters=supported or ["temperature", "top_p", "max_tokens"],
    )


def test_stream_sends_tools_in_payload() -> None:
    from mvge.core.invocations import SummonerRequest

    realm = MockStreamingRealm(api_key="test-key")
    model = _make_model()
    tools = [
        {
            "type": "function",
            "function": {
                "name": "bash",
                "description": "Run a command",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    config = ChannelConfig(model=model, tools=tools)
    invocations = [SummonerRequest(role="user", content="Hello")]

    captured_payload: dict[str, Any] = {}

    realm._client.stream = _make_stream_factory(  # type: ignore[method-assign]
        [
            b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
            b"data: [DONE]\n\n",
        ],
        captured_payload,
    )

    responses = asyncio.run(collect_responses(realm.stream(model, invocations, config)))

    assert captured_payload["tools"] == tools
    assert responses[-1].stop_reason == "stop"


def test_stream_accumulates_text_into_final_invocation() -> None:
    from mvge.core.channel import StopReason
    from mvge.core.invocations import SummonerRequest

    realm = MockStreamingRealm(api_key="test-key")
    model = _make_model()
    config = ChannelConfig(model=model)
    invocations = [SummonerRequest(role="user", content="Hello")]

    realm._client.stream = _make_stream_factory(  # type: ignore[method-assign]
        [
            b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":" world"}}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
            b"data: [DONE]\n\n",
        ]
    )

    responses = asyncio.run(collect_responses(realm.stream(model, invocations, config)))

    partials = [
        r
        for r in responses
        if r.invocation is not None and r.invocation.stop_reason == StopReason.PENDING
    ]
    final = responses[-1]
    assert len(partials) == 2
    assert final.invocation is not None
    assert final.invocation.content == [{"type": "text", "text": "Hello world"}]
    assert final.invocation.stop_reason == StopReason.STOP


def test_stream_surfaces_contemplation_deltas() -> None:
    from mvge.core.invocations import SummonerRequest

    realm = MockStreamingRealm(api_key="test-key")
    model = _make_model()
    config = ChannelConfig(model=model)
    invocations = [SummonerRequest(role="user", content="Think")]

    realm._client.stream = _make_stream_factory(  # type: ignore[method-assign]
        [
            b'data: {"choices":[{"delta":{"reasoning":"weighing options"}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":"Answer"}}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
            b"data: [DONE]\n\n",
        ]
    )

    responses = asyncio.run(collect_responses(realm.stream(model, invocations, config)))

    blocks = [
        block
        for r in responses
        if r.invocation is not None
        for block in (r.invocation.content or [])
    ]
    contemplation = [b for b in blocks if b.get("type") == "contemplation"]
    assert contemplation
    assert contemplation[0]["text"] == "weighing options"


def test_stream_keeps_contemplation_out_of_final_text() -> None:
    from mvge.core.invocations import SummonerRequest

    realm = MockStreamingRealm(api_key="test-key")
    model = _make_model()
    config = ChannelConfig(model=model)
    invocations = [SummonerRequest(role="user", content="Think")]

    realm._client.stream = _make_stream_factory(  # type: ignore[method-assign]
        [
            b'data: {"choices":[{"delta":{"reasoning":"private thought"}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":"Answer"}}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
            b"data: [DONE]\n\n",
        ]
    )

    responses = asyncio.run(collect_responses(realm.stream(model, invocations, config)))

    final = responses[-1]
    assert final.invocation is not None
    text = "".join(
        b.get("text", "") for b in final.invocation.content if b.get("type") == "text"
    )
    assert text == "Answer"
    assert "private thought" not in text


def test_stream_includes_contemplation_in_final_invocation() -> None:
    from mvge.core.invocations import SummonerRequest

    realm = MockStreamingRealm(api_key="test-key")
    model = _make_model()
    config = ChannelConfig(model=model)
    invocations = [SummonerRequest(role="user", content="Think")]

    realm._client.stream = _make_stream_factory(  # type: ignore[method-assign]
        [
            b'data: {"choices":[{"delta":{"reasoning":"weighing options"}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":"Answer"}}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
            b"data: [DONE]\n\n",
        ]
    )

    responses = asyncio.run(collect_responses(realm.stream(model, invocations, config)))

    final = responses[-1]
    assert final.invocation is not None
    # Final invocation should contain both contemplation and text blocks
    contemplation_blocks = [
        b for b in final.invocation.content if b.get("type") == "contemplation"
    ]
    text_blocks = [b for b in final.invocation.content if b.get("type") == "text"]
    assert len(contemplation_blocks) == 1
    assert contemplation_blocks[0]["text"] == "weighing options"
    assert len(text_blocks) == 1
    assert text_blocks[0]["text"] == "Answer"


def test_stream_attaches_mana_usage_to_final_invocation() -> None:
    from mvge.core.invocations import SummonerRequest

    realm = MockStreamingRealm(api_key="test-key")
    model = _make_model()
    config = ChannelConfig(model=model)
    invocations = [SummonerRequest(role="user", content="Hello")]

    realm._client.stream = _make_stream_factory(  # type: ignore[method-assign]
        [
            b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
            b'"usage":{"prompt_tokens":12,"completion_tokens":5,"total_tokens":17}}\n\n',
            b"data: [DONE]\n\n",
        ]
    )

    responses = asyncio.run(collect_responses(realm.stream(model, invocations, config)))
    final = responses[-1]

    assert final.mana_used == 17
    assert final.invocation is not None
    assert final.invocation.mana_usage == {
        "input": 12,
        "output": 5,
        "total": 17,
    }


def test_stream_tool_calls_produce_spell_use_invocation() -> None:
    from mvge.core.channel import StopReason
    from mvge.core.invocations import SummonerRequest

    realm = MockStreamingRealm(api_key="test-key")
    model = _make_model()
    config = ChannelConfig(model=model)
    invocations = [SummonerRequest(role="user", content="Run echo")]

    realm._client.stream = _make_stream_factory(  # type: ignore[method-assign]
        [
            (
                b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1",'
                b'"type":"function","function":{"name":"bash","arguments":"{\\"command\\":'
                b' \\"echo hello\\"}"}}]}}]}\n\n'
            ),
            b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n',
            b"data: [DONE]\n\n",
        ]
    )

    responses = asyncio.run(collect_responses(realm.stream(model, invocations, config)))

    final = responses[-1]
    assert final.invocation is not None
    assert final.invocation.stop_reason == StopReason.SPELL_USE
    tool_block = final.invocation.content[0]
    assert tool_block["type"] == "spell_cast"
    assert tool_block["spell_cast"]["id"] == "call_1"
    assert tool_block["spell_cast"]["name"] == "bash"
    assert tool_block["spell_cast"]["arguments"] == {"command": "echo hello"}


def test_invocations_to_messages_serializes_tool_calls() -> None:
    from mock_realm import _invocations_to_messages

    from mvge.core.channel import MvgeResponse
    from mvge.core.spells import SpellResultMessage

    invocations = [
        MvgeResponse(
            role="assistant",
            content=[
                {
                    "type": "spell_cast",
                    "spell_cast": {
                        "id": "call_1",
                        "name": "bash",
                        "arguments": {"command": "echo hi"},
                    },
                }
            ],
        ),
        SpellResultMessage(
            spell_cast_id="call_1",
            spell_name="bash",
            content=[{"type": "text", "text": "hi"}],
        ),
    ]

    messages = _invocations_to_messages(invocations)

    assert messages[0]["role"] == "assistant"
    assert messages[0]["tool_calls"][0]["function"]["name"] == "bash"
    args = messages[0]["tool_calls"][0]["function"]["arguments"]
    assert args == '{"command": "echo hi"}'
    assert messages[1]["role"] == "tool"
    assert messages[1]["tool_call_id"] == "call_1"
    assert messages[1]["content"] == "hi"
