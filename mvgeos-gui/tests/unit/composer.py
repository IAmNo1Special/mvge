"""Unit tests for the Composer component."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from nicegui import ui
from nicegui.testing import User

from mvgeos_gui.components.composer import _render_missing_rune_badge
from mvgeos_gui.state import AppState


@pytest.mark.asyncio
async def test_missing_rune_badge_names_rune_and_resolution(
    user: User,
) -> None:
    """The amber badge must name the missing rune and how to resolve it."""
    state = AppState()
    state.selected_realm = "openrouter"
    state.is_rune_installed = MagicMock(return_value=False)  # type: ignore[method-assign]

    @ui.page("/test_composer_missing_rune")
    def page() -> None:
        _render_missing_rune_badge(state)

    await user.open("/test_composer_missing_rune")
    await user.should_see("Missing rune: openrouter-realm")
    await user.should_see("Install openrouter-realm from the Marketplace")


@pytest.mark.asyncio
async def test_missing_rune_badge_click_opens_marketplace(user: User) -> None:
    """Clicking the badge navigates to the Marketplace view."""
    state = AppState()
    state.selected_realm = "openrouter"
    state.is_rune_installed = MagicMock(return_value=False)  # type: ignore[method-assign]
    state.set_current_view = MagicMock()  # type: ignore[method-assign]

    @ui.page("/test_composer_missing_rune_click")
    def page() -> None:
        _render_missing_rune_badge(state)

    await user.open("/test_composer_missing_rune_click")
    user.find(marker="missing_rune_badge").click()
    state.set_current_view.assert_called_once_with("packages")


@pytest.mark.asyncio
async def test_missing_rune_badge_hidden_when_rune_installed(
    user: User,
) -> None:
    """No badge when the rune is installed."""
    state = AppState()
    state.selected_realm = "openrouter"
    state.is_rune_installed = MagicMock(return_value=True)  # type: ignore[method-assign]

    @ui.page("/test_composer_rune_installed")
    def page() -> None:
        _render_missing_rune_badge(state)

    await user.open("/test_composer_rune_installed")
    await user.should_not_see("Missing rune")


@pytest.mark.asyncio
async def test_missing_rune_badge_hidden_for_other_realm(user: User) -> None:
    """No badge when the OpenRouter realm is not selected."""
    state = AppState()
    state.selected_realm = "ollama"
    state.is_rune_installed = MagicMock(return_value=False)  # type: ignore[method-assign]

    @ui.page("/test_composer_other_realm")
    def page() -> None:
        _render_missing_rune_badge(state)

    await user.open("/test_composer_other_realm")
    await user.should_not_see("Missing rune")


@pytest.mark.asyncio
async def test_composer_in_flight_actions_rendered_while_channeling(
    user: User,
) -> None:
    """When channeling, composer renders Stop, Steer, and Queue actions."""
    from mvgeos_gui.components.composer import render_composer

    state = AppState()
    state.is_channeling = True

    @ui.page("/test_composer_channeling")
    def page() -> None:
        render_composer(state)

    await user.open("/test_composer_channeling")
    assert user.find(marker="stop_channeling_btn")
    assert user.find(marker="steer_mvge_btn")
    assert user.find(marker="queue_follow_up_btn")


@pytest.mark.asyncio
async def test_composer_queued_tray_rendered_when_follow_ups_exist(
    user: User,
) -> None:
    """When queued follow-ups exist, composer renders the queued follow-ups tray."""
    from mvgeos_gui.components.composer import render_composer

    state = AppState()
    state.queued_follow_ups = ["Verify type annotations"]

    @ui.page("/test_composer_tray")
    def page() -> None:
        render_composer(state)

    await user.open("/test_composer_tray")
    await user.should_see("Queued: Verify type annotations")


@pytest.mark.asyncio
async def test_composer_steer_and_queue_actions(user: User) -> None:
    """Steer and queue button clicks invoke steer and follow-up actions."""
    from mvgeos_gui.components.composer import render_composer

    state = AppState()
    state.is_channeling = True
    state.steer_prompt = MagicMock()  # type: ignore[method-assign]
    state.queue_follow_up = MagicMock()  # type: ignore[method-assign]

    @ui.page("/test_composer_interactive")
    def page() -> None:
        render_composer(state)

    await user.open("/test_composer_interactive")
    # Set draft text
    next(iter(user.find(marker="prompt_input").elements)).value = "Course correction"

    user.find(marker="steer_mvge_btn").click()
    state.steer_prompt.assert_called_once_with("Course correction")

    # Set follow up text
    next(iter(user.find(marker="prompt_input").elements)).value = "Follow up later"
    user.find(marker="queue_follow_up_btn").click()
    state.queue_follow_up.assert_called_once_with("Follow up later")
