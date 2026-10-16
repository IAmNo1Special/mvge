import pytest
from mvgeos_core.channel import (
    MvgeResponse,
    StopReason,
)
from mvgeos_core.events import ContemplationLevel
from mvgeos_core.invocations import SummonerRequest

from mvgeos_agent.harness import MvgeHarness
from mvgeos_agent.harness._state import MvgeState


@pytest.mark.asyncio
async def test_full_harness_loop_integration() -> None:
    state = MvgeState(
        system_prompt="Test prompt",
        spells=[],
        invocations=[SummonerRequest(role="user", content="Hello")],
        contemplation_level=ContemplationLevel.MEDIUM,
    )
    harness = MvgeHarness(state=state)

    async def mock_stream_fn(invocations, signal=None):
        from mvgeos_core.channel import (
            Model,
            RealmResponse,
        )

        dummy_model = Model(
            id="test-model",
            name="Test Model",
            realm="test",
            base_url="http://localhost",
            api_key="test-key",
        )
        yield RealmResponse(
            model=dummy_model,
            invocation=MvgeResponse(
                role="assistant",
                content=[{"type": "text", "text": "Hi from mock stream"}],
                stop_reason=StopReason.STOP,
            ),
        )

    response = await harness.run(
        stream_fn=mock_stream_fn,
        model={"id": "test-model"},
    )

    assert isinstance(response, MvgeResponse)
    assert response.content == [{"type": "text", "text": "Hi from mock stream"}]
