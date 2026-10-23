"""Unit tests for the Adaptive Dual-Purpose Canvas component."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from mvgeos_runes import RuneUICanvasTab
from nicegui import ui
from nicegui.testing import User

from mvgeos_gui.components.canvas import render_canvas
from mvgeos_gui.models import Artifact, ArtifactType, ChangedFile
from mvgeos_gui.state import AppState


def _make_artifact(
    artifact_id: str = "art-1",
    title: str = "Architecture Canvas",
    content: str = "# Architecture\n\n```mermaid\ngraph TD;\nA-->B;\n```",
    artifact_type: ArtifactType = ArtifactType.DOCUMENT,
) -> Artifact:
    return Artifact(
        id=artifact_id,
        title=title,
        summary="System architecture overview",
        content=content,
        artifact_type=artifact_type,
    )


@pytest.mark.asyncio
async def test_render_canvas_review_tab_default(user: User) -> None:
    """Canvas defaults to Review tab showing changed files or empty state."""
    state = AppState()
    state.changed_files = [
        ChangedFile(path="src/main.py", status="modified", additions=5, deletions=2)
    ]

    @ui.page("/test_canvas_review_default")
    def page() -> None:
        render_canvas(state)

    await user.open("/test_canvas_review_default")
    await user.should_see("Review")
    await user.should_see("Changed files")
    await user.should_see("src/main.py")
    assert user.find(marker="canvas_tab_review")
    assert user.find(marker="canvas_dock_toggle_btn")
    assert user.find(marker="toggle_review_btn")


@pytest.mark.asyncio
async def test_render_canvas_artifact_tab_switching(user: User) -> None:
    """Opening an artifact shows the Artifact tab and renders artifact content."""
    state = AppState()
    artifact = _make_artifact()
    state.add_artifact(artifact)
    state.open_artifact(artifact.id)

    @ui.page("/test_canvas_artifact_tab")
    def page() -> None:
        render_canvas(state)

    await user.open("/test_canvas_artifact_tab")
    assert user.find(marker="canvas_tab_artifact")
    await user.should_see("Architecture Canvas")
    await user.should_see("System architecture overview")

    # Switch back to review tab
    user.find(marker="canvas_tab_review").click()
    assert state.active_canvas_tab == "review"


@pytest.mark.asyncio
async def test_render_canvas_sub_mvge_tab_and_tome_projection(user: User) -> None:
    """Inspecting a sub-tome activates Sub-Mvge tab with full tome action."""
    state = AppState()
    state.switch_to_tome = MagicMock()  # type: ignore[method-assign]
    state.inspect_sub_tome("child_tome_789", "Data Pipeline Sub-Mvge")

    @ui.page("/test_canvas_sub_mvge")
    def page() -> None:
        render_canvas(state)

    await user.open("/test_canvas_sub_mvge")
    assert user.find(marker="canvas_tab_sub_mvge")
    await user.should_see("Data Pipeline Sub-Mvge")
    assert user.find(marker="canvas_open_full_tome_btn")

    user.find(marker="canvas_open_full_tome_btn").click()
    state.switch_to_tome.assert_called_once_with("child_tome_789")


@pytest.mark.asyncio
async def test_render_canvas_dock_float_toggle(user: User) -> None:
    """Clicking dock toggle button toggles state.canvas_docked."""
    state = AppState()
    state.canvas_docked = True

    @ui.page("/test_canvas_dock_toggle")
    def page() -> None:
        render_canvas(state)

    await user.open("/test_canvas_dock_toggle")
    user.find(marker="canvas_dock_toggle_btn").click()
    assert state.canvas_docked is False


@pytest.mark.asyncio
async def test_render_canvas_html_artifact_iframe_sandboxing(user: User) -> None:
    """HTML/SVG artifacts render in an iframe omitting allow-same-origin."""
    state = AppState()
    html_artifact = _make_artifact(
        artifact_id="art-html",
        title="Widget Mockup",
        content="<!DOCTYPE html><html><body><h1>Mockup</h1></body></html>",
        artifact_type=ArtifactType.OTHER,
    )
    state.add_artifact(html_artifact)
    state.open_artifact(html_artifact.id)

    @ui.page("/test_canvas_iframe")
    def page() -> None:
        render_canvas(state)

    await user.open("/test_canvas_iframe")
    iframe = user.find(marker="sandboxed_artifact_iframe")
    assert iframe is not None
    # Verify sandbox attribute does not allow same-origin
    el = next(iter(iframe.elements))
    sandbox_attr = str(el.props.get("sandbox", ""))
    assert "allow-scripts" in sandbox_attr
    assert "allow-same-origin" not in sandbox_attr


@pytest.mark.asyncio
async def test_render_canvas_files_tab(user: User, tmp_path) -> None:
    """Canvas renders workspace file tree under Files tab."""
    test_file = tmp_path / "app_entry.py"
    test_file.write_text("print('hello')")

    state = AppState(project_path=tmp_path)
    state.set_canvas_tab("files")

    @ui.page("/test_canvas_files")
    def page() -> None:
        render_canvas(state)

    await user.open("/test_canvas_files")
    assert user.find(marker="canvas_tab_files")
    await user.should_see("app_entry.py")


@pytest.mark.asyncio
async def test_render_canvas_dynamic_contributed_tab(user: User) -> None:
    """Canvas should dynamically render tabs contributed by installed runes."""
    state = AppState()
    contributed_tabs = [
        RuneUICanvasTab(
            id="custom_inspect",
            title="Custom Inspect",
            icon="science",
            order=40,
        )
    ]

    with patch.object(
        state, "get_contributed_canvas_tabs", return_value=contributed_tabs
    ):

        @ui.page("/test_canvas_dynamic_tab")
        def page() -> None:
            render_canvas(state)

        await user.open("/test_canvas_dynamic_tab")
        tab_btn = user.find(marker="canvas_tab_custom_inspect")
        assert tab_btn is not None
        await user.should_see("Custom Inspect")

        tab_btn.click()
        assert state.active_canvas_tab == "custom_inspect"

    # Verify surface rendering when custom tab is active
    state2 = AppState()
    state2.active_canvas_tab = "custom_inspect"
    with patch.object(
        state2, "get_contributed_canvas_tabs", return_value=contributed_tabs
    ):

        @ui.page("/test_canvas_dynamic_surface")
        def page2() -> None:
            render_canvas(state2)

        await user.open("/test_canvas_dynamic_surface")
        await user.should_see("Custom Inspect Surface")
        await user.should_see("Contributed by custom_inspect rune.")
