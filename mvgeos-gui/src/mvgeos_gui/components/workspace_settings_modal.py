"""Project Workspace Settings modal component for mvgeos-gui."""

from __future__ import annotations

from mvgeos_gui.components.settings_modal import (
    CONTEMPLATION_LEVELS,
    render_app_settings_modal,
)
from mvgeos_gui.state import AppState

__all__ = ["CONTEMPLATION_LEVELS", "render_workspace_settings_modal"]


def render_workspace_settings_modal(state: AppState) -> None:
    """Render the Project Workspace Settings modal dialog."""
    render_app_settings_modal(state, default_tab="workspace")
