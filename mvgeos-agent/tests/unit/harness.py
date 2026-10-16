from __future__ import annotations

import asyncio
import json
import tempfile
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mvgeos_core.channel import (
    Model,
    MvgeResponse,
    RealmResponse,
    StopReason,
)
from mvgeos_core.errors import MaxTurnsExceededError
from mvgeos_core.event_bus import EventBus
from mvgeos_core.events import (
    ContemplationLevel,
    ContentType,
    MvgeEvent,
    MvgeEventType,
    QueueMode,
)
from mvgeos_core.invocations import (
    MvgeInvocation,
    SummonerRequest,
)
from mvgeos_core.loop import LoopContext
from mvgeos_core.spells import (
    MvgeSpell,
    SpellResultMessage,
)
from mvgeos_runes.rune_runner import RuneRunner
from mvgeos_runes.types import (
    AfterProviderResponseData,
    BeforeProviderHeadersData,
    ContextTransformData,
    InputData,
    PrepareNextTurnData,
    SigilHook,
)
from mvgeos_tome.handle import TomeHandleFactory

from mvgeos_agent.agent_session import MvgeTome
from mvgeos_agent.environment import PromptSource
from mvgeos_agent.harness import MvgeHarness
from mvgeos_agent.harness.compaction.compaction import DEFAULT_COMPACTION_SETTINGS
from mvgeos_agent.harness.harness import _apply_prepare_next_turn
from mvgeos_agent.harness._state import MvgeState


@pytest.mark.asyncio
async def test_harness_state_initialization_and_properties() -> None:
    mock_state = MagicMock(spec=MvgeState)
    mock_tome = MagicMock()
    mock_state.agent_tome = mock_tome
    mock_state.invocations = []

    harness = MvgeHarness(state=mock_state, tome=mock_tome)

    assert harness.state == mock_state
    assert harness.tome == mock_tome
    assert harness.emit is not None
    assert harness.compaction is None


@pytest.mark.asyncio
async def test_harness_runs_compaction_callback() -> None:
    mock_state = MagicMock(spec=MvgeState)
    mock_state.rune_runner = None
    mock_compaction = AsyncMock()
    mock_compaction.maybe_compact.return_value = [
        SummonerRequest(role="user", content="Compact summary")
    ]

    harness = MvgeHarness(state=mock_state, compaction=mock_compaction)
    callbacks = harness._build_callbacks()
    assert callbacks.after_invocation is not None

    invocations = [SummonerRequest(role="user", content="Test")]
    res = await callbacks.after_invocation(invocations)
    assert res == [SummonerRequest(role="user", content="Compact summary")]
    mock_compaction.maybe_compact.assert_awaited_once_with(invocations, None)


@pytest.mark.asyncio
async def test_harness_switch_tome() -> None:
    mock_state = MagicMock(spec=MvgeState)
    tome1 = MagicMock()
    tome2 = MagicMock()

    harness = MvgeHarness(state=mock_state, tome=tome1)
    assert harness.tome == tome1

    await harness.switch_tome(tome2)
    assert harness.tome == tome2
    assert mock_state.agent_tome == tome2


@pytest.mark.asyncio
async def test_harness_set_model_and_realm() -> None:
    mock_state = MagicMock(spec=MvgeState)
    tome = MagicMock()
    mock_realm = MagicMock()
    mock_model = MagicMock()

    harness = MvgeHarness(state=mock_state, tome=tome)
    assert harness.compaction is None

    harness.set_model_and_realm(mock_model, mock_realm)
    assert harness.compaction is not None


@pytest.mark.asyncio
async def test_harness_run_with_prompt_appends_invocation() -> None:
    mock_state = MagicMock(spec=MvgeState)
    mock_state.invocations = []
    mock_state.rune_runner = None
    mock_state.spells = []
    mock_state.system_prompt = "system"
    mock_state.prompt_source = PromptSource.BUILTIN
    mock_state.max_tokens = 1000
    mock_state.temperature = 0.5
    mock_state.spell_timeout_ms = 5000
    mock_state.contemplation_budget = None
    mock_state.exclude_contemplation = False
    mock_state.max_turns = 10
    mock_state.queue_mode = QueueMode.ALL

    harness = MvgeHarness(state=mock_state)
    with patch(
        "mvgeos_agent.harness.harness.run_loop",
        new=AsyncMock(
            return_value=[SummonerRequest(role="user", content="User question")]
        ),
    ):
        await harness.run(
            stream_fn=AsyncMock(),
            model={"id": "test-model"},
            prompt="User question",
        )

    assert len(mock_state.invocations) == 1
    assert isinstance(mock_state.invocations[0], SummonerRequest)
    assert mock_state.invocations[0].content == "User question"


def _crowded_model() -> Model:
    return Model(
        id="test-provider/test-model",
        name="Test Model",
        realm="test-realm",
        base_url="https://api.example.com/v1",
        api_key="test-key",
        context_window=100_000,
    )


def _harness_with_compaction(
    invocations: list[MvgeInvocation], model: Model | None = None
) -> tuple[MvgeHarness, MagicMock, MagicMock]:
    mock_state = MagicMock(spec=MvgeState)
    mock_state.invocations = list(invocations)
    mock_state.rune_runner = None
    mock_state.spells = []
    mock_state.system_prompt = "system"
    mock_state.prompt_source = PromptSource.BUILTIN
    mock_state.max_tokens = 1000
    mock_state.temperature = 0.5
    mock_state.spell_timeout_ms = 5000
    mock_state.contemplation_budget = None
    mock_state.exclude_contemplation = False
    mock_state.max_turns = 10
    mock_state.queue_mode = QueueMode.ALL

    mock_compaction = MagicMock()
    mock_compaction._settings = DEFAULT_COMPACTION_SETTINGS
    mock_compaction.force_compact = AsyncMock(return_value=None)
    harness = MvgeHarness(
        compaction=mock_compaction,
        state=mock_state,
        model=model or _crowded_model(),
    )
    return harness, mock_compaction, mock_state


@pytest.mark.asyncio
async def test_harness_precompact_when_pool_crowded() -> None:
    crowded = [
        MvgeResponse(
            role="assistant",
            content=[{"type": "text", "text": "prior"}],
            mana_usage={"total": 90_000.0},
            stop_reason=StopReason.STOP,
        )
    ]
    compacted = [SummonerRequest(role="user", content="summary")]
    harness, mock_compaction, mock_state = _harness_with_compaction(crowded)
    mock_compaction.force_compact = AsyncMock(return_value=compacted)

    with patch(
        "mvgeos_agent.harness.harness.run_loop", new=AsyncMock(return_value=compacted)
    ):
        await harness.run(stream_fn=AsyncMock(), model={"id": "test-model"})

    mock_compaction.force_compact.assert_awaited_once()
    assert mock_state.invocations == compacted


