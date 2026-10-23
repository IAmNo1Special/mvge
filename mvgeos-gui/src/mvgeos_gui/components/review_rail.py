"""Right-side review rail showing changed files and session status.

Delegates to the unified Adaptive Dual-Purpose Canvas surface (ADR-0026).
"""

from __future__ import annotations

from nicegui import ui

from mvgeos_gui.components.canvas import render_canvas
from mvgeos_gui.state import AppState


def render_review_rail(state: AppState) -> ui.column:
    """Render the right-side review rail / canvas."""
    return render_canvas(state)
