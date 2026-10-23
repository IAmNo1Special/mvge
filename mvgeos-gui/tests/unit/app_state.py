"""Unit tests for AppState management in mvgeos-gui."""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mvgeos_core.approval import (
    ApprovalOutcome,
    ApprovalReasonCode,
    ApprovalRequest,
    ApprovalScope,
)
from mvgeos_core.constants import DEFAULT_MODEL
from mvgeos_provider import get_model_options
from mvgeos_provider.realms import DEFAULT_REALM, realm_for_model_id
from mvgeos_tome.handle import TomeHandleFactory
from mvgeos_tome.types import TomeEntry, TomeEntryType
from nicegui import ui
from nicegui.testing import User

from mvgeos_gui import state as state_module
from mvgeos_gui.approval.types import PermissionsView
from mvgeos_gui.autocomplete import MentionChip
from mvgeos_gui.models import ChangedFile, ChatMessage, DiffView
from mvgeos_gui.services.agent_service import AgentService
from mvgeos_gui.services.config_service import ConfigService, WorkspaceSettings
from mvgeos_gui.services.tome_service import TomeService
from mvgeos_gui.state import AppState, ServerState, format_channeling_elapsed
from mvgeos_gui.transcript import InvocationTranscript


def test_app_state_defaults() -> None:
    """Verify default initial values for AppState."""
    state = AppState()
    assert state.project_path == Path.cwd()
    assert state.active_tome_id is None
    assert state.tome_title == "New Conversation"
    assert state.inspector_expanded is True
    assert state.selected_model == DEFAULT_MODEL
    assert state.is_channeling is False
    assert isinstance(state.recent_projects, list)
    assert state.tome_service is not None
    assert state.loaded_tomes == []
    assert state.messages == []
    assert state.total_mana_used == 0
    assert state.pending_attachments == []


def test_app_state_custom_init() -> None:
    """Verify custom initialization options for AppState."""
    custom_path = Path("/custom/project")
    state = AppState(
        project_path=custom_path,
        selected_model="custom/model",
        inspector_expanded=False,
    )
    assert state.project_path == custom_path
    assert state.selected_model == "custom/model"
    assert state.inspector_expanded is False
    assert custom_path in state.recent_projects


def test_toggle_sidebar() -> None:
    """Verify toggling sidebar visibility state."""
    state = AppState()
    assert state.sidebar_open is True
    state.toggle_sidebar()
    assert state.sidebar_open is False
    state.toggle_sidebar()
    assert state.sidebar_open is True


def test_toggle_inspector() -> None:
    """Verify toggling inspector expansion state."""
    state = AppState()
    assert state.inspector_expanded is True
    state.toggle_inspector()
    assert state.inspector_expanded is False
    state.toggle_inspector()
    assert state.inspector_expanded is True


def test_set_project() -> None:
    """Verify switching active workspace project path."""
    state = AppState(project_path=Path("/initial/dir"))
    new_dir = Path("/new/project/dir")
    state.set_project(new_dir)
    assert state.project_path == new_dir
    assert new_dir in state.recent_projects
    assert state.recent_projects[0] == new_dir


def test_add_recent_project_deduplication_and_ordering() -> None:
    """Verify recent projects are deduplicated and newest is first."""
    state = AppState()
    p1 = Path("/path/one")
    p2 = Path("/path/two")
    state.add_recent_project(p1)
    state.add_recent_project(p2)
    assert state.recent_projects[0] == p2
    assert state.recent_projects[1] == p1

    # Adding p1 again moves it to index 0
    state.add_recent_project(p1)
    assert state.recent_projects[0] == p1
    assert state.recent_projects.count(p1) == 1


def test_recent_projects_max_limit() -> None:
    """Verify recent projects list is capped at 10 items."""
    state = AppState()
    for i in range(15):
        state.add_recent_project(Path(f"/path/{i}"))
    assert len(state.recent_projects) == 10
    assert state.recent_projects[0] == Path("/path/14")


def test_new_conversation() -> None:
    """Verify resetting conversation session state."""
    state = AppState()
    state.active_tome_id = "tome-123"
    state.tome_title = "Refactoring loop"
    state.is_channeling = True
    state.messages.append(InvocationTranscript.for_summoner("hello"))
    state.total_mana_used = 500

    state.new_conversation()
    assert state.active_tome_id is None
    assert state.tome_title == "New Conversation"
    assert state.is_channeling is False
    assert state.messages == []
    assert state.total_mana_used == 0


def test_listeners_and_exception_handling() -> None:
    """Verify subscriptions and safe exception swallowing in notify."""
    state = AppState()
    called: list[str] = []

    def good_listener() -> None:
        called.append("good")

    def failing_listener() -> None:
        raise RuntimeError("boom")

    state.subscribe(good_listener)
    state.subscribe(good_listener)  # idempotent
    state.subscribe(failing_listener)

    state.notify()
    assert called == ["good"]


def test_switch_model() -> None:
    """Verify switching active Realm model updates state and notifies."""
    state = AppState()
    called: list[bool] = []
    state.subscribe(lambda: called.append(True))

    state.switch_model("anthropic/claude-3-5-sonnet")
    assert state.selected_model == "anthropic/claude-3-5-sonnet"
    assert called == [True]


def test_set_message_feedback() -> None:
    """Verify setting and toggling message feedback status."""
    state = AppState()
    msg = InvocationTranscript.from_tome_content("Response").message
    state.messages.append(msg)

    # Set up
    state.set_message_feedback(0, "up")
    assert msg.feedback == "up"

    # Toggling same feedback clears it
    state.set_message_feedback(0, "up")
    assert msg.feedback is None

    # Setting down
    state.set_message_feedback(0, "down")
    assert msg.feedback == "down"

    # Out of range is safe no-op
    state.set_message_feedback(99, "up")


@pytest.mark.asyncio
async def test_submit_prompt_appends_messages_and_starts_task() -> None:
    """Verify submit_prompt creates user and assistant messages."""
    state = AppState()
    mock_service = MagicMock()
    mock_service.run_prompt = AsyncMock()
    state.agent_service = mock_service

    state.submit_prompt("Build a widget")
    assert len(state.messages) == 2
    assert state.messages[0].role == "user"
    assert state.messages[0].content == "Build a widget"
    assert state.messages[1].role == "assistant"
    assert state.messages[1].is_streaming is True
    assert state.is_channeling is True

    # Empty prompt is a no-op
    state.submit_prompt("")
    assert len(state.messages) == 2


def test_stop_channeling_cancels_active_task() -> None:
    """Verify stop_channeling marks streaming as false and resets channeling state."""
    state = AppState()
    msg = ChatMessage(role="assistant", is_streaming=True)
    state.messages.append(msg)
    state.is_channeling = True

    mock_task = MagicMock()
    state.active_task = mock_task
    mock_service = MagicMock()
    state.agent_service = mock_service

    state.stop_channeling()
    assert state.is_channeling is False
    assert msg.is_streaming is False
    mock_task.cancel.assert_called_once()
    mock_service.cancel.assert_called_once()


@pytest.mark.asyncio
async def test_submit_prompt_records_channeling_start_time() -> None:
    """Verify submit_prompt stamps when channeling started for the elapsed timer."""
    state = AppState()
    assert state.channeling_started_at is None
    mock_service = MagicMock()
    mock_service.run_prompt = AsyncMock()
    state.agent_service = mock_service

    state.submit_prompt("Build a widget")

    assert state.is_channeling is True
    assert state.channeling_started_at is not None
    elapsed = state.elapsed_channeling_seconds()
    assert elapsed is not None
    assert elapsed >= 0.0


def test_stop_channeling_clears_channeling_start_time() -> None:
    """Verify stop_channeling clears the elapsed-timer stamp."""
    state = AppState()
    state.is_channeling = True
    state.channeling_started_at = 1234.5

    state.agent_service = MagicMock()
    state.stop_channeling()

    assert state.channeling_started_at is None
    assert state.elapsed_channeling_seconds() is None


def test_elapsed_channeling_seconds_none_when_not_channeling() -> None:
    """Verify the elapsed helper returns None outside channeling."""
    state = AppState()
    assert state.elapsed_channeling_seconds() is None

    state.channeling_started_at = 1234.5
    assert state.elapsed_channeling_seconds() is None


def test_elapsed_channeling_seconds_measures_from_start() -> None:
    """Verify elapsed time is measured from the recorded start stamp."""
    state = AppState()
    state.is_channeling = True
    state.channeling_started_at = time.monotonic() - 7.5

    elapsed = state.elapsed_channeling_seconds()
    assert elapsed is not None
    assert elapsed >= 7.5
    assert elapsed < 9.0


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0s"),
        (5, "5s"),
        (59, "59s"),
        (60, "1m 00s"),
        (65, "1m 05s"),
        (125, "2m 05s"),
    ],
)
def test_format_channeling_elapsed(seconds: float, expected: str) -> None:
    """Verify the channeling timer formats seconds compactly."""
    assert format_channeling_elapsed(seconds) == expected


# --- Tome service integration tests ---


def _make_state_with_tomes(
    project_cwd: str = "/test/project",
) -> tuple[AppState, Path]:
    """Create AppState backed by a temp tome dir with no tombs yet."""
    tmp_dir = tempfile.mkdtemp()
    tome_dir = Path(tmp_dir)
    service = TomeService(tome_dir)
    state = AppState(project_path=Path(project_cwd), tome_service=service)
    return state, tome_dir


def _create_tome(tome_dir: Path, cwd: str, tome_id: str | None = None) -> str:
    factory = TomeHandleFactory(tome_dir)
    return factory.create_tome(cwd, tome_id=tome_id).tome_id


def _append_message(
    tome_dir: Path, tome_id: str, role: str, content: str, entry_id: str
) -> TomeEntry:
    entry = TomeEntry(
        id=entry_id,
        parent_id=None,
        type=TomeEntryType.MESSAGE,
        timestamp=1000.0,
        payload={"role": role, "content": content},
    )
    TomeHandleFactory(tome_dir).open_write(tome_id).append(entry)
    return entry


def _append_leaf(tome_dir: Path, tome_id: str, target: str, entry_id: str) -> TomeEntry:
    entry = TomeEntry(
        id=entry_id,
        parent_id=None,
        type=TomeEntryType.LEAF,
        timestamp=1001.0,
        payload={"targetId": target},
    )
    TomeHandleFactory(tome_dir).open_write(tome_id).append(entry)
    return entry


def _append_tome_info(tome_dir: Path, tome_id: str, payload: dict) -> None:
    TomeHandleFactory(tome_dir).open_write(tome_id).append(
        TomeEntry(
            id="info-1",
            parent_id=None,
            type=TomeEntryType.TOME_INFO,
            timestamp=1002.0,
            payload=payload,
        )
    )


