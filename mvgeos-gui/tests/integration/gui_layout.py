"""Integration tests for 3-column obsidian shell layout and components."""

import shutil
import subprocess
from pathlib import Path

import pytest
from mvgeos_core.constants import DEFAULT_MODEL
from mvgeos_provider.realms import realm_for_model_id
from mvgeos_runes.types import (
    SkillManifest,
    SkillScope,
)
from mvgeos_tome.handle import TomeHandleFactory
from mvgeos_tome.types import TomeEntry, TomeEntryType
from nicegui import ui

from mvgeos_gui.app import build_page, init_app
from mvgeos_gui.models.user import User, UserRole
from mvgeos_gui.services.config_service import AppSettings, ConfigService
from mvgeos_gui.services.tome_service import TomeService
from mvgeos_gui.state import AppState, ServerState


def _mock_user() -> User:
    return User(
        id="test-user",
        username="test",
        password_hash="mock",
        role=UserRole.USER,
        is_active=True,
    )


def _make_manifest(
    name: str = "review",
    path: str = "/skills/review/SKILL.md",
    scope: str = "project",
    description: str = "Review code",
) -> SkillManifest:
    """Build a minimal SkillManifest for testing."""
    return SkillManifest(
        name=name,
        description=description,
        scope=SkillScope(scope),
        path=path,
    )


def _init_git_repo(repo_path: Path, branch: str) -> None:
    subprocess.run(
        ["git", "init", "-b", branch],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    (repo_path / "README.md").write_text("test", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )


@pytest.mark.asyncio
async def test_full_shell_layout_rendering(user: User) -> None:
    """Verify 3-pane desktop shell elements rendered correctly."""
    state = AppState(project_path=Path("C:/demo/my-project"))
    state.current_user = _mock_user()

    @ui.page("/test_shell_render")
    def page() -> None:
        build_page(state)

    await user.open("/test_shell_render")

    # Left sidebar branding, button, and navigation
    await user.should_see("MvgeOS")
    await user.should_see("New Conversation")
    await user.should_see("Home")
    await user.should_see("Chat")
    await user.should_see("Sessions")
    await user.should_see("Marketplace")
    await user.should_see("Settings")

    # Status bar and model info. The status bar shows a prefix of the selected
    # slug, so assert on the default's prefix rather than any one model's name.
    await user.should_see(realm_for_model_id(DEFAULT_MODEL))


@pytest.mark.asyncio
async def test_sidebar_toggle_interaction(user: User) -> None:
    """Verify toggling left sidebar visibility state."""
    state = AppState()

    @ui.page("/test_sidebar_toggle")
    def page() -> None:
        build_page(state)

    await user.open("/test_sidebar_toggle")
    assert state.sidebar_open is True

    state.toggle_sidebar()
    assert state.sidebar_open is False

    state.toggle_sidebar()
    assert state.sidebar_open is True


@pytest.mark.asyncio
async def test_review_rail_toggle_interaction(user: User) -> None:
    """Verify toggling review rail expansion state."""
    state = AppState(review_open=True)

    @ui.page("/test_review_toggle")
    def page() -> None:
        build_page(state)

    await user.open("/test_review_toggle")
    await user.should_see("Review")
    await user.should_not_see("Permission mode")
    await user.should_see("Changed files")

    state.toggle_review()
    assert state.review_open is False


@pytest.mark.asyncio
async def test_new_conversation_button_resets_state(user: User) -> None:
    """Verify clicking '+ New Conversation' resets conversation state."""
    state = AppState()
    state.active_tome_id = "tome-99"
    state.tome_title = "Existing session"
    state.current_user = _mock_user()

    @ui.page("/test_new_convo_btn")
    def page() -> None:
        build_page(state)

    await user.open("/test_new_convo_btn")
    user.find("new_conversation_btn").click()

    assert state.active_tome_id is None
    assert state.tome_title == "New Conversation"


@pytest.mark.asyncio
async def test_project_picker_selection(user: User) -> None:
    """Verify switching projects updates state."""
    state = AppState(project_path=Path("C:/demo/project-a"))
    proj_b = Path("C:/demo/project-b")
    state.add_recent_project(proj_b)

    @ui.page("/test_project_picker")
    def page() -> None:
        build_page(state)

    await user.open("/test_project_picker")

    # Select project-b
    state.set_project(proj_b)
    assert state.project_path == proj_b


@pytest.mark.asyncio
async def test_collapsed_panels_rendering(user: User) -> None:
    """Verify initial rendering when sidebar and review rail are collapsed."""
    state = AppState(sidebar_open=False, review_open=False)

    @ui.page("/test_collapsed")
    def page() -> None:
        build_page(state)

    await user.open("/test_collapsed")
    assert state.sidebar_open is False
    assert state.review_open is False


@pytest.mark.asyncio
async def test_channeling_input_dock(user: User) -> None:
    """Verify input dock state while channeling and stopping channeling."""
    state = AppState(is_channeling=True)

    @ui.page("/test_channeling")
    def page() -> None:
        build_page(state)

    await user.open("/test_channeling")
    assert state.is_channeling is True


@pytest.mark.asyncio
async def test_init_app_registers_index(user: User) -> None:
    """Verify init_app registers the root index page properly."""
    server = ServerState(project_path=Path("C:/demo/init-project"))
    returned = init_app(server)
    assert returned is server

    await user.open("/")
    await user.should_see("MvgeOS")


# --- Tome integration tests ---


@pytest.mark.asyncio
async def test_sidebar_displays_tomes(user: User, tmp_path: Path) -> None:
    """Verify sidebar lists tomes with titles, timestamps, and git branches."""
    tome_dir = tmp_path / "tomes"
    project_path = tmp_path / "proj"
    project_path.mkdir()
    factory = TomeHandleFactory(tome_dir)
    tome_id = factory.create_tome(str(project_path)).tome_id
    factory.open_write(tome_id).append(
        TomeEntry(
            id="info-1",
            parent_id=None,
            type=TomeEntryType.TOME_INFO,
            timestamp=1000.0,
            payload={"name": "Bug Fix Session"},
        )
    )

    service = TomeService(tome_dir)
    state = AppState(project_path=project_path, tome_service=service)
    state.load_tomes()

    @ui.page("/test_sidebar_tomes")
    def page() -> None:
        build_page(state)

    await user.open("/test_sidebar_tomes")
    await user.should_see("Bug Fix Session")


@pytest.mark.asyncio
async def test_sidebar_shows_git_branch(user: User, tmp_path: Path) -> None:
    """Verify sidebar shows git branch tags for tome entries."""
    if shutil.which("git") is None:
        pytest.skip("git not available")

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo, "feature-branch")

    tome_dir = tmp_path / "tomes"
    TomeHandleFactory(tome_dir).create_tome(str(repo))

    service = TomeService(tome_dir)
    state = AppState(project_path=repo, tome_service=service)
    state.load_tomes()

    @ui.page("/test_git_branch")
    def page() -> None:
        build_page(state)

    await user.open("/test_git_branch")
    await user.should_see("feature-branch")