@pytest.mark.asyncio
async def test_harness_no_precompact_when_room() -> None:
    roomy = [
        MvgeResponse(
            role="assistant",
            content=[{"type": "text", "text": "prior"}],
            mana_usage={"total": 100.0},
            stop_reason=StopReason.STOP,
        )
    ]
    harness, mock_compaction, _ = _harness_with_compaction(roomy)

    with patch(
        "mvgeos_agent.harness.harness.run_loop", new=AsyncMock(return_value=roomy)
    ):
        await harness.run(stream_fn=AsyncMock(), model={"id": "test-model"})

    mock_compaction.force_compact.assert_not_awaited()


@pytest.mark.asyncio
async def test_harness_no_precompact_without_window() -> None:
    crowded = [
        MvgeResponse(
            role="assistant",
            content=[{"type": "text", "text": "prior"}],
            mana_usage={"total": 90_000.0},
            stop_reason=StopReason.STOP,
        )
    ]
    windowless = _crowded_model()
    windowless.context_window = 0
    harness, mock_compaction, _ = _harness_with_compaction(crowded, windowless)

    with patch(
        "mvgeos_agent.harness.harness.run_loop", new=AsyncMock(return_value=crowded)
    ):
        await harness.run(stream_fn=AsyncMock(), model={"id": "test-model"})

    mock_compaction.force_compact.assert_not_awaited()


@pytest.mark.asyncio
async def test_harness_precompact_failure_does_not_fail_run() -> None:
    crowded = [
        MvgeResponse(
            role="assistant",
            content=[{"type": "text", "text": "prior"}],
            mana_usage={"total": 90_000.0},
            stop_reason=StopReason.STOP,
        )
    ]
    harness, mock_compaction, mock_state = _harness_with_compaction(crowded)
    mock_compaction.force_compact = AsyncMock(side_effect=RuntimeError("boom"))

    with patch(
        "mvgeos_agent.harness.harness.run_loop", new=AsyncMock(return_value=crowded)
    ):
        result = await harness.run(stream_fn=AsyncMock(), model={"id": "test-model"})

    assert result is not None
    assert mock_state.invocations == crowded


