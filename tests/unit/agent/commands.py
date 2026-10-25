from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from mvge.agent.commands import (
    SLASH_COMMANDS,
    CommandAction,
    CommandDispatcher,
    CommandOutcome,
)
from mvge.agent.protocol import MvgeAgent
from mvge.core.channel import Model
from mvge.core.events import QueueMode
from mvge.provider.model_registry import ModelRegistry


def _create_mock_agent(
    tome_id: str = "tome-123",
    model_id: str = "nvidia/nemotron-3-ultra-550b-a55b:free",
    queue_mode: QueueMode = QueueMode.ONE_AT_A_TIME,
    enabled_spells: list[str] | None = None,
    available_spells: list[str] | None = None,
    registered_providers: list[str] | None = None,
) -> MagicMock:
    agent = MagicMock(spec=MvgeAgent)
    agent.tome_id = tome_id
    agent.model_id = model_id
    agent.queue_mode = queue_mode
    agent.enabled_spells = (
        enabled_spells if enabled_spells is not None else ["read", "write"]
    )
    agent.available_spells = (
        available_spells
        if available_spells is not None
        else ["read", "write", "bash", "edit"]
    )
    agent.registered_providers = (
        registered_providers if registered_providers is not None else ["openrouter"]
    )
    agent.switch_model = AsyncMock()
    agent.set_enabled_spells = MagicMock()
    agent.steer = MagicMock()
    agent.follow_up = MagicMock()
    agent.reset_session = AsyncMock()
    return agent


def _create_mock_registry(models: list[Model] | None = None) -> MagicMock:
    reg = MagicMock(spec=ModelRegistry)
    m1 = Model(
        id="google/gemini-2.5-flash",
        name="Gemini 2.5 Flash",
        realm="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="sk-test",
        is_free=True,
    )
    m2 = Model(
        id="anthropic/claude-3.5-sonnet",
        name="Claude 3.5 Sonnet",
        realm="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="sk-test",
        is_free=False,
    )
    model_list = models if models is not None else [m1, m2]
    reg.list_all.return_value = model_list
    reg.get.side_effect = lambda mid: next((m for m in model_list if m.id == mid), None)
    reg.refresh = AsyncMock(return_value=5)
    return reg


@pytest.mark.asyncio
async def test_slash_commands_catalog() -> None:
    assert "/help" in SLASH_COMMANDS
    assert "/quit" in SLASH_COMMANDS
    assert "/model" in SLASH_COMMANDS
    assert "/spells" in SLASH_COMMANDS


@pytest.mark.asyncio
async def test_help_command() -> None:
    agent = _create_mock_agent()
    dispatcher = CommandDispatcher(agent)
    outcome = await dispatcher.dispatch("/help")
    assert outcome.action == CommandAction.HELP
    assert outcome.command == "/help"
    assert not outcome.should_exit
    assert outcome.data["commands"] == SLASH_COMMANDS
    assert "Available commands" in outcome.message


@pytest.mark.asyncio
async def test_quit_and_exit_commands() -> None:
    agent = _create_mock_agent()
    dispatcher = CommandDispatcher(agent)

    outcome_quit = await dispatcher.dispatch("/quit")
    assert outcome_quit.action == CommandAction.EXIT
    assert outcome_quit.should_exit is True

    outcome_exit = await dispatcher.dispatch("/exit")
    assert outcome_exit.action == CommandAction.EXIT
    assert outcome_exit.should_exit is True


@pytest.mark.asyncio
async def test_tome_command() -> None:
    agent = _create_mock_agent()
    dispatcher = CommandDispatcher(agent)
    outcome = await dispatcher.dispatch("/tome")
    assert outcome.action == CommandAction.TOME_INFO
    assert outcome.data["tome_id"] == "tome-123"
    assert outcome.data["model_id"] == "nvidia/nemotron-3-ultra-550b-a55b:free"
    assert outcome.data["enabled_spells"] == ["read", "write"]
    assert "tome-123" in outcome.message


@pytest.mark.asyncio
async def test_models_list_all() -> None:
    agent = _create_mock_agent()
    reg = _create_mock_registry()
    dispatcher = CommandDispatcher(agent, registry=reg)
    outcome = await dispatcher.dispatch("/models")
    assert outcome.action == CommandAction.MODELS_LISTED
    assert len(outcome.data["models"]) == 2
    assert "google/gemini-2.5-flash" in outcome.message