@pytest.mark.asyncio
async def test_clicking_tome_switches_session(user: User, tmp_path: Path) -> None:
    """Verify clicking a tome entry switches the active session."""
    tome_dir = tmp_path / "tomes"
    project_path = tmp_path / "proj"
    project_path.mkdir()
    factory = TomeHandleFactory(tome_dir)
    meta_id = factory.create_tome(str(project_path)).tome_id
    factory.open_write(meta_id).append(
        TomeEntry(
            id="info-1",
            parent_id=None,
            type=TomeEntryType.TOME_INFO,
            timestamp=1000.0,
            payload={"name": "Test Session"},
        )
    )

    service = TomeService(tome_dir)
    state = AppState(project_path=project_path, tome_service=service)
    state.load_tomes()

    @ui.page("/test_click_tome")
    def page() -> None:
        build_page(state)

    await user.open("/test_click_tome")
    await user.should_see("Test Session")

    state.switch_to_tome(meta_id)
    assert state.active_tome_id == meta_id
    assert state.tome_title == "Test Session"


@pytest.mark.asyncio
async def test_current_session_card_with_tome(user: User, tmp_path: Path) -> None:
    """Verify sidebar shows active tome title in current session card."""
    tome_dir = tmp_path / "tomes"
    project_path = tmp_path / "my-awesome-project"
    project_path.mkdir()
    factory = TomeHandleFactory(tome_dir)
    meta_id = factory.create_tome(str(project_path)).tome_id
    factory.open_write(meta_id).append(
        TomeEntry(
            id="info-1",
            parent_id=None,
            type=TomeEntryType.TOME_INFO,
            timestamp=1000.0,
            payload={"name": "Active Session"},
        )
    )

    service = TomeService(tome_dir)
    state = AppState(project_path=project_path, tome_service=service)
    state.load_tomes()
    state.switch_to_tome(meta_id)

    @ui.page("/test_current_session")
    def page() -> None:
        build_page(state)

    await user.open("/test_current_session")
    await user.should_see("Current Session")
    await user.should_see("Active Session")