class TestMvgeHarnessExecution:
    @pytest.fixture
    def mock_spell(self) -> MvgeSpell:
        spell = MagicMock(spec=MvgeSpell)
        spell.name = "test_spell"
        spell.description = "A test spell"
        spell.parameters = {"type": "object", "properties": {}}
        spell.execute = AsyncMock(return_value={"result": "success"})
        return spell

    @pytest.fixture
    def state(self, mock_spell: MvgeSpell) -> MvgeState:
        return MvgeState(
            system_prompt="You are a helpful assistant.",
            model={"id": "test-model", "name": "Test Model"},
            contemplation_level=ContemplationLevel.OFF,
            spells=[mock_spell],
            invocations=[
                SummonerRequest(role="user", content="Hello"),
            ],
            max_tokens=4096,
            temperature=0.7,
        )

    def _make_realm_stream(
        self, responses: list[RealmResponse]
    ) -> Callable[[list[Any], Any], AsyncIterator[RealmResponse]]:
        """Build a StreamFn yielding one response per turn, in order."""
        turn = -1

        def stream_fn(
            invocations: list[Any], signal: Any | None = None
        ) -> AsyncIterator[RealmResponse]:
            nonlocal turn
            turn += 1
            response = responses[min(turn, len(responses) - 1)]

            async def gen() -> AsyncIterator[RealmResponse]:
                yield response

            return gen()

        return stream_fn

    @pytest.mark.asyncio
    async def test_loop_single_turn_no_spells(self, state: MvgeState) -> None:

        model_obj = Model(
            id="test-model",
            name="Test Model",
            realm="test",
            base_url="https://api.test.com",
            api_key="test",
        )
        responses = [
            RealmResponse(
                model=model_obj,
                invocation=MvgeResponse(
                    role="assistant",
                    content=[{"type": "text", "text": "Hello!"}],
                    stop_reason=StopReason.STOP,
                ),
            ),
        ]
        stream_fn = self._make_realm_stream(responses)

        harness = MvgeHarness(state=state)
        result = await harness.run(stream_fn, {"id": "test-model"}, "none")

        assert isinstance(result, MvgeResponse)
        assert result.content == [{"type": "text", "text": "Hello!"}]
        assert result.stop_reason == StopReason.STOP
        assert len(state.invocations) == 2

    @pytest.mark.asyncio
    async def test_parts_prompt_reaches_realm_untouched(self, state: MvgeState) -> None:
        """A content-parts user prompt rides the harness-to-realm path intact.

        Regression: the native attachment blocks the GUI builds must arrive
        at stream_fn as the same list of blocks, never coerced to a string.
        """
        parts = [
            {"type": "text", "text": "describe this"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="},
            },
        ]
        state.invocations = [SummonerRequest(role="user", content=parts)]

        seen: list[list[Any]] = []

        def stream_fn(
            invocations: list[Any], signal: Any | None = None
        ) -> AsyncIterator[RealmResponse]:
            seen.append(invocations)

            async def gen() -> AsyncIterator[RealmResponse]:
                yield RealmResponse(
                    model=Model(
                        id="test-model",
                        name="Test Model",
                        realm="test",
                        base_url="https://api.test.com",
                        api_key="k",
                    ),
                    invocation=MvgeResponse(
                        role="assistant",
                        content=[{"type": "text", "text": "done"}],
                        stop_reason=StopReason.STOP,
                    ),
                )

            return gen()

        harness = MvgeHarness(state=state)
        await harness.run(stream_fn, {"id": "test-model"}, "none")

        assert seen, "stream_fn was never called"
        user_msgs = [inv for inv in seen[0] if isinstance(inv, SummonerRequest)]
        assert user_msgs, "no user invocation reached the realm"
        assert user_msgs[0].content == parts

    @pytest.mark.asyncio
    async def test_loop_with_spell_cast(
        self, state: MvgeState, mock_spell: MvgeSpell
    ) -> None:

        model_obj = Model(
            id="test-model",
            name="Test Model",
            realm="test",
            base_url="https://api.test.com",
            api_key="test",
        )
        responses = [
            RealmResponse(
                model=model_obj,
                invocation=MvgeResponse(
                    role="assistant",
                    content=[
                        {
                            "type": "spell_cast",
                            "spell_cast": {
                                "id": "call-1",
                                "name": "test_spell",
                                "arguments": {},
                            },
                        }
                    ],
                    stop_reason=StopReason.SPELL_USE,
                ),
            ),
            RealmResponse(
                model=model_obj,
                invocation=MvgeResponse(
                    role="assistant",
                    content=[{"type": "text", "text": "Spell executed successfully"}],
                    stop_reason=StopReason.STOP,
                ),
            ),
        ]
        stream_fn = self._make_realm_stream(responses)

        harness = MvgeHarness(state=state)
        result = await harness.run(stream_fn, {"id": "test-model"}, "none")

        assert isinstance(result, MvgeResponse)
        assert result.stop_reason == StopReason.STOP
        mock_spell.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_loop_spell_timeout(
        self, state: MvgeState, mock_spell: MvgeSpell
    ) -> None:

        async def slow_execute(*args: Any, **kwargs: Any) -> dict[str, Any]:
            await asyncio.sleep(10)
            return {}

        mock_spell.execute = slow_execute  # type: ignore[method-assign]
        state.spell_timeout_ms = 100

        model_obj = Model(
            id="test-model",
            name="Test Model",
            realm="test",
            base_url="https://api.test.com",
            api_key="test",
        )
        responses = [
            RealmResponse(
                model=model_obj,
                invocation=MvgeResponse(
                    role="assistant",
                    content=[
                        {
                            "type": "spell_cast",
                            "spell_cast": {
                                "id": "call-1",
                                "name": "test_spell",
                                "arguments": {},
                            },
                        }
                    ],
                    stop_reason=StopReason.SPELL_USE,
                ),
            ),
            RealmResponse(
                model=model_obj,
                invocation=MvgeResponse(
                    role="assistant",
                    content=[{"type": "text", "text": "Done"}],
                    stop_reason=StopReason.STOP,
                ),
            ),
        ]
        stream_fn = self._make_realm_stream(responses)

        harness = MvgeHarness(state=state)
        result = await harness.run(stream_fn, {"id": "test-model"}, "none")

        assert isinstance(result, MvgeResponse)
        assert result.stop_reason == StopReason.STOP

        spell_results = [
            inv for inv in state.invocations if isinstance(inv, SpellResultMessage)
        ]
        timeout_result = next((r for r in spell_results if r.is_error), None)
        assert timeout_result is not None
        assert "timed out" in timeout_result.content[0]["text"]
        assert "test_spell" in timeout_result.content[0]["text"]

    @pytest.mark.asyncio
    async def test_loop_persists_spell_use_invocation(self, state: MvgeState) -> None:

        model_obj = Model(
            id="test-model",
            name="Test Model",
            realm="test",
            base_url="https://api.test.com",
            api_key="test",
        )
        responses = [
            RealmResponse(
                model=model_obj,
                invocation=MvgeResponse(
                    role="assistant",
                    content=[
                        {
                            "type": "spell_cast",
                            "spell_cast": {
                                "id": "call-1",
                                "name": "test_spell",
                                "arguments": {},
                            },
                        }
                    ],
                    stop_reason=StopReason.SPELL_USE,
                ),
            ),
            RealmResponse(
                model=model_obj,
                invocation=MvgeResponse(
                    role="assistant",
                    content=[{"type": "text", "text": "Done"}],
                    stop_reason=StopReason.STOP,
                ),
            ),
        ]
        stream_fn = self._make_realm_stream(responses)

        harness = MvgeHarness(state=state)
        await harness.run(stream_fn, {"id": "test-model"}, "none")

        spell_cast_msgs = [
            inv
            for inv in state.invocations
            if isinstance(inv, MvgeResponse)
            and inv.content
            and inv.content[0].get("type") == "spell_cast"
        ]
        assert len(spell_cast_msgs) == 1
        assert spell_cast_msgs[0].stop_reason == StopReason.SPELL_USE

    @pytest.mark.asyncio
    async def test_loop_accumulates_mana_used(self, state: MvgeState) -> None:

        model_obj = Model(
            id="test-model",
            name="Test Model",
            realm="test",
            base_url="https://api.test.com",
            api_key="test",
        )
        responses = [
            RealmResponse(
                model=model_obj,
                mana_used=20,
                invocation=MvgeResponse(
                    role="assistant",
                    content=[{"type": "text", "text": "This uses a lot of mana"}],
                    stop_reason=StopReason.LENGTH,
                ),
            ),
        ]
        stream_fn = self._make_realm_stream(responses)

        harness = MvgeHarness(state=state)
        result = await harness.run(stream_fn, {"id": "test-model"}, "none")

        assert result.stop_reason == StopReason.LENGTH
        assert state.mana_used == 20

    @pytest.mark.asyncio
    async def test_loop_error_handling(self, state: MvgeState) -> None:

        async def error_stream_gen(
            invocations: list[Any] | None = None, signal: Any | None = None
        ) -> AsyncIterator[RealmResponse]:
            raise RuntimeError("API error")
            yield  # Never reached

        harness = MvgeHarness(state=state)
        with pytest.raises(RuntimeError, match="API error"):
            await harness.run(error_stream_gen, {"id": "test-model"}, "none")

    @pytest.mark.asyncio
    async def test_loop_raises_rate_limit_error(self, state: MvgeState) -> None:
        from mvgeos_core.errors import RateLimitError

        model_obj = Model(
            id="test-model",
            name="Test",
            realm="test",
            base_url="",
            api_key="",
        )

        async def rate_limit_stream(
            invocations: list[Any] | None = None, signal: Any | None = None
        ) -> AsyncIterator[RealmResponse]:
            yield RealmResponse(
                model=model_obj,
                error_message="You are being rate limited",
                error_code="rate_limited",
            )

        harness = MvgeHarness(state=state)
        with pytest.raises(RateLimitError, match="rate limited"):
            await harness.run(rate_limit_stream, {"id": "test-model"}, "none")

    @pytest.mark.asyncio
    async def test_loop_raises_auth_error(self, state: MvgeState) -> None:
        from mvgeos_core.errors import AuthenticationError

        model_obj = Model(
            id="test-model",
            name="Test",
            realm="test",
            base_url="",
            api_key="",
        )

        async def auth_stream(
            invocations: list[Any] | None = None, signal: Any | None = None
        ) -> AsyncIterator[RealmResponse]:
            yield RealmResponse(
                model=model_obj,
                error_message="User not found",
                error_code="auth_failed",
            )

        harness = MvgeHarness(state=state)
        with pytest.raises(AuthenticationError, match="User not found"):
            await harness.run(auth_stream, {"id": "test-model"}, "none")

    @pytest.mark.asyncio
    async def test_loop_generic_provider_error_stays_runtime_error(
        self, state: MvgeState
    ) -> None:

        model_obj = Model(
            id="test-model",
            name="Test",
            realm="test",
            base_url="",
            api_key="",
        )

        async def bad_request_stream(
            invocations: list[Any] | None = None, signal: Any | None = None
        ) -> AsyncIterator[RealmResponse]:
            yield RealmResponse(
                model=model_obj,
                error_message="Bad request",
                error_code=None,
            )

        harness = MvgeHarness(state=state)
        with pytest.raises(RuntimeError, match="Bad request"):
            await harness.run(bad_request_stream, {"id": "test-model"}, "none")

    @pytest.mark.asyncio
    async def test_loop_no_initial_invocation(self) -> None:

        state = MvgeState(system_prompt="test")

        async def empty_stream(
            invocations: list[Any] | None = None,
        ) -> AsyncIterator[RealmResponse]:
            if False:
                yield

        stream_fn = empty_stream

        harness = MvgeHarness(state=state)
        with pytest.raises(RuntimeError, match="No invocations to process"):
            await harness.run(stream_fn, {"id": "test-model"}, "none")


