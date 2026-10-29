from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from mvge.agent.protocol import MvgeAgent, ReloadResult
from mvge.cli.commands.dispatcher import CliCommandDispatcher
from mvge.core.channel import Model
from mvge.core.events import QueueMode
from mvge.provider.model_registry import ModelRegistry
from mvge.runes import RegisteredCommand


@pytest.fixture
def mock_agent() -> MagicMock:
    agent = MagicMock(spec=MvgeAgent)
    agent.tome_id = "tome-12345678"
    agent.model_id = "nvidia/nemotron"
    agent.enabled_spells = ["read", "write"]
    agent.available_spells = ["read", "write", "bash"]
    agent.registered_providers = ["openrouter"]
    agent.queue_mode = QueueMode.ONE_AT_A_TIME
    agent.switch_model = AsyncMock()
    agent.reset_session = AsyncMock()
    agent.set_enabled_spells = MagicMock()
    agent.steer = MagicMock()
    agent.follow_up = MagicMock()
    return agent


@pytest.fixture
def mock_registry() -> MagicMock:
    registry = MagicMock(spec=ModelRegistry)
    fake_model = Model(
        id="nvidia/nemotron",
        name="Nemotron",
        realm="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="",
        max_completion_mana=0,
        context_window=1000,
        max_tokens=1000,
        supported_parameters=[],
        is_free=True,
    )
    registry.list_all.return_value = [fake_model]
    registry.get.side_effect = lambda mid: (
        fake_model if mid == "nvidia/nemotron" else None
    )
    registry.refresh = AsyncMock(return_value=5)
    return registry


@pytest.mark.asyncio
async def test_help_command(mock_agent: MagicMock, mock_registry: MagicMock) -> None:
    output: list[str] = []
    dispatcher = CliCommandDispatcher(mock_agent, mock_registry, out=output.append)

    should_exit = await dispatcher.dispatch("/help")
    assert should_exit is False
    assert any("Available commands" in line for line in output)


@pytest.mark.asyncio
async def test_quit_command(mock_agent: MagicMock, mock_registry: MagicMock) -> None:
    dispatcher = CliCommandDispatcher(mock_agent, mock_registry)
    assert await dispatcher.dispatch("/quit") is True
    assert await dispatcher.dispatch("/exit") is True


@pytest.mark.asyncio
async def test_model_list(mock_agent: MagicMock, mock_registry: MagicMock) -> None:
    output: list[str] = []
    dispatcher = CliCommandDispatcher(mock_agent, mock_registry, out=output.append)

    should_exit = await dispatcher.dispatch("/model")
    assert should_exit is False
    assert any("nvidia/nemotron" in line for line in output)


@pytest.mark.asyncio
async def test_model_switch_success(
    mock_agent: MagicMock, mock_registry: MagicMock
) -> None:
    output: list[str] = []
    dispatcher = CliCommandDispatcher(mock_agent, mock_registry, out=output.append)

    should_exit = await dispatcher.dispatch("/model nvidia/nemotron")
    assert should_exit is False
    mock_agent.switch_model.assert_awaited_once_with("nvidia/nemotron")
    assert any("Model switched: nvidia/nemotron" in line for line in output)


@pytest.mark.asyncio
async def test_model_switch_unknown(
    mock_agent: MagicMock, mock_registry: MagicMock
) -> None:
    output: list[str] = []
    dispatcher = CliCommandDispatcher(mock_agent, mock_registry, out=output.append)

    should_exit = await dispatcher.dispatch("/model unknown/model")
    assert should_exit is False
    mock_agent.switch_model.assert_not_awaited()
    assert any("Unknown model: unknown/model" in line for line in output)


@pytest.mark.asyncio
async def test_refresh_models(mock_agent: MagicMock, mock_registry: MagicMock) -> None:
    output: list[str] = []
    dispatcher = CliCommandDispatcher(mock_agent, mock_registry, out=output.append)

    should_exit = await dispatcher.dispatch("/refresh-models")
    assert should_exit is False
    mock_registry.refresh.assert_awaited_once_with(force_refresh=True)
    assert any("5 new models" in line for line in output)


@pytest.mark.asyncio
async def test_new_session(mock_agent: MagicMock, mock_registry: MagicMock) -> None:
    output: list[str] = []
    dispatcher = CliCommandDispatcher(mock_agent, mock_registry, out=output.append)

    should_exit = await dispatcher.dispatch("/new")
    assert should_exit is False
    mock_agent.reset_session.assert_awaited_once_with(resume_tome_id=None)
    assert any("New tome: tome-12345678" in line for line in output)


@pytest.mark.asyncio
async def test_resume_session(mock_agent: MagicMock, mock_registry: MagicMock) -> None:
    output: list[str] = []
    dispatcher = CliCommandDispatcher(mock_agent, mock_registry, out=output.append)

    should_exit = await dispatcher.dispatch("/resume some/path.jsonl")
    assert should_exit is False
    mock_agent.reset_session.assert_awaited_once_with(resume_tome_id="some/path.jsonl")


@pytest.mark.asyncio
async def test_spells_list(mock_agent: MagicMock, mock_registry: MagicMock) -> None:
    output: list[str] = []
    dispatcher = CliCommandDispatcher(mock_agent, mock_registry, out=output.append)

    should_exit = await dispatcher.dispatch("/spells")
    assert should_exit is False
    assert any("read" in line for line in output)


