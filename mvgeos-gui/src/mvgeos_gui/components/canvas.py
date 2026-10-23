"""Adaptive Dual-Purpose Canvas component for mvgeos-gui.

Unifies the Git Review Rail, Artifact Inspection, and Sub-Mvge Tome Projections
into an adaptive, multi-surface right panel per ADR-0026.
"""

from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING

from nicegui import ui

from mvgeos_gui.components.file_tree import render_file_tree
from mvgeos_gui.models import ArtifactType
from mvgeos_gui.utils import copy_to_clipboard, download_artifact

if TYPE_CHECKING:
    from mvgeos_gui.state import AppState


def _render_markdown_with_mermaid(content: str) -> None:
    """Render markdown content, extracting mermaid blocks for dedicated rendering."""
    mermaid_pattern = re.compile(r"```mermaid\n(.*?)\n```", re.DOTALL)
    parts = mermaid_pattern.split(content)

    for i, part in enumerate(parts):
        if i % 2 == 0:
            if part.strip():
                ui.markdown(part).classes("markdown-content max-w-none w-full")
        else:
            with ui.column().classes(
                "w-full my-3 p-3 rounded-lg bg-[var(--bg-well-deep)] border "
                "border-[var(--bg-tint)]"
            ):
                ui.label("Mermaid Diagram").classes(
                    "text-[10px] text-[var(--text-muted)] uppercase font-mono mb-2"
                )
                try:
                    ui.mermaid(part.strip()).classes("w-full")
                except Exception:
                    ui.code(part.strip()).classes(
                        "w-full text-[11px] bg-[var(--bg-sunken)] p-2 rounded font-mono"
                    )