class TestMvgeHarnessProviderHooks:
    @pytest.mark.asyncio
    async def test_before_provider_request_is_emitted(self) -> None:

        state = MvgeState(
            system_prompt="test",
            model={"id": "test-model", "name": "Test"},
            invocations=[SummonerRequest(role="user", content="hi")],
        )
        runner = RuneRunner()
        handler = MagicMock()
        runner.register_handler(SigilHook.BEFORE_PROVIDER_REQUEST, handler)
        state.rune_runner = runner

        model_obj = Model(
            id="test-model",
            name="Test",
            realm="test",
            base_url="",
            api_key="",
        )
        responses = [
            RealmResponse(
                model=model_obj,
                invocation=MvgeResponse(
                    role="assistant",
                    content=[{"type": "text", "text": "Hello"}],
                    stop_reason=StopReason.STOP,
                ),
            ),
        ]

        async def stream(
            invocations: list[Any] | None = None, signal: Any | None = None
        ) -> AsyncIterator[RealmResponse]:
            for r in responses:
                yield r

        harness = MvgeHarness(state=state)
        await harness.run(stream, {"id": "test-model"}, "none")
        handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_after_provider_response_is_emitted(self) -> None:

        state = MvgeState(
            system_prompt="test",
            model={"id": "test-model", "name": "Test"},
            invocations=[SummonerRequest(role="user", content="hi")],
        )
        runner = RuneRunner()
        handler = MagicMock()
        runner.register_handler(SigilHook.AFTER_PROVIDER_RESPONSE, handler)
        state.rune_runner = runner

        model_obj = Model(
            id="test-model",
            name="Test",
            realm="test",
            base_url="",
            api_key="",
        )
        responses = [
            RealmResponse(
                model=model_obj,
                invocation=MvgeResponse(
                    role="assistant",
                    content=[{"type": "text", "text": "Hello"}],
                    stop_reason=StopReason.STOP,
                ),
            ),
        ]

        async def stream(
            invocations: list[Any] | None = None, signal: Any | None = None
        ) -> AsyncIterator[RealmResponse]:
            for r in responses:
                yield r

        harness = MvgeHarness(state=state)
        await harness.run(stream, {"id": "test-model"}, "none")
        handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_before_provider_headers_chain_applies_headers(self) -> None:

        state = MvgeState(
            system_prompt="test",
            model={"id": "test-model", "name": "Test"},
            invocations=[SummonerRequest(role="user", content="hi")],
        )
        runner = RuneRunner()

        def add_header(data: BeforeProviderHeadersData) -> BeforeProviderHeadersData:
            data.headers["X-Custom"] = "value"
            return data

        runner.register_handler(SigilHook.BEFORE_PROVIDER_HEADERS, add_header)
        state.rune_runner = runner

        model_obj = Model(
            id="test-model",
            name="Test",
            realm="test",
            base_url="",
            api_key="",
        )
        responses = [
            RealmResponse(
                model=model_obj,
                invocation=MvgeResponse(
                    role="assistant",
                    content=[{"type": "text", "text": "Hello"}],
                    stop_reason=StopReason.STOP,
                ),
            ),
        ]

        async def stream(
            invocations: list[Any] | None = None, signal: Any | None = None
        ) -> AsyncIterator[RealmResponse]:
            for r in responses:
                yield r

        harness = MvgeHarness(state=state)
        await harness.run(stream, {"id": "test-model"}, "none")

        assert "headers" in state.model
        assert state.model["headers"].get("X-Custom") == "value"

    @pytest.mark.asyncio
    async def test_provider_hooks_error_propagates(self) -> None:

        state = MvgeState(
            system_prompt="test",
            model={"id": "test-model", "name": "Test"},
            invocations=[SummonerRequest(role="user", content="hi")],
        )
        runner = RuneRunner()

        def bad_handler(data: dict) -> None:
            raise ValueError("oops")

        runner.register_handler(SigilHook.BEFORE_PROVIDER_REQUEST, bad_handler)
        state.rune_runner = runner

        model_obj = Model(
            id="test-model",
            name="Test",
            realm="test",
            base_url="",
            api_key="",
        )
        responses = [
            RealmResponse(
                model=model_obj,
                invocation=MvgeResponse(
                    role="assistant",
                    content=[{"type": "text", "text": "Hello"}],
                    stop_reason=StopReason.STOP,
                ),
            ),
        ]

        async def stream(
            invocations: list[Any] | None = None, signal: Any | None = None
        ) -> AsyncIterator[RealmResponse]:
            for r in responses:
                yield r

        harness = MvgeHarness(state=state)
        with pytest.raises(ValueError, match="oops"):
            await harness.run(stream, {"id": "test-model"}, "none")


class TestMvgeHarnessContextTransform:
    @pytest.mark.asyncio
    async def test_context_transform_modifies_invocations(self) -> None:

        state = MvgeState(
            system_prompt="test",
            model={"id": "test-model", "name": "Test"},
            invocations=[SummonerRequest(role="user", content="original")],
        )
        runner = RuneRunner()

        def transform(invocations: list) -> list:
            invocations[0].content = "transformed"
            return invocations

        runner.register_handler(SigilHook.CONTEXT_TRANSFORM, transform)
        state.rune_runner = runner

        model_obj = Model(
            id="test-model",
            name="Test",
            realm="test",
            base_url="",
            api_key="",
        )
        responses = [
            RealmResponse(
                model=model_obj,
                invocation=MvgeResponse(
                    role="assistant",
                    content=[{"type": "text", "text": "Hello"}],
                    stop_reason=StopReason.STOP,
                ),
            ),
        ]

        async def stream(
            invocations: list[Any] | None = None, signal: Any | None = None
        ) -> AsyncIterator[RealmResponse]:
            for r in responses:
                yield r

        harness = MvgeHarness(state=state)
        await harness.run(stream, {"id": "test-model"}, "none")

        assert state.invocations[0].content == "transformed"


class TestMvgeHarnessInputHook:
    @pytest.mark.asyncio
    async def test_input_hook_emitted_for_summoner_request(self) -> None:

        state = MvgeState(
            system_prompt="test",
            model={"id": "test-model", "name": "Test"},
            invocations=[SummonerRequest(role="user", content="my input")],
        )
        runner = RuneRunner()
        handler = MagicMock()
        runner.register_handler(SigilHook.INPUT, handler)
        state.rune_runner = runner

        model_obj = Model(
            id="test-model",
            name="Test",
            realm="test",
            base_url="",
            api_key="",
        )
        responses = [
            RealmResponse(
                model=model_obj,
                invocation=MvgeResponse(
                    role="assistant",
                    content=[{"type": "text", "text": "Hello"}],
                    stop_reason=StopReason.STOP,
                ),
            ),
        ]

        async def stream(
            invocations: list[Any] | None = None, signal: Any | None = None
        ) -> AsyncIterator[RealmResponse]:
            for r in responses:
                yield r

        harness = MvgeHarness(state=state)
        await harness.run(stream, {"id": "test-model"}, "none")

        handler.assert_called_once()
        call_data = handler.call_args[0][0]
        assert call_data.content == "my input"