@pytest.mark.asyncio
async def test_models_list_free_only() -> None:
    agent = _create_mock_agent()
    reg = _create_mock_registry()
    dispatcher = CommandDispatcher(agent, registry=reg)
    outcome = await dispatcher.dispatch("/models --free")
    assert outcome.action == CommandAction.MODELS_LISTED
    assert len(outcome.data["models"]) == 1
    assert outcome.data["models"][0].id == "google/gemini-2.5-flash"


@pytest.mark.asyncio
async def test_model_switch_success() -> None:
    agent = _create_mock_agent()
    reg = _create_mock_registry()
    dispatcher = CommandDispatcher(agent, registry=reg)
    outcome = await dispatcher.dispatch("/model google/gemini-2.5-flash")
    assert outcome.action == CommandAction.MODEL_SWITCHED
    assert outcome.data["model_id"] == "google/gemini-2.5-flash"
    agent.switch_model.assert_awaited_once_with("google/gemini-2.5-flash")
    assert "Model switched: google/gemini-2.5-flash" in outcome.message


@pytest.mark.asyncio
async def test_model_switch_unknown_model() -> None:
    agent = _create_mock_agent()
    reg = _create_mock_registry()
    dispatcher = CommandDispatcher(agent, registry=reg)
    outcome = await dispatcher.dispatch("/model non-existent-model")
    assert outcome.action == CommandAction.ERROR
    assert "Unknown model" in outcome.message
    agent.switch_model.assert_not_called()


@pytest.mark.asyncio
async def test_model_switch_failure() -> None:
    agent = _create_mock_agent()
    agent.switch_model.side_effect = RuntimeError("Switch failed")
    reg = _create_mock_registry()
    dispatcher = CommandDispatcher(agent, registry=reg)
    outcome = await dispatcher.dispatch("/model google/gemini-2.5-flash")
    assert outcome.action == CommandAction.ERROR
    assert "Switch failed" in outcome.message


@pytest.mark.asyncio
async def test_refresh_models_success() -> None:
    agent = _create_mock_agent()
    reg = _create_mock_registry()
    dispatcher = CommandDispatcher(agent, registry=reg)
    outcome = await dispatcher.dispatch("/refresh-models")
    assert outcome.action == CommandAction.CATALOG_REFRESHED
    assert outcome.data["new_models_count"] == 5
    reg.refresh.assert_awaited_once_with(force_refresh=True)


@pytest.mark.asyncio
async def test_refresh_models_failure() -> None:
    agent = _create_mock_agent()
    reg = _create_mock_registry()
    reg.refresh.side_effect = ConnectionError("Network down")
    dispatcher = CommandDispatcher(agent, registry=reg)
    outcome = await dispatcher.dispatch("/refresh-models")
    assert outcome.action == CommandAction.ERROR
    assert "Failed to refresh models: Network down" in outcome.message


@pytest.mark.asyncio
async def test_spells_list() -> None:
    agent = _create_mock_agent()
    dispatcher = CommandDispatcher(agent)
    outcome = await dispatcher.dispatch("/spells")
    assert outcome.action == CommandAction.SPELLS_LISTED
    assert outcome.data["enabled_spells"] == ["read", "write"]
    assert outcome.data["available_spells"] == ["read", "write", "bash", "edit"]
    assert "read, write" in outcome.message


@pytest.mark.asyncio
async def test_spells_update() -> None:
    agent = _create_mock_agent()
    dispatcher = CommandDispatcher(agent)
    outcome = await dispatcher.dispatch("/spells bash, grep")
    assert outcome.action == CommandAction.SPELLS_UPDATED
    assert outcome.data["enabled_spells"] == ["bash", "grep"]
    agent.set_enabled_spells.assert_called_once_with(["bash", "grep"])
    assert "Spells set to: bash, grep" in outcome.message


@pytest.mark.asyncio
async def test_mode_toggle() -> None:
    agent = _create_mock_agent(queue_mode=QueueMode.ONE_AT_A_TIME)
    dispatcher = CommandDispatcher(agent)
    outcome = await dispatcher.dispatch("/mode")
    assert outcome.action == CommandAction.QUEUE_MODE_CHANGED
    assert outcome.data["queue_mode"] == QueueMode.ALL
    assert agent.queue_mode == QueueMode.ALL

    # Toggle back
    outcome2 = await dispatcher.dispatch("/mode")
    assert outcome2.action == CommandAction.QUEUE_MODE_CHANGED
    assert outcome2.data["queue_mode"] == QueueMode.ONE_AT_A_TIME
    assert agent.queue_mode == QueueMode.ONE_AT_A_TIME


