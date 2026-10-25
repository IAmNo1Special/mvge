"""Artifact cards and sliding markdown preview drawer for mvgeos-gui."""

from __future__ import annotations

import re

from nicegui import ui
from nicegui_ai import AiMarkdown

from mvgeos_gui.models import Artifact, ArtifactType
from mvgeos_gui.state import AppState
from mvgeos_gui.utils import copy_to_clipboard, download_artifact

_ARTIFACT_TYPE_ICONS: dict[ArtifactType, str] = {
    ArtifactType.WALKTHROUGH: "route",
    ArtifactType.IMPLEMENTATION_PLAN: "checklist",
    ArtifactType.CODE_REVIEW: "rate_review",
    ArtifactType.DOCUMENT: "description",
    ArtifactType.OTHER: "category",
}

_ARTIFACT_TYPE_COLORS: dict[ArtifactType, str] = {
    ArtifactType.WALKTHROUGH: "text-[var(--accent-primary)]",
    ArtifactType.IMPLEMENTATION_PLAN: "text-[var(--addition-green)]",
    ArtifactType.CODE_REVIEW: "text-[var(--text-warn)]",
    ArtifactType.DOCUMENT: "text-[var(--accent-primary)]",
    ArtifactType.OTHER: "text-[var(--text-secondary)]",
}


def _render_markdown_with_mermaid(content: str) -> None:
    """Render markdown content, extracting mermaid blocks for dedicated rendering."""
    mermaid_pattern = re.compile(r"```mermaid\n(.*?)\n```", re.DOTALL)
    parts = mermaid_pattern.split(content)

    for i, part in enumerate(parts):
        if i % 2 == 0:
            if part.strip():
                AiMarkdown(part).classes("markdown-content max-w-none w-full")
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


def render_artifact_card(artifact: Artifact, state: AppState) -> None:
    """Render an in-stream Artifact card with title, summary, and action buttons."""
    icon_name = _ARTIFACT_TYPE_ICONS.get(artifact.artifact_type, "category")
    color_class = _ARTIFACT_TYPE_COLORS.get(
        artifact.artifact_type, "text-[var(--text-secondary)]"
    )
    type_label = artifact.artifact_type.value.replace("_", " ").title()

    with (
        ui.column().classes("w-full max-w-3xl mx-auto px-6 py-2 items-start"),
        ui.card().classes(
            "w-full bg-[var(--bg-raised)] border border-[var(--border-subtle)] "
            "rounded-xl p-4 gap-2 shadow-md"
        ),
    ):
        with ui.row().classes("w-full items-center justify-between gap-4"):
            with ui.row().classes("items-center gap-2"):
                ui.icon(icon_name, size="16px").classes(color_class)
                ui.label(artifact.title).classes(
                    "text-sm font-semibold text-[var(--text-primary)]"
                )
                ui.badge(type_label).props("rounded dense").classes(
                    "text-[9px] text-[var(--text-secondary)] font-mono uppercase "
                    "bg-[var(--bg-card)] border border-[var(--border-subtle)]"
                )
            with ui.row().classes("items-center gap-1"):
                ui.label(artifact.created_at).classes(
                    "text-[10px] text-[var(--text-muted)] font-mono"
                )
                ui.button(
                    icon="visibility",
                    on_click=lambda a_id=artifact.id: state.open_artifact(a_id),
                ).props("flat dense round size=xs text-color=grey-5").mark(
                    "review_artifact_btn"
                )
                ui.tooltip("Review artifact")

        if artifact.summary:
            ui.label(artifact.summary).classes(
                "text-xs text-[var(--text-secondary)] leading-relaxed"
            )


def render_artifact_drawer(state: AppState) -> None:
    """Render a sliding preview drawer for the selected artifact."""
    artifact = state.get_selected_artifact()
    if artifact is None:
        return

    def _on_close() -> None:
        state.close_artifact()

    with (
        ui.dialog().classes("w-full max-w-4xl").on("close", _on_close) as dialog,
        ui.card().classes(
            "w-full bg-[var(--bg-surface)] border border-[var(--border-subtle)] "
            "rounded-xl p-0 overflow-hidden"
        ),
    ):
        with ui.row().classes(
            "w-full h-11 px-4 items-center justify-between "
            "border-b border-[var(--border-subtle)] bg-[var(--bg-raised)]"
        ):
            with ui.row().classes("items-center gap-2"):
                ui.icon("description", size="16px").classes(
                    "text-[var(--accent-primary)]"
                )
                ui.label(artifact.title).classes(
                    "text-sm font-medium text-[var(--text-primary)]"
                )
                ui.badge(
                    artifact.artifact_type.value.replace("_", " ").title(),
                ).props("rounded dense").classes(
                    "text-[9px] text-[var(--text-secondary)] font-mono uppercase "
                    "bg-[var(--bg-card)] border border-[var(--border-subtle)]"
                )
            ui.button(
                icon="close",
                on_click=lambda: dialog.close(),
            ).props("flat dense round text-color=grey-5 size=sm")

        with ui.row().classes(
            "w-full px-4 py-2 items-center gap-2 border-b "
            "border-[var(--border-subtle)] bg-[var(--bg-raised)]"
        ):
            ui.button(
                icon="content_copy",
                on_click=lambda: copy_to_clipboard(artifact.content),
            ).props("flat dense no-caps size=xs text-color=grey-5")
            ui.tooltip("Copy content")
            ui.button(
                icon="download",
                on_click=lambda: download_artifact(artifact.title, artifact.content),
            ).props("flat dense no-caps size=xs text-color=grey-5")
            ui.tooltip("Download artifact")
            if artifact.file_paths:
                ui.label("Files:").classes(
                    "text-[10px] text-[var(--text-muted)] font-mono"
                )
                for path in artifact.file_paths:
                    ui.label(path).classes(
                        "text-[10px] text-[var(--accent-primary)] font-mono"
                    )

        content_container = ui.column().classes(
            "w-full max-h-[70vh] overflow-auto bg-[var(--bg-sunken)] p-4"
        )
        with content_container:
            if artifact.summary:
                with ui.row().classes(
                    "w-full items-center gap-2 p-3 mb-3 rounded-lg "
                    "bg-[var(--bg-card)] border border-[var(--border-subtle)]"
                ):
                    ui.icon("info", size="14px").classes("text-[var(--accent-primary)]")
                    ui.label(artifact.summary).classes(
                        "text-xs text-[var(--text-dim)] italic"
                    )

            _render_markdown_with_mermaid(artifact.content)

    dialog.open()