class TestMvgeHarnessTomeHooks:
    @pytest.mark.asyncio
    async def test_tome_start_emitted_when_agent_tome_in_state(
        self,
    ) -> None:

        with tempfile.TemporaryDirectory() as tmp:
            factory = TomeHandleFactory(Path(tmp))
            write = factory.create_tome("/tmp")
            read = factory.open_read(write.tome_id)
            runner = RuneRunner()
            handler = MagicMock()
            runner.register_handler(SigilHook.SESSION_START, handler)

            session = MvgeTome(factory, write, read, runner)
            state = MvgeState(
                system_prompt="test",
                model={"id": "test-model", "name": "Test"},
                invocations=[SummonerRequest(role="user", content="hi")],
                rune_runner=runner,
                agent_tome=session,
            )

            model_obj = Model(
                id="test-model",
                name="Test",
                realm="test",
                base_url="",
                api_key="",
            )
            responses = [
                RealmResponse(
                    model=model_obj,
                    invocation=MvgeResponse(
                        role="assistant",
                        content=[{"type": "text", "text": "Hello"}],
                        stop_reason=StopReason.STOP,
                    ),
                ),
            ]

            async def stream(
                invocations: list[Any] | None = None, signal: Any | None = None
            ) -> AsyncIterator[RealmResponse]:
                for r in responses:
                    yield r

            harness = MvgeHarness(state=state)
            await session.start()
            await harness.run(stream, {"id": "test-model"}, "none")

            handler.assert_called_once()
            call_data = handler.call_args[0][0]
            assert call_data.session_name == write.tome_id

    @pytest.mark.asyncio
    async def test_tome_shutdown_emitted_when_agent_tome_in_state(
        self,
    ) -> None:

        with tempfile.TemporaryDirectory() as tmp:
            factory = TomeHandleFactory(Path(tmp))
            write = factory.create_tome("/tmp")
            read = factory.open_read(write.tome_id)
            runner = RuneRunner()
            handler = MagicMock()
            runner.register_handler(SigilHook.SESSION_SHUTDOWN, handler)

            session = MvgeTome(factory, write, read, runner)
            state = MvgeState(
                system_prompt="test",
                model={"id": "test-model", "name": "Test"},
                invocations=[SummonerRequest(role="user", content="hi")],
                rune_runner=runner,
                agent_tome=session,
            )

            model_obj = Model(
                id="test-model",
                name="Test",
                realm="test",
                base_url="",
                api_key="",
            )
            responses = [
                RealmResponse(
                    model=model_obj,
                    invocation=MvgeResponse(
                        role="assistant",
                        content=[{"type": "text", "text": "Hello"}],
                        stop_reason=StopReason.STOP,
                    ),
                ),
            ]

            async def stream(
                invocations: list[Any] | None = None, signal: Any | None = None
            ) -> AsyncIterator[RealmResponse]:
                for r in responses:
                    yield r

            harness = MvgeHarness(state=state)
            await session.start()
            await harness.run(stream, {"id": "test-model"}, "none")
            await session.shutdown()

            handler.assert_called_once()


class TestPromptSourceIntrospection:
    """prompt_source flows from MvgeState through MvgeLoop to LoopContext."""

    def test_mvge_state_has_prompt_source_default(self) -> None:
        state = MvgeState(system_prompt="test")
        assert state.prompt_source == PromptSource.BUILTIN

    def test_mvge_state_prompt_source_can_be_set(self) -> None:
        state = MvgeState(system_prompt="test", prompt_source=PromptSource.AGENT_MD)
        assert state.prompt_source == PromptSource.AGENT_MD

    def test_loop_context_has_prompt_source_default(self) -> None:
        from mvgeos_core.loop import LoopContext

        ctx = LoopContext(system_prompt="test")
        assert ctx.prompt_source == PromptSource.BUILTIN

    def test_loop_context_prompt_source_from_state(self) -> None:
        from mvgeos_core.loop import LoopContext

        ctx = LoopContext(system_prompt="test", prompt_source=PromptSource.PROJECT_MD)
        assert ctx.prompt_source == PromptSource.PROJECT_MD

    @pytest.mark.asyncio
    async def test_loop_passes_prompt_source_to_context(self) -> None:
        from unittest.mock import patch

        state = MvgeState(
            system_prompt="test",
            prompt_source=PromptSource.AGENT_MD,
            invocations=[SummonerRequest(role="user", content="hi")],
        )
        harness = MvgeHarness(state=state)

        model_obj = Model(
            id="test-model",
            name="Test Model",
            realm="test",
            base_url="https://api.test.com",
            api_key="test",
        )
        responses = [
            RealmResponse(
                model=model_obj,
                invocation=MvgeResponse(
                    role="assistant",
                    content=[{"type": "text", "text": "ok"}],
                    stop_reason=StopReason.STOP,
                ),
            ),
        ]

        captured: dict[str, LoopContext] = {}

        async def fake_run_loop(context, *args, **kwargs):
            captured["context"] = context
            return []

        with patch("mvgeos_agent.harness.harness.run_loop", side_effect=fake_run_loop):

            def stream_fn(inv, sig=None):
                async def gen():
                    for r in responses:
                        yield r

                return gen()

            await harness.run(stream_fn, {"id": "test-model"}, "none")

        assert captured["context"].prompt_source == PromptSource.AGENT_MD


async def _make_stream(
    responses: list[RealmResponse],
):
    async def gen():
        for r in responses:
            yield r

    return gen()


@pytest.mark.asyncio
async def test_loop_streaming_deduplication_and_contemplation() -> None:
    event_bus = EventBus()
    emitted_updates: list[dict[str, Any]] = []

    def on_update(event: MvgeEvent) -> None:
        emitted_updates.append(event.data)

    event_bus.on(MvgeEventType.MESSAGE_UPDATE, on_update)

    state = MvgeState(
        system_prompt="test",
        invocations=[SummonerRequest(role="user", content="hi")],
        event_bus=event_bus,
    )
    harness = MvgeHarness(state=state)
    model_obj = Model(
        id="test-model",
        name="Test Model",
        realm="test",
        base_url="https://api.test.com",
        api_key="test",
    )

    responses = [
        # Contemplation chunk
        RealmResponse(
            model=model_obj,
            invocation=MvgeResponse(
                role="assistant",
                content=[{"type": ContentType.CONTEMPLATION, "text": "Thinking..."}],
                stop_reason=StopReason.PENDING,
            ),
        ),
        # Text chunk 1
        RealmResponse(
            model=model_obj,
            invocation=MvgeResponse(
                role="assistant",
                content=[{"type": ContentType.TEXT, "text": "Hello"}],
                stop_reason=StopReason.PENDING,
            ),
        ),
        # Text chunk 2
        RealmResponse(
            model=model_obj,
            invocation=MvgeResponse(
                role="assistant",
                content=[{"type": ContentType.TEXT, "text": " world!"}],
                stop_reason=StopReason.PENDING,
            ),
        ),
        # Final accumulated summary
        RealmResponse(
            model=model_obj,
            invocation=MvgeResponse(
                role="assistant",
                content=[{"type": ContentType.TEXT, "text": "Hello world!"}],
                stop_reason=StopReason.STOP,
            ),
        ),
    ]

    def stream_fn(invs: list[Any], signal: Any | None = None) -> Any:
        async def gen():
            for r in responses:
                yield r

        return gen()

    await harness.run(stream_fn, {"id": "test-model"}, "none")

    # Verify contemplation chunk was emitted
    contemplation_events = [
        e for e in emitted_updates if e.get("kind") == "contemplation"
    ]
    assert len(contemplation_events) == 1
    assert contemplation_events[0]["text"] == "Thinking..."

    # Verify text chunks were emitted incrementally and NOT duplicated on final summary
    text_events = [
        e for e in emitted_updates if "kind" not in e or e.get("kind") == "text"
    ]
    text_contents = [e.get("text") for e in text_events]
    assert text_contents == ["Hello", " world!"]