@pytest.mark.asyncio
async def test_steer_with_and_without_args() -> None:
    agent = _create_mock_agent()
    dispatcher = CommandDispatcher(agent)

    outcome = await dispatcher.dispatch("/steer focus on tests")
    assert outcome.action == CommandAction.STEERING_QUEUED
    assert outcome.data["message"] == "focus on tests"
    agent.steer.assert_called_once_with("focus on tests")

    outcome_empty = await dispatcher.dispatch("/steer")
    assert outcome_empty.action == CommandAction.ERROR


@pytest.mark.asyncio
async def test_followup_with_and_without_args() -> None:
    agent = _create_mock_agent()
    dispatcher = CommandDispatcher(agent)

    outcome = await dispatcher.dispatch("/followup clean up later")
    assert outcome.action == CommandAction.FOLLOWUP_QUEUED
    assert outcome.data["message"] == "clean up later"
    agent.follow_up.assert_called_once_with("clean up later")

    outcome_empty = await dispatcher.dispatch("/followup")
    assert outcome_empty.action == CommandAction.INFO


@pytest.mark.asyncio
async def test_new_session_success() -> None:
    agent = _create_mock_agent(tome_id="new-tome-456")
    dispatcher = CommandDispatcher(agent)
    outcome = await dispatcher.dispatch("/new")
    assert outcome.action == CommandAction.SESSION_RESET
    assert outcome.data["tome_id"] == "new-tome-456"
    agent.reset_session.assert_awaited_once_with(resume_tome_id=None)


@pytest.mark.asyncio
async def test_new_session_failure() -> None:
    agent = _create_mock_agent()
    agent.reset_session.side_effect = RuntimeError("Reset failed")
    dispatcher = CommandDispatcher(agent)
    outcome = await dispatcher.dispatch("/new")
    assert outcome.action == CommandAction.ERROR
    assert "Reset failed" in outcome.message


@pytest.mark.asyncio
async def test_resume_session_success() -> None:
    agent = _create_mock_agent(tome_id="resumed-tome-789")
    dispatcher = CommandDispatcher(agent)
    outcome = await dispatcher.dispatch("/resume path/to/tome.jsonl")
    assert outcome.action == CommandAction.SESSION_RESUMED
    assert outcome.data["tome_id"] == "resumed-tome-789"
    assert outcome.data["path"] == "path/to/tome.jsonl"
    agent.reset_session.assert_awaited_once_with(resume_tome_id="path/to/tome.jsonl")


@pytest.mark.asyncio
async def test_resume_session_missing_arg() -> None:
    agent = _create_mock_agent()
    dispatcher = CommandDispatcher(agent)
    outcome = await dispatcher.dispatch("/resume")
    assert outcome.action == CommandAction.ERROR
    assert "Usage: /resume" in outcome.message


@pytest.mark.asyncio
async def test_unknown_command() -> None:
    agent = _create_mock_agent()
    agent.get_skills_catalog = MagicMock(return_value=[])
    dispatcher = CommandDispatcher(agent)
    outcome = await dispatcher.dispatch("/foobar")
    assert outcome.action == CommandAction.ERROR
    assert "Unknown command: /foobar" in outcome.message


@pytest.mark.asyncio
async def test_skills_listed_empty() -> None:
    agent = _create_mock_agent()
    agent.get_skills_catalog = MagicMock(return_value=[])
    dispatcher = CommandDispatcher(agent)
    outcome = await dispatcher.dispatch("/skills")
    assert outcome.action == CommandAction.SKILLS_LISTED
    assert "No skills registered" in outcome.message


@pytest.mark.asyncio
async def test_skills_listed_with_skills() -> None:
    agent = _create_mock_agent()
    agent.get_skills_catalog = MagicMock(
        return_value=[
            {
                "name": "pdf-tool",
                "description": "Extracts PDFs",
                "scope": "project",
                "path": "/path/to/pdf",
            }
        ]
    )
    dispatcher = CommandDispatcher(agent)
    outcome = await dispatcher.dispatch("/skills")
    assert outcome.action == CommandAction.SKILLS_LISTED
    assert "pdf-tool" in outcome.message
    assert "Extracts PDFs" in outcome.message


@pytest.mark.asyncio
async def test_skill_command_missing_arg() -> None:
    agent = _create_mock_agent()
    dispatcher = CommandDispatcher(agent)
    outcome = await dispatcher.dispatch("/skill")
    assert outcome.action == CommandAction.ERROR
    assert "Usage: /skill" in outcome.message


