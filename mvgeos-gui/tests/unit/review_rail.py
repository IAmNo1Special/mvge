"""Unit tests for the Review Rail component."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from nicegui import ui
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction

from mvgeos_gui.components.review_rail import render_review_rail
from mvgeos_gui.models import ChangedFile
from mvgeos_gui.state import AppState


@pytest.mark.asyncio
async def test_render_review_rail_empty_changes(user: User) -> None:
    """Review rail should show 'No changes detected' when changed_files is empty."""
    state = AppState()
    state.changed_files = []

    @ui.page("/test_rail_empty")
    def page() -> None:
        render_review_rail(state)

    await user.open("/test_rail_empty")
    await user.should_see("Review")
    await user.should_see("Changed files")
    await user.should_see("No Mvge changes yet")
    await user.should_see("changes made by the Mvge in this Tome")


@pytest.mark.asyncio
async def test_render_review_rail_with_changes(user: User) -> None:
    """Review rail should display file paths and change counts for each file."""
    state = AppState()
    state.changed_files = [
        ChangedFile(path="src/new_file.py", status="new", additions=15, deletions=0),
        ChangedFile(
            path="src/mod_file.py", status="modified", additions=5, deletions=3
        ),
        ChangedFile(path="src/del_file.py", status="deleted", additions=0, deletions=8),
        ChangedFile(path="src/ren_file.py", status="renamed", additions=1, deletions=1),
    ]

    @ui.page("/test_rail_files")
    def page() -> None:
        render_review_rail(state)

    await user.open("/test_rail_files")
    await user.should_see("src/new_file.py")
    await user.should_see("+15")
    await user.should_see("src/mod_file.py")
    await user.should_see("+5")
    await user.should_see("-3")
    await user.should_see("src/del_file.py")
    await user.should_see("-8")
    await user.should_see("src/ren_file.py")


@pytest.mark.asyncio
async def test_render_review_rail_click_file_opens_diff(user: User) -> None:
    """Clicking a changed file row should call open_diff_review with the path."""
    state = AppState()
    state.changed_files = [
        ChangedFile(
            path="src/mod_file.py", status="modified", additions=2, deletions=1
        ),
    ]
    state.open_diff_review = MagicMock()

    @ui.page("/test_rail_click")
    def page() -> None:
        render_review_rail(state)

    await user.open("/test_rail_click")
    label = next(iter(user.find("src/mod_file.py").elements))
    row = label.parent_slot.parent
    UserInteraction(user, [row], target=None).click()
    state.open_diff_review.assert_called_once_with("src/mod_file.py")


@pytest.mark.asyncio
async def test_render_review_rail_toggle_button(user: User) -> None:
    """Clicking close button in review rail should toggle review state."""
    state = AppState()
    state.toggle_review = MagicMock()

    @ui.page("/test_rail_toggle")
    def page() -> None:
        render_review_rail(state)

    await user.open("/test_rail_toggle")
    user.find(marker="toggle_review_btn").click()
    state.toggle_review.assert_called_once()


@pytest.mark.asyncio
async def test_render_review_rail_refresh_button(user: User) -> None:
    """Clicking refresh button should call refresh_changed_files."""
    state = AppState()
    state.refresh_changed_files = MagicMock()

    @ui.page("/test_rail_refresh")
    def page() -> None:
        render_review_rail(state)

    await user.open("/test_rail_refresh")
    user.find(marker="refresh_changes_btn").click()
    state.refresh_changed_files.assert_called_once()


@pytest.mark.asyncio
async def test_review_rail_has_no_permission_dropdown(user: User) -> None:
    """The decorative permission dropdown is gone from the Review rail."""
    state = AppState()
    state.changed_files = []

    @ui.page("/test_rail_no_permission")
    def page() -> None:
        render_review_rail(state)

    await user.open("/test_rail_no_permission")
    await user.should_not_see("Permission mode")
    await user.should_see("Changed files")


@pytest.mark.asyncio
async def test_render_review_rail_empty_state_names_what_it_tracks(
    user: User,
) -> None:
    """The empty state must say it is waiting on agent-made changes."""
    state = AppState()
    state.changed_files = []

    @ui.page("/test_rail_empty_guidance")
    def page() -> None:
        render_review_rail(state)

    await user.open("/test_rail_empty_guidance")
    await user.should_see("No Mvge changes yet")
    await user.should_see("changes made by the Mvge in this Tome")
