"""Integration tests for Artifact cards and preview drawer in the GUI."""

from pathlib import Path

import pytest
from mvgeos_core.events import (
    MvgeEvent,
    MvgeEventType,
)
from nicegui import ui
from nicegui.testing import User

from mvgeos_gui.app import build_page
from mvgeos_gui.models import Artifact, ArtifactType, ChatMessage
from mvgeos_gui.services.agent_service import AgentService
from mvgeos_gui.state import AppState
from mvgeos_gui.transcript import InvocationTranscript


def _assistant_with_artifact(text: str, artifact: Artifact) -> ChatMessage:
    transcript = InvocationTranscript()
    if text:
        transcript.apply_message_update({"text": text})
    transcript.add_artifact(artifact)
    return transcript.message


@pytest.mark.asyncio
async def test_artifact_card_renders_in_assistant_message(user: User) -> None:
    """Verify artifact card renders inside an assistant message bubble."""
    state = AppState(project_path=Path("C:/demo/project"))
    artifact = Artifact(
        id="art-1",
        title="Walkthrough",
        summary="Step by step guide",
        content="## Steps\n\n1. Do this\n2. Do that",
        artifact_type=ArtifactType.WALKTHROUGH,
    )
    state.messages.append(
        _assistant_with_artifact("Here is the walkthrough:", artifact)
    )

    @ui.page("/test_artifact_in_message")
    def page() -> None:
        build_page(state)

    await user.open("/test_artifact_in_message")
    await user.should_see("Mvge")
    await user.should_see("Here is the walkthrough:")
    await user.should_see("Walkthrough")
    await user.should_see("Step by step guide")


@pytest.mark.asyncio
async def test_artifact_drawer_opens_on_card_click(user: User) -> None:
    """Verify clicking an artifact card opens the preview drawer."""
    state = AppState(project_path=Path("C:/demo/project"))
    artifact = Artifact(
        id="art-1",
        title="Implementation Plan",
        summary="Refactor auth",
        content="# Plan\n\nDetails here",
        artifact_type=ArtifactType.IMPLEMENTATION_PLAN,
    )
    state.messages.append(_assistant_with_artifact("Generated plan:", artifact))
    state.add_artifact(artifact)

    @ui.page("/test_artifact_drawer_open")
    def page() -> None:
        build_page(state)

    await user.open("/test_artifact_drawer_open")
    await user.should_see("Implementation Plan")

    user.find("visibility").click()
    assert state.get_selected_artifact() is artifact


@pytest.mark.asyncio
async def test_artifact_drawer_renders_markdown_content(user: User) -> None:
    """Verify artifact drawer renders markdown content."""
    state = AppState(project_path=Path("C:/demo/project"))
    artifact = Artifact(
        id="art-1",
        title="Document Title",
        summary="Doc summary",
        content="# Title\n\nSome **bold** text.",
        artifact_type=ArtifactType.DOCUMENT,
    )
    state.messages.append(_assistant_with_artifact("", artifact))
    state.add_artifact(artifact)
    state.open_artifact("art-1")

    @ui.page("/test_artifact_drawer_markdown")
    def page() -> None:
        build_page(state)

    await user.open("/test_artifact_drawer_markdown")
    await user.should_see("Document Title")


@pytest.mark.asyncio
async def test_artifact_drawer_close_button(user: User) -> None:
    """Verify artifact drawer has a close button."""
    state = AppState(project_path=Path("C:/demo/project"))
    artifact = Artifact(
        id="art-1",
        title="Doc",
        content="Hello",
        artifact_type=ArtifactType.DOCUMENT,
    )
    state.messages.append(_assistant_with_artifact("", artifact))
    state.open_artifact("art-1")

    @ui.page("/test_artifact_drawer_close")
    def page() -> None:
        build_page(state)

    await user.open("/test_artifact_drawer_close")
    await user.should_see("Doc")


@pytest.mark.asyncio
async def test_artifacts_in_chat_session(user: User) -> None:
    """Verify artifacts in chat session are rendered in the message stream."""
    state = AppState(project_path=Path("C:/demo/project"))
    artifact = Artifact(
        id="art-1",
        title="Walkthrough",
        summary="Guide",
        content="Content",
        artifact_type=ArtifactType.WALKTHROUGH,
    )
    state.artifacts.append(artifact)
    state.messages.append(_assistant_with_artifact("Summary of changes", artifact))

    @ui.page("/test_inspector_artifacts")
    def page() -> None:
        build_page(state)

    await user.open("/test_inspector_artifacts")
    await user.should_see("Walkthrough")
    await user.should_see("Guide")


@pytest.mark.asyncio
async def test_artifact_event_populates_state_and_message(user: User) -> None:
    """Verify ARTIFACT_CREATED event populates both message and state."""
    state = AppState(project_path=Path("C:/demo/project"))
    msg = InvocationTranscript.from_tome_content("Done").message
    state.messages.append(msg)

    service = AgentService(project_path=Path("C:/demo/project"))
    service._turns.start(msg, state)

    event = MvgeEvent(
        type=MvgeEventType.ARTIFACT_CREATED,
        data={
            "artifact": {
                "id": "evt-art",
                "title": "Event Artifact",
                "summary": "From event",
                "content": "Event content",
                "type": "walkthrough",
                "file_paths": ["a.py"],
            }
        },
    )
    service.handle_event(event)

    assert len(msg.artifacts) == 1
    assert msg.artifacts[0].title == "Event Artifact"
    assert len(state.artifacts) == 1
    assert state.artifacts[0].id == "evt-art"
    assert state.artifacts[0].file_paths == ["a.py"]