@pytest.mark.asyncio
async def test_spells_set(mock_agent: MagicMock, mock_registry: MagicMock) -> None:
    output: list[str] = []
    dispatcher = CliCommandDispatcher(mock_agent, mock_registry, out=output.append)

    should_exit = await dispatcher.dispatch("/spells bash,read")
    assert should_exit is False
    mock_agent.set_enabled_spells.assert_called_once_with(["bash", "read"])
    assert any("Spells set to: bash, read" in line for line in output)


@pytest.mark.asyncio
async def test_mode_toggle(mock_agent: MagicMock, mock_registry: MagicMock) -> None:
    dispatcher = CliCommandDispatcher(mock_agent, mock_registry)

    mock_agent.queue_mode = QueueMode.ONE_AT_A_TIME
    await dispatcher.dispatch("/mode")
    assert mock_agent.queue_mode == QueueMode.ALL

    await dispatcher.dispatch("/mode")
    assert mock_agent.queue_mode == QueueMode.ONE_AT_A_TIME


@pytest.mark.asyncio
async def test_steer_and_followup(
    mock_agent: MagicMock, mock_registry: MagicMock
) -> None:
    dispatcher = CliCommandDispatcher(mock_agent, mock_registry)

    await dispatcher.dispatch("/steer focus on bug")
    mock_agent.steer.assert_called_once_with("focus on bug")

    await dispatcher.dispatch("/followup run tests next")
    mock_agent.follow_up.assert_called_once_with("run tests next")


@pytest.mark.asyncio
async def test_unknown_command(mock_agent: MagicMock, mock_registry: MagicMock) -> None:
    output: list[str] = []
    dispatcher = CliCommandDispatcher(mock_agent, mock_registry, out=output.append)

    should_exit = await dispatcher.dispatch("/unknown")
    assert should_exit is False
    assert any("Unknown command: /unknown" in line for line in output)


@pytest.mark.asyncio
async def test_model_switch_displays_contemplation_levels(
    mock_agent: MagicMock, mock_registry: MagicMock
) -> None:
    output: list[str] = []
    mock_registry.get_supported_contemplation_levels.return_value = [
        "none",
        "low",
        "medium",
        "high",
        "x-high",
    ]
    dispatcher = CliCommandDispatcher(mock_agent, mock_registry, out=output.append)

    await dispatcher.dispatch("/model nvidia/nemotron")
    assert any("Supported contemplation levels" in line for line in output)
    assert any("x-high" in line for line in output)


@pytest.mark.asyncio
async def test_reload_command_renders_reloaded_outcome(
    mock_agent: MagicMock, mock_registry: MagicMock
) -> None:
    output: list[str] = []
    mock_agent.reload = AsyncMock(
        return_value=ReloadResult(
            ok=True,
            message="Reload complete: prompt unchanged, 2 spell(s) (unchanged)",
        )
    )
    dispatcher = CliCommandDispatcher(mock_agent, mock_registry, out=output.append)

    should_exit = await dispatcher.dispatch("/reload")
    assert should_exit is False
    mock_agent.reload.assert_awaited_once()
    assert any("Reload complete" in line for line in output)


@pytest.mark.asyncio
async def test_reload_command_queued_renders_queued_outcome(
    mock_agent: MagicMock, mock_registry: MagicMock
) -> None:
    output: list[str] = []
    mock_agent.reload = AsyncMock(
        return_value=ReloadResult(ok=True, queued=True, message="Reload queued")
    )
    dispatcher = CliCommandDispatcher(mock_agent, mock_registry, out=output.append)

    should_exit = await dispatcher.dispatch("/reload")
    assert should_exit is False
    assert any("Reload queued" in line for line in output)


@pytest.mark.asyncio
async def test_rune_command_renders_handler_output(
    mock_agent: MagicMock, mock_registry: MagicMock
) -> None:
    output: list[str] = []
    mock_agent.get_registered_commands = MagicMock(
        return_value=[
            RegisteredCommand(
                name="renderprobe",
                description="Probe rune command",
                handler=lambda args: f"render-probe says: {args}",
            )
        ]
    )
    dispatcher = CliCommandDispatcher(mock_agent, mock_registry, out=output.append)

    should_exit = await dispatcher.dispatch("/renderprobe hello")
    assert should_exit is False
    assert any("render-probe says: hello" in line for line in output)


@pytest.mark.asyncio
async def test_contemplation_command(
    mock_agent: MagicMock, mock_registry: MagicMock
) -> None:
    output: list[str] = []
    mock_agent.contemplation_level = "medium"
    mock_agent.set_contemplation_level = AsyncMock()
    mock_registry.get_supported_contemplation_levels.return_value = [
        "none",
        "low",
        "high",
    ]
    dispatcher = CliCommandDispatcher(mock_agent, mock_registry, out=output.append)

    # Inspect current
    await dispatcher.dispatch("/contemplation")
    assert any("Current contemplation level: medium" in line for line in output)
    assert any(
        "Supported contemplation levels: none, low, high" in line for line in output
    )

    # Set new
    await dispatcher.dispatch("/contemplation high")
    mock_agent.set_contemplation_level.assert_awaited_once_with("high")