@pytest.mark.asyncio
async def test_record_invocation_spell_result_serializes_structured_json() -> None:

    from mvgeos_core.events import (
        MvgeEvent,
        MvgeEventType,
    )

    with tempfile.TemporaryDirectory() as tmp:
        factory = TomeHandleFactory(Path(tmp))
        write = factory.create_tome("/tmp")
        read = factory.open_read(write.tome_id)
        session = MvgeTome(factory, write, read)
        await session.start()

        state = MvgeState(
            system_prompt="test",
            model={"id": "test-provider/test-model", "name": "Test"},
            invocations=[],
            agent_tome=session,
        )
        harness = MvgeHarness(state=state)

        spell_result = SpellResultMessage(
            spell_cast_id="cast_123",
            spell_name="read_file",
            content=[{"type": "text", "text": "file content here"}],
            is_error=False,
        )

        await harness.emit(
            MvgeEvent(
                type=MvgeEventType.MESSAGE_END,
                data={"invocation": spell_result},
            )
        )

        entries = factory.get_entries(write.tome_id)
        msg_entries = [e for e in entries if e.payload.get("role") == "spellResult"]
        assert len(msg_entries) == 1
        entry = msg_entries[0]
        assert entry.payload["role"] == "spellResult"
        assert isinstance(entry.payload["content"], list)
        assert entry.payload["content"] == [
            {"type": "text", "text": "file content here"}
        ]

        tome_file = factory.tome_file(write.tome_id)
        lines = tome_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) >= 2
        for line in lines:
            parsed = json.loads(line)
            if (
                parsed.get("type") == "message"
                and parsed.get("payload", {}).get("role") == "spellResult"
            ):
                assert isinstance(parsed["payload"]["content"], list)
                assert parsed["payload"]["content"] == [
                    {"type": "text", "text": "file content here"}
                ]


class TestMvgeHarnessRecordInvocationParentId:
    @pytest.mark.asyncio
    async def test_record_invocation_supplies_active_leaf_id(self) -> None:

        with tempfile.TemporaryDirectory() as tmp:
            factory = TomeHandleFactory(Path(tmp))
            write = factory.create_tome("/tmp")
            read = factory.open_read(write.tome_id)
            tome = MvgeTome(factory, write, read)
            tome._started = True

            state = MvgeState(
                system_prompt="test",
                prompt_source="system",
                model={"id": "openrouter/meta-llama/llama-3"},
                contemplation_level=ContemplationLevel.OFF,
                spells=[],
                invocations=[],
                agent_tome=tome,
            )
            harness = MvgeHarness(state=state)

            # Record SummonerRequest
            req = SummonerRequest(role="user", content="hello")
            event1 = MvgeEvent(
                type=MvgeEventType.MESSAGE_END,
                data={"invocation": req},
            )
            await harness._record_invocation(event1)

            e1_id = tome.active_leaf_id
            assert e1_id is not None
            entry1 = factory.get_entry(tome.tome_id, e1_id)
            assert entry1 is not None
            assert entry1.parent_id is None

            # Record MvgeResponse
            resp = MvgeResponse(
                role="assistant",
                content=[{"type": ContentType.TEXT, "text": "calling tool"}],
                stop_reason=StopReason.SPELL_USE,
            )
            event2 = MvgeEvent(
                type=MvgeEventType.MESSAGE_END,
                data={"invocation": resp},
            )
            await harness._record_invocation(event2)

            e2_id = tome.active_leaf_id
            assert e2_id is not None
            assert e2_id != e1_id
            entry2 = factory.get_entry(tome.tome_id, e2_id)
            assert entry2 is not None
            assert entry2.parent_id == e1_id

            # Record SpellResultMessage
            result = SpellResultMessage(
                role="spellResult",
                spell_name="bash",
                spell_cast_id="call_1",
                content=[{"type": ContentType.TEXT, "text": "tool output"}],
            )
            event3 = MvgeEvent(
                type=MvgeEventType.MESSAGE_END,
                data={"invocation": result},
            )
            await harness._record_invocation(event3)

            e3_id = tome.active_leaf_id
            assert e3_id is not None
            assert e3_id != e2_id
            entry3 = factory.get_entry(tome.tome_id, e3_id)
            assert entry3 is not None
            assert entry3.parent_id == e2_id

            # Context lookup from e3_id should reconstruct [entry1, entry2, entry3]
            context = factory.get_entries_for_context(tome.tome_id, leaf_id=e3_id)
            assert [e.id for e in context] == [e1_id, e2_id, e3_id]


