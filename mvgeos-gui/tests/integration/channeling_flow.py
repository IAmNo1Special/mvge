"""Integration tests for live agent channeling, Mana tracking, and step cards."""

import time
from pathlib import Path
from typing import Any

import pytest
from nicegui import ui
from nicegui.testing import User

from mvgeos_gui.app import build_page
from mvgeos_gui.models import (
    ChatMessage,
    CommandExecution,
    ExecutionStep,
    FileExploration,
    MessagePart,
    MessagePartType,
    StepType,
)
from mvgeos_gui.state import AppState
from mvgeos_gui.transcript import InvocationTranscript


def _part(kind: MessagePartType, text: str) -> MessagePart:
    return MessagePart(part_type=kind, text=text)


def _assistant_msg(*parts: MessagePart, **kwargs: Any) -> ChatMessage:
    msg = ChatMessage(role="assistant", **kwargs)
    msg.parts.extend(parts)
    return msg


@pytest.mark.asyncio
async def test_user_and_assistant_message_rendering(user: User) -> None:
    """Verify user message bubble and assistant response bubble rendering."""
    state = AppState(project_path=Path("C:/demo/project"))
    state.messages.append(
        InvocationTranscript.for_summoner("Refactor the database module")
    )
    state.messages.append(
        _assistant_msg(
            _part(
                MessagePartType.TEXT, "I have refactored the database connection pool."
            ),
            model="nvidia/nemotron-3-ultra-550b-a55b:free",
            mana_used=1250,
        )
    )

    @ui.page("/test_messages_render")
    def page() -> None:
        build_page(state)

    await user.open("/test_messages_render")

    # User message
    await user.should_see("Summoner")
    await user.should_see("Refactor the database module")

    # Assistant message
    await user.should_see("Mvge")
    await user.should_see("nemotron-3-ultra")
    await user.should_see("1,250 Mana")
    await user.should_see("I have refactored the database connection pool.")


@pytest.mark.asyncio
async def test_collapsible_step_cards_in_conversation(user: User) -> None:
    """Verify intermediate step cards render in assistant response bubble."""
    state = AppState(project_path=Path("C:/demo/project"))
    assistant_msg = _assistant_msg(
        MessagePart(
            part_type=MessagePartType.STEP,
            step=ExecutionStep(
                step_type=StepType.WORKED,
                title="Worked for 3.1s",
                details=["Analyzed directory structure", "Parsed abstract syntax tree"],
                is_complete=True,
            ),
        ),
        MessagePart(
            part_type=MessagePartType.STEP,
            step=ExecutionStep(
                step_type=StepType.FILES,
                title="Explored 2 files",
                files=[
                    FileExploration(
                        path="src/main.py", lines="L1-100", operation="read"
                    ),
                    FileExploration(
                        path="src/utils.py", lines="L50-75", operation="grep"
                    ),
                ],
            ),
        ),
        MessagePart(
            part_type=MessagePartType.STEP,
            step=ExecutionStep(
                step_type=StepType.COMMANDS,
                title="Ran 1 command",
                commands=[
                    CommandExecution(
                        command="git status",
                        output="On branch main\nnothing to commit",
                        duration_seconds=0.15,
                    )
                ],
            ),
        ),
        _part(MessagePartType.TEXT, "Here are the search results."),
    )
    state.messages.append(assistant_msg)

    @ui.page("/test_step_cards_flow")
    def page() -> None:
        build_page(state)

    await user.open("/test_step_cards_flow")

    await user.should_see("Worked for 3.1s")
    await user.should_see("Explored 2 files")
    await user.should_see("Ran 1 command")
    await user.should_see("src/main.py")
    await user.should_see("git status")


@pytest.mark.asyncio
async def test_streaming_indicator_and_stop_button(user: User) -> None:
    """Verify channeling indicator and Stop button during active generation."""
    state = AppState(project_path=Path("C:/demo/project"), is_channeling=True)
    state.messages.append(
        _assistant_msg(
            _part(MessagePartType.TEXT, "Drafting response..."),
            is_streaming=True,
        )
    )

    @ui.page("/test_channeling_flow")
    def page() -> None:
        build_page(state)

    await user.open("/test_channeling_flow")

    await user.should_see("Channeling response...")

    # Click stop button
    user.find("stop_channeling_btn").click()
    assert state.is_channeling is False


@pytest.mark.asyncio
async def test_channeling_elapsed_timer_beside_stop_button(user: User) -> None:
    """Verify the elapsed channeling timer renders beside the stop button."""
    state = AppState(project_path=Path("C:/demo/project"), is_channeling=True)
    state.channeling_started_at = time.monotonic() - 65

    @ui.page("/test_channeling_elapsed")
    def page() -> None:
        build_page(state)

    await user.open("/test_channeling_elapsed")

    (elapsed_element,) = user.find("channeling_elapsed").elements
    assert elapsed_element.text == "1m 05s"
    # The stop button itself still renders untouched
    user.find("stop_channeling_btn")


