"""File tree panel for browsing workspace files."""

from __future__ import annotations

from pathlib import Path

from nicegui import ui

from mvgeos_gui.state import AppState


def render_file_tree(state: AppState) -> None:
    """Render the workspace file tree."""
    root = state.project_path
    if not root.exists():
        ui.label("Project folder not found").classes(
            "text-xs text-[var(--deletion-red)] p-4"
        )
        return

    with ui.column().classes("w-full flex-1 min-h-0 overflow-y-auto p-2"):
        _render_tree(state, root, root, 0)


def _render_tree(state: AppState, root: Path, path: Path, depth: int) -> None:
    """Recursively render a directory tree."""
    try:
        entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except (PermissionError, OSError):
        return

    for entry in entries:
        # All dotfiles stay hidden, including version-control internals
        # (.git) and tooling dirs (.agents): the tree is for browsing the
        # workspace, not repository plumbing.
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            with ui.expansion(
                entry.name,
                group=f"tree-{path}",
                value=False,
            ).classes(f"pl-{depth * 4} text-xs"):
                _render_tree(state, root, entry, depth + 1)
        else:
            row_cls = (
                f"pl-{depth * 4 + 2} py-1 cursor-pointer hover:bg-[var(--bg-card)] "
                f"rounded text-xs w-full items-center gap-1"
            )
            with (
                ui.row()
                .classes(row_cls)
                .on("click", lambda p=entry: state.open_file_preview(p))
            ):
                ui.icon("insert_drive_file", size="12px").classes(
                    "text-[var(--text-secondary)]"
                )
                ui.label(entry.name).classes("text-[var(--text-primary)] truncate")
