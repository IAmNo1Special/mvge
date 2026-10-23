"""Application shell layout: sidebar + main + review rail + status bar."""

from __future__ import annotations

from typing import Any

from nicegui import ui

from mvgeos_gui.components.approval_dialog import mount_approval_overlay
from mvgeos_gui.components.artifact_drawer import render_artifact_drawer
from mvgeos_gui.components.chat_panel import render_chat_panel
from mvgeos_gui.components.command_palette import (
    render_command_palette,
    render_rename_dialog,
)
from mvgeos_gui.components.diagnostics_panel import render_diagnostics_panel
from mvgeos_gui.components.file_preview import render_file_preview_dialog
from mvgeos_gui.components.home_screen import render_home_screen
from mvgeos_gui.components.login_screen import render_login_screen
from mvgeos_gui.components.packages_panel import render_packages_panel
from mvgeos_gui.components.review_rail import render_review_rail
from mvgeos_gui.components.sessions_panel import render_sessions_panel
from mvgeos_gui.components.settings_modal import render_app_settings_modal
from mvgeos_gui.components.sidebar import render_sidebar
from mvgeos_gui.components.skills_panel import render_skills_panel
from mvgeos_gui.components.status_bar import render_status_bar
from mvgeos_gui.components.workspace_settings_modal import (
    render_workspace_settings_modal,
)
from mvgeos_gui.state import AppState


