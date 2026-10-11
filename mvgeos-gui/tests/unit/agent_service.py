"""Unit tests for AgentService event sink and channeling bridge."""

import asyncio
import logging
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mvgeos_agent.commands import CommandAction
from mvgeos_core.channel import Model, MvgeResponse, RealmResponse
from mvgeos_core.errors import AuthenticationError, RateLimitError
from mvgeos_core.events import (
    MvgeEvent,
    MvgeEventType,
)
from mvgeos_runes import RegisteredCommand
from mvgeos_tome.handle import TomeHandleFactory

from mvgeos_gui.models import ChatMessage, StepType, TaskStatus
from mvgeos_gui.services.agent_service import AgentService, resolve_api_key
from mvgeos_gui.services.config_service import AppSettings, ConfigService
from mvgeos_gui.services.tome_service import TomeService
from mvgeos_gui.state import AppState, ServerState


@pytest.fixture
def app_state(tmp_path: Path) -> AppState:
    return AppState(project_path=tmp_path)


@pytest.fixture
def agent_service(tmp_path: Path) -> AgentService:
    return AgentService(project_path=tmp_path, api_key="test-api-key")


def test_agent_service_initialization(
    agent_service: AgentService, tmp_path: Path
) -> None:
    """Verify default properties of AgentService."""
    assert agent_service.project_path == tmp_path
    assert agent_service.is_running is False


def test_resolve_api_key_variants() -> None:
    """Verify API key resolution from explicit args, env, and auth file."""
    # 1. Explicit key
    assert resolve_api_key("sk-explicit") == "sk-explicit"

    # 2. OPENROUTER_API_KEY env var
    # The realm's own variable is consulted first, so it is blanked here --
    # otherwise an ambient value on the developer's machine would win over the
    # one under test and the test would pass for the wrong reason.
    with patch.dict(
        "os.environ", {"OPENROUTER_API_KEY": "sk-or-env", "OPENCODE_API_KEY": ""}
    ):
        assert resolve_api_key(None, "openrouter") == "sk-or-env"

    # 3. MVGEOS_API_KEY fallback env var
    with patch.dict(
        "os.environ",
        {
            "OPENROUTER_API_KEY": "",
            "OPENCODE_API_KEY": "",
            "MVGEOS_API_KEY": "sk-mvgeos-env",
        },
    ):
        assert resolve_api_key(None, "openrouter") == "sk-mvgeos-env"

    # 4. load_api_key_for_realm fallback
    with (
        patch.dict(
            "os.environ",
            {"OPENROUTER_API_KEY": "", "OPENCODE_API_KEY": "", "MVGEOS_API_KEY": ""},
        ),
        patch(
            "mvgeos_gui.services.agent_service.load_api_key_for_realm",
            return_value="sk-auth-file",
        ),
    ):
        assert resolve_api_key(None, "openrouter") == "sk-auth-file"

    # 5. None when no source is available
    with (
        patch.dict(
            "os.environ",
            {"OPENROUTER_API_KEY": "", "OPENCODE_API_KEY": "", "MVGEOS_API_KEY": ""},
        ),
        patch(
            "mvgeos_gui.services.agent_service.load_api_key_for_realm",
            return_value=None,
        ),
    ):
        assert resolve_api_key(None, "openrouter") is None


def test_an_openrouter_key_is_not_served_to_another_realm() -> None:
    """One Realm's credential must never be handed to a different Realm's host.

    The settings dialog keeps a single key and stores it under an
    ``openrouter_api_key`` name, so it is only ever an OpenRouter credential.
    With ``opencode`` as the default Realm, returning it for ``opencode`` sent
    the Summoner's OpenRouter key to opencode.ai.
    """
    with (
        patch.dict(
            "os.environ",
            {
                "OPENROUTER_API_KEY": "sk-or-env",
                "OPENCODE_API_KEY": "",
                "MVGEOS_API_KEY": "",
            },
        ),
        patch(
            "mvgeos_gui.services.agent_service._load_saved_gui_api_key",
            return_value="sk-or-saved",
        ),
        patch(
            "mvgeos_gui.services.agent_service.load_api_key_for_realm",
            return_value=None,
        ),
    ):
        assert resolve_api_key(None, "openrouter") == "sk-or-env"
        # The same environment, asked about a different Realm: the OpenRouter
        # key is not an answer, and neither is the saved slot.
        assert resolve_api_key(None, "opencode") is None


def test_agent_service_loads_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify AgentService loads .env from the project directory."""
    # The default Realm's variable. Since ADR-0020 the default is the OpenRouter
    # router, so this is the variable `load_api_key_for_realm` reads -- the test
    # is that the project .env is loaded at all, and the default Realm is what
    # decides which variable that is.
    (tmp_path / ".env").write_text(
        "OPENROUTER_API_KEY=sk-or-from-dotenv\n", encoding="utf-8"
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    monkeypatch.delenv("MVGEOS_API_KEY", raising=False)

    with patch(
        "mvgeos_gui.services.agent_service.load_api_key_for_realm",
        return_value=None,
    ):
        service = AgentService(project_path=tmp_path, api_key=None)

    assert service._api_key == "sk-or-from-dotenv"


def test_get_or_create_agent_with_factory(app_state: AppState) -> None:
    """Verify get_or_create_agent uses custom agent_factory if provided."""
    mock_agent = MagicMock()
    factory = MagicMock(return_value=mock_agent)
    service = AgentService(
        project_path=app_state.project_path,
        api_key="test-key",
        agent_factory=factory,
    )

    agent = service.get_or_create_agent(app_state)
    assert agent == mock_agent
    factory.assert_called_once_with(
        project_path=app_state.project_path,
        api_key=service._api_key,
        state=app_state,
    )
    # Subsequent call returns cached instance
    assert service.get_or_create_agent(app_state) == mock_agent
    assert factory.call_count == 1


def test_get_or_create_agent_binds_approval_presenter(
    app_state: AppState,
) -> None:
    """Agent creation binds the Approval Rune GUI presenter to the runner."""
    mock_agent = MagicMock()
    service = AgentService(
        project_path=app_state.project_path,
        api_key="test-api-key",
        agent_factory=MagicMock(return_value=mock_agent),
    )

    service.get_or_create_agent(app_state)

    presenter = app_state._approval_presenter
    assert presenter is not None
    mock_agent._runner.set_approval_presenter.assert_called_once_with(presenter)
    # A cached agent is not rebound: rebinding would fail pending casts.
    service.get_or_create_agent(app_state)
    assert mock_agent._runner.set_approval_presenter.call_count == 1


@patch("mvgeos_gui.services.agent_service.MvgeEnvironment")
@patch("mvgeos_gui.services.agent_service.Mvge")
def test_get_or_create_agent_default(
    mock_coding_mvge: MagicMock,
    mock_env: MagicMock,
    app_state: AppState,
) -> None:
    """Verify get_or_create_agent instantiates default CodingMvge."""
    mock_instance = MagicMock()
    mock_coding_mvge.return_value = mock_instance
    service = AgentService(project_path=app_state.project_path, api_key="test-key")

    agent = service.get_or_create_agent(app_state)
    assert agent == mock_instance
    mock_coding_mvge.assert_called_once()


@patch("mvgeos_gui.services.agent_service.MvgeEnvironment")
@patch("mvgeos_gui.services.agent_service.Mvge")
def test_reset_agent_clears_cached_instance(
    mock_coding_mvge: MagicMock,
    mock_env: MagicMock,
    app_state: AppState,
) -> None:
    """Verify reset_agent clears the cached agent instance."""
    mock_coding_mvge.side_effect = [MagicMock(), MagicMock()]
    service = AgentService(project_path=app_state.project_path, api_key="test-key")

    agent1 = service.get_or_create_agent(app_state)
    assert service.get_or_create_agent(app_state) == agent1
    assert mock_coding_mvge.call_count == 1

    service.reset_agent()
    agent2 = service.get_or_create_agent(app_state)
    assert agent2 != agent1
    assert mock_coding_mvge.call_count == 2


def test_handle_agent_start_event(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify AGENT_START initializes timing and streaming flags."""
    msg = ChatMessage(role="assistant", is_streaming=False)
    app_state.messages.append(msg)
    app_state.is_channeling = False

    event = MvgeEvent(type=MvgeEventType.AGENT_START, data={})
    agent_service.handle_event(event, msg, app_state)
    assert msg.is_streaming is True
    assert app_state.is_channeling is True


