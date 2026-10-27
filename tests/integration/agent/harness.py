import pytest

from mvge.agent.harness import MvgeHarness
from mvge.agent.harness._state import MvgeState
from mvge.core.channel import (
    MvgeResponse,
    StopReason,
)
from mvge.core.events import ContemplationLevel
from mvge.core.invocations import SummonerRequest


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
        from mvge.core.channel import (
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