@pytest.mark.asyncio
async def test_feedback_buttons_interaction(user: User) -> None:
    """Verify thumbs up and thumbs down feedback toggle on assistant message."""
    state = AppState(project_path=Path("C:/demo/project"))
    state.messages.append(
        _assistant_msg(
            _part(MessagePartType.TEXT, "All unit tests passed successfully."),
            model="google/gemini-2.5-pro",
        )
    )

    @ui.page("/test_feedback_flow")
    def page() -> None:
        build_page(state)

    await user.open("/test_feedback_flow")

    # Set thumbs up
    state.set_message_feedback(0, "up")
    assert state.messages[0].feedback == "up"

    # Toggle thumbs down
    state.set_message_feedback(0, "down")
    assert state.messages[0].feedback == "down"


@pytest.mark.asyncio
async def test_model_selector_dropdown(user: User) -> None:
    """Verify switching models via state."""
    state = AppState(project_path=Path("C:/demo/project"))

    @ui.page("/test_model_selector")
    def page() -> None:
        build_page(state)

    await user.open("/test_model_selector")

    state.switch_model("google/gemini-2.5-pro")
    assert state.selected_model == "google/gemini-2.5-pro"


@pytest.mark.asyncio
async def test_contemplation_card_in_conversation(user: User) -> None:
    """Verify contemplation/thought block renders separately from response content."""
    state = AppState(project_path=Path("C:/demo/project"))
    state.messages.append(
        _assistant_msg(
            _part(
                MessagePartType.CONTEMPLATION,
                "User wants greeting. Respond politely.",
            ),
            _part(MessagePartType.TEXT, "Hey there! How can I help you today?"),
            model="nvidia/nemotron-3-ultra-550b-a55b:free",
        )
    )

    @ui.page("/test_thought_rendering")
    def page() -> None:
        build_page(state)

    await user.open("/test_thought_rendering")
    await user.should_see("Contemplation")
    await user.should_see("User wants greeting. Respond politely.")
    await user.should_see("Hey there! How can I help you today?")


@pytest.mark.asyncio
async def test_multiple_contemplation_segments(user: User) -> None:
    """Verify each reasoning segment renders in its own Thought card."""
    state = AppState(project_path=Path("C:/demo/project"))
    state.messages.append(
        _assistant_msg(
            _part(
                MessagePartType.CONTEMPLATION,
                "First, I need to understand the request.",
            ),
            _part(
                MessagePartType.CONTEMPLATION,
                "Now I will plan the implementation.",
            ),
            _part(MessagePartType.TEXT, "Here is the implementation plan."),
            model="nvidia/nemotron-3-ultra-550b-a55b:free",
        )
    )

    @ui.page("/test_multi_thought")
    def page() -> None:
        build_page(state)

    await user.open("/test_multi_thought")
    await user.should_see("Contemplation")
    await user.should_see("First, I need to understand the request.")
    await user.should_see("Now I will plan the implementation.")
    await user.should_see("Here is the implementation plan.")


@pytest.mark.asyncio
async def test_interleaved_thoughts_and_steps_flow(user: User) -> None:
    """Verify interleaved thoughts and tool steps render in order without merging."""
    state = AppState(project_path=Path("C:/demo/project"))
    step = ExecutionStep(
        step_type=StepType.FILES,
        title="Explored 1 file(s)",
        files=[
            FileExploration(path="auth.py", operation="read", details="class Auth: ...")
        ],
        is_complete=True,
    )
    msg = ChatMessage(
        role="assistant",
        parts=[
            MessagePart(
                part_type=MessagePartType.CONTEMPLATION,
                text="Checking auth implementation before editing.",
            ),
            MessagePart(
                part_type=MessagePartType.STEP,
                step=step,
            ),
            MessagePart(
                part_type=MessagePartType.CONTEMPLATION,
                text="Now I see how Auth is structured. Proceeding with answer.",
            ),
            MessagePart(
                part_type=MessagePartType.TEXT,
                text="Auth is configured properly.",
            ),
        ],
        model="nvidia/nemotron-3-ultra-550b-a55b:free",
    )
    state.messages.append(msg)

    @ui.page("/test_interleaved_thoughts")
    def page() -> None:
        build_page(state)

    await user.open("/test_interleaved_thoughts")
    await user.should_see("Checking auth implementation before editing.")
    await user.should_see("Explored 1 file(s)")
    await user.should_see("Now I see how Auth is structured. Proceeding with answer.")
    await user.should_see("Auth is configured properly.")
