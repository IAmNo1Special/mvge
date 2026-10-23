"""Comprehensive end-user journey integration test for mvgeos-gui.

Simulates an end user from first launch through:
1. Initial empty workspace & Antigravity layout
2. Status bar indicators (default realm, model, idle status)
3. Composer autocomplete with @-mentions and /-slash commands
4. Prompt submission & streaming channeling response
5. Step cards rendering (worked, file exploration, commands)
6. Artifact generation & slide-over drawer preview
7. Review rail & Git diff viewer modal
8. Application Settings modal & model switching
9. Marketplace & Packages panel exploration
10. Sessions panel & New Conversation reset
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from mvgeos_core.constants import DEFAULT_MODEL
from mvgeos_provider.realms import realm_for_model_id
from mvgeos_runes.types import SkillManifest, SkillScope
from mvgeos_tome.handle import TomeHandleFactory
from mvgeos_tome.types import TomeEntry, TomeEntryType
from nicegui import ui
from nicegui.testing import User

from mvgeos_gui.app import build_page
from mvgeos_gui.autocomplete import (
    AutocompleteService,
    MentionIndex,
    SlashCommandRegistry,
)
from mvgeos_gui.models import (
    Artifact,
    ArtifactType,
    CommandExecution,
    ExecutionStep,
    FileExploration,
    MessagePart,
    MessagePartType,
    StepType,
)
from mvgeos_gui.models.user import User as GuiUser
from mvgeos_gui.models.user import UserRole
from mvgeos_gui.services.config_service import AppSettings, ConfigService
from mvgeos_gui.services.tome_service import TomeService
from mvgeos_gui.state import AppState


def _mock_user() -> GuiUser:
    return GuiUser(
        id="summoner-1",
        username="summoner",
        password_hash="mock",
        role=UserRole.USER,
        is_active=True,
    )


def _init_git_workspace(repo_path: Path) -> None:
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=repo_path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "summoner@example.com"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Summoner"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    (repo_path / "hello.py").write_text("print('hello world')", encoding="utf-8")
    (repo_path / "utils.py").write_text("def add(a, b): return a + b", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )


@pytest.mark.asyncio
async def test_end_user_complete_lifecycle(user: User, tmp_path: Path) -> None:
    """Exercise complete end-user journey through all primary GUI features."""
    if shutil.which("git") is None:
        pytest.skip("git not available")

    # 1. Workspace setup
    workspace = tmp_path / "my_project"
    workspace.mkdir()
    _init_git_workspace(workspace)

    tome_dir = tmp_path / "tomes"
    factory = TomeHandleFactory(tome_dir)
    initial_tome = factory.create_tome(str(workspace))
    factory.open_write(initial_tome.tome_id).append(
        TomeEntry(
            id="entry-1",
            parent_id=None,
            type=TomeEntryType.TOME_INFO,
            timestamp=1000.0,
            payload={"name": "Setup Repository"},
        )
    )

    tome_service = TomeService(tome_dir)
    config_service = ConfigService(config_dir=tmp_path / "config")
    config_service.save_app_settings(
        AppSettings(
            default_model=DEFAULT_MODEL,
            theme="dark",
            api_key="sk-test-key",
        )
    )

    state = AppState(
        project_path=workspace,
        tome_service=tome_service,
    )
    state.current_user = _mock_user()
    state._config_service = config_service
    state.load_tomes()

    skill = SkillManifest(
        name="code-review",
        description="Run code review on staged changes",
        scope=SkillScope.PROJECT,
        path=str(workspace / "SKILL.md"),
    )
    index = MentionIndex(workspace, skills=[skill])
    state._autocomplete_service = AutocompleteService(index, SlashCommandRegistry())

    @ui.page("/journey")
    def page() -> None:
        build_page(state)

    await user.open("/journey")

    # 2. Verify initial empty workspace hero & Antigravity shell
    await user.should_see("MvgeOS")
    await user.should_see("New Conversation")
    await user.should_see("What should Mvge work on?")
    await user.should_see("Explain this project structure")
    await user.should_see("Find all TODO comments")

    # 3. Verify status bar indicators
    expected_realm = realm_for_model_id(DEFAULT_MODEL)
    await user.should_see(expected_realm)
    assert state.mvge_status == "idle"

    # 4. Test Autocomplete trigger in composer
    ac = state.get_autocomplete_service()
    ac.process_input("Check @hello")
    assert ac.mode.value == "mention"
    items = ac.get_visible_items()
    assert any("hello.py" in ac.get_item_label(item) for item in items)

    ac.process_input("/help")
    assert ac.mode.value == "command"
    slash_items = ac.get_visible_items()
    assert any("help" in ac.get_item_label(item) for item in slash_items)

    # 5. Submit user prompt and simulate agent channeling response
    user_prompt = "Refactor calculation logic in utils.py"
    state.submit_prompt(user_prompt)
    assert state.is_channeling is True
    assert state.mvge_status == "channeling"
    assert len(state.messages) >= 2

    # Simulate assistant message with step cards
    assistant_msg = state.messages[-1]
    assistant_msg.model = DEFAULT_MODEL
    assistant_msg.mana_used = 850
    assistant_msg.parts.append(
        MessagePart(
            part_type=MessagePartType.STEP,
            step=ExecutionStep(
                step_type=StepType.WORKED,
                title="Worked for 2.4s",
                details=["Analyzed AST in utils.py", "Validated arithmetic types"],
                is_complete=True,
            ),
        )
    )
    assistant_msg.parts.append(
        MessagePart(
            part_type=MessagePartType.STEP,
            step=ExecutionStep(
                step_type=StepType.FILES,
                title="Explored 1 file",
                files=[
                    FileExploration(path="utils.py", lines="L1-2", operation="read")
                ],
            ),
        )
    )
    assistant_msg.parts.append(
        MessagePart(
            part_type=MessagePartType.STEP,
            step=ExecutionStep(
                step_type=StepType.COMMANDS,
                title="Ran bash command",
                commands=[
                    CommandExecution(
                        command="python -m py_compile utils.py",
                        exit_code=0,
                        output="",
                    )
                ],
            ),
        )
    )
    assistant_msg.parts.append(
        MessagePart(
            part_type=MessagePartType.TEXT,
            text="I have reviewed and refactored `utils.py` successfully.",
        )
    )

    # Generate an artifact (Architecture Plan)
    plan_artifact = Artifact(
        id="art-journey-1",
        title="Refactor Architecture Plan",
        summary="Detailed plan for arithmetic refactoring",
        content="# Refactoring Plan\n\n- Add type annotations\n- Ensure hermetic tests",
        artifact_type=ArtifactType.IMPLEMENTATION_PLAN,
    )
    assistant_msg.parts.append(
        MessagePart(
            part_type=MessagePartType.ARTIFACT,
            artifact=plan_artifact,
        )
    )
    state.add_artifact(plan_artifact)

    # Complete channeling
    state.stop_channeling()
    assert state.is_channeling is False
    assert state.mvge_status == "idle"

    # Refresh chat panel view
    state.notify()

    # Verify messages and step cards are visible
    await user.should_see(user_prompt)
    await user.should_see("Worked for 2.4s")
    await user.should_see("Explored 1 file")
    await user.should_see("Ran bash command")
    await user.should_see("Refactor Architecture Plan")
    await user.should_see("I have reviewed and refactored")

    # 6. Test opening artifact drawer and reviewing markdown
    state.open_artifact("art-journey-1")
    assert state.get_selected_artifact() is plan_artifact

    # 7. Test Git Diff review
    # 7. Test Git Diff review
    (workspace / "utils.py").write_text(
        "def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8"
    )
    state.open_diff_review("utils.py")
    diff_view = state.get_selected_diff_view()
    assert diff_view is not None
    assert diff_view.file_path == "utils.py"
    assert diff_view.additions >= 1
    state.clear_diff_selection()
    assert state.get_selected_diff_view() is None

    # 8. Test Settings Modal & Model Switching
    state.open_app_settings()
    assert state._show_app_settings is True
    state.switch_model("openrouter/free")
    assert state.selected_model == "openrouter/free"
    state.close_app_settings()
    assert state._show_app_settings is False

    # 9. Test Navigation to Marketplace / Packages Panel
    state.set_current_view("packages")
    assert state.current_view == "packages"
    await user.should_see("Marketplace")

    # 10. Test Navigation to Sessions Panel
    state.set_current_view("sessions")
    assert state.current_view == "sessions"
    await user.should_see("Sessions")
    await user.should_see("Setup Repository")

    # 11. Test Reset to New Conversation
    state.new_conversation()
    state.set_current_view("chat")
    assert state.active_tome_id is None
    assert state.tome_title == "New Conversation"
    assert len(state.messages) == 0
