from unittest.mock import MagicMock

import pytest
from nicegui import ui
from nicegui.testing import User

from mvgeos_gui.components.diff_viewer import render_diff_viewer
from mvgeos_gui.state import AppState


@pytest.mark.asyncio
async def test_render_diff_viewer_no_selection(user: User, tmp_path) -> None:
    state = AppState(project_path=tmp_path)

    @ui.page("/test_diff_none")
    def page() -> None:
        render_diff_viewer(state)

    await user.open("/test_diff_none")
    await user.should_see("No diff selected")


@pytest.mark.asyncio
async def test_render_diff_viewer_with_hunk(user: User, tmp_path) -> None:
    state = AppState(project_path=tmp_path)
    # mock view with minimal hunk
    mock_hunk = MagicMock()
    mock_hunk.source_start = 1
    mock_hunk.source_length = 3
    mock_hunk.target_start = 1
    mock_hunk.target_length = 4
    mock_line = MagicMock()
    mock_line.line_type = "addition"
    mock_line.content = "new line"
    mock_line.old_line_number = None
    mock_line.new_line_number = 1
    mock_hunk.lines = [mock_line]

    mock_view = MagicMock()
    mock_view.file_path = "src/foo.py"
    mock_view.status = "modified"
    mock_view.additions = 1
    mock_view.deletions = 0
    mock_view.hunks = [mock_hunk]

    state.get_selected_diff_view = MagicMock(return_value=mock_view)

    @ui.page("/test_diff_hunk")
    def page() -> None:
        render_diff_viewer(state)

    await user.open("/test_diff_hunk")
    await user.should_see("src/foo.py")


@pytest.mark.asyncio
async def test_render_diff_viewer_empty_state_names_what_it_tracks(
    user: User, tmp_path
) -> None:
    """The empty state must say it is waiting on agent-made changes."""
    state = AppState(project_path=tmp_path)

    @ui.page("/test_diff_empty_guidance")
    def page() -> None:
        render_diff_viewer(state)

    await user.open("/test_diff_empty_guidance")
    await user.should_see("No diff selected")
    await user.should_see("changes made by the Mvge in this Tome")