def render_shell(state: AppState) -> None:
    """Render the full 3-pane MvgeOS desktop shell."""
    ui.query(".nicegui-content").classes("p-0 m-0")
    ui.add_css("body { overflow: hidden; }")

    with ui.column().classes(
        "w-screen h-screen max-h-screen overflow-hidden m-0 p-0 flex flex-col"
    ):
        with ui.row().classes(
            "flex-1 w-full h-full overflow-hidden m-0 p-0 flex flex-row no-wrap"
        ):

            def _sidebar_snapshot() -> tuple[Any, ...]:
                return (
                    state.sidebar_open,
                    state.current_view,
                    state.active_tome_id,
                    state.tome_title,
                    len(state.loaded_tomes),
                    str(state.project_path),
                    state.current_user,
                    tuple(v.id for v in state.get_contributed_views()),
                )

            last_sidebar_state = [_sidebar_snapshot()]

            @ui.refreshable
            def sidebar_container() -> None:
                render_sidebar(state)
                last_sidebar_state[0] = _sidebar_snapshot()

            sidebar_container()

            def _on_sidebar_check() -> None:
                cur = _sidebar_snapshot()
                if cur != last_sidebar_state[0]:
                    sidebar_container.refresh()

            state.subscribe(_on_sidebar_check)

            with ui.column().classes(
                "flex-1 w-full h-full flex flex-col overflow-hidden"
            ):
                current_rendered_view = [state.current_view]

                @ui.refreshable
                def main_content() -> None:
                    view = state.current_view
                    current_rendered_view[0] = view
                    if view == "home":
                        render_home_screen(state)
                    elif view == "sessions":
                        render_sessions_panel(state)
                    elif view == "skills":
                        render_skills_panel(state)
                    elif view == "diagnostics":
                        render_diagnostics_panel(state)
                    elif view == "packages":
                        render_packages_panel(state)
                    elif state.has_contributed_view(view):
                        _render_contributed_rune_view(state, view)
                    else:
                        render_chat_panel(state)

                main_content()

                def _on_view_change() -> None:
                    if current_rendered_view[0] != state.current_view:
                        main_content.refresh()

                state.subscribe(_on_view_change)

            last_review_state = [
                (
                    state.review_open,
                    state.current_view,
                    state.active_canvas_tab,
                    state._selected_artifact_id,
                    state.inspected_sub_tome_id,
                    state.canvas_docked,
                    len(state.changed_files),
                    state._selected_diff_path,
                )
            ]

            @ui.refreshable
            def review_container() -> None:
                if state.review_open and state.current_view == "chat":
                    render_review_rail(state)
                last_review_state[0] = (
                    state.review_open,
                    state.current_view,
                    state.active_canvas_tab,
                    state._selected_artifact_id,
                    state.inspected_sub_tome_id,
                    state.canvas_docked,
                    len(state.changed_files),
                    state._selected_diff_path,
                )

            review_container()

            def _on_review_check() -> None:
                cur = (
                    state.review_open,
                    state.current_view,
                    state.active_canvas_tab,
                    state._selected_artifact_id,
                    state.inspected_sub_tome_id,
                    state.canvas_docked,
                    len(state.changed_files),
                    state._selected_diff_path,
                )
                if cur != last_review_state[0]:
                    review_container.refresh()

            state.subscribe(_on_review_check)

        with ui.row().classes("w-full h-7 shrink-0 overflow-hidden"):
            last_status_state = [
                (
                    state.mvge_status,
                    state.selected_model,
                    state.total_mana_used,
                    state.context_input_tokens,
                    state.context_output_tokens,
                    state.is_channeling,
                    state.sidebar_open,
                    state.review_open,
                )
            ]

            @ui.refreshable
            def status_bar_container() -> None:
                render_status_bar(state)
                last_status_state[0] = (
                    state.mvge_status,
                    state.selected_model,
                    state.total_mana_used,
                    state.context_input_tokens,
                    state.context_output_tokens,
                    state.is_channeling,
                    state.sidebar_open,
                    state.review_open,
                )

            status_bar_container()

            def _on_status_check() -> None:
                cur = (
                    state.mvge_status,
                    state.selected_model,
                    state.total_mana_used,
                    state.context_input_tokens,
                    state.context_output_tokens,
                    state.is_channeling,
                    state.sidebar_open,
                    state.review_open,
                )
                if cur != last_status_state[0]:
                    status_bar_container.refresh()

            state.subscribe(_on_status_check)

    last_overlay_state = [
        (
            getattr(state, "_command_palette_open", False),
            getattr(state, "_selected_artifact_id", None),
            getattr(state, "_show_app_settings", False),
            getattr(state, "_show_workspace_settings", False),
            getattr(state, "_show_login", False),
            getattr(state, "_show_rename_dialog", False),
            getattr(state, "_preview_file", None),
            len(state.artifacts),
        )
    ]

    @ui.refreshable
    def overlay_dialogs() -> None:
        render_command_palette(state)
        render_rename_dialog(state)
        render_artifact_drawer(state)
        render_app_settings_modal(state)
        render_workspace_settings_modal(state)
        render_file_preview_dialog(state)
        if getattr(state, "_show_login", False):
            render_login_screen(state)
        last_overlay_state[0] = (
            getattr(state, "_command_palette_open", False),
            getattr(state, "_selected_artifact_id", None),
            getattr(state, "_show_app_settings", False),
            getattr(state, "_show_workspace_settings", False),
            getattr(state, "_show_login", False),
            getattr(state, "_show_rename_dialog", False),
            getattr(state, "_preview_file", None),
            len(state.artifacts),
        )

    overlay_dialogs()

    def _on_overlay_check() -> None:
        cur = (
            getattr(state, "_command_palette_open", False),
            getattr(state, "_selected_artifact_id", None),
            getattr(state, "_show_app_settings", False),
            getattr(state, "_show_workspace_settings", False),
            getattr(state, "_show_login", False),
            getattr(state, "_show_rename_dialog", False),
            getattr(state, "_preview_file", None),
            len(state.artifacts),
        )
        if cur != last_overlay_state[0]:
            overlay_dialogs.refresh()

    state.subscribe(_on_overlay_check)

    # Approval Rune overlay: one modal per cast, mounted once per page.
    mount_approval_overlay(state)


def _render_contributed_rune_view(state: AppState, view_id: str) -> None:
    """Render a generic panel for a rune-contributed view."""
    matching = [v for v in state.get_contributed_views() if v.id == view_id]
    title = matching[0].title if matching else view_id.title()
    desc = (
        matching[0].description
        if matching and matching[0].description
        else f"Custom view provided by the {view_id} extension."
    )

    with ui.column().classes("w-full h-full overflow-y-auto p-6 gap-4"):
        ui.label(title).classes("text-2xl font-semibold text-[var(--text-primary)]")
        ui.label(desc).classes("text-sm text-[var(--text-secondary)]")
        with ui.row().classes("mt-4"):
            ui.button(
                "Back to Chat",
                on_click=lambda: state.set_current_view("chat"),
            ).props("flat no-caps text-color=grey-5")