def test_agent_start_sets_mvge_status_channeling(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify AGENT_START flips the Mvge status light to channeling."""
    msg = ChatMessage(role="assistant")
    app_state.messages.append(msg)

    event = MvgeEvent(type=MvgeEventType.AGENT_START, data={})
    agent_service.handle_event(event, msg, app_state)
    assert app_state.mvge_status == "channeling"


def test_spell_casting_start_sets_mvge_status_working(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify SPELL_CASTING_START flips the Mvge status light to working."""
    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    event = MvgeEvent(
        type=MvgeEventType.SPELL_CASTING_START,
        data={
            "spellCastId": "s1",
            "spellName": "bash",
            "arguments": {"command": "ls"},
        },
    )
    agent_service.handle_event(event, msg, app_state)
    assert app_state.mvge_status == "working"


def test_agent_end_sets_mvge_status_idle(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify AGENT_END returns the Mvge status light to idle."""
    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    event = MvgeEvent(type=MvgeEventType.AGENT_END, data={})
    agent_service.handle_event(event, msg, app_state)
    assert app_state.mvge_status == "idle"


def test_handle_message_update_event(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify MESSAGE_UPDATE appends streamed tokens to ChatMessage content."""
    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    event1 = MvgeEvent(type=MvgeEventType.MESSAGE_UPDATE, data={"text": "Hello"})
    agent_service.handle_event(event1, msg, app_state)
    assert msg.content == "Hello"

    event2 = MvgeEvent(type=MvgeEventType.MESSAGE_UPDATE, data={"text": " world!"})
    agent_service.handle_event(event2, msg, app_state)
    assert msg.content == "Hello world!"


def test_notify_throttled_during_streaming(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify rapid MESSAGE_UPDATE events are throttled to prevent UI freezing."""
    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    notified: list[int] = []
    app_state.subscribe(lambda: notified.append(1))

    # Send 10 rapid events with 0-delay
    for i in range(10):
        event = MvgeEvent(type=MvgeEventType.MESSAGE_UPDATE, data={"text": f"token{i}"})
        agent_service.handle_event(event, msg, app_state)

    # First event notifies, subsequent rapid events within 50ms are throttled
    assert len(notified) == 1
    assert msg.content == "".join(f"token{i}" for i in range(10))


def test_handle_message_update_contemplation(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify MESSAGE_UPDATE with kind=contemplation routes to msg.contemplation."""
    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    event = MvgeEvent(
        type=MvgeEventType.MESSAGE_UPDATE,
        data={"text": "Thinking deeply...", "kind": "contemplation"},
    )
    agent_service.handle_event(event, msg, app_state)
    assert msg.contemplation == ["Thinking deeply..."]
    assert msg.content == ""


def test_handle_message_update_inline_think_tags(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify inline <think> tags are stripped from content to contemplation."""
    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    event = MvgeEvent(
        type=MvgeEventType.MESSAGE_UPDATE,
        data={"text": "<think>Let me formulate response</think>Hello!"},
    )
    agent_service.handle_event(event, msg, app_state)
    assert msg.contemplation == ["Let me formulate response"]
    assert msg.content == "Hello!"


def test_handle_provider_response_mana_tracking(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify AFTER_PROVIDER_RESPONSE updates mana metrics."""
    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    event = MvgeEvent(
        type=MvgeEventType.AFTER_PROVIDER_RESPONSE,
        data={"mana_used": 150},
    )
    agent_service.handle_event(event, msg, app_state)
    assert msg.mana_used == 150
    assert app_state.total_mana_used == 150


def test_handle_provider_response_records_context_usage(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify AFTER_PROVIDER_RESPONSE records real token usage for the gauge."""

    model = Model(
        id="test/model",
        name="Test",
        realm="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="<redacted>",
    )
    response = RealmResponse(
        model=model,
        invocation=MvgeResponse(
            mana_usage={"input": 44.0, "output": 1048.0, "total": 1092.0}
        ),
    )
    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    event = MvgeEvent(
        type=MvgeEventType.AFTER_PROVIDER_RESPONSE,
        data={"response": response, "mana_used": 1092},
    )
    agent_service.handle_event(event, msg, app_state)
    assert app_state.context_input_tokens == 44
    assert app_state.context_output_tokens == 1048


def test_handle_provider_response_without_usage_keeps_gauge_hidden(
    agent_service: AgentService, app_state: AppState
) -> None:
    """No usage on the response: gauge stays hidden, mana still tracked."""
    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    event = MvgeEvent(
        type=MvgeEventType.AFTER_PROVIDER_RESPONSE,
        data={"mana_used": 150},
    )
    agent_service.handle_event(event, msg, app_state)
    assert app_state.context_input_tokens is None
    assert app_state.context_output_tokens is None
    assert app_state.total_mana_used == 150


def test_handle_command_spell_dispatch(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify bash spell dispatch generates a Ran N Commands step card."""
    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    # Start spell
    start_event = MvgeEvent(
        type=MvgeEventType.SPELL_CASTING_START,
        data={
            "spellCastId": "cast-1",
            "spellName": "bash",
            "arguments": {"command": "pytest -v"},
        },
    )
    agent_service.handle_event(start_event, msg, app_state)

    cmd_steps = [s for s in msg.steps if s.step_type == StepType.COMMANDS]
    assert len(cmd_steps) == 1
    assert len(cmd_steps[0].commands) == 1
    assert "pytest -v" in cmd_steps[0].commands[0].command

    # End spell
    end_event = MvgeEvent(
        type=MvgeEventType.SPELL_CASTING_END,
        data={
            "spellCastId": "cast-1",
            "result": "82 passed in 0.45s",
        },
    )
    agent_service.handle_event(end_event, msg, app_state)
    assert cmd_steps[0].commands[0].output == "82 passed in 0.45s"
    assert cmd_steps[0].commands[0].is_error is False


def test_handle_file_exploration_spell_dispatch(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify read/grep spell dispatch generates an Explored N Files step card."""
    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    # Read spell start
    start_event = MvgeEvent(
        type=MvgeEventType.SPELL_CASTING_START,
        data={
            "spellCastId": "cast-file-1",
            "spellName": "read",
            "arguments": {"path": "src/main.py", "lines": "1-50"},
        },
    )
    agent_service.handle_event(start_event, msg, app_state)

    file_steps = [s for s in msg.steps if s.step_type == StepType.FILES]
    assert len(file_steps) == 1
    assert len(file_steps[0].files) == 1
    assert file_steps[0].files[0].path == "src/main.py"
    assert file_steps[0].files[0].lines == "1-50"

    # End spell
    end_event = MvgeEvent(
        type=MvgeEventType.SPELL_CASTING_END,
        data={
            "spellCastId": "cast-file-1",
            "result": "file contents...",
        },
    )
    agent_service.handle_event(end_event, msg, app_state)
    assert file_steps[0].files[0].details == "file contents..."


def test_handle_file_spell_missing_path_does_not_append_file(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify read spell without a path does not append a FileExploration."""
    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    start_event = MvgeEvent(
        type=MvgeEventType.SPELL_CASTING_START,
        data={
            "spellCastId": "cast-file-2",
            "spellName": "read",
        },
    )
    agent_service.handle_event(start_event, msg, app_state)

    file_steps = [s for s in msg.steps if s.step_type == StepType.FILES]
    assert len(file_steps) == 0


def test_handle_generic_worked_step(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify generic spell dispatch generates a Worked for Xs step card."""
    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    start_event = MvgeEvent(
        type=MvgeEventType.SPELL_CASTING_START,
        data={
            "spellCastId": "cast-edit-1",
            "spellName": "edit",
            "path": "config.py",
            "arguments": {"path": "config.py", "content": "hello"},
        },
    )
    agent_service.handle_event(start_event, msg, app_state)

    worked_steps = [s for s in msg.steps if s.step_type == StepType.WORKED]
    assert len(worked_steps) == 1
    assert worked_steps[0].spell_name == "edit"
    assert worked_steps[0].params == {"path": "config.py", "content": "hello"}
    assert "edit" in worked_steps[0].details[0]

    end_event = MvgeEvent(
        type=MvgeEventType.SPELL_CASTING_END,
        data={
            "spellCastId": "cast-edit-1",
            "result": "Saved edits",
        },
    )
    agent_service.handle_event(end_event, msg, app_state)
    assert worked_steps[0].duration_seconds >= 0.0
    assert worked_steps[0].result == "Saved edits"


def test_handle_agent_end_finalizes_state(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify AGENT_END marks message stream complete and is_channeling false."""
    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)
    app_state.is_channeling = True

    event = MvgeEvent(type=MvgeEventType.AGENT_END, data={"stop_reason": "stop"})
    agent_service.handle_event(event, msg, app_state)

    assert msg.is_streaming is False
    assert app_state.is_channeling is False


def test_spell_casting_start_tracks_background_task(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify SPELL_CASTING_START registers a running background task in state."""
    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    start_event = MvgeEvent(
        type=MvgeEventType.SPELL_CASTING_START,
        data={
            "spellCastId": "cast-bg-1",
            "spellName": "bash",
            "arguments": {"command": "pytest -v"},
        },
    )
    agent_service.handle_event(start_event, msg, app_state)

    assert len(app_state.background_tasks) == 1
    task = app_state.background_tasks[0]
    assert task.id == "cast-bg-1"
    assert task.name == "bash"
    assert task.status == TaskStatus.RUNNING
    assert task.progress == 0.0


def test_spell_casting_start_is_idempotent_on_duplicate_id(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify duplicate spellCastId does not create a second background task."""
    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    start_event = MvgeEvent(
        type=MvgeEventType.SPELL_CASTING_START,
        data={"spellCastId": "cast-bg-2", "spellName": "read"},
    )
    agent_service.handle_event(start_event, msg, app_state)
    agent_service.handle_event(start_event, msg, app_state)

    assert len(app_state.background_tasks) == 1


def test_spell_casting_start_uses_parent_spell_id(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify parentSpellCastId is propagated onto the background task."""
    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    start_event = MvgeEvent(
        type=MvgeEventType.SPELL_CASTING_START,
        data={
            "spellCastId": "cast-child",
            "spellName": "edit",
            "parentSpellCastId": "cast-parent",
        },
    )
    agent_service.handle_event(start_event, msg, app_state)

    assert app_state.background_tasks[0].parent_id == "cast-parent"


def test_spell_casting_end_marks_task_complete(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify SPELL_CASTING_END finalises the background task as complete."""
    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    start_event = MvgeEvent(
        type=MvgeEventType.SPELL_CASTING_START,
        data={"spellCastId": "cast-bg-3", "spellName": "bash"},
    )
    agent_service.handle_event(start_event, msg, app_state)

    end_event = MvgeEvent(
        type=MvgeEventType.SPELL_CASTING_END,
        data={
            "spellCastId": "cast-bg-3",
            "result": "82 passed in 0.45s",
        },
    )
    agent_service.handle_event(end_event, msg, app_state)

    task = app_state.background_tasks[0]
    assert task.status == TaskStatus.COMPLETE
    assert task.result == "82 passed in 0.45s"
    assert task.progress == 100.0
    assert task.ended_at is not None


def test_spell_casting_end_marks_task_error(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify SPELL_CASTING_END with an error marks the task errored."""
    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    start_event = MvgeEvent(
        type=MvgeEventType.SPELL_CASTING_START,
        data={"spellCastId": "cast-bg-4", "spellName": "bash"},
    )
    agent_service.handle_event(start_event, msg, app_state)

    end_event = MvgeEvent(
        type=MvgeEventType.SPELL_CASTING_END,
        data={
            "spellCastId": "cast-bg-4",
            "error": "Command timed out",
        },
    )
    agent_service.handle_event(end_event, msg, app_state)

    task = app_state.background_tasks[0]
    assert task.status == TaskStatus.ERROR
    assert task.error == "Command timed out"
    assert task.progress == 100.0


def test_spell_casting_end_unknown_id_is_noop(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify SPELL_CASTING_END for an unknown spell id does not crash."""
    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    end_event = MvgeEvent(
        type=MvgeEventType.SPELL_CASTING_END,
        data={"spellCastId": "never-started", "result": "ok"},
    )
    agent_service.handle_event(end_event, msg, app_state)

    assert app_state.background_tasks == []


def test_spell_casting_start_without_id_is_noop(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify a spell cast with no spellCastId does not create a task."""
    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    start_event = MvgeEvent(
        type=MvgeEventType.SPELL_CASTING_START,
        data={"spellName": "bash"},
    )
    agent_service.handle_event(start_event, msg, app_state)

    assert app_state.background_tasks == []


def test_register_subagent_task(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify register_subagent_task adds a subagent background task."""
    task = agent_service.register_subagent_task(
        app_state, "sub-1", "Reviewer", parent_id="cast-parent"
    )
    assert task is not None
    assert task.id == "sub-1"
    assert task.name == "Reviewer"
    assert task.parent_id == "cast-parent"
    assert task.status == TaskStatus.RUNNING


def test_register_subagent_task_idempotent(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify register_subagent_task is idempotent on duplicate id."""
    agent_service.register_subagent_task(app_state, "sub-2", "First")
    task = agent_service.register_subagent_task(app_state, "sub-2", "Second")
    assert len(app_state.background_tasks) == 1
    assert task.name == "First"


def test_update_subagent_task_progress_and_status(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify update_subagent_task updates progress and status."""
    agent_service.register_subagent_task(app_state, "sub-3", "Worker")

    updated = agent_service.update_subagent_task(
        app_state, "sub-3", status=TaskStatus.COMPLETE, progress=100.0
    )
    assert updated is not None
    assert updated.status == TaskStatus.COMPLETE
    assert updated.progress == 100.0


def test_update_subagent_task_unknown_id_returns_none(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify update_subagent_task returns None for unknown task id."""
    assert (
        agent_service.update_subagent_task(app_state, "missing", progress=50.0) is None
    )


def test_background_tasks_cleared_on_new_conversation(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify new_conversation clears tracked background tasks."""
    agent_service.register_subagent_task(app_state, "sub-x", "Worker")
    app_state.new_conversation()
    assert app_state.background_tasks == []


@pytest.mark.asyncio
async def test_run_prompt_routes_dynamic_slash_command(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Dynamic rune commands (e.g. /selfmod) must route to
    dispatch_slash_command, never to the model. The CLI REPL already routes
    every '/'-prefixed line through the dispatcher; the GUI chat must match
    for commands the dispatcher knows. Caught by installed GUI proof:
    '/selfmod status' was sent to the model as a regular turn instead of
    reaching the rune."""
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock()
    mock_agent.get_skills_catalog = MagicMock(return_value=[])
    mock_agent.get_registered_commands = MagicMock(
        return_value=[
            RegisteredCommand(
                name="selfmod",
                description="Self-mod bridge",
                handler=AsyncMock(),
            )
        ]
    )
    agent_service._agent = mock_agent

    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    with patch.object(
        agent_service, "dispatch_slash_command", new=AsyncMock()
    ) as routed:
        await agent_service.run_prompt("/selfmod status", app_state, msg)

    routed.assert_awaited_once_with("/selfmod status", app_state, msg)
    mock_agent.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_prompt_no_api_key(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify run_prompt gives helpful instructions when no API key exists."""
    agent_service._api_key = None

    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    await agent_service.run_prompt("Hello", app_state, msg)
    assert msg.is_error is True
    assert "Authentication Required" in msg.content
    assert agent_service.is_running is False


@pytest.mark.asyncio
async def test_run_prompt_success(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify run_prompt drives agent.run successfully and switches model."""
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock()
    mock_agent.switch_model = AsyncMock()
    mock_agent.on = MagicMock()
    agent_service._agent = mock_agent

    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)
    app_state.selected_model = "anthropic/claude-3-5-sonnet"

    await agent_service.run_prompt("Build feature", app_state, msg)

    mock_agent.switch_model.assert_called_once_with("anthropic/claude-3-5-sonnet")
    mock_agent.run.assert_called_once_with("Build feature")
    assert agent_service.is_running is False


@pytest.mark.asyncio
async def test_run_prompt_cancelled_error(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify run_prompt handles asyncio.CancelledError."""
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(side_effect=asyncio.CancelledError())
    mock_agent.switch_model = AsyncMock()
    mock_agent.on = MagicMock()
    agent_service._agent = mock_agent

    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    await agent_service.run_prompt("Test cancel", app_state, msg)
    assert "Cancelled by summoner" in msg.content
    assert agent_service.is_running is False


@pytest.mark.asyncio
async def test_run_prompt_authentication_error(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify run_prompt handles AuthenticationError with 401 message."""
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(side_effect=AuthenticationError("HTTP 401"))
    mock_agent.switch_model = AsyncMock()
    mock_agent.on = MagicMock()
    agent_service._agent = mock_agent

    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    await agent_service.run_prompt("Test 401", app_state, msg)
    assert msg.is_error is True
    assert "Authentication Failed (HTTP 401)" in msg.content


@pytest.mark.asyncio
async def test_run_prompt_rate_limit_error(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify run_prompt handles RateLimitError gracefully without traceback."""
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(side_effect=RateLimitError("Rate limit exceeded"))
    mock_agent.switch_model = AsyncMock()
    mock_agent.on = MagicMock()
    agent_service._agent = mock_agent

    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    await agent_service.run_prompt("Test 429", app_state, msg)
    assert msg.is_error is True
    assert msg.error_message == "Rate limit exceeded"
    assert "Rate Limit Exceeded (HTTP 429)" in msg.content
    assert "Suggested actions" in msg.content
    assert msg.is_streaming is False
    assert app_state.is_channeling is False


@pytest.mark.asyncio
async def test_run_prompt_rate_limit_error_with_retry_after(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify run_prompt includes retry-after hint when available."""
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(
        side_effect=RateLimitError("Rate limit exceeded", retry_after=12.0)
    )
    mock_agent.switch_model = AsyncMock()
    mock_agent.on = MagicMock()
    agent_service._agent = mock_agent

    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    await agent_service.run_prompt("Test 429 with retry", app_state, msg)
    assert msg.is_error is True
    assert "Please wait 12s before retrying" in msg.content
    assert msg.is_streaming is False
    assert app_state.is_channeling is False


@pytest.mark.asyncio
async def test_run_prompt_rate_limit_daily_quota(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify run_prompt renders daily quota details, reset time, and remedy."""
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(
        side_effect=RateLimitError(
            "Rate limit exceeded: free-models-per-day.",
            limit_source="openrouter_free_tier_daily",
            quota_limit=50,
            quota_remaining=0,
            reset_at=1788566400.0,
            remedy_hint="Wait for the daily reset, or purchase credits.",
        )
    )
    mock_agent.switch_model = AsyncMock()
    mock_agent.on = MagicMock()
    agent_service._agent = mock_agent

    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    await agent_service.run_prompt("Test daily quota", app_state, msg)
    assert msg.is_error is True
    assert "Daily Free Tier Quota Reached" in msg.content
    assert "50/50 requests used" in msg.content
    assert "Resets at" in msg.content
    assert "Wait for the daily reset, or purchase credits." in msg.content


@pytest.mark.asyncio
async def test_run_prompt_rate_limit_daily_quota_without_reported_window(
    agent_service: AgentService, app_state: AppState
) -> None:
    """The router's 402 names the allowance without a quota or a reset time."""
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(
        side_effect=RateLimitError(
            "Provider returned error",
            limit_source="openrouter_free_tier_daily",
            remedy_hint="Wait for the daily reset.",
        )
    )
    mock_agent.switch_model = AsyncMock()
    mock_agent.on = MagicMock()
    agent_service._agent = mock_agent

    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    await agent_service.run_prompt("Test spent router allowance", app_state, msg)
    assert msg.is_error is True
    assert "Daily Free Tier Quota Reached" in msg.content
    assert "requests used" not in msg.content
    assert "Resets at" not in msg.content
    assert "HTTP 402" not in msg.content
    assert "Wait for the daily reset." in msg.content


@pytest.mark.asyncio
async def test_run_prompt_rate_limit_upstream_overload(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify run_prompt renders upstream provider overload information."""
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(
        side_effect=RateLimitError(
            "Provider returned error: Upstream error from Nvidia: "
            "Service temporarily overloaded",
            limit_source="upstream_rate_limit",
        )
    )
    mock_agent.switch_model = AsyncMock()
    mock_agent.on = MagicMock()
    agent_service._agent = mock_agent

    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    await agent_service.run_prompt("Test upstream overload", app_state, msg)
    assert msg.is_error is True
    assert "Upstream Provider Overloaded (HTTP 429)" in msg.content
    assert "Service temporarily overloaded" in msg.content


def test_handle_provider_error_event(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify PROVIDER_ERROR marks the message and renders stall guidance."""
    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    event = MvgeEvent(
        type=MvgeEventType.PROVIDER_ERROR,
        data={
            "error_code": "upstream_idle_timeout",
            "error_message": "Upstream idle timeout exceeded",
        },
    )
    agent_service.handle_event(event, msg, app_state)
    assert msg.is_error is True
    assert msg.error_message == "Upstream idle timeout exceeded"
    assert "Upstream Provider Stalled" in msg.content
    assert "model selector below" in msg.content


@pytest.mark.asyncio
async def test_run_prompt_upstream_timeout(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify run_prompt renders stall guidance for UpstreamTimeoutError."""
    from mvgeos_core.errors import UpstreamTimeoutError

    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(
        side_effect=UpstreamTimeoutError("Upstream idle timeout exceeded")
    )
    mock_agent.switch_model = AsyncMock()
    mock_agent.on = MagicMock()
    agent_service._agent = mock_agent

    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    await agent_service.run_prompt("Test upstream stall", app_state, msg)
    assert msg.is_error is True
    assert "Upstream Provider Stalled" in msg.content
    assert "Upstream idle timeout exceeded" in msg.content
    assert msg.is_streaming is False
    assert app_state.is_channeling is False


@pytest.mark.asyncio
async def test_run_prompt_exception_handling(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify run_prompt handles general exceptions."""
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(side_effect=ValueError("Model rejected"))
    mock_agent.switch_model = AsyncMock()
    mock_agent.on = MagicMock()
    agent_service._agent = mock_agent

    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    await agent_service.run_prompt("Test error", app_state, msg)
    assert msg.is_error is True
    assert "Model rejected" in msg.content


def test_agent_service_cancel_method(agent_service: AgentService) -> None:
    """Verify cancel() cancels active task."""
    mock_task = MagicMock()
    agent_service._active_task = mock_task
    agent_service._is_running = True

    agent_service.cancel()
    mock_task.cancel.assert_called_once()
    assert agent_service.is_running is False


# --- Skill tracking tests ---


def _make_skill_manifest(
    name: str = "review",
    path: str = "/skills/review/SKILL.md",
    scope: str = "project",
    description: str = "Review code",
) -> Any:
    """Build a minimal SkillManifest for testing."""
    from mvgeos_runes.types import (
        SkillManifest,
        SkillScope,
    )

    return SkillManifest(
        name=name,
        description=description,
        scope=SkillScope(scope),
        path=path,
    )


def test_populate_skills_seeds_active_skills(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify populate_skills adds manifests from the agent's runner."""
    manifest = _make_skill_manifest()
    mock_runner = MagicMock()
    mock_runner.get_skills.return_value = [manifest]
    mock_agent = MagicMock()
    mock_agent.runner = mock_runner

    agent_service.populate_skills(mock_agent, app_state)

    assert len(app_state.active_skills) == 1
    skill = app_state.active_skills[0]
    assert skill.name == "review"
    assert skill.description == "Review code"
    assert skill.scope == "project"
    assert skill.path == "/skills/review/SKILL.md"


def test_populate_skills_is_idempotent(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify populate_skills does not duplicate already-tracked skills."""
    manifest = _make_skill_manifest()
    mock_runner = MagicMock()
    mock_runner.get_skills.return_value = [manifest]
    mock_agent = MagicMock()
    mock_agent.runner = mock_runner

    agent_service.populate_skills(mock_agent, app_state)
    agent_service.populate_skills(mock_agent, app_state)

    assert len(app_state.active_skills) == 1


def test_populate_skills_handles_missing_runner(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify populate_skills is a safe no-op when the agent has no runner."""
    mock_agent = MagicMock()
    mock_agent.runner = None

    agent_service.populate_skills(mock_agent, app_state)
    assert app_state.active_skills == []


def test_populate_skills_handles_get_skills_failure(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify populate_skills tolerates a failing get_skills call."""
    mock_runner = MagicMock()
    mock_runner.get_skills.side_effect = RuntimeError("boom")
    mock_agent = MagicMock()
    mock_agent.runner = mock_runner

    agent_service.populate_skills(mock_agent, app_state)
    assert app_state.active_skills == []


def test_mark_skill_invoked_by_skill_md_path(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify reading a SKILL.md file marks the skill as invoked."""
    manifest = _make_skill_manifest(path="/skills/review")
    mock_runner = MagicMock()
    mock_runner.get_skills.return_value = [manifest]
    mock_agent = MagicMock()
    mock_agent.runner = mock_runner
    agent_service.populate_skills(mock_agent, app_state)

    assert app_state.active_skills[0].invoked is False

    # Simulate the agent reading the skill's SKILL.md file.
    agent_service.mark_skill_invoked("/skills/review/SKILL.md", state=app_state)

    assert app_state.active_skills[0].invoked is True


def test_mark_skill_invoked_deduplicates(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify the same skill is only marked invoked once."""
    manifest = _make_skill_manifest(path="/skills/review")
    mock_runner = MagicMock()
    mock_runner.get_skills.return_value = [manifest]
    mock_agent = MagicMock()
    mock_agent.runner = mock_runner
    agent_service.populate_skills(mock_agent, app_state)

    agent_service.mark_skill_invoked("/skills/review/SKILL.md", state=app_state)
    agent_service.mark_skill_invoked("/skills/review/SKILL.md", state=app_state)

    assert app_state.active_skills[0].invoked is True


def test_mark_skill_invoked_windows_backslashes(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify reading a SKILL.md with Windows backslashes marks the skill as invoked."""
    manifest = _make_skill_manifest(path=r"C:\Users\user\.agents\skills\location")
    mock_runner = MagicMock()
    mock_runner.get_skills.return_value = [manifest]
    mock_agent = MagicMock()
    mock_agent.runner = mock_runner
    agent_service.populate_skills(mock_agent, app_state)

    assert app_state.active_skills[0].invoked is False

    agent_service.mark_skill_invoked(
        r"C:\Users\user\.agents\skills\location\SKILL.md", state=app_state
    )
    assert app_state.active_skills[0].invoked is True


def test_mark_skill_invoked_no_match_is_safe(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify mark_skill_invoked is a no-op when path matches no skill."""
    agent_service.mark_skill_invoked("/some/random/file.py", state=app_state)
    assert app_state.active_skills == []


def test_mark_skill_invoked_empty_path_is_safe(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify mark_skill_invoked tolerates an empty path."""
    agent_service.mark_skill_invoked("", state=app_state)
    assert app_state.active_skills == []


def test_mark_skill_invoked_no_state_is_safe(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify mark_skill_invoked is a no-op without an active state."""
    agent_service._active_state = None
    agent_service.mark_skill_invoked("/skills/review/SKILL.md")
    assert app_state.active_skills == []


def test_reset_skill_tracking_clears_invocation_state(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify reset_skill_tracking clears the invocation bookkeeping."""
    agent_service._invoked_skill_names.add("review")
    agent_service._loaded_skill_names.add("review")
    agent_service.reset_skill_tracking()
    assert agent_service._invoked_skill_names == set()
    assert agent_service._loaded_skill_names == set()


def test_get_or_create_agent_populates_skills(
    app_state: AppState, tmp_path: Path
) -> None:
    """Verify get_or_create_agent seeds active_skills on agent creation."""
    manifest = _make_skill_manifest()
    mock_runner = MagicMock()
    mock_runner.get_skills.return_value = [manifest]
    mock_agent = MagicMock()
    mock_agent.runner = mock_runner

    service = AgentService(
        project_path=tmp_path,
        api_key="test-key",
        agent_factory=lambda **kwargs: mock_agent,
    )

    service.get_or_create_agent(app_state)
    assert len(app_state.active_skills) == 1
    assert app_state.active_skills[0].name == "review"


def test_handle_agent_start_populates_skills(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify AGENT_START seeds active_skills from the bound agent's runner."""
    manifest = _make_skill_manifest()
    mock_runner = MagicMock()
    mock_runner.get_skills.return_value = [manifest]
    mock_agent = MagicMock()
    mock_agent.runner = mock_runner
    agent_service._agent = mock_agent

    msg = ChatMessage(role="assistant", is_streaming=False)
    app_state.messages.append(msg)
    app_state.is_channeling = False

    event = MvgeEvent(type=MvgeEventType.AGENT_START, data={})
    agent_service.handle_event(event, msg, app_state)

    assert msg.is_streaming is True
    assert app_state.is_channeling is True
    assert len(app_state.active_skills) == 1
    assert app_state.active_skills[0].name == "review"


def test_handle_read_skill_md_marks_invoked(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify reading a SKILL.md path via the read spell marks it invoked."""
    manifest = _make_skill_manifest(path="/skills/review")
    mock_runner = MagicMock()
    mock_runner.get_skills.return_value = [manifest]
    mock_agent = MagicMock()
    mock_agent.runner = mock_runner
    agent_service._agent = mock_agent
    agent_service.populate_skills(mock_agent, app_state)

    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    start_event = MvgeEvent(
        type=MvgeEventType.SPELL_CASTING_START,
        data={
            "spellCastId": "cast-skill-1",
            "spellName": "read",
            "arguments": {"path": "/skills/review/SKILL.md"},
        },
    )
    agent_service.handle_event(start_event, msg, app_state)

    assert app_state.active_skills[0].invoked is True


def test_handle_read_non_skill_path_does_not_invoke(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify reading a non-SKILL.md path does not mark any skill invoked."""
    manifest = _make_skill_manifest(path="/skills/review")
    mock_runner = MagicMock()
    mock_runner.get_skills.return_value = [manifest]
    mock_agent = MagicMock()
    mock_agent.runner = mock_runner
    agent_service._agent = mock_agent
    agent_service.populate_skills(mock_agent, app_state)

    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    start_event = MvgeEvent(
        type=MvgeEventType.SPELL_CASTING_START,
        data={
            "spellCastId": "cast-file-1",
            "spellName": "read",
            "arguments": {"path": "src/main.py"},
        },
    )
    agent_service.handle_event(start_event, msg, app_state)

    assert app_state.active_skills[0].invoked is False


@pytest.mark.asyncio
async def test_multi_turn_prompt_isolation(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify consecutive prompts only update their respective ChatMessage."""
    callbacks: dict[MvgeEventType, list[Any]] = {}

    def mock_on(event_type: MvgeEventType, cb: Any) -> None:
        callbacks.setdefault(event_type, []).append(cb)

    mock_agent = MagicMock()
    mock_agent.on = MagicMock(side_effect=mock_on)
    mock_agent.switch_model = AsyncMock()

    async def fake_run_turn_1(prompt: str) -> None:
        for cb in callbacks.get(MvgeEventType.MESSAGE_UPDATE, []):
            cb(
                MvgeEvent(
                    type=MvgeEventType.MESSAGE_UPDATE,
                    data={"text": "Turn 1 Thought", "kind": "contemplation"},
                )
            )
            cb(
                MvgeEvent(
                    type=MvgeEventType.MESSAGE_UPDATE,
                    data={"text": "Turn 1 Response"},
                )
            )
        for cb in callbacks.get(MvgeEventType.AGENT_END, []):
            cb(MvgeEvent(type=MvgeEventType.AGENT_END, data={}))

    async def fake_run_turn_2(prompt: str) -> None:
        for cb in callbacks.get(MvgeEventType.MESSAGE_UPDATE, []):
            cb(
                MvgeEvent(
                    type=MvgeEventType.MESSAGE_UPDATE,
                    data={"text": "Turn 2 Thought", "kind": "contemplation"},
                )
            )
            cb(
                MvgeEvent(
                    type=MvgeEventType.MESSAGE_UPDATE,
                    data={"text": "Turn 2 Response"},
                )
            )
        for cb in callbacks.get(MvgeEventType.AGENT_END, []):
            cb(MvgeEvent(type=MvgeEventType.AGENT_END, data={}))

    agent_service._agent = mock_agent

    # Turn 1
    mock_agent.run = AsyncMock(side_effect=fake_run_turn_1)
    msg1 = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg1)
    await agent_service.run_prompt("hey there", app_state, msg1)

    assert msg1.contemplation == ["Turn 1 Thought"]
    assert msg1.content == "Turn 1 Response"

    # Turn 2
    mock_agent.run = AsyncMock(side_effect=fake_run_turn_2)
    msg2 = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg2)
    await agent_service.run_prompt("read README.md", app_state, msg2)

    # Turn 1 must NOT be polluted by Turn 2
    assert msg1.contemplation == ["Turn 1 Thought"]
    assert msg1.content == "Turn 1 Response"

    # Turn 2 has its own distinct content
    assert msg2.contemplation == ["Turn 2 Thought"]
    assert msg2.content == "Turn 2 Response"


def test_handle_interleaved_thoughts_and_events(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify thoughts separated by tool steps or text create separate parts."""
    from mvgeos_gui.models import MessagePartType

    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    # 1. First thought
    agent_service.handle_event(
        MvgeEvent(
            type=MvgeEventType.MESSAGE_UPDATE,
            data={
                "text": "I should read the config file first.",
                "kind": "contemplation",
            },
        ),
        msg,
        app_state,
    )

    # 2. Tool step (read config.json)
    agent_service.handle_event(
        MvgeEvent(
            type=MvgeEventType.SPELL_CASTING_START,
            data={
                "spellCastId": "cast-1",
                "spellName": "read",
                "arguments": {"path": "config.json"},
            },
        ),
        msg,
        app_state,
    )
    agent_service.handle_event(
        MvgeEvent(
            type=MvgeEventType.SPELL_CASTING_END,
            data={"spellCastId": "cast-1", "result": '{"env": "prod"}'},
        ),
        msg,
        app_state,
    )

    # 3. Second thought (after tool execution)
    agent_service.handle_event(
        MvgeEvent(
            type=MvgeEventType.MESSAGE_UPDATE,
            data={
                "text": "The config is in prod mode. Let's explain.",
                "kind": "contemplation",
            },
        ),
        msg,
        app_state,
    )

    # 4. Final text response
    agent_service.handle_event(
        MvgeEvent(
            type=MvgeEventType.MESSAGE_UPDATE,
            data={"text": "Here is the explanation for prod mode."},
        ),
        msg,
        app_state,
    )

    # Check parts
    parts = msg.parts
    assert len(parts) == 4
    assert parts[0].part_type == MessagePartType.CONTEMPLATION
    assert parts[0].text == "I should read the config file first."
    assert parts[1].part_type == MessagePartType.STEP
    assert parts[2].part_type == MessagePartType.CONTEMPLATION
    assert parts[2].text == "The config is in prod mode. Let's explain."
    assert parts[3].part_type == MessagePartType.TEXT
    assert parts[3].text == "Here is the explanation for prod mode."


class TestEnsureListeners:
    def test_does_not_double_bind(self, tmp_path: Path) -> None:
        """_ensure_listeners must not re-bind handlers when called twice."""
        from unittest.mock import MagicMock

        service = AgentService(project_path=tmp_path, api_key="test-key")

        mock_agent = MagicMock()
        mock_agent._gui_listeners_bound = False
        mock_agent.on = MagicMock()

        service._ensure_listeners(mock_agent)
        first_call_count = mock_agent.on.call_count

        service._ensure_listeners(mock_agent)
        second_call_count = mock_agent.on.call_count

        assert first_call_count == second_call_count, (
            "Listeners were re-bound on second _ensure_listeners call"
        )
        assert mock_agent._gui_listeners_bound is True

    def test_does_not_rebind_with_truthy_non_true_flag(self, tmp_path: Path) -> None:
        """_ensure_listeners must not re-bind when flag is any truthy value."""
        from unittest.mock import MagicMock

        service = AgentService(project_path=tmp_path, api_key="test-key")
        mock_agent = MagicMock()
        mock_agent._gui_listeners_bound = 1  # truthy but not True
        mock_agent.on = MagicMock()

        service._ensure_listeners(mock_agent)

        mock_agent.on.assert_not_called()


class TestPendingSpellStartsCleanup:
    def test_agent_end_clears_pending_spell_starts(
        self, agent_service: AgentService, app_state: AppState
    ) -> None:
        """AGENT_END must clear any orphaned pending spell starts."""
        msg = ChatMessage(role="assistant", is_streaming=True)
        app_state.messages.append(msg)

        start_event = MvgeEvent(
            type=MvgeEventType.SPELL_CASTING_START,
            data={"spellCastId": "cast-orphan", "spellName": "bash"},
        )
        agent_service.handle_event(start_event, msg, app_state)

        assert "cast-orphan" in agent_service._pending_spell_starts

        end_event = MvgeEvent(type=MvgeEventType.AGENT_END, data={})
        agent_service.handle_event(end_event, msg, app_state)

        assert "cast-orphan" not in agent_service._pending_spell_starts

    def test_turn_end_clears_pending_spell_starts(
        self, agent_service: AgentService, app_state: AppState
    ) -> None:
        """TURN_END must clear any orphaned pending spell starts."""
        msg = ChatMessage(role="assistant", is_streaming=True)
        app_state.messages.append(msg)

        start_event = MvgeEvent(
            type=MvgeEventType.SPELL_CASTING_START,
            data={"spellCastId": "cast-orphan-2", "spellName": "read"},
        )
        agent_service.handle_event(start_event, msg, app_state)

        assert "cast-orphan-2" in agent_service._pending_spell_starts

        end_event = MvgeEvent(type=MvgeEventType.TURN_END, data={})
        agent_service.handle_event(end_event, msg, app_state)

        assert "cast-orphan-2" not in agent_service._pending_spell_starts


class TestGetOrCreateAgentApiKeyGuard:
    def test_raises_without_api_key_and_factory(self, tmp_path: Path) -> None:
        """get_or_create_agent must raise when api_key is None and no factory."""
        service = AgentService(project_path=tmp_path, api_key="")
        service._api_key = None

        with pytest.raises(RuntimeError, match="API key"):
            service.get_or_create_agent(AppState(project_path=tmp_path))

    def test_uses_factory_even_without_api_key(self, tmp_path: Path) -> None:
        """Factory path passes api_key=None through when no key configured."""
        mock_agent = MagicMock()
        factory = MagicMock(return_value=mock_agent)
        service = AgentService(project_path=tmp_path, api_key="", agent_factory=factory)
        service._api_key = None

        agent = service.get_or_create_agent(AppState(project_path=tmp_path))
        assert agent is mock_agent
        _, kwargs = factory.call_args
        assert kwargs["api_key"] is None


class TestSubmitPromptNoLoop:
    def test_logs_warning_without_running_event_loop(
        self, app_state: AppState, caplog: pytest.LogCaptureFixture
    ) -> None:
        """submit_prompt must log a warning and clean up without an event loop."""
        with caplog.at_level(logging.WARNING):
            app_state.submit_prompt("hello")

        assert any("event loop" in record.message.lower() for record in caplog.records)
        assert len(app_state.messages) == 1
        assert app_state.messages[0].role == "user"
        assert app_state.is_channeling is False


class TestSubagentLifecycleObservability:
    def test_subagent_events_track_in_background_tasks(
        self, agent_service: AgentService, app_state: AppState
    ) -> None:
        """Verify subagent events populate background tasks without
        ending main agent.
        """
        msg = ChatMessage(role="assistant", is_streaming=True)
        app_state.messages.append(msg)
        agent_service._is_running = True

        # 1. Subagent AGENT_START
        start_ev = MvgeEvent(
            type=MvgeEventType.AGENT_START,
            data={
                "subagent": "skill_proposer",
                "taskId": "sub-prop-1",
                "name": "Skill Proposer Sub-Agent",
            },
        )
        agent_service.handle_event(start_ev, msg, app_state)
        task = app_state.get_background_task("sub-prop-1")
        assert task is not None
        assert task.name == "Skill Proposer Sub-Agent"
        assert task.status == TaskStatus.RUNNING

        # 2. Subagent TURN_START
        turn_ev = MvgeEvent(
            type=MvgeEventType.TURN_START,
            data={
                "subagent": "skill_proposer",
                "parentTaskId": "sub-prop-1",
                "turn": 1,
                "progress": 25.0,
            },
        )
        agent_service.handle_event(turn_ev, msg, app_state)
        assert task.progress == 25.0

        # 3. Subagent SPELL_CASTING_START & END
        spell_start_ev = MvgeEvent(
            type=MvgeEventType.SPELL_CASTING_START,
            data={
                "subagent": "skill_proposer",
                "parentTaskId": "sub-prop-1",
                "spellCastId": "cast-tool-1",
                "spellName": "read_file",
                "arguments": {"path": "knowledge/index.md"},
            },
        )
        agent_service.handle_event(spell_start_ev, msg, app_state)
        child_task = app_state.get_background_task("cast-tool-1")
        assert child_task is not None
        assert child_task.parent_id == "sub-prop-1"

        spell_end_ev = MvgeEvent(
            type=MvgeEventType.SPELL_CASTING_END,
            data={
                "subagent": "skill_proposer",
                "parentTaskId": "sub-prop-1",
                "spellCastId": "cast-tool-1",
                "spellName": "read_file",
                "result": "File content here",
            },
        )
        agent_service.handle_event(spell_end_ev, msg, app_state)
        assert child_task.status == TaskStatus.COMPLETE

        # 4. Subagent AGENT_END
        agent_end_ev = MvgeEvent(
            type=MvgeEventType.AGENT_END,
            data={
                "subagent": "skill_proposer",
                "parentTaskId": "sub-prop-1",
                "result": {"success": True, "action": "no_action"},
            },
        )
        agent_service.handle_event(agent_end_ev, msg, app_state)
        assert task.status == TaskStatus.COMPLETE
        assert task.progress == 100.0
        assert agent_service._is_running is True


# ---------------------------------------------------------------------------
# Slash command dispatching & state sync tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_slash_command_structured_outcome(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify dispatch_slash_command handles structured CommandOutcome."""
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock()
    mock_agent.switch_model = AsyncMock()
    mock_agent.on = MagicMock()
    mock_agent.enabled_spells = ["read", "write"]
    mock_agent.available_spells = ["read", "write", "bash"]
    agent_service._agent = mock_agent

    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    await agent_service.run_prompt("/spells", app_state, msg)
    assert "Enabled spells: read, write" in msg.content
    assert msg.is_streaming is False


@pytest.mark.asyncio
async def test_run_prompt_slash_command_help(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify /help executes locally via CommandDispatcher without LLM turn."""
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock()
    mock_agent.switch_model = AsyncMock()
    mock_agent.on = MagicMock()
    agent_service._agent = mock_agent

    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    await agent_service.run_prompt("/help", app_state, msg)

    mock_agent.run.assert_not_called()
    assert msg.is_streaming is False
    assert app_state.is_channeling is False
    assert app_state.mvge_status == "idle"
    assert "Available commands:" in msg.content
    assert "/tome" in msg.content
    assert "/help" in msg.content


@pytest.mark.asyncio
async def test_run_prompt_slash_command_tome(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify /tome displays agent tome information without LLM turn."""
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock()
    mock_agent.switch_model = AsyncMock()
    mock_agent.on = MagicMock()
    mock_agent.tome_id = "tome-xyz-123"
    mock_agent.model_id = "test/model-id"
    mock_agent.enabled_spells = ["read_file", "write_to_file"]
    mock_agent.registered_providers = ["openrouter"]
    agent_service._agent = mock_agent

    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    await agent_service.run_prompt("/tome", app_state, msg)

    mock_agent.run.assert_not_called()
    assert msg.is_streaming is False
    assert app_state.is_channeling is False
    assert "Tome ID: tome-xyz-123" in msg.content
    assert "Model: test/model-id" in msg.content
    assert "read_file" in msg.content


@pytest.mark.asyncio
async def test_run_prompt_slash_command_model_switch_syncs_state(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify /model <id> switches agent model and syncs AppState."""
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock()
    mock_agent.switch_model = AsyncMock()
    mock_agent.on = MagicMock()
    mock_agent.model_id = "openai/gpt-4o"
    agent_service._agent = mock_agent

    mock_registry = MagicMock()
    mock_registry.get.return_value = MagicMock(id="openai/gpt-4o")
    agent_service._model_registry = mock_registry

    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    await agent_service.run_prompt("/model openai/gpt-4o", app_state, msg)

    mock_agent.run.assert_not_called()
    mock_agent.switch_model.assert_called_with("openai/gpt-4o")
    assert app_state.selected_model == "openai/gpt-4o"
    assert msg.is_streaming is False
    assert "Model switched: openai/gpt-4o" in msg.content


@pytest.mark.asyncio
async def test_run_prompt_slash_command_new_tome_syncs_state(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify /new resets session and syncs AppState new_conversation."""
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock()
    mock_agent.reset_session = AsyncMock()
    mock_agent.on = MagicMock()
    mock_agent.tome_id = "new-tome-id"
    agent_service._agent = mock_agent

    app_state.active_tome_id = "old-tome-id"
    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    await agent_service.run_prompt("/new", app_state, msg)

    mock_agent.run.assert_not_called()
    mock_agent.reset_session.assert_called_once_with(resume_tome_id=None)
    assert app_state.active_tome_id is None
    assert msg.is_streaming is False
    assert "New tome:" in msg.content


@pytest.mark.asyncio
async def test_run_prompt_skill_invocation_runs_agent(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify skill invocations like /grill-me pass through to agent.run."""
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock()
    mock_agent.switch_model = AsyncMock()
    mock_agent.on = MagicMock()
    agent_service._agent = mock_agent

    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    await agent_service.run_prompt("/grill-me please test the plan", app_state, msg)

    mock_agent.run.assert_called_once_with("/grill-me please test the plan")


@pytest.mark.asyncio
async def test_run_prompt_mid_prompt_skill_runs_agent(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify natural language with inline skills runs agent.run."""
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock()
    mock_agent.switch_model = AsyncMock()
    mock_agent.on = MagicMock()
    agent_service._agent = mock_agent

    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    prompt = "let's use /grill-me and then /to-tickets"
    await agent_service.run_prompt(prompt, app_state, msg)

    mock_agent.run.assert_called_once_with(prompt)


@pytest.mark.asyncio
async def test_run_prompt_handles_no_realm_registered_error(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify NoRealmRegisteredError sets missing_rune and helpful transcript.

    Built through ``no_realm_registered`` rather than by hand, because that is
    the only thing that raises it and it is what fills in ``rune_name``. The GUI
    reads that attribute instead of parsing the message, which is the point of
    the change -- so a hand-built error with no realm is now a blank badge, and
    this test would not have caught it.
    """
    from mvgeos_core.channel import Model
    from mvgeos_provider.registry import no_realm_registered

    model = Model(
        id="openrouter/free",
        name="Free Models Router",
        realm="openrouter",
        base_url="",
        api_key="",
    )
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(side_effect=no_realm_registered(model))
    mock_agent.switch_model = AsyncMock()
    mock_agent.on = MagicMock()
    agent_service._agent = mock_agent

    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    await agent_service.run_prompt("Hello", app_state, msg)

    assert msg.is_error is True
    assert msg.missing_rune == "openrouter-realm"
    assert "Missing Realm Extension" in msg.content
    assert "openrouter-realm" in msg.content


def _make_compact_agent(
    tome_id: str | None, result: str = "Compaction completed"
) -> MagicMock:
    """Fake Mvge agent whose _agent_tome carries the given tome id."""
    agent = MagicMock()
    agent_tome = MagicMock()
    agent_tome.tome_id = tome_id
    agent._agent_tome = agent_tome
    agent.compact = AsyncMock(return_value=result)
    return agent


class TestCompactActiveTome:
    @pytest.mark.asyncio
    async def test_compact_no_active_tome_refuses(
        self, agent_service: AgentService, app_state: AppState
    ) -> None:
        app_state.active_tome_id = None

        result = await agent_service.compact_active_tome(app_state)

        assert "No active session" in result

    @pytest.mark.asyncio
    async def test_compact_while_channeling_refuses(
        self, agent_service: AgentService, app_state: AppState
    ) -> None:
        app_state.active_tome_id = "tome-1"
        app_state.is_channeling = True
        agent_service._agent = _make_compact_agent("tome-1")

        result = await agent_service.compact_active_tome(app_state)

        assert "channeling" in result.lower()
        agent_service._agent.compact.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_compact_agent_tome_mismatch_refuses(
        self, agent_service: AgentService, app_state: AppState
    ) -> None:
        app_state.active_tome_id = "tome-1"
        agent_service._agent = _make_compact_agent("tome-2")

        result = await agent_service.compact_active_tome(app_state)

        assert "not attached" in result
        agent_service._agent.compact.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_compact_no_agent_refuses(
        self, agent_service: AgentService, app_state: AppState
    ) -> None:
        app_state.active_tome_id = "tome-1"
        agent_service._agent = None
        agent_service._api_key = None
        agent_service._agent_factory = None

        result = await agent_service.compact_active_tome(app_state)

        assert "not attached" in result

    @pytest.mark.asyncio
    async def test_compact_agent_tome_none_refuses(
        self, agent_service: AgentService, app_state: AppState
    ) -> None:
        app_state.active_tome_id = "tome-1"
        agent_service._agent = _make_compact_agent(None)

        result = await agent_service.compact_active_tome(app_state)

        assert "not attached" in result
        agent_service._agent.compact.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_compact_runtime_error_becomes_message(
        self, agent_service: AgentService, app_state: AppState
    ) -> None:
        app_state.active_tome_id = "tome-1"
        agent = _make_compact_agent("tome-1")
        agent.compact = AsyncMock(side_effect=RuntimeError("boom"))
        agent_service._agent = agent

        result = await agent_service.compact_active_tome(app_state)

        assert "boom" in result
        assert "failed" in result.lower()

    @pytest.mark.asyncio
    async def test_compact_success_returns_engine_result(
        self, agent_service: AgentService, app_state: AppState
    ) -> None:
        app_state.active_tome_id = "tome-1"
        agent_service._agent = _make_compact_agent("tome-1", "Compaction completed")

        result = await agent_service.compact_active_tome(app_state)

        assert result == "Compaction completed"
        agent_service._agent.compact.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_prompt_adopts_agent_tome_when_no_active_tome(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify run_prompt adopts the engine-created tome id.

    Without adoption the session lifecycle commands (rename/fork/export/
    compact) stay disabled forever because they gate on active_tome_id.
    """
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock()
    mock_agent.switch_model = AsyncMock()
    mock_agent.on = MagicMock()
    mock_agent.tome_id = "abc123tome"
    agent_service._agent = mock_agent

    assert app_state.active_tome_id is None
    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    await agent_service.run_prompt("Hello", app_state, msg)

    assert app_state.active_tome_id == "abc123tome"


@pytest.mark.asyncio
async def test_run_prompt_keeps_existing_active_tome(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify run_prompt never overwrites an already-active tome."""
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock()
    mock_agent.switch_model = AsyncMock()
    mock_agent.on = MagicMock()
    mock_agent.tome_id = "new-tome-id"
    agent_service._agent = mock_agent

    app_state.active_tome_id = "existing-tome"
    msg = ChatMessage(role="assistant", is_streaming=True)
    app_state.messages.append(msg)

    await agent_service.run_prompt("Hello", app_state, msg)

    assert app_state.active_tome_id == "existing-tome"


@pytest.mark.asyncio
async def test_compact_active_tome_attaches_fresh_agent(
    agent_service: AgentService, app_state: AppState
) -> None:
    """Verify compact binds a fresh agent when none is cached.

    After a fork or session switch the cached agent is dropped; compact
    must attach to the active tome on demand instead of refusing.
    """
    fresh = _make_compact_agent("tome-1", "Compaction completed")
    fresh.initialize = AsyncMock()
    agent_service._agent = None
    agent_service._agent_factory = lambda **kwargs: fresh  # noqa: E731
    app_state.active_tome_id = "tome-1"

    result = await agent_service.compact_active_tome(app_state)

    assert result == "Compaction completed"
    fresh.initialize.assert_awaited_once()
    fresh.compact.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_prompt_adopts_tome_as_session_row(tmp_path: Path) -> None:
    """Successful turns must surface a persisted session row (Major #9).

    The engine creates the tome during the turn; run_prompt's finally block
    adopts it into state. Assert the session appears in loaded_tomes (what
    the Sessions page and Recent Sessions render) and becomes the active
    tome.
    """
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    tome_dir = tmp_path / "tomes"
    tome_service = TomeService(tome_dir=tome_dir)
    tome_id = TomeHandleFactory(tome_dir).create_tome(str(project_dir)).tome_id

    state = AppState(project_path=project_dir, tome_service=tome_service)
    stub = MagicMock()
    stub.tome_id = tome_id
    stub.run = AsyncMock()
    service = AgentService(
        project_path=project_dir,
        api_key="test-api-key",
        agent_factory=lambda **kwargs: stub,  # noqa: E731
    )
    message = ChatMessage(role="assistant", is_streaming=True)
    await service.run_prompt("hello", state, message)

    assert state.active_tome_id == tome_id
    assert tome_id in [entry.tome_id for entry in state.loaded_tomes]


# ---------------------------------------------------------------------------
# Lazy rune loading, API-key propagation, agent teardown, error rendering
# ---------------------------------------------------------------------------


class _LazyRuneAgent:
    """Test double: rune runner appears only after ``load_runes()`` runs.

    Mirrors the real Mvge delegation: ``get_registered_commands()`` serves
    the runner's commands once the runner exists.
    """

    def __init__(self) -> None:
        self._runner: Any = None
        self.load_runes_calls = 0
        self.close_calls = 0

    def get_registered_commands(self) -> list[Any]:
        if self._runner is None:
            return []
        return self._runner.get_commands()

    async def load_runes(self) -> None:
        self.load_runes_calls += 1
        runner = MagicMock()

        async def _handle_selfmod(args: str) -> str:
            return "selfmod-ok"

        command = MagicMock()
        command.name = "selfmod"
        command.description = "Run selfmod"
        command.handler = _handle_selfmod
        runner.get_commands.return_value = [command]
        self._runner = runner

    async def close(self) -> None:
        self.close_calls += 1


def _lazy_agent_service(tmp_path: Path, **kwargs: Any) -> AgentService:
    return AgentService(
        project_path=tmp_path,
        api_key="sk-test",
        agent_factory=lambda **kw: _LazyRuneAgent(),
        **kwargs,
    )


def test_resolve_api_key_reads_saved_gui_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saved GUI settings must feed key resolution when no env key exists."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    monkeypatch.delenv("MVGEOS_API_KEY", raising=False)
    # AUTH_FILE_PATH is expanded at import time: point it at tmp too.
    monkeypatch.setattr("mvgeos_agent.auth.AUTH_FILE_PATH", tmp_path / "auth.json")
    ConfigService().save_app_settings(AppSettings(api_key="sk-saved-gui"))

    # Asked for by Realm: the saved slot holds an OpenRouter credential, so it
    # is only an answer for OpenRouter.
    assert resolve_api_key(None, "openrouter") == "sk-saved-gui"


def test_resolve_api_key_env_beats_saved_gui_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Explicit env keys keep precedence over the saved GUI settings."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    # Asked for by realm: the default realm reads its own variable first, so an
    # OPENROUTER_API_KEY would not be consulted for it.
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-env")
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    monkeypatch.setattr("mvgeos_agent.auth.AUTH_FILE_PATH", tmp_path / "auth.json")
    ConfigService().save_app_settings(AppSettings(api_key="sk-saved-gui"))

    assert resolve_api_key(None, "openrouter") == "sk-env"


@pytest.mark.asyncio
async def test_set_api_key_rekeys_service_and_closes_old_agent(
    tmp_path: Path,
) -> None:
    """set_api_key must rekey future agents and retire the cached one."""
    seen_keys: list[Any] = []
    fakes: list[_LazyRuneAgent] = []

    def factory(**kwargs: Any) -> _LazyRuneAgent:
        seen_keys.append(kwargs.get("api_key"))
        fake = _LazyRuneAgent()
        fakes.append(fake)
        return fake

    service = AgentService(
        project_path=tmp_path, api_key="sk-old", agent_factory=factory
    )
    state = AppState(project_path=tmp_path)
    old_agent = service.get_or_create_agent(state)
    assert seen_keys == ["sk-old"]

    service.set_api_key("sk-new")
    for _ in range(100):
        if old_agent.close_calls:
            break
        await asyncio.sleep(0.01)
    assert old_agent.close_calls == 1

    new_agent = service.get_or_create_agent(state)
    assert new_agent is not old_agent
    assert seen_keys == ["sk-old", "sk-new"]

    # Re-saving the same key must not churn the agent.
    service.set_api_key("sk-new")
    await asyncio.sleep(0.05)
    assert old_agent.close_calls == 1
    assert service.get_or_create_agent(state) is new_agent


@pytest.mark.asyncio
async def test_rune_commands_dispatch_on_fresh_agent(tmp_path: Path) -> None:
    """Dynamic rune commands must dispatch on a fresh agent (no model turn).

    Regression: on a fresh page load the cached agent exists but its rune
    runner is not initialized, so /<rune-command> fell through to the model
    turn instead of dispatching locally.
    """
    service = _lazy_agent_service(tmp_path)
    state = AppState(project_path=tmp_path)
    agent = service.get_or_create_agent(state)
    assert isinstance(agent, _LazyRuneAgent)

    msg = ChatMessage(role="assistant", is_streaming=True)
    await service.run_prompt("/selfmod status", state, msg)

    assert agent.load_runes_calls == 1
    assert msg.content == "selfmod-ok"
    assert msg.is_streaming is False

    # A second command reuses the loaded runner without reloading.
    msg2 = ChatMessage(role="assistant", is_streaming=True)
    await service.run_prompt("/selfmod status", state, msg2)
    assert agent.load_runes_calls == 1
    assert msg2.content == "selfmod-ok"


@pytest.mark.asyncio
async def test_close_agent_closes_cached_agent(tmp_path: Path) -> None:
    """close_agent() must close the cached agent and drop the reference."""
    fakes: list[_LazyRuneAgent] = []
    service = AgentService(
        project_path=tmp_path,
        api_key="sk-test",
        agent_factory=lambda **kw: fakes.append(_LazyRuneAgent()) or fakes[-1],
    )
    state = AppState(project_path=tmp_path)
    first = service.get_or_create_agent(state)

    await service.close_agent()

    assert first.close_calls == 1
    # The reference was dropped: the next turn builds a fresh agent.
    second = service.get_or_create_agent(state)
    assert second is not first
    # Closing with nothing cached is a safe no-op.
    await service.close_agent()


@pytest.mark.asyncio
async def test_close_agent_tolerates_close_failure(tmp_path: Path) -> None:
    """A failing agent.close() must not propagate: teardown is fail-closed."""

    class _ExplodingAgent(_LazyRuneAgent):
        async def close(self) -> None:
            raise RuntimeError("watcher shutdown blew up")

    service = AgentService(
        project_path=tmp_path,
        api_key="sk-test",
        agent_factory=lambda **kw: _ExplodingAgent(),
    )
    state = AppState(project_path=tmp_path)
    first = service.get_or_create_agent(state)

    await service.close_agent()  # must not raise

    # The reference was still dropped: the next turn builds a fresh agent.
    assert service.get_or_create_agent(state) is not first


def test_close_agent_in_background_without_loop_drops_reference(
    tmp_path: Path,
) -> None:
    """Without a running loop the agent reference is dropped, not closed.

    There is no event loop to schedule the async close on (documented
    fallback); the stale agent is at least dereferenced.
    """
    service = _lazy_agent_service(tmp_path)
    state = AppState(project_path=tmp_path)
    agent = service.get_or_create_agent(state)

    service.close_agent_in_background()

    assert agent.close_calls == 0
    assert service.get_or_create_agent(state) is not agent


@pytest.mark.asyncio
async def test_lazy_rune_load_failure_does_not_break_dispatch(
    tmp_path: Path,
) -> None:
    """A failing load_runes() degrades to 'no dynamic commands', never a crash."""

    class _BrokenLoader(_LazyRuneAgent):
        async def load_runes(self) -> None:
            raise RuntimeError("rune dir unreadable")

    service = AgentService(
        project_path=tmp_path,
        api_key="sk-test",
        agent_factory=lambda **kw: _BrokenLoader(),
    )
    state = AppState(project_path=tmp_path)
    msg = ChatMessage(role="assistant", is_streaming=True)

    outcome = await service.dispatch_slash_command("/selfmod status", state, msg)

    assert outcome is not None
    assert outcome.action == CommandAction.ERROR


@pytest.mark.asyncio
async def test_drop_client_state_closes_client_agent(tmp_path: Path) -> None:
    """Client disconnect must stop the per-client agent's rune watchers."""
    server = ServerState()
    state = server.new_client_state()
    fake = _LazyRuneAgent()
    service = AgentService(
        project_path=tmp_path,
        api_key="sk-test",
        agent_factory=lambda **kw: fake,
    )
    state.agent_service = service
    service.get_or_create_agent(state)

    server.drop_client_state(state)

    for _ in range(100):
        if fake.close_calls:
            break
        await asyncio.sleep(0.01)
    assert fake.close_calls == 1


@pytest.mark.asyncio
async def test_dispatch_error_outcome_not_duplicated_in_body(
    tmp_path: Path,
) -> None:
    """ERROR outcomes render once as the error, not as body + error line."""
    service = _lazy_agent_service(tmp_path)
    state = AppState(project_path=tmp_path)
    msg = ChatMessage(role="assistant", is_streaming=True)

    outcome = await service.dispatch_slash_command(
        "/model definitely-not-a-real-model", state, msg
    )

    assert outcome is not None
    assert outcome.action == CommandAction.ERROR
    assert msg.error_message == (
        "Unknown model: definitely-not-a-real-model\n"
        "Try /refresh-models to fetch the latest catalog"
    )
    assert msg.content == ""


@pytest.mark.asyncio
async def test_dispatch_construction_failure_not_duplicated_in_body(
    tmp_path: Path,
) -> None:
    """Agent-construction failure renders once as the error, not body + error."""

    def _boom(**kwargs: Any) -> Any:
        raise RuntimeError("Cannot instantiate Mvge without an API key")

    service = AgentService(
        project_path=tmp_path, api_key="sk-test", agent_factory=_boom
    )
    state = AppState(project_path=tmp_path)
    msg = ChatMessage(role="assistant", is_streaming=True)

    outcome = await service.dispatch_slash_command("/help", state, msg)

    assert outcome is None
    assert msg.is_error is True
    assert msg.error_message == "Cannot instantiate Mvge without an API key"
    assert msg.content == ""


@pytest.mark.asyncio
async def test_execution_error_body_code_spans_exception_text(
    tmp_path: Path,
) -> None:
    """Raw exception text in the markdown body must not be emphasis-mangled.

    Identifiers like OPENROUTER_API_KEY would otherwise render as
    OPENROUTER*API*KEY (the sweep saw "OPENROUTERAPI/KEY").
    """

    class _FailingAgent(_LazyRuneAgent):
        async def run(self, prompt: object) -> None:
            raise RuntimeError("provider said OPENROUTER_API_KEY is bad")

    service = AgentService(
        project_path=tmp_path,
        api_key="sk-test",
        agent_factory=lambda **kw: _FailingAgent(),
    )
    state = AppState(project_path=tmp_path)
    msg = ChatMessage(role="assistant", is_streaming=True)

    await service.run_prompt("hello", state, msg)

    assert msg.is_error is True
    assert "OPENROUTER_API_KEY" in msg.content
    assert "`provider said OPENROUTER_API_KEY is bad`" in msg.content


@pytest.mark.asyncio
async def test_missing_key_error_names_openrouter_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The no-key error must name OPENROUTER_API_KEY (never OPENROUTERAPIKEY)."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    # Windows expanduser() uses USERPROFILE, not HOME: without this the
    # GUI-saved key leaks in from the real profile and can_create_agent
    # wrongly returns True.
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    monkeypatch.delenv("MVGEOS_API_KEY", raising=False)
    monkeypatch.setattr("mvgeos_agent.auth.AUTH_FILE_PATH", tmp_path / "auth.json")

    service = AgentService(project_path=tmp_path)
    assert service.can_create_agent() is False
    state = AppState(project_path=tmp_path)
    msg = ChatMessage(role="assistant", is_streaming=True)

    await service.run_prompt("hello", state, msg)

    assert "OPENROUTER_API_KEY" in msg.content
    assert "OPENROUTERAPIKEY" not in msg.content