class TestMvgeHarnessQueueMode:
    """Tests that MvgeLoop's drain callbacks respect MvgeState.queue_mode."""

    @pytest.fixture
    def state(self) -> MvgeState:
        return MvgeState(
            system_prompt="test",
            model={"id": "test-model", "name": "Test"},
            invocations=[SummonerRequest(role="user", content="hi")],
        )

    @pytest.fixture
    def model_obj(self) -> Model:
        return Model(
            id="test-model",
            name="Test Model",
            realm="test",
            base_url="https://api.test.com",
            api_key="test",
        )

    def _make_realm_stream(
        self, responses: list[RealmResponse]
    ) -> Callable[[list[Any], Any], AsyncIterator[RealmResponse]]:
        turn = -1

        def stream_fn(
            invocations: list[Any], signal: Any | None = None
        ) -> AsyncIterator[RealmResponse]:
            nonlocal turn
            turn += 1
            response = responses[min(turn, len(responses) - 1)]

            async def gen() -> AsyncIterator[RealmResponse]:
                yield response

            return gen()

        return stream_fn

    @pytest.mark.asyncio
    async def test_all_mode_drains_entire_steer_queue(
        self, state: MvgeState, model_obj: Model
    ) -> None:

        state.queue_mode = QueueMode.ALL
        state.steer_queue = [
            SummonerRequest(role="user", content="steer one"),
            SummonerRequest(role="user", content="steer two"),
            SummonerRequest(role="user", content="steer three"),
        ]

        responses = [
            RealmResponse(
                model=model_obj,
                invocation=MvgeResponse(
                    role="assistant",
                    content=[{"type": "text", "text": "Hello!"}],
                    stop_reason=StopReason.STOP,
                ),
            )
        ]
        stream_fn = self._make_realm_stream(responses)

        harness = MvgeHarness(state=state)
        await harness.run(stream_fn, {"id": "test-model"}, "none")

        assert len(state.steer_queue) == 0

    @pytest.mark.asyncio
    async def test_one_at_a_time_drains_single_steer_message(
        self, state: MvgeState, model_obj: Model
    ) -> None:

        state.queue_mode = QueueMode.ONE_AT_A_TIME
        state.steer_queue = [
            SummonerRequest(role="user", content="steer one"),
            SummonerRequest(role="user", content="steer two"),
            SummonerRequest(role="user", content="steer three"),
        ]

        responses = [
            RealmResponse(
                model=model_obj,
                invocation=MvgeResponse(
                    role="assistant",
                    content=[{"type": "text", "text": "First"}],
                    stop_reason=StopReason.STOP,
                ),
            ),
            RealmResponse(
                model=model_obj,
                invocation=MvgeResponse(
                    role="assistant",
                    content=[{"type": "text", "text": "Second"}],
                    stop_reason=StopReason.STOP,
                ),
            ),
            RealmResponse(
                model=model_obj,
                invocation=MvgeResponse(
                    role="assistant",
                    content=[{"type": "text", "text": "Third"}],
                    stop_reason=StopReason.STOP,
                ),
            ),
        ]
        stream_fn = self._make_realm_stream(responses)

        harness = MvgeHarness(state=state)
        await harness.run(stream_fn, {"id": "test-model"}, "none")

        assert len(state.steer_queue) == 0

    @pytest.mark.asyncio
    async def test_one_at_a_time_drains_single_followup_message(
        self, state: MvgeState, model_obj: Model
    ) -> None:

        state.queue_mode = QueueMode.ONE_AT_A_TIME
        state.followup_queue = [
            SummonerRequest(role="user", content="followup one"),
            SummonerRequest(role="user", content="followup two"),
            SummonerRequest(role="user", content="followup three"),
        ]

        responses = [
            RealmResponse(
                model=model_obj,
                invocation=MvgeResponse(
                    role="assistant",
                    content=[{"type": "text", "text": "First"}],
                    stop_reason=StopReason.STOP,
                ),
            ),
            RealmResponse(
                model=model_obj,
                invocation=MvgeResponse(
                    role="assistant",
                    content=[{"type": "text", "text": "Second"}],
                    stop_reason=StopReason.STOP,
                ),
            ),
            RealmResponse(
                model=model_obj,
                invocation=MvgeResponse(
                    role="assistant",
                    content=[{"type": "text", "text": "Third"}],
                    stop_reason=StopReason.STOP,
                ),
            ),
        ]
        stream_fn = self._make_realm_stream(responses)

        harness = MvgeHarness(state=state)
        await harness.run(stream_fn, {"id": "test-model"}, "none")

        assert len(state.followup_queue) == 0


class TestMvgeHarnessConfigChangeEvent:
    """Tests for CONFIG_CHANGE event emission on config setters."""

    @pytest.mark.asyncio
    async def test_set_model_and_realm_emits_config_change(self) -> None:
        state = MvgeState(
            system_prompt="test",
            model={"id": "old-model", "name": "Old"},
            invocations=[SummonerRequest(role="user", content="hi")],
        )
        event_bus = EventBus()
        state.event_bus = event_bus

        received: list[MvgeEvent] = []
        event_bus.on(MvgeEventType.CONFIG_CHANGE, lambda e: received.append(e))

        mock_realm = MagicMock()
        mock_realm.__class__.__name__ = "TestRealm"
        mock_model = MagicMock()
        mock_model.id = "new-model"

        harness = MvgeHarness(state=state)
        harness.set_model_and_realm(mock_model, mock_realm)

        await asyncio.sleep(0.01)  # allow create_task to run

        assert len(received) == 1
        assert received[0].type == MvgeEventType.CONFIG_CHANGE
        assert received[0].data["model"] == "new-model"
        assert received[0].data["realm"] == "TestRealm"

    @pytest.mark.asyncio
    async def test_set_model_and_realm_updates_configured_snapshot(self) -> None:
        state = MvgeState(
            system_prompt="test",
            model={"id": "old-model", "name": "Old"},
            invocations=[SummonerRequest(role="user", content="hi")],
        )

        mock_realm = MagicMock()
        mock_realm.__class__.__name__ = "TestRealm"
        mock_model = MagicMock()
        mock_model.id = "new-model"

        harness = MvgeHarness(state=state)
        harness.set_model_and_realm(mock_model, mock_realm)

        snap = harness.snapshot
        assert snap.configured_model == "new-model"
        assert snap.configured_realm == "TestRealm"


class TestMvgeHarnessExecutionSnapshot:
    """Tests for the ExecutionSnapshot read-only observability property."""

    @pytest.mark.asyncio
    async def test_snapshot_reflects_configured_and_captured(self) -> None:
        state = MvgeState(
            system_prompt="test",
            model={"id": "configured-model", "name": "Configured"},
            invocations=[SummonerRequest(role="user", content="hi")],
        )
        mock_realm = MagicMock()
        mock_realm.__class__.__name__ = "TestRealm"
        model_obj = Model(
            id="configured-model",
            name="Configured",
            realm="test",
            base_url="https://api.test.com",
            api_key="test",
        )

        harness = MvgeHarness(state=state, realm=mock_realm, model=model_obj)

        # Before run: configured set, captured None
        snap = harness.snapshot
        assert snap.configured_model == "configured-model"
        assert snap.captured_model is None
        assert snap.configured_realm == "TestRealm"
        assert snap.captured_realm is None
        assert snap.active_spell_count == 0
        assert snap.contemplation_budget is None

        # After run: captured should be set
        model_obj = Model(
            id="configured-model",
            name="Configured",
            realm="test",
            base_url="https://api.test.com",
            api_key="test",
        )
        responses = [
            RealmResponse(
                model=model_obj,
                invocation=MvgeResponse(
                    role="assistant",
                    content=[{"type": "text", "text": "ok"}],
                    stop_reason=StopReason.STOP,
                ),
            ),
        ]

        async def stream(invs, sig=None):
            for r in responses:
                yield r

        await harness.run(stream, {"id": "configured-model"}, "none")

        snap = harness.snapshot
        assert snap.captured_model == "configured-model"
        assert snap.captured_realm == "TestRealm"

    @pytest.mark.asyncio
    async def test_snapshot_reflects_configured_when_not_run(self) -> None:
        state = MvgeState(
            system_prompt="test",
            model={"id": "base-model", "name": "Base"},
            invocations=[SummonerRequest(role="user", content="hi")],
        )
        model_obj = Model(
            id="base-model",
            name="Base",
            realm="test",
            base_url="https://api.test.com",
            api_key="test",
        )

        harness = MvgeHarness(state=state, model=model_obj)

        # Test that snapshot reflects configured model correctly before run
        snap = harness.snapshot
        assert snap.configured_model == "base-model"
        assert snap.captured_model is None