class TestLoadTomes:
    def test_loads_tomes_for_active_project(self) -> None:
        state, tome_dir = _make_state_with_tomes("/proj/a")
        _create_tome(tome_dir, "/proj/a")
        _create_tome(tome_dir, "/proj/b")

        state.load_tomes()

        assert len(state.loaded_tomes) == 1
        assert state.loaded_tomes[0].tome_id is not None

    def test_filters_out_other_projects(self) -> None:
        state, tome_dir = _make_state_with_tomes("/proj/a")
        _create_tome(tome_dir, "/proj/a")
        _create_tome(tome_dir, "/proj/b")

        state.load_tomes()

        assert len(state.loaded_tomes) == 1

    def test_empty_when_no_tomes(self) -> None:
        state, tome_dir = _make_state_with_tomes("/proj/a")

        state.load_tomes()

        assert state.loaded_tomes == []

    def test_sets_active_flag_on_loaded_tomes(self) -> None:
        state, tome_dir = _make_state_with_tomes("/proj/a")
        tome_id = _create_tome(tome_dir, "/proj/a")
        state.active_tome_id = tome_id

        state.load_tomes()

        active_entries = [e for e in state.loaded_tomes if e.is_active]
        assert len(active_entries) == 1
        assert active_entries[0].tome_id == tome_id

    def test_notifies_listeners(self) -> None:
        state, tome_dir = _make_state_with_tomes("/proj/a")
        called: list[bool] = []
        state.subscribe(lambda: called.append(True))
        _create_tome(tome_dir, "/proj/a")

        state.load_tomes()

        assert called == [True]


class TestSwitchToTome:
    def test_sets_active_tome_id_and_title(self) -> None:
        state, tome_dir = _make_state_with_tomes("/proj/a")
        tome_id = _create_tome(tome_dir, "/proj/a")
        _append_tome_info(tome_dir, tome_id, {"name": "Bug Fix Session"})

        state.switch_to_tome(tome_id)

        assert state.active_tome_id == tome_id
        assert state.tome_title == "Bug Fix Session"

    def test_loads_existing_messages_from_tome(self) -> None:
        state, tome_dir = _make_state_with_tomes("/proj/a")
        tome_id = _create_tome(tome_dir, "/proj/a")
        _append_message(tome_dir, tome_id, "user", "How do I fix this?", "m1")
        _append_message(tome_dir, tome_id, "assistant", "Here is the fix.", "m2")

        state.switch_to_tome(tome_id)

        assert len(state.messages) == 2
        assert state.messages[0].content == "How do I fix this?"
        assert state.messages[1].content == "Here is the fix."

    def test_sets_title_from_tome_info_title_key(self) -> None:
        state, tome_dir = _make_state_with_tomes("/proj/a")
        tome_id = _create_tome(tome_dir, "/proj/a")
        _append_tome_info(tome_dir, tome_id, {"title": "Refactor Loop"})

        state.switch_to_tome(tome_id)

        assert state.tome_title == "Refactor Loop"

    def test_title_fallback_conversation(self) -> None:
        state, tome_dir = _make_state_with_tomes("/proj/a")
        tome_id = _create_tome(tome_dir, "/proj/a")

        state.switch_to_tome(tome_id)

        assert state.tome_title == "Conversation"

    def test_clears_channeling(self) -> None:
        state, tome_dir = _make_state_with_tomes("/proj/a")
        state.is_channeling = True
        tome_id = _create_tome(tome_dir, "/proj/a")

        state.switch_to_tome(tome_id)

        assert state.is_channeling is False

    def test_unknown_tome_id_is_noop(self) -> None:
        state, _ = _make_state_with_tomes("/proj/a")
        state.active_tome_id = None
        state.tome_title = "New Conversation"

        state.switch_to_tome("nonexistent_tome_id")

        assert state.active_tome_id is None
        assert state.tome_title == "New Conversation"

    def test_notifies_listeners(self) -> None:
        state, tome_dir = _make_state_with_tomes("/proj/a")
        called: list[bool] = []
        state.subscribe(lambda: called.append(True))
        tome_id = _create_tome(tome_dir, "/proj/a")

        state.switch_to_tome(tome_id)

        assert called == [True, True]


class TestForkTome:
    def test_forks_active_tome_and_switches(self) -> None:
        state, tome_dir = _make_state_with_tomes("/proj/a")
        tome_id = _create_tome(tome_dir, "/proj/a")
        entry = _append_message(tome_dir, tome_id, "user", "hello", "m1")
        _append_leaf(tome_dir, tome_id, entry.id, "l1")
        state.active_tome_id = tome_id
        state.tome_title = "Original"

        forked_id = state.fork_tome()

        assert forked_id is not None
        assert state.active_tome_id == forked_id
        assert state.active_tome_id != tome_id

    def test_no_active_tome_returns_none(self) -> None:
        state, _ = _make_state_with_tomes("/proj/a")

        assert state.fork_tome() is None

    def test_no_leaf_returns_none(self) -> None:
        state, tome_dir = _make_state_with_tomes("/proj/a")
        tome_id = _create_tome(tome_dir, "/proj/a")
        state.active_tome_id = tome_id

        assert state.fork_tome() is None


class TestExportTome:
    def test_exports_to_project_dir(self) -> None:
        with tempfile.TemporaryDirectory() as proj_tmp:
            state, tome_dir = _make_state_with_tomes(proj_tmp)
            tome_id = _create_tome(tome_dir, proj_tmp)
            _append_message(tome_dir, tome_id, "user", "hello", "m1")
            _append_message(tome_dir, tome_id, "assistant", "hi", "m2")
            state.active_tome_id = tome_id

            result = state.export_tome()

            assert result is not None
            assert result.suffix == ".jsonl"
            assert result.exists()

    def test_no_active_tome_returns_none(self) -> None:
        state, _ = _make_state_with_tomes("/proj/a")

        assert state.export_tome() is None

    def test_export_filename_uses_short_id(self) -> None:
        with tempfile.TemporaryDirectory() as proj_tmp:
            state, tome_dir = _make_state_with_tomes(proj_tmp)
            tome_id = _create_tome(tome_dir, proj_tmp)
            state.active_tome_id = tome_id

            result = state.export_tome()

            assert result is not None
            assert result.name == f"{tome_id[:8]}.jsonl"


class TestClearHistory:
    def test_resets_conversation_state(self) -> None:
        state, tome_dir = _make_state_with_tomes("/proj/a")
        tome_id = _create_tome(tome_dir, "/proj/a")
        state.active_tome_id = tome_id
        state.tome_title = "Old Session"
        state.is_channeling = True

        state.clear_history()

        assert state.active_tome_id is None
        assert state.tome_title == "New Conversation"
        assert state.is_channeling is False

    def test_notifies_listeners(self) -> None:
        state, _ = _make_state_with_tomes("/proj/a")
        called: list[bool] = []
        state.subscribe(lambda: called.append(True))

        state.clear_history()

        assert called == [True]


class TestSetProjectReloadsTomes:
    def test_set_project_loads_tomes_for_new_project(self) -> None:
        state, tome_dir = _make_state_with_tomes("/proj/a")
        _create_tome(tome_dir, "/proj/b")

        state.set_project(Path("/proj/b"))

        assert len(state.loaded_tomes) == 1


# --- Attachment tests ---


class TestPendingAttachments:
    def test_add_attachment_appends(self) -> None:
        state = AppState()
        state.add_attachment("main.py")
        state.add_attachment("utils.py")
        assert state.pending_attachments == ["main.py", "utils.py"]

    def test_add_attachment_deduplicates(self) -> None:
        state = AppState()
        state.add_attachment("main.py")
        state.add_attachment("main.py")
        assert state.pending_attachments == ["main.py"]

    def test_add_attachment_empty_is_noop(self) -> None:
        state = AppState()
        state.add_attachment("")
        assert state.pending_attachments == []

    def test_add_attachment_notifies_listeners(self) -> None:
        state = AppState()
        called: list[bool] = []
        state.subscribe(lambda: called.append(True))
        state.add_attachment("main.py")
        assert called == [True]

    def test_remove_attachment_by_index(self) -> None:
        state = AppState()
        state.pending_attachments = ["a.py", "b.py", "c.py"]
        state.remove_attachment(1)
        assert state.pending_attachments == ["a.py", "c.py"]

    def test_remove_attachment_out_of_range_safe(self) -> None:
        state = AppState()
        state.pending_attachments = ["a.py"]
        state.remove_attachment(99)
        assert state.pending_attachments == ["a.py"]
        state.remove_attachment(-1)
        assert state.pending_attachments == ["a.py"]

    def test_remove_attachment_notifies_listeners(self) -> None:
        state = AppState()
        state.pending_attachments = ["a.py", "b.py"]
        called: list[bool] = []
        state.subscribe(lambda: called.append(True))
        state.remove_attachment(0)
        assert called == [True]

    def test_clear_attachments(self) -> None:
        state = AppState()
        state.pending_attachments = ["a.py", "b.py"]
        state.clear_attachments()
        assert state.pending_attachments == []

    def test_clear_attachments_empty_is_noop(self) -> None:
        state = AppState()
        called: list[bool] = []
        state.subscribe(lambda: called.append(True))
        state.clear_attachments()
        assert state.pending_attachments == []
        assert called == []

    def test_set_project_clears_attachments(self) -> None:
        state = AppState(project_path=Path("/proj/a"))
        state.pending_attachments = ["a.py"]
        state.set_project(Path("/proj/b"))
        assert state.pending_attachments == []


class TestSubmitPromptAttachments:
    def test_submit_prompt_binds_attachments_to_message(self) -> None:
        state = AppState()
        state.pending_attachments = ["file1.py", "file2.py"]
        mock_service = MagicMock()
        mock_service.run_prompt = AsyncMock()
        state.agent_service = mock_service

        state.submit_prompt("Analyze these files")

        user_msg = state.messages[0]
        assert user_msg.attachments == ["file1.py", "file2.py"]

    def test_submit_prompt_clears_pending_attachments(self) -> None:
        state = AppState()
        state.pending_attachments = ["file1.py"]
        mock_service = MagicMock()
        mock_service.run_prompt = AsyncMock()
        state.agent_service = mock_service

        state.submit_prompt("Hello")

        assert state.pending_attachments == []

    @pytest.mark.asyncio
    async def test_submit_prompt_empty_with_attachments_sends(self) -> None:
        state = AppState()
        state.pending_attachments = ["file1.py"]
        state.pending_attachment_contents = {"file1.py": b"print('hi')"}
        mock_service = MagicMock()
        mock_service.run_prompt = AsyncMock()
        state.agent_service = mock_service

        state.submit_prompt("")

        assert state.pending_attachments == []
        assert state.pending_attachment_contents == {}
        user_msg = state.messages[0]
        assert user_msg.attachments == ["file1.py"]
        assert "[Attached file:" not in user_msg.content
        mock_service.run_prompt.assert_called_once()
        sent = mock_service.run_prompt.call_args[0][0]
        assert isinstance(sent, list)
        assert sent[0]["type"] == "file"
        assert sent[0]["file"]["filename"] == "file1.py"

    @pytest.mark.asyncio
    async def test_submit_prompt_text_with_attachments_sends_parts(self) -> None:
        state = AppState()
        state.pending_attachments = ["pic.png"]
        state.pending_attachment_contents = {"pic.png": b"\x89PNG"}
        mock_service = MagicMock()
        mock_service.run_prompt = AsyncMock()
        state.agent_service = mock_service

        state.submit_prompt("look at this")

        user_msg = state.messages[0]
        assert user_msg.attachments == ["pic.png"]
        assert user_msg.content == "look at this"
        mock_service.run_prompt.assert_called_once()
        sent = mock_service.run_prompt.call_args[0][0]
        assert isinstance(sent, list)
        assert sent[0] == {"type": "text", "text": "look at this"}
        assert sent[1]["type"] == "image_url"

    @pytest.mark.asyncio
    async def test_submit_prompt_without_attachments_sends_plain_string(self) -> None:
        state = AppState()
        mock_service = MagicMock()
        mock_service.run_prompt = AsyncMock()
        state.agent_service = mock_service

        state.submit_prompt("Hello")

        mock_service.run_prompt.assert_called_once()
        sent = mock_service.run_prompt.call_args[0][0]
        assert sent == "Hello"

    def test_submit_prompt_no_attachments_field_empty(self) -> None:
        state = AppState()
        mock_service = MagicMock()
        mock_service.run_prompt = AsyncMock()
        state.agent_service = mock_service

        state.submit_prompt("Hello")

        user_msg = state.messages[0]
        assert user_msg.attachments == []

    def test_new_conversation_clears_attachments(self) -> None:
        state = AppState()
        state.pending_attachments = ["file1.py"]
        state.new_conversation()
        assert state.pending_attachments == []

    def test_clear_history_clears_attachments(self) -> None:
        state = AppState()
        state.pending_attachments = ["file1.py"]
        state.clear_history()
        assert state.pending_attachments == []