@pytest.mark.asyncio
async def test_skill_command_activates_and_steers() -> None:
    agent = _create_mock_agent()
    agent.activate_skill = AsyncMock(
        return_value='<skill_content name="pdf-tool">PDF tools</skill_content>'
    )
    dispatcher = CommandDispatcher(agent)
    outcome = await dispatcher.dispatch("/skill pdf-tool process report.pdf")
    assert outcome.action == CommandAction.SKILL_ACTIVATED
    assert outcome.data["skill"] == "pdf-tool"
    assert outcome.data["args"] == "process report.pdf"
    assert "Queued instruction: process report.pdf" in outcome.message
    agent.activate_skill.assert_awaited_once_with("pdf-tool")
    agent.steer.assert_called_once()
    assert "process report.pdf" in agent.steer.call_args[0][0]


@pytest.mark.asyncio
async def test_dynamic_skill_slash_command() -> None:
    agent = _create_mock_agent()
    agent.get_skills_catalog = MagicMock(
        return_value=[
            {
                "name": "hi",
                "description": "Says hi",
                "scope": "project",
                "path": "/skills/hi",
            }
        ]
    )
    agent.activate_skill = AsyncMock(
        return_value='<skill_content name="hi">Hi instructions</skill_content>'
    )
    dispatcher = CommandDispatcher(agent)

    # Dispatch /hi directly
    outcome = await dispatcher.dispatch("/hi")
    assert outcome.action == CommandAction.SKILL_ACTIVATED
    assert outcome.data["skill"] == "hi"
    assert "Skill 'hi' activated." in outcome.message
    agent.activate_skill.assert_awaited_once_with("hi")
    agent.steer.assert_called_once_with(
        '<skill_content name="hi">Hi instructions</skill_content>'
    )


@pytest.mark.asyncio
async def test_dynamic_skill_slash_command_with_extra_args() -> None:
    agent = _create_mock_agent()
    agent.get_skills_catalog = MagicMock(
        return_value=[
            {
                "name": "hi",
                "description": "Says hi",
                "scope": "project",
                "path": "/skills/hi",
            }
        ]
    )
    agent.activate_skill = AsyncMock(
        return_value='<skill_content name="hi">Hi instructions</skill_content>'
    )
    dispatcher = CommandDispatcher(agent)

    # Dispatch /hi with extra instructions
    outcome = await dispatcher.dispatch("/hi say hello to everyone")
    assert outcome.action == CommandAction.SKILL_ACTIVATED
    assert outcome.data["skill"] == "hi"
    assert "Queued instruction: say hello to everyone" in outcome.message
    agent.activate_skill.assert_awaited_once_with("hi")
    agent.steer.assert_called_once()
    steered_text = agent.steer.call_args[0][0]
    assert '<skill_content name="hi">' in steered_text
    assert "say hello to everyone" in steered_text


@pytest.mark.asyncio
async def test_help_shows_dynamic_skills() -> None:
    agent = _create_mock_agent()
    agent.get_skills_catalog = MagicMock(
        return_value=[
            {
                "name": "hi",
                "description": "Says hi",
                "scope": "project",
                "path": "/skills/hi",
            }
        ]
    )
    dispatcher = CommandDispatcher(agent)
    outcome = await dispatcher.dispatch("/help")
    assert outcome.action == CommandAction.HELP
    assert "/hi" in outcome.message
    assert "Run skill hi" in outcome.message


@pytest.mark.asyncio
async def test_help_shows_dynamic_rune_commands() -> None:
    from mvge.runes import RegisteredCommand

    agent = _create_mock_agent()
    agent.get_registered_commands = MagicMock(
        return_value=[
            RegisteredCommand(name="mcp", description="Manage MCP servers"),
        ]
    )
    dispatcher = CommandDispatcher(agent)
    outcome = await dispatcher.dispatch("/help")
    assert outcome.action == CommandAction.HELP
    assert "/mcp" in outcome.message
    assert "Manage MCP servers" in outcome.message