def _sigil_stop_model() -> Model:
    return Model(
        id="test-model",
        name="Test",
        realm="test",
        base_url="",
        api_key="<redacted>",
    )


def _sigil_stop_response(model_obj: Model) -> RealmResponse:
    return RealmResponse(
        model=model_obj,
        invocation=MvgeResponse(
            role="assistant",
            content=[{"type": "text", "text": "Hello"}],
            stop_reason=StopReason.STOP,
        ),
    )


class TestSigilPayloadContracts:
    """Observable proof for the payload-ownership fixes (issue #163)."""

    @pytest.mark.asyncio
    async def test_input_transform_rewrites_summoner_content(self) -> None:
        """INPUT is a real transform hook: a rune rewriting the content
        changes what the turn runs on."""

        state = MvgeState(
            system_prompt="test",
            model={"id": "test-model", "name": "Test"},
            invocations=[SummonerRequest(role="user", content="original")],
        )
        runner = RuneRunner()

        def rewrite(data: InputData) -> InputData:
            assert isinstance(data, InputData)
            data.content = "rewritten by rune"
            return data

        runner.register_handler(SigilHook.INPUT, rewrite)
        state.rune_runner = runner
        model_obj = _sigil_stop_model()

        async def stream(
            invocations: list[Any] | None = None, signal: Any | None = None
        ) -> AsyncIterator[RealmResponse]:
            yield _sigil_stop_response(model_obj)

        harness = MvgeHarness(state=state)
        await harness.run(stream, {"id": "test-model"}, "none")

        assert state.invocations[0].content == "rewritten by rune"

    @pytest.mark.asyncio
    async def test_input_readonly_handler_leaves_content(self) -> None:
        """A rune that only observes INPUT (the session-title pattern)
        changes nothing."""

        state = MvgeState(
            system_prompt="test",
            model={"id": "test-model", "name": "Test"},
            invocations=[SummonerRequest(role="user", content="original")],
        )
        runner = RuneRunner()
        seen: list[Any] = []
        runner.register_handler(SigilHook.INPUT, lambda data: seen.append(data))
        state.rune_runner = runner
        model_obj = _sigil_stop_model()

        async def stream(
            invocations: list[Any] | None = None, signal: Any | None = None
        ) -> AsyncIterator[RealmResponse]:
            yield _sigil_stop_response(model_obj)

        harness = MvgeHarness(state=state)
        await harness.run(stream, {"id": "test-model"}, "none")

        assert len(seen) == 1
        assert isinstance(seen[0], InputData)
        assert state.invocations[0].content == "original"

    @pytest.mark.asyncio
    async def test_prepare_next_turn_accepts_documented_dataclass(self) -> None:
        """A rune returning PrepareNextTurnData is applied, not discarded.

        The handler sets max_turns=1 on the documented dataclass; the loop
        honors it on the following turn by raising MaxTurnsExceededError.
        Under the old code the return was silently discarded and no error
        was raised.
        """

        state = MvgeState(
            system_prompt="test",
            model={"id": "test-model", "name": "Test"},
            invocations=[SummonerRequest(role="user", content="hi")],
            followup_queue=[
                SummonerRequest(role="user", content="one"),
                SummonerRequest(role="user", content="two"),
                SummonerRequest(role="user", content="three"),
            ],
        )
        runner = RuneRunner()
        seen: list[Any] = []

        def on_prepare(data: PrepareNextTurnData) -> PrepareNextTurnData:
            seen.append(data)
            data.max_turns = 1
            return data

        runner.register_handler(SigilHook.PREPARE_NEXT_TURN, on_prepare)
        state.rune_runner = runner
        model_obj = _sigil_stop_model()

        async def stream(
            invocations: list[Any] | None = None, signal: Any | None = None
        ) -> AsyncIterator[RealmResponse]:
            yield _sigil_stop_response(model_obj)

        harness = MvgeHarness(state=state)
        with pytest.raises(MaxTurnsExceededError):
            await harness.run(stream, {"id": "test-model"}, "none")

        assert len(seen) == 1
        assert isinstance(seen[0], PrepareNextTurnData)
        assert seen[0].system_prompt == "test"

    @pytest.mark.asyncio
    async def test_context_transform_accepts_documented_dataclass_return(
        self,
    ) -> None:
        """A rune returning ContextTransformData no longer crashes the turn."""

        state = MvgeState(
            system_prompt="test",
            model={"id": "test-model", "name": "Test"},
            invocations=[SummonerRequest(role="user", content="original")],
        )
        runner = RuneRunner()

        def on_transform(data: list) -> ContextTransformData:
            assert isinstance(data, list)
            return ContextTransformData(
                invocations=[SummonerRequest(role="user", content="transformed")]
            )

        runner.register_handler(SigilHook.CONTEXT_TRANSFORM, on_transform)
        state.rune_runner = runner
        model_obj = _sigil_stop_model()
        stream_calls: list[list[Any]] = []

        async def stream(
            invocations: list[Any] | None = None, signal: Any | None = None
        ) -> AsyncIterator[RealmResponse]:
            stream_calls.append(list(invocations or []))
            yield _sigil_stop_response(model_obj)

        harness = MvgeHarness(state=state)
        await harness.run(stream, {"id": "test-model"}, "none")

        assert any(
            getattr(inv, "content", None) == "transformed" for inv in stream_calls[0]
        )

    @pytest.mark.asyncio
    async def test_after_provider_response_carries_mana_used(self) -> None:
        """The AFTER_PROVIDER_RESPONSE payload no longer drops mana_used."""

        state = MvgeState(
            system_prompt="test",
            model={"id": "test-model", "name": "Test"},
            invocations=[SummonerRequest(role="user", content="hi")],
        )
        runner = RuneRunner()
        seen: list[Any] = []
        runner.register_handler(
            SigilHook.AFTER_PROVIDER_RESPONSE, lambda data: seen.append(data)
        )
        state.rune_runner = runner
        model_obj = _sigil_stop_model()

        async def stream(
            invocations: list[Any] | None = None, signal: Any | None = None
        ) -> AsyncIterator[RealmResponse]:
            yield _sigil_stop_response(model_obj)

        harness = MvgeHarness(state=state)
        await harness.run(stream, {"id": "test-model"}, "none")

        assert len(seen) == 1
        assert isinstance(seen[0], AfterProviderResponseData)
        assert isinstance(seen[0].mana_used, int)

    @pytest.mark.asyncio
    async def test_apply_prepare_next_turn_coerces_enums(self) -> None:
        """The dataclass's wire strings become the loop's enum values."""
        context = LoopContext(system_prompt="a")
        data = PrepareNextTurnData(
            system_prompt="b",
            contemplation_level="high",
            queue_mode="one-at-a-time",
            prompt_source="builtin",
        )
        updated = _apply_prepare_next_turn(data, context)
        assert updated.system_prompt == "b"
        assert updated.contemplation_level == ContemplationLevel.HIGH
        assert updated.queue_mode == QueueMode.ONE_AT_A_TIME
        assert updated.prompt_source == PromptSource.BUILTIN