class TestSelectedMentions:
    def test_add_mention_appends_and_notifies(self) -> None:
        state = AppState()
        called: list[bool] = []
        state.subscribe(lambda: called.append(True))
        chip = MentionChip(text="@main.py", kind="file", icon="code")
        state.add_mention(chip)
        assert state.selected_mentions == [chip]
        assert called == [True]

    def test_remove_last_mention_removes_recent(self) -> None:
        state = AppState()
        state.selected_mentions = [
            MentionChip(text="@a.py", kind="file"),
            MentionChip(text="/help", kind="slash"),
        ]
        state.remove_last_mention()
        assert len(state.selected_mentions) == 1
        assert state.selected_mentions[0].text == "@a.py"

    def test_remove_last_mention_empty_is_noop(self) -> None:
        state = AppState()
        called: list[bool] = []
        state.subscribe(lambda: called.append(True))
        state.remove_last_mention()
        assert state.selected_mentions == []
        assert called == []

    def test_clear_mentions_empties_list(self) -> None:
        state = AppState()
        state.selected_mentions = [
            MentionChip(text="@a.py", kind="file"),
            MentionChip(text="/help", kind="slash"),
        ]
        called: list[bool] = []
        state.subscribe(lambda: called.append(True))
        state.clear_mentions()
        assert state.selected_mentions == []
        assert called == [True]

    def test_clear_mentions_empty_is_noop(self) -> None:
        state = AppState()
        called: list[bool] = []
        state.subscribe(lambda: called.append(True))
        state.clear_mentions()
        assert state.selected_mentions == []
        assert called == []

    def test_submit_prompt_prepends_mentions(self) -> None:
        state = AppState()
        state.selected_mentions = [
            MentionChip(text="@main.py", kind="file"),
            MentionChip(text="/help", kind="slash"),
        ]
        mock_service = MagicMock()
        mock_service.run_prompt = AsyncMock()
        state.agent_service = mock_service

        state.submit_prompt("Analyze this")

        user_msg = state.messages[0]
        assert user_msg.content == "@main.py /help Analyze this"
        assert state.selected_mentions == []

    def test_submit_prompt_mentions_only(self) -> None:
        state = AppState()
        state.selected_mentions = [
            MentionChip(text="@main.py", kind="file"),
        ]
        mock_service = MagicMock()
        mock_service.run_prompt = AsyncMock()
        state.agent_service = mock_service

        state.submit_prompt("")

        user_msg = state.messages[0]
        assert user_msg.content == "@main.py"
        assert state.selected_mentions == []

    def test_submit_prompt_no_mentions(self) -> None:
        state = AppState()
        mock_service = MagicMock()
        mock_service.run_prompt = AsyncMock()
        state.agent_service = mock_service

        state.submit_prompt("Hello world")

        user_msg = state.messages[0]
        assert user_msg.content == "Hello world"
        assert state.selected_mentions == []

    def test_new_conversation_clears_mentions(self) -> None:
        state = AppState()
        state.selected_mentions = [MentionChip(text="@a.py", kind="file")]
        state.new_conversation()
        assert state.selected_mentions == []

    def test_set_project_clears_mentions(self) -> None:
        state = AppState(project_path=Path("/proj/a"))
        state.selected_mentions = [MentionChip(text="@a.py", kind="file")]
        state.set_project(Path("/proj/b"))
        assert state.selected_mentions == []


# --- Active skills tests ---