def render_canvas(state: AppState) -> ui.column:
    """Render the Adaptive Dual-Purpose Canvas surface."""
    is_wide_surface = state.active_canvas_tab in ("artifact", "sub_mvge")
    canvas_width = (
        max(state._review_width, 500) if is_wide_surface else state._review_width
    )

    layout_classes = (
        "h-full bg-[var(--bg-surface)] border-l border-[var(--border-subtle)] "
        "shrink-0 flex flex-col overflow-hidden"
        if state.canvas_docked
        else "fixed right-0 top-0 bottom-7 z-40 shadow-2xl bg-[var(--bg-surface)] "
        "border-l border-[var(--border-subtle)] flex flex-col overflow-hidden"
    )

    container = ui.column().classes(layout_classes).style(f"width: {canvas_width}px")

    with container:
        # Multi-surface Tab Bar Header
        with ui.row().classes(
            "w-full h-11 items-center justify-between px-2 "
            "border-b border-[var(--border-subtle)] bg-[var(--bg-raised)] "
            "shrink-0 gap-1"
        ):
            with ui.row().classes("items-center gap-1 flex-1 overflow-x-auto no-wrap"):
                # Review Tab
                is_review = state.active_canvas_tab == "review"
                review_cls = (
                    "bg-[var(--bg-card)] text-[var(--text-primary)] font-semibold "
                    "border-b-2 border-[var(--accent-primary)]"
                    if is_review
                    else "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                )
                review_count = len(state.changed_files)
                review_label = f"Review ({review_count})" if review_count else "Review"
                with (
                    ui.button(
                        on_click=lambda: state.set_canvas_tab("review"),
                    )
                    .props("flat dense no-caps size=xs")
                    .classes(f"px-2 py-1 rounded-t text-xs {review_cls}")
                    .mark("canvas_tab_review")
                ):
                    ui.label(review_label).classes("text-xs uppercase tracking-wider")

                # Files Tab
                is_files = state.active_canvas_tab == "files"
                files_cls = (
                    "bg-[var(--bg-card)] text-[var(--text-primary)] font-semibold "
                    "border-b-2 border-[var(--accent-primary)]"
                    if is_files
                    else "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                )
                with (
                    ui.button(
                        on_click=lambda: state.set_canvas_tab("files"),
                    )
                    .props("flat dense no-caps size=xs")
                    .classes(f"px-2 py-1 rounded-t text-xs {files_cls}")
                    .mark("canvas_tab_files")
                ):
                    ui.label("Files").classes("text-xs uppercase tracking-wider")

                # Artifact Tab
                selected_artifact = state.get_selected_artifact()
                if selected_artifact is not None:
                    is_art = state.active_canvas_tab == "artifact"
                    art_cls = (
                        "bg-[var(--bg-card)] text-[var(--text-primary)] font-semibold "
                        "border-b-2 border-[var(--accent-primary)]"
                        if is_art
                        else "text-[var(--text-secondary)] "
                        "hover:text-[var(--text-primary)]"
                    )
                    with (
                        ui.row()
                        .classes(
                            "items-center gap-1 px-2 py-1 rounded-t cursor-pointer "
                            f"{art_cls}"
                        )
                        .on("click", lambda: state.set_canvas_tab("artifact"))
                        .mark("canvas_tab_artifact")
                    ):
                        ui.icon("description", size="12px").classes(
                            "text-[var(--accent-primary)]"
                        )
                        ui.label(selected_artifact.title).classes(
                            "text-xs truncate max-w-[120px]"
                        )
                        ui.icon("close", size="10px").classes(
                            "text-[var(--text-muted)] "
                            "hover:text-[var(--text-primary)] cursor-pointer"
                        ).on("click", lambda _: state.close_artifact())

                # Sub-Mvge Tab
                if state.inspected_sub_tome_id is not None:
                    is_sub = state.active_canvas_tab == "sub_mvge"
                    sub_cls = (
                        "bg-[var(--bg-card)] text-[var(--text-primary)] font-semibold "
                        "border-b-2 border-[var(--accent-primary)]"
                        if is_sub
                        else "text-[var(--text-secondary)] "
                        "hover:text-[var(--text-primary)]"
                    )
                    with (
                        ui.row()
                        .classes(
                            "items-center gap-1 px-2 py-1 rounded-t cursor-pointer "
                            f"{sub_cls}"
                        )
                        .on("click", lambda: state.set_canvas_tab("sub_mvge"))
                        .mark("canvas_tab_sub_mvge")
                    ):
                        ui.icon("hub", size="12px").classes(
                            "text-[var(--addition-green)]"
                        )
                        sub_title = state.inspected_sub_tome_title or "Sub-Mvge"
                        ui.label(sub_title).classes("text-xs truncate max-w-[120px]")
                        ui.icon("close", size="10px").classes(
                            "text-[var(--text-muted)] "
                            "hover:text-[var(--text-primary)] cursor-pointer"
                        ).on("click", lambda _: state.close_sub_tome())

                # Other Rune Contributed Canvas Tabs
                known_tabs = {"files", "review", "artifact", "sub_mvge"}
                for ctab in state.get_contributed_canvas_tabs():
                    if ctab.id in known_tabs:
                        continue
                    is_active = state.active_canvas_tab == ctab.id
                    tab_cls = (
                        "bg-[var(--bg-card)] text-[var(--text-primary)] font-semibold "
                        "border-b-2 border-[var(--accent-primary)]"
                        if is_active
                        else (
                            "text-[var(--text-secondary)] "
                            "hover:text-[var(--text-primary)]"
                        )
                    )
                    with (
                        ui.button(
                            on_click=lambda tid=ctab.id: state.set_canvas_tab(tid),
                        )
                        .props("flat dense no-caps size=xs")
                        .classes(f"px-2 py-1 rounded-t text-xs {tab_cls}")
                        .mark(f"canvas_tab_{ctab.id}")
                    ):
                        ui.label(ctab.title).classes("text-xs uppercase tracking-wider")

            # Header action icons (Dock/Float and Close)
            with ui.row().classes("items-center gap-1 shrink-0"):
                dock_icon = "view_sidebar" if state.canvas_docked else "open_in_new"
                dock_tooltip = "Float Canvas" if state.canvas_docked else "Dock Canvas"
                with (
                    ui.button(
                        icon=dock_icon,
                        on_click=state.toggle_canvas_docked,
                    )
                    .props("flat dense round text-color=grey-5 size=xs")
                    .mark("canvas_dock_toggle_btn")
                ):
                    ui.tooltip(dock_tooltip)

                with (
                    ui.button(
                        icon="close",
                        on_click=state.toggle_review,
                    )
                    .props("flat dense round text-color=grey-5 size=xs")
                    .mark("toggle_review_btn")
                ):
                    ui.tooltip("Close Canvas")

        # Surface 1: Git Review Rail
        if state.active_canvas_tab == "review":
            with ui.row().classes(
                "px-3 py-2 border-b border-[var(--border-subtle-a60)] "
                "items-center justify-between"
            ):
                ui.label("Changed files").classes(
                    "text-[10px] font-medium text-[var(--text-muted)] uppercase "
                    "tracking-wider"
                )
                ui.button(
                    icon="refresh",
                    on_click=state.refresh_changed_files,
                ).props("flat dense round text-color=grey-5 size=xs").mark(
                    "refresh_changes_btn"
                )

            with ui.column().classes("flex-1 overflow-y-auto px-3 py-2"):
                if not state.changed_files:
                    with ui.column().classes("gap-1"):
                        ui.label("No Mvge changes yet").classes(
                            "text-[11px] text-[var(--text-muted)]"
                        )
                        ui.label(
                            "This panel tracks changes made by the Mvge in this Tome."
                        ).classes("text-[10px] text-[var(--text-muted-a70)]")
                else:
                    for cf in state.changed_files:
                        with (
                            ui.row()
                            .classes(
                                "w-full items-center gap-2 px-2 py-1.5 rounded-md "
                                "hover:bg-[var(--bg-card)] cursor-pointer text-xs"
                            )
                            .on("click", lambda p=cf.path: state.open_diff_review(p))
                        ):
                            badge_color = {
                                "new": "text-[var(--addition-green)]",
                                "modified": "text-[var(--text-warn)]",
                                "deleted": "text-[var(--deletion-red)]",
                                "renamed": "text-[var(--accent-primary)]",
                            }.get(cf.status, "text-[var(--text-secondary)]")
                            ui.label(cf.status.upper()[:3]).classes(
                                f"text-[10px] font-mono font-bold {badge_color}"
                            )
                            ui.label(cf.path).classes(
                                "text-[var(--text-primary)] truncate flex-1"
                            )
                            with ui.row().classes("items-center gap-1"):
                                if cf.additions:
                                    ui.label(f"+{cf.additions}").classes(
                                        "text-[10px] text-[var(--addition-green)]"
                                    )
                                if cf.deletions:
                                    ui.label(f"-{cf.deletions}").classes(
                                        "text-[10px] text-[var(--deletion-red)]"
                                    )

        # Surface 2: Workspace File Tree
        elif state.active_canvas_tab == "files":
            with ui.column().classes("w-full flex-1 min-h-0 overflow-hidden"):
                render_file_tree(state)

        # Surface 3: Artifact Surface
        elif state.active_canvas_tab == "artifact":
            artifact = state.get_selected_artifact()
            if artifact is None:
                with ui.column().classes("flex-1 items-center justify-center p-6"):
                    ui.label("No artifact selected").classes(
                        "text-xs text-[var(--text-muted)]"
                    )
            else:
                with ui.row().classes(
                    "w-full px-4 py-2 items-center justify-between border-b "
                    "border-[var(--border-subtle)] bg-[var(--bg-raised)]"
                ):
                    with ui.row().classes("items-center gap-2"):
                        ui.badge(
                            artifact.artifact_type.value.replace("_", " ").title()
                        ).props("rounded dense").classes(
                            "text-[9px] text-[var(--text-secondary)] font-mono "
                            "uppercase bg-[var(--bg-card)] border "
                            "border-[var(--border-subtle)]"
                        )
                        if artifact.file_paths:
                            for fp in artifact.file_paths:
                                ui.label(fp).classes(
                                    "text-[10px] text-[var(--accent-primary)] font-mono"
                                )

                    with ui.row().classes("items-center gap-1"):
                        ui.button(
                            icon="content_copy",
                            on_click=lambda: copy_to_clipboard(artifact.content),
                        ).props("flat dense no-caps size=xs text-color=grey-5").mark(
                            "artifact_copy_btn"
                        )
                        ui.tooltip("Copy content")
                        ui.button(
                            icon="download",
                            on_click=lambda: download_artifact(
                                artifact.title, artifact.content
                            ),
                        ).props("flat dense no-caps size=xs text-color=grey-5").mark(
                            "artifact_download_btn"
                        )
                        ui.tooltip("Download artifact")

                with ui.column().classes(
                    "flex-1 overflow-y-auto p-4 bg-[var(--bg-sunken)] gap-3"
                ):
                    if artifact.summary:
                        with ui.row().classes(
                            "w-full items-center gap-2 p-3 rounded-lg "
                            "bg-[var(--bg-card)] border border-[var(--border-subtle)]"
                        ):
                            ui.icon("info", size="14px").classes(
                                "text-[var(--accent-primary)]"
                            )
                            ui.label(artifact.summary).classes(
                                "text-xs text-[var(--text-dim)] italic"
                            )

                    # Check for HTML/SVG content for sandboxed iframe
                    content_lower = artifact.content.strip().lower()
                    if (
                        artifact.artifact_type == ArtifactType.OTHER
                        or content_lower.startswith("<!doctype html")
                        or content_lower.startswith("<html")
                        or content_lower.startswith("<svg")
                    ):
                        escaped_doc = html.escape(artifact.content)
                        ui.element("iframe").props('sandbox="allow-scripts"').props(
                            f'srcdoc="{escaped_doc}"'
                        ).classes(
                            "w-full h-[500px] border border-[var(--border-subtle)] "
                            "rounded-lg bg-white"
                        ).mark("sandboxed_artifact_iframe")
                    else:
                        _render_markdown_with_mermaid(artifact.content)

        # Surface 3: Sub-Mvge Transcript Surface
        elif state.active_canvas_tab == "sub_mvge":
            sub_tome_id = state.inspected_sub_tome_id or ""
            sub_title = state.inspected_sub_tome_title or "Sub-Mvge"

            with ui.row().classes(
                "w-full px-4 py-2 items-center justify-between border-b "
                "border-[var(--border-subtle)] bg-[var(--bg-raised)]"
            ):
                with ui.row().classes("items-center gap-2"):
                    ui.icon("hub", size="14px").classes("text-[var(--addition-green)]")
                    ui.label(sub_title).classes(
                        "text-xs font-semibold text-[var(--text-primary)]"
                    )
                    ui.label(f"Tome: {sub_tome_id[:8]}...").classes(
                        "text-[10px] text-[var(--text-muted)] font-mono"
                    )

                with (
                    ui.button(
                        "Open as Full Tome",
                        icon="open_in_new",
                        on_click=lambda: state.switch_to_tome(sub_tome_id),
                    )
                    .props("unelevated dense size=xs color=primary")
                    .mark("canvas_open_full_tome_btn")
                ):
                    ui.tooltip("Switch full workspace to this Tome")

            with ui.column().classes(
                "flex-1 overflow-y-auto p-4 bg-[var(--bg-sunken)] gap-2"
            ):
                loaded_entries = False
                try:
                    entries = state.tome_service.factory.get_entries(sub_tome_id)
                    if entries:
                        loaded_entries = True
                        for entry in entries:
                            with ui.card().classes(
                                "w-full p-2 bg-[var(--bg-card)] border "
                                "border-[var(--border-subtle)] rounded text-xs"
                            ):
                                ui.label(f"[{entry.type.upper()}]").classes(
                                    "text-[10px] font-mono text-[var(--text-muted)]"
                                )
                                if hasattr(entry, "content"):
                                    ui.markdown(str(entry.content)).classes(
                                        "markdown-content text-xs"
                                    )
                except Exception:
                    pass

                if not loaded_entries:
                    ui.label("Waiting for Sub-Mvge Channeling...").classes(
                        "text-xs text-[var(--text-muted)] italic"
                    )

        # Surface 5: Dynamic Contributed Rune Canvas Surface
        elif state.has_contributed_canvas_tab(state.active_canvas_tab):
            matching = [
                t
                for t in state.get_contributed_canvas_tabs()
                if t.id == state.active_canvas_tab
            ]
            tab_title = (
                matching[0].title if matching else state.active_canvas_tab.title()
            )
            with ui.column().classes("flex-1 p-6 items-center justify-center gap-2"):
                ui.icon("extension", size="32px").classes(
                    "text-[var(--accent-primary)]"
                )
                ui.label(f"{tab_title} Surface").classes(
                    "text-sm font-semibold text-[var(--text-primary)]"
                )
                source_name = matching[0].id if matching else "extension"
                ui.label(f"Contributed by {source_name} rune.").classes(
                    "text-xs text-[var(--text-muted)]"
                )

    return container