@pytest.mark.asyncio
async def test_dynamic_rune_command_dispatch_sync_handler() -> None:
    from mvge.runes import RegisteredCommand

    agent = _create_mock_agent()
    handler_called_with = None

    def mcp_handler(args: str) -> str:
        nonlocal handler_called_with
        handler_called_with = args
        return f"MCP status: {args}"

    agent.get_registered_commands = MagicMock(
        return_value=[
            RegisteredCommand(
                name="mcp", description="Manage MCP servers", handler=mcp_handler
            ),
        ]
    )
    dispatcher = CommandDispatcher(agent)
    outcome = await dispatcher.dispatch("/mcp status")
    assert outcome.action == CommandAction.RUNE_COMMAND
    assert outcome.command == "/mcp status"
    assert outcome.data["command"] == "mcp"
    assert outcome.message == "MCP status: status"
    assert handler_called_with == "status"


@pytest.mark.asyncio
async def test_dynamic_rune_command_dispatch_async_handler() -> None:
    from mvge.runes import RegisteredCommand

    agent = _create_mock_agent()

    async def async_handler(args: str) -> str:
        await asyncio.sleep(0.001)
        return f"Connected to {args}"

    agent.get_registered_commands = MagicMock(
        return_value=[
            RegisteredCommand(
                name="mcp", description="Manage MCP servers", handler=async_handler
            ),
        ]
    )
    dispatcher = CommandDispatcher(agent)
    outcome = await dispatcher.dispatch("/mcp connect sse http://localhost:8000")
    assert outcome.action == CommandAction.RUNE_COMMAND
    assert "Connected to connect sse http://localhost:8000" in outcome.message


@pytest.mark.asyncio
async def test_dynamic_rune_command_dispatch_outcome_passthrough() -> None:
    from mvge.runes import RegisteredCommand

    agent = _create_mock_agent()
    custom_outcome = CommandOutcome(
        command="/mcp",
        action=CommandAction.INFO,
        message="Custom info outcome",
    )

    def custom_handler(args: str) -> CommandOutcome:
        return custom_outcome

    agent.get_registered_commands = MagicMock(
        return_value=[
            RegisteredCommand(
                name="mcp", description="Manage MCP servers", handler=custom_handler
            ),
        ]
    )
    dispatcher = CommandDispatcher(agent)
    outcome = await dispatcher.dispatch("/mcp")
    assert outcome is custom_outcome


@pytest.mark.asyncio
async def test_dynamic_rune_command_error_handling() -> None:
    from mvge.runes import RegisteredCommand

    agent = _create_mock_agent()

    def failing_handler(args: str) -> None:
        raise RuntimeError("MCP server connection failed")

    agent.get_registered_commands = MagicMock(
        return_value=[
            RegisteredCommand(
                name="mcp", description="Manage MCP servers", handler=failing_handler
            ),
        ]
    )
    dispatcher = CommandDispatcher(agent)
    outcome = await dispatcher.dispatch("/mcp test")
    assert outcome.action == CommandAction.ERROR
    assert "MCP server connection failed" in outcome.message


@pytest.mark.asyncio
async def test_is_command_static_and_aliases() -> None:
    agent = _create_mock_agent()
    dispatcher = CommandDispatcher(agent)
    assert await dispatcher.is_command("/help") is True
    assert await dispatcher.is_command("/model gemini") is True
    assert await dispatcher.is_command("/m") is True
    assert await dispatcher.is_command("/thinking deep") is True
    assert await dispatcher.is_command("  /spells  ") is True


@pytest.mark.asyncio
async def test_is_command_dynamic_rune_command() -> None:
    from mvge.runes import RegisteredCommand

    agent = _create_mock_agent()
    agent.get_skills_catalog = MagicMock(return_value=[])
    agent.get_registered_commands = MagicMock(
        return_value=[
            RegisteredCommand(
                name="selfmod", description="Self-mod bridge", handler=AsyncMock()
            ),
        ]
    )
    dispatcher = CommandDispatcher(agent)
    assert await dispatcher.is_command("/selfmod status") is True
    assert await dispatcher.is_command("/selfmod") is True


@pytest.mark.asyncio
async def test_is_command_dynamic_skill() -> None:
    agent = _create_mock_agent()
    agent.get_skills_catalog = MagicMock(return_value=[{"name": "grill-me"}])
    dispatcher = CommandDispatcher(agent)
    assert await dispatcher.is_command("/grill-me now") is True


@pytest.mark.asyncio
async def test_is_command_unknown() -> None:
    agent = _create_mock_agent()
    agent.get_skills_catalog = MagicMock(return_value=[])
    dispatcher = CommandDispatcher(agent)
    assert await dispatcher.is_command("/foobar") is False
    assert await dispatcher.is_command("hello") is False
    assert await dispatcher.is_command("") is False