def _make_manifest(
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


def test_active_skills_default_empty() -> None:
    """Verify active_skills defaults to an empty list."""
    state = AppState()
    assert state.active_skills == []


def test_skill_info_from_manifest() -> None:
    """Verify SkillInfo is derived from a SkillManifest correctly."""
    state = AppState()
    manifest = _make_manifest(
        name="review",
        path="/skills/review/SKILL.md",
        scope="user",
        description="Review code",
    )
    info = state.skill_info_from_manifest(manifest)
    assert info.name == "review"
    assert info.description == "Review code"
    assert info.scope == "user"
    assert info.path == "/skills/review/SKILL.md"
    assert info.invoked is False


def test_add_skill_appends_and_notifies() -> None:
    """Verify add_skill appends a new skill and notifies listeners."""
    state = AppState()
    called: list[bool] = []
    state.subscribe(lambda: called.append(True))

    manifest = _make_manifest()
    added = state.add_skill(manifest)
    assert added is True
    assert len(state.active_skills) == 1
    assert state.active_skills[0].name == "review"
    assert called == [True]


def test_add_skill_deduplicates() -> None:
    """Verify add_skill does not duplicate an existing skill by name."""
    state = AppState()
    manifest = _make_manifest()
    state.add_skill(manifest)
    state.add_skill(manifest)
    assert len(state.active_skills) == 1


def test_add_skill_returns_false_when_present() -> None:
    """Verify add_skill returns False when the skill is already tracked."""
    state = AppState()
    manifest = _make_manifest()
    state.add_skill(manifest)
    assert state.add_skill(manifest) is False


def test_remove_skill_by_name() -> None:
    """Verify remove_skill removes a skill by name."""
    state = AppState()
    state.add_skill(_make_manifest(name="review"))
    state.add_skill(_make_manifest(name="lint", path="/skills/lint/SKILL.md"))
    removed = state.remove_skill("review")
    assert removed is True
    assert [s.name for s in state.active_skills] == ["lint"]


def test_remove_skill_unknown_is_safe() -> None:
    """Verify remove_skill is a safe no-op for unknown names."""
    state = AppState()
    state.add_skill(_make_manifest())
    assert state.remove_skill("nonexistent") is False
    assert len(state.active_skills) == 1


def test_remove_skill_notifies_listeners() -> None:
    """Verify remove_skill notifies listeners when a skill is removed."""
    state = AppState()
    state.add_skill(_make_manifest())
    called: list[bool] = []
    state.subscribe(lambda: called.append(True))
    state.remove_skill("review")
    assert called == [True]


def test_clear_skills_removes_all() -> None:
    """Verify clear_skills empties the active_skills list."""
    state = AppState()
    state.add_skill(_make_manifest(name="review"))
    state.add_skill(_make_manifest(name="lint", path="/skills/lint/SKILL.md"))
    state.clear_skills()
    assert state.active_skills == []


def test_clear_skills_empty_is_noop() -> None:
    """Verify clear_skills does not notify when there are no skills."""
    state = AppState()
    called: list[bool] = []
    state.subscribe(lambda: called.append(True))
    state.clear_skills()
    assert called == []


def test_new_conversation_clears_skills() -> None:
    """Verify new_conversation resets active_skills."""
    state = AppState()
    state.add_skill(_make_manifest())
    state.new_conversation()
    assert state.active_skills == []


class TestChangedFiles:
    def test_default_changed_files_empty(self) -> None:
        """Verify changed_files defaults to empty list."""
        state = AppState()
        assert state.changed_files == []

    def test_refresh_changed_files_updates_state(self) -> None:
        """Verify refresh_changed_files updates changed_files from git."""
        state = AppState()
        changed = [
            ChangedFile(
                path="src/main.py",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ]
        with patch("mvgeos_gui.state.get_changed_files", return_value=changed):
            state.refresh_changed_files()
        assert len(state.changed_files) == 1
        assert state.changed_files[0].path == "src/main.py"

    def test_refresh_changed_files_notifies_listeners(self) -> None:
        """Verify refresh_changed_files notifies listeners."""
        state = AppState()
        called: list[bool] = []
        state.subscribe(lambda: called.append(True))
        with patch("mvgeos_gui.state.get_changed_files", return_value=[]):
            state.refresh_changed_files()
        assert called == [True]

    def test_open_diff_review_sets_selected_path(self) -> None:
        """Verify open_diff_review sets _selected_diff_path."""
        state = AppState()
        state.open_diff_review("src/main.py")
        assert state._selected_diff_path == "src/main.py"

    def test_open_diff_review_notifies_listeners(self) -> None:
        """Verify open_diff_review notifies listeners."""
        state = AppState()
        called: list[bool] = []
        state.subscribe(lambda: called.append(True))
        state.open_diff_review("src/main.py")
        assert called == [True]

    def test_get_selected_diff_view_returns_none_when_empty(self) -> None:
        """Verify get_selected_diff_view returns None when no path selected."""
        state = AppState()
        assert state.get_selected_diff_view() is None

    def test_get_selected_diff_view_returns_view(self) -> None:
        """Verify get_selected_diff_view returns DiffView for selected path."""
        state = AppState()
        state._selected_diff_path = "src/main.py"
        view = DiffView(
            file_path="src/main.py",
            status="modified",
            additions=1,
            deletions=0,
        )
        with patch("mvgeos_gui.state.get_diff_for_file", return_value=view):
            result = state.get_selected_diff_view()
        assert result is not None
        assert result.file_path == "src/main.py"

    def test_clear_diff_selection_clears_path(self) -> None:
        """Verify clear_diff_selection clears _selected_diff_path."""
        state = AppState()
        state._selected_diff_path = "src/main.py"
        state.clear_diff_selection()
        assert state._selected_diff_path is None

    def test_clear_diff_selection_notifies_listeners(self) -> None:
        """Verify clear_diff_selection notifies listeners."""
        state = AppState()
        state._selected_diff_path = "src/main.py"
        called: list[bool] = []
        state.subscribe(lambda: called.append(True))
        state.clear_diff_selection()
        assert called == [True]


class TestSettingsModals:
    def test_open_app_settings_sets_flag(self) -> None:
        state = AppState()
        assert state._show_app_settings is False
        state.open_app_settings()
        assert state._show_app_settings is True

    def test_open_app_settings_closes_workspace(self) -> None:
        state = AppState()
        state._show_workspace_settings = True
        state.open_app_settings()
        assert state._show_workspace_settings is False
        assert state._show_app_settings is True

    def test_close_app_settings_clears_flag(self) -> None:
        state = AppState()
        state._show_app_settings = True
        state.close_app_settings()
        assert state._show_app_settings is False

    def test_open_workspace_settings_sets_flag(self) -> None:
        state = AppState()
        assert state._show_workspace_settings is False
        state.open_workspace_settings()
        assert state._show_workspace_settings is True

    def test_open_workspace_settings_closes_app(self) -> None:
        state = AppState()
        state._show_app_settings = True
        state.open_workspace_settings()
        assert state._show_app_settings is False
        assert state._show_workspace_settings is True

    def test_close_workspace_settings_clears_flag(self) -> None:
        state = AppState()
        state._show_workspace_settings = True
        state.close_workspace_settings()
        assert state._show_workspace_settings is False

    def test_open_app_settings_notifies_listeners(self) -> None:
        state = AppState()
        called: list[bool] = []
        state.subscribe(lambda: called.append(True))
        state.open_app_settings()
        assert called == [True]

    def test_open_workspace_settings_notifies_listeners(self) -> None:
        state = AppState()
        called: list[bool] = []
        state.subscribe(lambda: called.append(True))
        state.open_workspace_settings()
        assert called == [True]

    def test_unsubscribe_listener(self) -> None:
        state = AppState()
        called: list[int] = []

        def listener() -> None:
            called.append(1)

        state.subscribe(listener)
        state.notify()
        assert len(called) == 1
        state.unsubscribe(listener)
        state.notify()
        assert len(called) == 1

    def test_clear_listeners(self) -> None:
        state = AppState()
        called: list[int] = []

        state.subscribe(lambda: called.append(1))
        state.subscribe(lambda: called.append(2))
        assert len(state._change_listeners) == 2
        state.clear_listeners()
        assert len(state._change_listeners) == 0
        state.notify()
        assert called == []

    def test_notify_coroutine_without_running_loop_closes_coroutine(
        self,
    ) -> None:
        import warnings

        state = AppState()
        executed = False

        async def async_listener() -> None:
            nonlocal executed
            executed = True

        state.subscribe(async_listener)
        with warnings.catch_warnings(record=True) as record:
            warnings.simplefilter("always")
            state.notify()
            unawaited_warnings = [
                (w.category, str(w.message))
                for w in record
                if issubclass(w.category, RuntimeWarning)
                and "was never awaited" in str(w.message)
            ]
            assert not unawaited_warnings, f"Found unawaited: {unawaited_warnings}"
        assert executed is False

    @pytest.mark.asyncio
    async def test_notify_coroutine_with_running_loop(self) -> None:
        state = AppState()
        called: list[bool] = []

        async def async_listener() -> None:
            called.append(True)

        state.subscribe(async_listener)
        state.notify()
        await asyncio.sleep(0.01)
        assert called == [True]

    def test_command_palette_toggle_and_set(self) -> None:
        state = AppState()
        assert state.command_palette_open is False
        state.set_command_palette_open(True)
        assert state.command_palette_open is True
        state.toggle_command_palette()
        assert state.command_palette_open is False


class TestMvgeStatus:
    def test_default_status_is_idle(self) -> None:
        state = AppState()
        assert state.mvge_status == "idle"

    def test_set_mvge_status_updates_field(self) -> None:
        state = AppState()
        state.set_mvge_status("channeling")
        assert state.mvge_status == "channeling"
        state.set_mvge_status("working")
        assert state.mvge_status == "working"

    def test_set_mvge_status_notifies_listeners(self) -> None:
        state = AppState()
        called: list[bool] = []
        state.subscribe(lambda: called.append(True))
        state.set_mvge_status("channeling")
        assert called == [True]


def test_notify_does_not_call_private_fire() -> None:
    """Verify notify does not invoke private _fire to prevent duplicate refreshes
    and unawaited coroutines."""
    state = AppState()

    fired = False

    class FakeAwaitableResponse:
        async def _fire(self) -> None:
            nonlocal fired
            fired = True

    fake = FakeAwaitableResponse()

    def listener() -> FakeAwaitableResponse:
        return fake

    state.subscribe(listener)
    state.notify()
    assert fired is False


class TestLoadMessagesForTome:
    def test_notifies_listeners_after_loading(self, tmp_path: Path) -> None:
        """load_messages_for_tome must notify subscribers after loading entries."""
        state, tome_dir = _make_state_with_tomes(str(tmp_path))
        tome_id = _create_tome(tome_dir, str(tmp_path))
        _append_message(tome_dir, tome_id, "user", "Hello", "m1")
        _append_message(tome_dir, tome_id, "assistant", "Hi there", "m2")

        called: list[bool] = []
        state.subscribe(lambda: called.append(True))
        state.load_messages_for_tome(tome_id)

        assert called == [True]
        assert len(state.messages) == 2


class TestCardExpansion:
    """Verify card expansion state tracking in AppState."""

    def test_default_expansion(self) -> None:
        state = AppState()
        assert state.is_card_expanded("card_1", default=False) is False
        assert state.is_card_expanded("card_1", default=True) is True

    def test_set_card_expanded(self) -> None:
        state = AppState()
        state.set_card_expansion("thought_0_0", True)
        assert state.is_card_expanded("thought_0_0", default=False) is True
        assert state.is_card_expanded("thought_0_0", default=True) is True

    def test_set_card_collapsed(self) -> None:
        state = AppState()
        state.set_card_expansion("thought_0_0", True)
        state.set_card_expansion("thought_0_0", False)
        assert state.is_card_expanded("thought_0_0", default=True) is False

    def test_set_card_expansion_does_not_notify(self) -> None:
        state = AppState()
        called: list[bool] = []
        state.subscribe(lambda: called.append(True))
        state.set_card_expansion("step_1_0", True)
        assert called == []


class TestStreamingListeners:
    """Verify isolated streaming listener registration and dispatch."""

    def test_streaming_listener_isolation(self) -> None:
        state = AppState()
        general_notified: list[int] = []
        streaming_notified: list[int] = []

        state.subscribe(lambda: general_notified.append(1))
        state.subscribe_streaming(lambda: streaming_notified.append(1))

        # Rapid streaming notification only notifies streaming listener
        state.notify_streaming()
        assert streaming_notified == [1]
        assert general_notified == []

        # Full notify triggers only general listeners
        state.notify()
        assert general_notified == [1]
        assert streaming_notified == [1]

    def test_streaming_fallback_when_no_streaming_listeners(self) -> None:
        state = AppState()
        general_notified: list[int] = []
        state.subscribe(lambda: general_notified.append(1))

        # Falls back to general listener when no streaming listeners registered
        state.notify_streaming()
        assert general_notified == [1]

    def test_unsubscribe_streaming(self) -> None:
        state = AppState()
        notified: list[int] = []

        def cb() -> None:
            notified.append(1)

        state.subscribe_streaming(cb)
        state.notify_streaming()
        assert notified == [1]

        state.unsubscribe_streaming(cb)
        state.notify_streaming()
        assert notified == [1]


class TestViewListeners:
    def test_subscribe_view_registers_globally(self) -> None:
        state = AppState()
        notified: list[int] = []
        state.subscribe_view("panel", lambda: notified.append(1))
        state.notify()
        assert notified == [1]
        assert state.view_listener_count("panel") == 1
        assert state.view_listener_count() == 1

    def test_subscribe_view_dedupes(self) -> None:
        state = AppState()

        def cb() -> None:
            pass

        state.subscribe_view("panel", cb)
        state.subscribe_view("panel", cb)
        assert state.view_listener_count("panel") == 1

    def test_subscribe_streaming_view_targets_streaming(self) -> None:
        state = AppState()
        notified: list[int] = []
        state.subscribe_streaming_view("bubble", lambda: notified.append(1))
        state.notify_streaming()
        assert notified == [1]
        assert state.view_listener_count("bubble") == 1

    def test_clear_view_listeners_detaches_key(self) -> None:
        state = AppState()
        notified: list[int] = []
        state.subscribe_view("panel", lambda: notified.append(1))
        state.subscribe_view("other", lambda: notified.append(1))
        state.clear_view_listeners("panel")
        state.notify()
        assert notified == [1]
        assert state.view_listener_count("panel") == 0
        assert state.view_listener_count() == 1

    def test_clear_view_listeners_missing_key_is_noop(self) -> None:
        state = AppState()
        state.clear_view_listeners("absent")
        assert state.view_listener_count() == 0

    def test_clear_all_view_listeners(self) -> None:
        state = AppState()
        state.subscribe_view("a", lambda: None)
        state.subscribe_streaming_view("b", lambda: None)
        state.clear_all_view_listeners()
        assert state.view_listener_count() == 0
        notified: list[int] = []
        state.subscribe(lambda: notified.append(1))
        state.notify()
        assert notified == [1]


class TestCascadingSelectorState:
    def test_default_cascading_state(self) -> None:
        state = AppState()
        assert state.selected_realm == DEFAULT_REALM
        # Derived from the model, not copied from the Realm: the two coincide
        # only while the default slug is directly served, and asserting the copy
        # would make this a test of the default's shape rather than of the
        # selector's starting state.
        assert state.selected_provider == realm_for_model_id(DEFAULT_MODEL)
        assert state.selected_model == DEFAULT_MODEL
        assert state.contemplation_level == "medium"
        # The Realm must be selectable on a fresh install, which is the state
        # this default has to work in.
        assert state.selected_realm in state.get_realms()

    def test_default_selector_state_matches_the_default_model(self) -> None:
        """Realm, provider and model must agree on the first screen.

        They were three separate literals, so the model could be changed and the
        selector would still open on a Realm that cannot serve it -- and its own
        options would not contain the value it was set to.
        """
        state = AppState()
        assert state.selected_model == DEFAULT_MODEL
        assert state.selected_realm == realm_for_model_id(DEFAULT_MODEL) or (
            state.selected_realm == DEFAULT_REALM
        )
        assert state.selected_realm in state.get_realms()
        # The provider tier the selector opens on is the id prefix, which is the
        # Realm for a directly-served slug and the provider for a routed one.
        # Asserted as a derivation so this holds under either shape of default.
        assert state.selected_provider == realm_for_model_id(DEFAULT_MODEL)

    def test_get_realms(self) -> None:
        state = AppState()
        realms = state.get_realms()
        assert "openrouter" in realms

    def test_a_routed_slug_selects_its_own_provider_tier(self) -> None:
        """A routed slug's provider tier comes from the id, not from the Realm.

        With ``selected_provider`` set to the Realm instead, the cascading
        selector asks the catalog for models under provider ``openrouter``, finds
        the router's own entry, and opens offering "Free Models Router" for a
        model that is neither routed nor a router selection. Asserted through
        ``get_models_for_selection`` because that is where the consequence lands.
        """
        state = AppState()
        state.selected_realm = "openrouter"
        state.selected_model = "nvidia/nemotron-3-ultra-550b-a55b:free"
        state.selected_provider = realm_for_model_id(state.selected_model)

        models = state.get_models_for_selection()

        assert state.selected_model in models
        assert "openrouter/free" not in models

    def test_get_providers_for_router_and_direct(self) -> None:
        # Router-realm behaviour, so it is asked of the router Realm explicitly.
        # Asserting it of whatever the default happens to be makes the test a
        # question about the default model.
        state = AppState()
        state.selected_realm = "openrouter"
        providers = state.get_providers_for_selected_realm()
        assert isinstance(providers, list)
        assert len(providers) > 0
        assert "nvidia" in providers

        # Direct realm has no providers
        with patch.object(state, "is_router_realm", return_value=False):
            assert state.get_providers_for_selected_realm() == []

    def test_switch_realm_to_direct_clears_provider(self) -> None:
        state = AppState()
        with patch("mvgeos_gui.state.is_realm_router", return_value=False):
            state.switch_realm("ollama")
            assert state.selected_realm == "ollama"
            assert state.selected_provider is None

    def test_switch_provider_cascades_model(self) -> None:
        state = AppState()
        state.switch_provider("anthropic")
        assert state.selected_provider == "anthropic"

    def test_switch_model_syncs_provider_prefix(self) -> None:
        # Only a router Realm has providers to sync against, so the Realm is
        # selected explicitly rather than inherited from the default model.
        state = AppState()
        state.selected_realm = "openrouter"
        state.switch_model("anthropic/claude-3-5-sonnet")
        assert state.selected_model == "anthropic/claude-3-5-sonnet"
        assert state.selected_provider == "anthropic"

    def test_set_contemplation_level(self) -> None:
        state = AppState()
        called: list[bool] = []
        state.subscribe(lambda: called.append(True))
        state.set_contemplation_level("x-high")
        assert state.contemplation_level == "x-high"
        assert called == [True]

    def test_supports_contemplation_and_levels(self) -> None:
        # Pinned to a reasoning-capable model rather than left on the default.
        # The default is a free tier with no contemplation parameter, so asking
        # "does the selector offer Contemplation" about whatever it happens to be
        # would be a question about the default model wearing a selector test.
        state = AppState()
        state.selected_model = "nvidia/nemotron-3-ultra-550b-a55b:free"
        levels = state.get_contemplation_levels_for_selected_model()
        assert isinstance(levels, list)
        assert state.supports_contemplation_for_selected_model() is True

    def test_get_model_options_for_selection_router_strips_provider_prefix(
        self,
    ) -> None:
        state = AppState()
        options = state.get_model_options_for_selection()
        assert DEFAULT_MODEL in options
        # The label is whatever the catalog says, not a literal: the display name
        # is catalog data and a copy of it here would fail on a rename that was
        # never a behaviour change.
        assert options[DEFAULT_MODEL] == get_model_options()[DEFAULT_MODEL]

    def test_get_model_options_for_selection_direct_preserves_names(self) -> None:
        state = AppState()
        mock_models = {"direct/model-1": "Direct: Model 1"}
        with (
            patch.object(state, "is_router_realm", return_value=False),
            patch("mvgeos_gui.state.get_model_options", return_value=mock_models),
            patch.object(
                state, "get_models_for_selection", return_value=["direct/model-1"]
            ),
        ):
            options = state.get_model_options_for_selection()
            assert options["direct/model-1"] == "Direct: Model 1"


class TestRuneManagementState:
    """Unit tests for rune marketplace and extension management in AppState."""

    def test_is_rune_installed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = AppState()
        monkeypatch.setenv("MVGEOS_GLOBAL_DIR", str(tmp_path))
        (tmp_path / "extensions" / "test-rune").mkdir(parents=True)
        assert state.is_rune_installed("test-rune") is True
        assert state.is_rune_installed("missing-rune") is False

    @pytest.mark.asyncio
    async def test_fetch_marketplace_runes_async(self) -> None:
        state = AppState()
        mock_catalog = {"openrouter-realm": {"version": "0.1.0"}}
        with patch(
            "mvgeos_gui.state.fetch_marketplace_runes", return_value=mock_catalog
        ):
            result = await state.fetch_marketplace_runes_async()
            assert result == mock_catalog

    @pytest.mark.asyncio
    async def test_list_installed_runes_async(self) -> None:
        state = AppState()
        mock_installed = [{"name": "test-rune", "version": "1.0.0"}]
        with patch(
            "mvgeos_gui.state.list_installed_runes", return_value=mock_installed
        ):
            result = await state.list_installed_runes_async()
            assert result == mock_installed

    @pytest.mark.asyncio
    async def test_install_rune_async_success(self) -> None:
        state = AppState()
        notified: list[bool] = []
        state.subscribe(lambda: notified.append(True))
        mock_agent = MagicMock()
        mock_agent._load_runes = AsyncMock()
        state.agent_service = MagicMock()
        state.agent_service._agent = mock_agent

        with patch(
            "mvgeos_gui.state.install_rune", return_value=Path("/tmp/installed")
        ):
            result = await state.install_rune_async("my-rune")
            assert result is True
            assert len(notified) > 0
            mock_agent._load_runes.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_install_rune_async_failure(self) -> None:
        state = AppState()
        with patch("mvgeos_gui.state.install_rune", side_effect=RuntimeError("fail")):
            result = await state.install_rune_async("bad-rune")
            assert result is False

    @pytest.mark.asyncio
    async def test_uninstall_rune_async(self) -> None:
        state = AppState()
        notified: list[bool] = []
        state.subscribe(lambda: notified.append(True))

        with (
            patch(
                "mvgeos_gui.state.uninstall_rune", return_value=True
            ) as mock_uninstall,
            patch("mvgeos_gui.state.get_default_realm_registry") as mock_registry_fn,
        ):
            mock_registry = MagicMock()
            mock_registry_fn.return_value = mock_registry
            result = await state.uninstall_rune_async("my-rune")
            assert result is True
            mock_uninstall.assert_called_once_with("my-rune")
            mock_registry.unregister_realm_factory.assert_called_once_with("my-rune")
            assert len(notified) > 0

    def test_is_mvge_installed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = AppState()
        monkeypatch.setenv("MVGEOS_GLOBAL_DIR", str(tmp_path))
        # A hyphenated name resolves to the underscore spelling on disk.
        agent_dir = tmp_path / "agents" / "my_mvge"
        assert state.is_mvge_installed("my_mvge") is False
        agent_dir.mkdir(parents=True)
        assert state.is_mvge_installed("my-mvge") is True

    @pytest.mark.asyncio
    async def test_fetch_marketplace_mvges_async(self) -> None:
        state = AppState()
        mock_data = {"test": {"name": "test"}}
        with patch("mvgeos_gui.state.fetch_marketplace_mvges", return_value=mock_data):
            result = await state.fetch_marketplace_mvges_async()
            assert result == mock_data

    @pytest.mark.asyncio
    async def test_list_installed_mvges_async(self) -> None:
        state = AppState()
        mock_installed = [{"name": "test"}]
        with patch(
            "mvgeos_gui.state.list_installed_mvges", return_value=mock_installed
        ):
            result = await state.list_installed_mvges_async()
            assert result == mock_installed

    @pytest.mark.asyncio
    async def test_install_mvge_async_success(self) -> None:
        state = AppState()
        notified: list[bool] = []
        state.subscribe(lambda: notified.append(True))
        with patch(
            "mvgeos_gui.state.install_mvge", return_value=Path("/tmp/installed")
        ):
            result = await state.install_mvge_async("my-mvge")
            assert result is True
            assert len(notified) > 0

    @pytest.mark.asyncio
    async def test_install_mvge_async_failure(self) -> None:
        state = AppState()
        with patch("mvgeos_gui.state.install_mvge", side_effect=RuntimeError("fail")):
            result = await state.install_mvge_async("bad-mvge")
            assert result is False

    @pytest.mark.asyncio
    async def test_uninstall_mvge_async_success(self) -> None:
        state = AppState()
        notified: list[bool] = []
        state.subscribe(lambda: notified.append(True))
        with patch(
            "mvgeos_gui.state.uninstall_mvge", return_value=True
        ) as mock_uninstall:
            result = await state.uninstall_mvge_async("my-mvge")
            assert result is True
            mock_uninstall.assert_called_once_with("my-mvge")
            assert len(notified) > 0

    @pytest.mark.asyncio
    async def test_uninstall_mvge_async_failure(self) -> None:
        state = AppState()
        with patch("mvgeos_gui.state.uninstall_mvge", side_effect=RuntimeError("fail")):
            result = await state.uninstall_mvge_async("bad-mvge")
            assert result is False


class TestRenameTome:
    def test_rename_updates_title_and_appends_tome_info(self) -> None:
        state, tome_dir = _make_state_with_tomes("/proj/a")
        tome_id = _create_tome(tome_dir, "/proj/a")
        state.active_tome_id = tome_id
        state.tome_title = "Old Title"

        assert state.rename_tome("New Title") is True

        assert state.tome_title == "New Title"
        assert state.tome_service.get_tome_title(tome_id) == "New Title"
        info_entries = [
            e
            for e in TomeHandleFactory(tome_dir).get_entries(tome_id)
            if e.type == TomeEntryType.TOME_INFO
        ]
        assert info_entries
        assert info_entries[-1].payload["title"] == "New Title"

    def test_rename_no_active_tome_returns_false(self) -> None:
        state, _ = _make_state_with_tomes("/proj/a")

        assert state.rename_tome("Anything") is False
        assert state.tome_title == "New Conversation"

    def test_rename_blank_title_returns_false(self) -> None:
        state, tome_dir = _make_state_with_tomes("/proj/a")
        tome_id = _create_tome(tome_dir, "/proj/a")
        state.active_tome_id = tome_id

        assert state.rename_tome("   ") is False

        info_entries = [
            e
            for e in TomeHandleFactory(tome_dir).get_entries(tome_id)
            if e.type == TomeEntryType.TOME_INFO
        ]
        assert info_entries == []

    def test_rename_latest_title_wins(self) -> None:
        state, tome_dir = _make_state_with_tomes("/proj/a")
        tome_id = _create_tome(tome_dir, "/proj/a")
        state.active_tome_id = tome_id

        assert state.rename_tome("First") is True
        assert state.rename_tome("Second") is True

        assert state.tome_title == "Second"
        assert state.tome_service.get_tome_title(tome_id) == "Second"

    def test_rename_missing_tome_file_returns_false(self) -> None:
        state, _ = _make_state_with_tomes("/proj/a")
        state.active_tome_id = "does-not-exist"

        assert state.rename_tome("Ghost") is False


def test_switch_to_tome_resets_cached_agent() -> None:
    """Verify switching tomes closes the cached agent.

    Otherwise the next send would run against the previously attached tome
    instead of the newly active one, and the old agent's rune watchers
    would keep firing.
    """
    state, tome_dir = _make_state_with_tomes("/proj/a")
    tome_id = _create_tome(tome_dir, "/proj/a")
    state.agent_service = MagicMock()

    state.switch_to_tome(tome_id)

    state.agent_service.close_agent_in_background.assert_called_once_with()


def test_new_conversation_resets_cached_agent() -> None:
    """Verify starting a new session closes the cached agent.

    Otherwise the next send would resume the old tome instead of creating
    a fresh one, and the old agent's rune watchers would keep firing.
    """
    state, _tome_dir = _make_state_with_tomes("/proj/a")
    state.agent_service = MagicMock()

    state.new_conversation()

    state.agent_service.close_agent_in_background.assert_called_once_with()


def test_fork_tome_preserves_custom_title() -> None:
    """Verify a forked session keeps the parent's custom title.

    The engine fork copies the message chain up to the leaf, which drops
    the parent_id-less TOME_INFO title entry, so the GUI re-applies it.
    """
    state, tome_dir = _make_state_with_tomes("/proj/a")
    tome_id = _create_tome(tome_dir, "/proj/a")
    entry = _append_message(tome_dir, tome_id, "user", "hello", "m1")
    _append_leaf(tome_dir, tome_id, entry.id, "l1")
    _append_tome_info(tome_dir, tome_id, {"title": "My Session"})
    state.active_tome_id = tome_id
    state.tome_title = "My Session"

    forked_id = state.fork_tome()

    assert forked_id is not None
    assert state.tome_title == "My Session"
    assert state.tome_service.get_tome_title(forked_id) == "My Session"


def _approval_request(cast_id: str = "call_1") -> ApprovalRequest:
    """Build a minimal approval request for queue tests."""
    return ApprovalRequest(
        cast_id=cast_id,
        spell_name="write",
        spell_identity={
            "name": "write",
            "source_kind": "builtin",
            "source_id": "",
            "runner_origin": "false",
            "read_only": "false",
        },
        arguments={"path": "/proj/a.txt"},
        argument_digest="sha256:args",
        project_root="/proj/a",
        tome_id="t1",
        agent_name="coding_mvge",
    )


@pytest.mark.asyncio
async def test_enqueue_approval_activates_and_resolves() -> None:
    """AppState owns the queue: enqueue activates the head, resolve advances."""
    state = AppState()
    state.project_path = Path("/proj/a")
    future = state.enqueue_approval(_approval_request("call_1"))
    assert state.approval_active_request is not None
    assert state.approval_active_request.cast_id == "call_1"
    assert state.approval_pending_count == 0

    state.enqueue_approval(_approval_request("call_2"))
    assert state.approval_pending_count == 1

    state.active_tome_id = "t1"
    effective = state.resolve_active_approval(
        ApprovalOutcome.ALLOW, ApprovalScope.ONCE, ApprovalReasonCode.USER
    )
    assert effective is not None
    assert effective.outcome is ApprovalOutcome.ALLOW
    assert future.result().outcome is ApprovalOutcome.ALLOW
    # Queue advanced to the next cast.
    assert state.approval_active_request is not None
    assert state.approval_active_request.cast_id == "call_2"


@pytest.mark.asyncio
async def test_deny_active_approval_on_dialog_close() -> None:
    """Closing the dialog denies the active request and advances the queue."""
    state = AppState()
    future = state.enqueue_approval(_approval_request("call_1"))
    state.deny_active_approval()
    assert future.done()
    assert future.result().outcome is ApprovalOutcome.DENY
    assert state.approval_active_request is None


@pytest.mark.asyncio
async def test_stale_dialog_close_does_not_deny_next_cast() -> None:
    """A close from a destroyed dialog must not deny the newly active cast."""
    state = AppState()
    state.project_path = Path("/proj/a")
    state.active_tome_id = "t1"
    f1 = state.enqueue_approval(_approval_request("call_1"))
    f2 = state.enqueue_approval(_approval_request("call_2"))

    state.resolve_active_approval(
        ApprovalOutcome.ALLOW, ApprovalScope.ONCE, ApprovalReasonCode.USER
    )
    assert f1.done()
    assert state.approval_active_request is not None
    assert state.approval_active_request.cast_id == "call_2"

    # Stale close from the destroyed dialog for cast 1: no-op.
    state.deny_approval_if_active("call_1")
    assert not f2.done()
    assert state.approval_active_request is not None
    assert state.approval_active_request.cast_id == "call_2"

    # A genuine close for the live cast still denies it.
    state.deny_approval_if_active("call_2")
    assert f2.done()
    assert f2.result().outcome is ApprovalOutcome.DENY
    assert state.approval_active_request is None


@pytest.mark.asyncio
async def test_cancel_pending_approvals_denies_all() -> None:
    """cancel_pending_approvals fails closed across the whole queue."""
    state = AppState()
    f1 = state.enqueue_approval(_approval_request("call_1"))
    f2 = state.enqueue_approval(_approval_request("call_2"))
    state.cancel_pending_approvals()
    assert f1.result().outcome is ApprovalOutcome.DENY
    assert f2.result().outcome is ApprovalOutcome.DENY
    assert state.approval_active_request is None
    assert state.approval_pending_count == 0


@pytest.mark.asyncio
async def test_set_project_cancels_pending_approvals() -> None:
    """Project changes deny pending approvals before the change completes."""
    state = AppState(project_path=Path("/proj/a"))
    future = state.enqueue_approval(_approval_request("call_1"))
    state.set_project(Path("/proj/b"))
    assert future.done()
    assert future.result().outcome is ApprovalOutcome.DENY
    assert state.approval_active_request is None


@pytest.mark.asyncio
async def test_new_conversation_cancels_pending_and_clears_badge() -> None:
    """A new session ends pending approvals and session approve-all."""
    state = AppState()
    future = state.enqueue_approval(_approval_request("call_1"))
    state.set_approval_session_badge(True)
    state.new_conversation()
    assert future.result().outcome is ApprovalOutcome.DENY
    assert state.approval_session_approve_all is False


@pytest.mark.asyncio
async def test_stop_channeling_denies_pending_approvals() -> None:
    """Aborting the agent denies approvals waiting on its decisions."""
    state = AppState()
    future = state.enqueue_approval(_approval_request("call_1"))
    state.stop_channeling()
    assert future.result().outcome is ApprovalOutcome.DENY


def test_session_badge_toggle_and_sync() -> None:
    """The badge flag toggles and re-syncs from the rune's session state."""
    state = AppState()
    assert state.approval_session_approve_all is False
    state.set_approval_session_badge(True)
    assert state.approval_session_approve_all is True
    view = PermissionsView.from_dict({"session": {"approve_all_active": False}})
    state.sync_approval_session_badge(view)
    assert state.approval_session_approve_all is False
    state.sync_approval_session_badge(None)
    assert state.approval_session_approve_all is False


def test_permissions_deep_link_flag_round_trip() -> None:
    """The deep-link flag is consumed exactly once."""
    state = AppState()
    assert state.take_approval_permissions_open_request() is False
    state.request_approval_permissions_open()
    assert state.take_approval_permissions_open_request() is True
    assert state.take_approval_permissions_open_request() is False


def _prompt_state() -> AppState:
    """AppState with a mocked agent service for submit_prompt tests."""
    state = AppState()
    mock_service = MagicMock()
    mock_service.run_prompt = AsyncMock()
    state.agent_service = mock_service
    return state


@pytest.mark.asyncio
async def test_submit_prompt_sends_attachment_as_native_part() -> None:
    """Verify attached file content reaches the agent as a file part."""
    state = _prompt_state()
    state.add_attachment("hello.py", b"print('hi')")

    state.submit_prompt("What does this do?")

    assert state.messages[0].content == "What does this do?"
    assert state.messages[0].attachments == ["hello.py"]
    assert state.pending_attachments == []
    assert state.pending_attachment_contents == {}
    sent = state.agent_service.run_prompt.call_args[0][0]
    assert isinstance(sent, list)
    assert sent[0] == {"type": "text", "text": "What does this do?"}
    file_part = sent[1]
    assert file_part["type"] == "file"
    assert file_part["file"]["filename"] == "hello.py"
    assert file_part["file"]["file_data"].startswith("data:text/x-python;base64,")


@pytest.mark.asyncio
async def test_submit_prompt_binary_attachment_sent_as_native_part() -> None:
    """Verify binary attachments travel as native file parts, not text."""
    state = _prompt_state()
    state.add_attachment("img.png", b"\x89PNG\r\n\x1a\n\x00\xff\x00\x01")

    state.submit_prompt("Look at this")

    assert "binary" not in state.messages[0].content
    sent = state.agent_service.run_prompt.call_args[0][0]
    assert isinstance(sent, list)
    assert sent[0] == {"type": "text", "text": "Look at this"}
    assert sent[1]["type"] == "image_url"
    assert sent[1]["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_submit_prompt_large_attachment_not_truncated() -> None:
    """Verify large attachments ride whole in a native file part."""
    state = _prompt_state()
    state.add_attachment("big.log", b"x" * 200_000)

    state.submit_prompt("Summarize")

    sent = state.agent_service.run_prompt.call_args[0][0]
    assert isinstance(sent, list)
    file_part = sent[1]
    assert file_part["type"] == "file"
    assert file_part["file"]["filename"] == "big.log"
    assert "truncat" not in state.messages[0].content


def test_remove_attachment_drops_content() -> None:
    """Verify removing a chip also drops its stored content."""
    state = AppState()
    state.add_attachment("a.py", b"x = 1")
    state.add_attachment("b.py", b"y = 2")

    state.remove_attachment(0)

    assert state.pending_attachments == ["b.py"]
    assert state.pending_attachment_contents == {"b.py": b"y = 2"}


class TestServerStateSplit:
    """Major #1: AppState is per-client; server-global config lives on
    ServerState and is shared across the clients of one server."""

    def test_server_state_defaults(self) -> None:
        from mvgeos_gui.state import ServerState

        server = ServerState()
        assert server.project_path == Path.cwd()
        assert server.selected_model == DEFAULT_MODEL
        assert server.recent_projects == [Path.cwd()]
        assert server.api_key is None
        assert server.client_states == []

    def test_new_client_state_registers(self) -> None:
        from mvgeos_gui.state import ServerState

        server = ServerState()
        a = server.new_client_state()
        b = server.new_client_state()
        assert a is not b
        assert len(server.client_states) == 2

    def test_client_states_share_server_config(self) -> None:
        from mvgeos_gui.state import ServerState

        server = ServerState()
        a = server.new_client_state()
        b = server.new_client_state()

        a.switch_model("openai/gpt-4o-mini")
        assert b.selected_model == "openai/gpt-4o-mini"

        b.set_contemplation_level("high")
        assert a.contemplation_level == "high"

        a.set_project(Path("/shared/proj"))
        assert b.project_path == Path("/shared/proj")
        assert a.project_path == Path("/shared/proj")

    def test_client_states_have_independent_ui_state(self) -> None:
        from mvgeos_gui.state import ServerState

        server = ServerState()
        a = server.new_client_state()
        b = server.new_client_state()

        a.open_app_settings()
        a.set_current_view("sessions")
        a.toggle_sidebar()
        a.plan_mode = True
        a.messages.append(InvocationTranscript.for_summoner("msg-a"))

        assert b._show_app_settings is False
        assert b.current_view == "chat"
        assert b.sidebar_open is True
        assert b.plan_mode is False
        assert b.messages == []

    def test_drop_client_state_stops_channeling(self) -> None:
        server = ServerState()
        a = server.new_client_state()
        a.is_channeling = True
        server.drop_client_state(a)
        assert server.client_states == []
        assert a.is_channeling is False

    @pytest.mark.asyncio
    async def test_server_ashutdown_awaits_every_client_agent_close(
        self, tmp_path: Path
    ) -> None:
        """Server shutdown awaits each client's agent close (watchers stop).

        NiceGUI awaits async app.on_shutdown handlers inside App.stop()
        before uvicorn cancels pending tasks, so an awaited shutdown is the
        only path that guarantees cached agents are closed. A sync
        shutdown that merely schedules background closes would be
        cancelled before the closes run.
        """

        class _ClosingAgent:
            def __init__(self) -> None:
                self.close_calls = 0

            async def close(self) -> None:
                self.close_calls += 1

        server = ServerState()
        agents: list[_ClosingAgent] = []
        for _ in range(2):
            state = server.new_client_state()
            agent = _ClosingAgent()
            agents.append(agent)

            def _factory(_agent: _ClosingAgent = agent, **kwargs: Any) -> _ClosingAgent:
                return _agent

            service = AgentService(
                project_path=tmp_path,
                api_key="dummy-key",
                agent_factory=_factory,
            )
            state.agent_service = service
            service.get_or_create_agent(state)

        await server.ashutdown()

        assert [agent.close_calls for agent in agents] == [1, 1]
        assert server.client_states == []

    @pytest.mark.asyncio
    async def test_server_ashutdown_still_fails_closed_on_close_error(
        self, tmp_path: Path
    ) -> None:
        """A close() failure during shutdown must not break the shutdown."""

        class _ExplodingAgent:
            async def close(self) -> None:
                raise RuntimeError("watcher shutdown blew up")

        server = ServerState()
        state = server.new_client_state()
        agent = _ExplodingAgent()
        service = AgentService(
            project_path=tmp_path,
            api_key="dummy-key",
            agent_factory=lambda **kwargs: agent,
        )
        state.agent_service = service
        service.get_or_create_agent(state)

        await server.ashutdown()

        assert server.client_states == []

    def test_standalone_app_state_keeps_working(self) -> None:
        """AppState() without a server behaves exactly like the old
        single shared state (backwards compatible for tests/embedding)."""
        state = AppState(project_path=Path("/solo"))
        assert state.project_path == Path("/solo")
        assert Path("/solo") in state.recent_projects
        state.switch_model("openai/gpt-4o-mini")
        assert state.selected_model == "openai/gpt-4o-mini"
        state.open_app_settings()
        assert state._show_app_settings is True

    def test_set_project_invalidates_all_clients_caches(self, tmp_path: Path) -> None:
        """set_project is server-global: every client's project-bound
        caches are dropped and their tome lists reloaded."""
        from mvgeos_gui.state import ServerState

        server = ServerState()
        state_a = server.new_client_state()
        state_b = server.new_client_state()
        new_project = tmp_path / "proj"
        new_project.mkdir()

        state_b.agent_service = object()  # type: ignore[assignment]
        state_b._autocomplete_service = object()  # type: ignore[assignment]

        state_a.set_project(new_project)

        assert state_a.project_path == new_project
        assert state_b.project_path == new_project
        assert state_a.agent_service is None
        assert state_b.agent_service is None
        assert state_b._autocomplete_service is None
        assert new_project in state_b.recent_projects

    def test_shared_write_notifies_all_clients(self) -> None:
        """A shared configuration write refreshes every connected client."""
        from mvgeos_gui.state import ServerState

        server = ServerState()
        state_a = server.new_client_state()
        state_b = server.new_client_state()
        seen: list[str] = []
        state_a.subscribe(lambda: seen.append("a"))
        state_b.subscribe(lambda: seen.append("b"))

        state_a.selected_model = "anthropic/claude-opus-4-6"

        assert state_b.selected_model == "anthropic/claude-opus-4-6"
        assert seen == ["a", "b"]


class TestMentionIndexServerShared:
    """Major #3: one @-mention index per server, refreshed on tree changes."""

    @staticmethod
    def _labels(service: Any) -> list[str]:
        return [service.get_item_label(i) for i in service.get_visible_items()]

    def test_files_added_after_empty_startup_appear_without_restart(
        self, tmp_path: Path
    ) -> None:
        """Empty project at startup: @ lists files added later, no restart."""
        from mvgeos_gui.state import ServerState

        server = ServerState(project_path=tmp_path)
        client = server.new_client_state()
        service = client.get_autocomplete_service()
        service.process_input("@")
        assert service.get_visible_items() == []

        (tmp_path / "readme.md").write_text("# hi", encoding="utf-8")
        service.process_input("@r")
        assert "readme.md" in self._labels(service)

    def test_index_is_shared_across_clients(self, tmp_path: Path) -> None:
        """Two clients share one server index: both see later-added files."""
        from mvgeos_gui.state import ServerState

        (tmp_path / "a.py").write_text("x", encoding="utf-8")
        server = ServerState(project_path=tmp_path)
        client_a = server.new_client_state()
        client_b = server.new_client_state()
        svc_a = client_a.get_autocomplete_service()
        svc_b = client_b.get_autocomplete_service()

        (tmp_path / "b.py").write_text("y", encoding="utf-8")
        svc_a.process_input("@b")
        svc_b.process_input("@b")
        assert "b.py" in self._labels(svc_a)
        assert "b.py" in self._labels(svc_b)

    def test_set_project_rebuilds_index_for_new_project(self, tmp_path: Path) -> None:
        """Switching projects drops the old index and indexes the new path."""
        proj_a = tmp_path / "a"
        proj_b = tmp_path / "b"
        proj_a.mkdir()
        proj_b.mkdir()
        (proj_a / "alpha.py").write_text("x", encoding="utf-8")
        (proj_b / "beta.py").write_text("y", encoding="utf-8")

        state = AppState(project_path=proj_a)
        service = state.get_autocomplete_service()
        service.process_input("@a")
        assert "alpha.py" in self._labels(service)

        state.set_project(proj_b)
        new_service = state.get_autocomplete_service()
        new_service.process_input("@")
        labels = self._labels(new_service)
        assert "beta.py" in labels
        assert "alpha.py" not in labels


# ---------------------------------------------------------------------------
# Project display name, agent teardown, active-session persistence
# ---------------------------------------------------------------------------


def test_project_display_name_prefers_persisted_project_name(
    tmp_path: Path,
) -> None:
    """Persisted workspace project_name wins over the directory name."""
    service = ConfigService(config_dir=tmp_path / "cfg")
    service.save_workspace_settings(
        tmp_path, WorkspaceSettings(project_name="My Quest")
    )
    state = AppState(project_path=tmp_path)
    state._config_service = service
    assert state.project_display_name == "My Quest"


def test_project_display_name_falls_back_to_directory_name(
    tmp_path: Path,
) -> None:
    """Without a persisted name the workspace directory name is shown."""
    state = AppState(project_path=tmp_path)
    assert state.project_display_name == tmp_path.name


def test_project_display_name_tolerates_settings_failure(
    tmp_path: Path,
) -> None:
    """A workspace-settings read failure still shows the directory name."""

    class _BrokenConfigService:
        def load_workspace_settings(self, project_dir: Path) -> Any:
            raise RuntimeError("settings unreadable")

    state = AppState(project_path=tmp_path)
    state._config_service = _BrokenConfigService()  # type: ignore[assignment]
    assert state.project_display_name == tmp_path.name


def test_restore_active_tome_tolerates_broken_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A storage failure restores nothing instead of breaking page load."""

    class _BrokenUser(dict):  # type: ignore[type-arg]
        def get(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("storage unavailable")

    fake_app = _FakeNiceGUIApp()
    fake_app.storage.user = _BrokenUser()
    monkeypatch.setattr(state_module, "nicegui_app", fake_app)

    state = AppState(project_path=tmp_path)
    assert state.restore_active_tome() is False


def test_restore_active_tome_tolerates_switch_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stored id that fails to switch restores nothing."""
    store = _patch_browser_storage(monkeypatch)
    store["active-tome-id"] = "tome-boom"

    def _boom(tome_id: str) -> None:
        raise RuntimeError("tome backend exploded")

    state = AppState(project_path=tmp_path)
    monkeypatch.setattr(state, "switch_to_tome", _boom)
    assert state.restore_active_tome() is False


@pytest.mark.asyncio
async def test_reset_agent_closes_cached_agent(tmp_path: Path) -> None:
    """reset_agent() must close the discarded agent (stops rune watchers)."""
    closed: list[str] = []

    class _Agent:
        async def close(self) -> None:
            closed.append("closed")

    service = AgentService(
        project_path=tmp_path,
        api_key="sk-test",
        agent_factory=lambda **kwargs: _Agent(),
    )
    state = AppState(project_path=tmp_path)
    state.agent_service = service
    agent = service.get_or_create_agent(state)

    state.reset_agent()

    for _ in range(100):
        if closed:
            break
        await asyncio.sleep(0.01)
    assert closed == ["closed"]
    # The reference was dropped: the next turn builds a fresh agent.
    assert service.get_or_create_agent(state) is not agent


class _FakeUserStorage:
    def __init__(self) -> None:
        self.user: dict[str, Any] = {}


class _FakeNiceGUIApp:
    def __init__(self) -> None:
        self.storage = _FakeUserStorage()


def _patch_browser_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Swap NiceGUI per-browser storage for a plain dict (no page needed)."""
    store: dict[str, Any] = {}
    fake_app = _FakeNiceGUIApp()
    fake_app.storage.user = store
    monkeypatch.setattr(state_module, "nicegui_app", fake_app)
    return store


def test_persist_and_restore_active_tome_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """switch_to_tome persists the session; a new state restores it."""
    store = _patch_browser_storage(monkeypatch)
    tome_dir = tmp_path / "tomes"
    tome_dir.mkdir(exist_ok=True)
    tome_id = _create_tome(tome_dir, str(tmp_path))

    state = AppState(project_path=tmp_path, tome_service=TomeService(tome_dir))
    state.switch_to_tome(tome_id)
    assert state.active_tome_id == tome_id
    assert store.get("active-tome-id") == tome_id

    fresh = AppState(project_path=tmp_path, tome_service=TomeService(tome_dir))
    assert fresh.restore_active_tome() is True
    assert fresh.active_tome_id == tome_id


def test_new_conversation_clears_persisted_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Starting a new conversation must not resurrect the old session."""
    store = _patch_browser_storage(monkeypatch)
    tome_dir = tmp_path / "tomes"
    tome_dir.mkdir(exist_ok=True)
    tome_id = _create_tome(tome_dir, str(tmp_path))

    state = AppState(project_path=tmp_path, tome_service=TomeService(tome_dir))
    state.switch_to_tome(tome_id)
    assert store.get("active-tome-id") == tome_id

    state.new_conversation()
    assert store.get("active-tome-id") is None

    fresh = AppState(project_path=tmp_path, tome_service=TomeService(tome_dir))
    assert fresh.restore_active_tome() is False
    assert fresh.active_tome_id is None


def test_restore_active_tome_ignores_unknown_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A persisted id that no longer resolves restores nothing."""
    store = _patch_browser_storage(monkeypatch)
    store["active-tome-id"] = "tome-that-does-not-exist"
    tome_dir = tmp_path / "tomes"
    tome_dir.mkdir(exist_ok=True)

    state = AppState(project_path=tmp_path, tome_service=TomeService(tome_dir))
    assert state.restore_active_tome() is False
    assert state.active_tome_id is None


def test_restore_active_tome_ignores_project_mismatched_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A persisted id naming a tome from another workspace restores nothing."""
    store = _patch_browser_storage(monkeypatch)
    tome_dir = tmp_path / "tomes"
    tome_dir.mkdir(exist_ok=True)
    other_project = tmp_path / "other-project"
    other_project.mkdir(exist_ok=True)
    foreign_tome_id = _create_tome(tome_dir, str(other_project))
    store["active-tome-id"] = foreign_tome_id

    state = AppState(project_path=tmp_path, tome_service=TomeService(tome_dir))
    assert state.restore_active_tome() is False
    assert state.active_tome_id is None


@pytest.mark.asyncio
async def test_active_session_restored_on_fresh_page_load(
    user: User, tmp_path: Path
) -> None:
    """A real page reload re-selects the session from browser storage."""
    tome_dir = tmp_path / "tomes"
    tome_dir.mkdir(exist_ok=True)
    tome_id = _create_tome(tome_dir, str(tmp_path))

    state = AppState(project_path=tmp_path, tome_service=TomeService(tome_dir))

    @ui.page("/test_session_restore_first")
    def first_page() -> None:
        state.switch_to_tome(tome_id)

    await user.open("/test_session_restore_first")
    assert state.active_tome_id == tome_id

    fresh_state = AppState(project_path=tmp_path, tome_service=TomeService(tome_dir))

    @ui.page("/test_session_restore_second")
    def second_page() -> None:
        fresh_state.load_tomes()
        fresh_state.restore_active_tome()

    await user.open("/test_session_restore_second")
    assert fresh_state.active_tome_id == tome_id


def _rune_dict(
    name: str,
    commands: list[str] | None = None,
    enabled: bool = True,
    description: str = "",
) -> dict[str, Any]:
    return {
        "name": name,
        "version": "0.1.0",
        "description": description or f"{name} description",
        "enabled": enabled,
        "commands": commands or [],
    }


def _slash_names(state: AppState, query: str = "/") -> list[str]:
    """Drive autocomplete the way the chat input does and read item names."""
    service = state.get_autocomplete_service()
    service.process_input(query, len(query))
    return [str(getattr(item, "name", "")) for item in service.get_visible_items()]


def test_autocomplete_suggests_installed_rune_commands() -> None:
    state = AppState()
    runes = [_rune_dict("selfmod-bridge", ["selfmod"])]
    with patch.object(state_module, "list_installed_runes", return_value=runes):
        names = _slash_names(state, "/self")
    assert "/selfmod" in names
    assert "/help" in _slash_names(state, "/help")


def test_autocomplete_hides_disabled_rune_commands() -> None:
    state = AppState()
    runes = [_rune_dict("selfmod-bridge", ["selfmod"], enabled=False)]
    with patch.object(state_module, "list_installed_runes", return_value=runes):
        assert "/selfmod" not in _slash_names(state, "/self")


def test_autocomplete_rune_command_cannot_shadow_cli_command() -> None:
    state = AppState()
    runes = [_rune_dict("evil-rune", ["reload", "selfmod"])]
    with patch.object(state_module, "list_installed_runes", return_value=runes):
        names = _slash_names(state, "/")
    assert "/selfmod" in names
    # /reload stays the single engine-owned entry
    assert names.count("/reload") == 1


def test_install_rune_refreshes_autocomplete_suggestions() -> None:
    state = AppState()
    installed: list[dict[str, Any]] = []
    with patch.object(state_module, "list_installed_runes", return_value=installed):
        assert "/selfmod" not in _slash_names(state, "/self")
    installed.append(_rune_dict("selfmod-bridge", ["selfmod"]))
    with (
        patch.object(state_module, "install_rune", return_value=None),
        patch.object(state_module, "list_installed_runes", return_value=installed),
    ):
        assert asyncio.run(state.install_rune_async("selfmod-bridge")) is True
        assert "/selfmod" in _slash_names(state, "/self")


def test_uninstall_rune_refreshes_autocomplete_suggestions() -> None:
    state = AppState()
    installed = [_rune_dict("selfmod-bridge", ["selfmod"])]
    with patch.object(state_module, "list_installed_runes", return_value=installed):
        assert "/selfmod" in _slash_names(state, "/self")
    installed.clear()
    with (
        patch.object(state_module, "uninstall_rune", return_value=True),
        patch.object(state_module, "list_installed_runes", return_value=installed),
    ):
        assert asyncio.run(state.uninstall_rune_async("selfmod-bridge")) is True
        assert "/selfmod" not in _slash_names(state, "/self")


def test_disable_rune_refreshes_autocomplete_suggestions() -> None:
    state = AppState()
    rune = _rune_dict("selfmod-bridge", ["selfmod"])
    with patch.object(state_module, "list_installed_runes", return_value=[rune]):
        assert "/selfmod" in _slash_names(state, "/self")
    rune["enabled"] = False
    with (
        patch.object(state_module, "set_rune_enabled", return_value=True),
        patch.object(state_module, "list_installed_runes", return_value=[rune]),
    ):
        assert (
            asyncio.run(state.set_rune_enabled_async("selfmod-bridge", False)) is True
        )
        assert "/selfmod" not in _slash_names(state, "/self")


def _make_skill_dir(parent: Path, name: str, filename: str = "SKILL.md") -> Path:
    skill_dir = parent / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / filename).write_text(
        f"---\nname: {name}\ndescription: Desc of {name}\n---\nBody",
        encoding="utf-8",
    )
    return skill_dir


def test_scan_skill_manifests_lowercase_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A skill with only lowercase skill.md is discovered (protocol casing)."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    project = tmp_path / "proj"
    skills_dir = project / ".agents" / "skills"
    _make_skill_dir(skills_dir, "lower-skill", "skill.md")

    manifests = state_module._scan_skill_manifests(project)

    assert [m.name for m in manifests] == ["lower-skill"]


def test_scan_skill_manifests_uppercase_preferred(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SKILL.md wins when both casings exist."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    project = tmp_path / "proj"
    skills_dir = project / ".agents" / "skills"
    s_dir = _make_skill_dir(skills_dir, "both-skill", "SKILL.md")
    (s_dir / "skill.md").write_text(
        "---\nname: both-skill\ndescription: Lower\n---\nLower",
        encoding="utf-8",
    )

    manifests = state_module._scan_skill_manifests(project)

    assert [m.name for m in manifests] == ["both-skill"]
    assert manifests[0].path == str(s_dir)


def test_scan_skill_manifests_neither_casing_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No manifest file at all means not a skill: skipped."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    project = tmp_path / "proj"
    skills_dir = project / ".agents" / "skills"
    (skills_dir / "empty-dir").mkdir(parents=True)

    manifests = state_module._scan_skill_manifests(project)

    assert manifests == []


def test_scan_skill_manifests_uppercase_still_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Uppercase SKILL.md skills keep being discovered (regression)."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    project = tmp_path / "proj"
    skills_dir = project / ".agents" / "skills"
    _make_skill_dir(skills_dir, "upper-skill", "SKILL.md")

    manifests = state_module._scan_skill_manifests(project)

    assert [m.name for m in manifests] == ["upper-skill"]


def test_steer_prompt_dispatches_to_service() -> None:
    """Verify steer_prompt calls agent_service.steer with stripped text."""
    state = AppState()
    mock_service = MagicMock()
    state.agent_service = mock_service

    state.steer_prompt("  Refactor math logic instead  ")

    mock_service.steer.assert_called_once_with("Refactor math logic instead")


def test_steer_prompt_ignores_blank() -> None:
    """Verify steer_prompt does nothing when text is empty or blank."""
    state = AppState()
    mock_service = MagicMock()
    state.agent_service = mock_service

    state.steer_prompt("   ")

    mock_service.steer.assert_not_called()


def test_queue_follow_up_adds_and_dispatches() -> None:
    """Verify queue_follow_up adds prompt to queued_follow_ups list."""
    state = AppState()
    mock_service = MagicMock()
    state.agent_service = mock_service

    state.queue_follow_up("Run pytest after this")

    assert state.queued_follow_ups == ["Run pytest after this"]
    mock_service.follow_up.assert_called_once_with("Run pytest after this")


def test_remove_follow_up_removes_by_index() -> None:
    """Verify remove_follow_up removes the specified item from queued_follow_ups."""
    state = AppState()
    state.queued_follow_ups = ["Prompt 1", "Prompt 2", "Prompt 3"]

    state.remove_follow_up(1)

    assert state.queued_follow_ups == ["Prompt 1", "Prompt 3"]


def test_get_contributed_views_from_installed_runes() -> None:
    """Verify get_contributed_views extracts and sorts views from enabled runes."""
    state = AppState()
    mock_runes = [
        {
            "name": "disabled-rune",
            "enabled": False,
            "ui": {
                "views": [{"id": "hidden", "title": "Hidden", "order": 10}],
            },
        },
        {
            "name": "skills-bridge",
            "enabled": True,
            "ui": {
                "views": [
                    {
                        "id": "skills",
                        "title": "Skills",
                        "icon": "auto_awesome",
                        "order": 50,
                        "description": "Agent skills",
                    }
                ],
            },
        },
        {
            "name": "custom-scheduler",
            "enabled": True,
            "ui": {
                "views": [
                    {
                        "id": "scheduler",
                        "title": "Scheduler",
                        "icon": "schedule",
                        "order": 20,
                        "description": "Cron tasks",
                    }
                ],
            },
        },
    ]

    with patch("mvgeos_gui.state.list_installed_runes", return_value=mock_runes):
        views = state.get_contributed_views()
        assert len(views) == 2
        # Sorted by order: scheduler (20) first, then skills (50)
        assert views[0].id == "scheduler"
        assert views[0].title == "Scheduler"
        assert views[0].icon == "schedule"
        assert views[1].id == "skills"
        assert views[1].title == "Skills"
        assert views[1].icon == "auto_awesome"

        assert state.has_contributed_view("skills") is True
        assert state.has_contributed_view("scheduler") is True
        assert state.has_contributed_view("hidden") is False
        assert state.has_contributed_view("nonexistent") is False


def test_get_contributed_canvas_tabs_from_installed_runes() -> None:
    """Verify get_contributed_canvas_tabs extracts and sorts tabs from enabled runes."""
    state = AppState()
    mock_runes = [
        {
            "name": "disabled-tab-rune",
            "enabled": False,
            "ui": {
                "canvas_tabs": [
                    {"id": "disabled_tab", "title": "Disabled", "order": 5}
                ],
            },
        },
        {
            "name": "submvge-bridge",
            "enabled": True,
            "ui": {
                "canvas_tabs": [
                    {
                        "id": "sub_mvge",
                        "title": "Sub-Mvge",
                        "icon": "hub",
                        "order": 30,
                    }
                ],
            },
        },
        {
            "name": "checkpoint-bridge",
            "enabled": True,
            "ui": {
                "canvas_tabs": [
                    {
                        "id": "review",
                        "title": "Review",
                        "icon": "rate_review",
                        "order": 10,
                    }
                ],
            },
        },
    ]

    with patch("mvgeos_gui.state.list_installed_runes", return_value=mock_runes):
        tabs = state.get_contributed_canvas_tabs()
        assert len(tabs) == 2
        # Sorted by order: review (10) first, then sub_mvge (30)
        assert tabs[0].id == "review"
        assert tabs[0].title == "Review"
        assert tabs[1].id == "sub_mvge"
        assert tabs[1].title == "Sub-Mvge"

        assert state.has_contributed_canvas_tab("review") is True
        assert state.has_contributed_canvas_tab("sub_mvge") is True
        assert state.has_contributed_canvas_tab("disabled_tab") is False
        assert state.has_contributed_canvas_tab("nonexistent") is False
