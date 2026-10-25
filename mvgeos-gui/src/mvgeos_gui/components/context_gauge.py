"""Render the context window gauge into the status bar."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui
from nicegui_ai import TokenRing

from mvgeos_gui.context_usage import build_context_gauge

if TYPE_CHECKING:
    from mvgeos_gui.state import AppState


def render_context_gauge(state: AppState) -> None:
    """Render the gauge; renders nothing when data is missing or unverified."""
    gauge = build_context_gauge(state)
    if gauge is None:
        return
    with ui.row().classes("items-center gap-1.5 mr-4"):
        TokenRing(
            used=gauge.used_tokens,
            limit=gauge.window_tokens,
            size=14,
            stroke_width=2.0,
        )
        ui.label(gauge.format()).classes(
            "text-[10px] text-[var(--text-secondary)] font-mono"
        ).mark("context_gauge")