@pytest.mark.asyncio
async def test_viewport_shows_chat(user: User, tmp_path: Path) -> None:
    """Verify chat view shows active session title."""
    tome_dir = tmp_path / "tomes"
    project_path = tmp_path / "proj"
    project_path.mkdir()
    factory = TomeHandleFactory(tome_dir)
    meta_id = factory.create_tome(str(project_path)).tome_id
    factory.open_write(meta_id).append(
        TomeEntry(
            id="info-1",
            parent_id=None,
            type=TomeEntryType.TOME_INFO,
            timestamp=1000.0,
            payload={"name": "Active Session"},
        )
    )

    service = TomeService(tome_dir)
    state = AppState(project_path=project_path, tome_service=service)
    state.load_tomes()
    state.switch_to_tome(meta_id)

    @ui.page("/test_viewport_transition")
    def page() -> None:
        build_page(state)

    await user.open("/test_viewport_transition")
    await user.should_see("Active Session")


@pytest.mark.asyncio
async def test_command_palette_opens_and_lists_views(user: User) -> None:
    """Verify command palette lists all primary views."""
    state = AppState(_command_palette_open=True)

    @ui.page("/test_palette")
    def page() -> None:
        build_page(state)

    await user.open("/test_palette")
    await user.should_see("Quick Switcher")
    await user.should_see("Sessions")
    await user.should_see("Marketplace")
    await user.should_see("Skills")
    await user.should_see("Diagnostics")
    await user.should_see("Settings")


@pytest.mark.asyncio
async def test_empty_state_prompt_rendered(user: User) -> None:
    """Verify empty chat state prompt renders."""
    state = AppState()

    @ui.page("/test_empty_state")
    def page() -> None:
        build_page(state)

    await user.open("/test_empty_state")
    await user.should_see("What should Mvge work on?")


@pytest.mark.asyncio
async def test_new_conversation_resets_active_tome(user: User, tmp_path: Path) -> None:
    """Verify new conversation resets active tome."""
    tome_dir = tmp_path / "tomes"
    project_path = tmp_path / "proj"
    project_path.mkdir()
    factory = TomeHandleFactory(tome_dir)
    meta_id = factory.create_tome(str(project_path)).tome_id

    service = TomeService(tome_dir)
    state = AppState(project_path=project_path, tome_service=service)
    state.load_tomes()
    state.switch_to_tome(meta_id)
    state.current_user = _mock_user()

    @ui.page("/test_new_convo_transition")
    def page() -> None:
        build_page(state)

    await user.open("/test_new_convo_transition")
    user.find("new_conversation_btn").click()

    assert state.active_tome_id is None
    assert state.tome_title == "New Conversation"


# --- Skills panel tests ---


@pytest.mark.asyncio
async def test_skills_panel_renders_data(user: User) -> None:
    """Verify skills panel shows skill name and description when skills are active."""
    state = AppState(current_view="skills")
    state.add_skill(
        _make_manifest(
            name="code-review",
            description="Review code",
            path="/skills/code-review/SKILL.md",
        )
    )

    @ui.page("/test_skills_panel")
    def page() -> None:
        build_page(state)

    await user.open("/test_skills_panel")
    await user.should_see("Skills")
    await user.should_see("code-review")
    await user.should_see("Review code")


@pytest.mark.asyncio
async def test_diagnostics_panel_renders(user: User) -> None:
    """Verify diagnostics panel renders properly."""
    state = AppState(current_view="diagnostics")

    @ui.page("/test_diagnostics_panel")
    def page() -> None:
        build_page(state)

    await user.open("/test_diagnostics_panel")
    await user.should_see("Diagnostics")


@pytest.mark.asyncio
async def test_packages_panel_renders(user: User) -> None:
    """Verify packages panel renders properly."""
    state = AppState(current_view="packages")

    @ui.page("/test_packages_panel")
    def page() -> None:
        build_page(state)

    await user.open("/test_packages_panel")
    await user.should_see("Marketplace")


@pytest.mark.asyncio
async def test_build_page_uses_saved_theme(user, tmp_path: Path) -> None:
    """build_page boots with the persisted theme and keeps the DarkMode handle.

    Observable result: with "light" persisted, the page's DarkMode handle
    is off (``value is False``).
    """
    service = ConfigService(config_dir=tmp_path)
    service.save_app_settings(AppSettings(theme="light"))
    state = AppState(project_path=tmp_path)
    state._config_service = service

    @ui.page("/test_saved_theme")
    def page() -> None:
        build_page(state)

    await user.open("/test_saved_theme")
    assert state._dark_mode is not None
    assert state._dark_mode.value is False
