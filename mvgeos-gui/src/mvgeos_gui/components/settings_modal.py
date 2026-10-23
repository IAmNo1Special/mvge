"""Application and Workspace Settings modal component for mvgeos-gui."""

from __future__ import annotations

from typing import Any

from mvgeos_provider import get_model_options
from nicegui import ui

from mvgeos_gui.services.config_service import (
    AppSettings,
    ConfigService,
    WorkspaceSettings,
)
from mvgeos_gui.state import AppState
from mvgeos_gui.styles import apply_theme
from mvgeos_gui.utils import install_focus_trap

AVAILABLE_THEMES = ["dark", "light"]
CONTEMPLATION_LEVELS = ["none", "low", "medium", "high", "x-high"]


def render_app_settings_modal(
    state: AppState, default_tab: str = "application"
) -> None:
    """Render the unified Application and Workspace Settings modal dialog."""
    show_app = getattr(state, "_show_app_settings", False)
    show_ws = getattr(state, "_show_workspace_settings", False)
    if not show_app and not show_ws:
        return

    active_tab: list[str] = (
        ["workspace"]
        if (default_tab == "workspace" or (show_ws and not show_app))
        else ["application"]
    )

    config_service = getattr(state, "_config_service", None) or ConfigService()
    current = config_service.load_app_settings()

    project_dir = state.project_path
    current_ws = config_service.load_workspace_settings(project_dir)

    edited: dict[str, Any] = {
        "api_key": current.api_key,
        "default_model": current.default_model,
        "mana_limit": current.mana_limit,
        "temperature": current.temperature,
        "theme": current.theme,
    }

    ws_edited: dict[str, Any] = {
        "project_name": current_ws.project_name or project_dir.name,
        "contemplation_level": current_ws.contemplation_level,
    }

    def _on_close() -> None:
        state.close_app_settings()
        state.close_workspace_settings()

    def _save() -> None:
        if active_tab[0] == "workspace" and not project_dir.is_dir():
            ui.notify(
                f"Workspace directory does not exist: {project_dir}",
                type="negative",
            )
            return
        errors: list[str] = []
        mana_limit = 0
        try:
            mana_limit = int(edited["mana_limit"])
            if mana_limit <= 0:
                raise ValueError("Mana limit must be a positive integer")
        except (ValueError, TypeError):
            errors.append("Invalid mana limit: must be a positive integer")
        temperature = 0.0
        try:
            temperature = float(edited["temperature"])
            if temperature < 0.0 or temperature > 2.0:
                raise ValueError("Temperature must be between 0.0 and 2.0")
        except (ValueError, TypeError):
            errors.append("Invalid temperature: must be between 0.0 and 2.0")
        if errors:
            ui.notify("\n".join(errors), type="negative", multi_line=True)
            return

        config_service.save_app_settings(
            AppSettings(
                api_key=str(edited["api_key"]),
                default_model=str(edited["default_model"]),
                mana_limit=mana_limit,
                temperature=temperature,
                theme=str(edited["theme"]),
            )
        )

        if project_dir.is_dir():
            config_service.save_workspace_settings(
                project_dir,
                WorkspaceSettings(
                    project_name=str(ws_edited["project_name"]),
                    spells_enabled=list(current_ws.spells_enabled),
                    contemplation_level=str(ws_edited["contemplation_level"]),
                    temperature=temperature,
                    max_tokens=mana_limit,
                ),
            )
            state.reset_agent()

        new_key = str(edited["api_key"]).strip() or None
        state.api_key = new_key
        server = state._ensure_server()
        seen: set[int] = set()
        for client in (state, *server.client_states):
            if id(client) in seen:
                continue
            seen.add(id(client))
            service = client.agent_service
            if service is not None:
                service.set_api_key(new_key)
        new_theme = str(edited["theme"])
        if new_theme != current.theme and state._dark_mode is not None:
            apply_theme(new_theme, state._dark_mode)
        if str(edited["default_model"]) != state.selected_model:
            state.switch_model(str(edited["default_model"]))

        state.close_app_settings()
        state.close_workspace_settings()
        ui.notify("Settings saved", type="positive")

    with (
        ui.dialog().classes("w-full max-w-2xl").on("close", _on_close) as dialog,
        ui.card().classes(
            "w-full bg-[var(--bg-surface)] border border-[var(--border-subtle)] "
            "rounded-xl p-0 overflow-hidden settings-dialog-card"
        ),
    ):

        @ui.refreshable
        def header_tabs() -> None:
            with ui.row().classes(
                "w-full h-11 px-4 items-center justify-between "
                "border-b border-[var(--border-subtle)] bg-[var(--bg-raised)]"
            ):
                with ui.row().classes("items-center gap-1"):
                    ui.icon("settings", size="16px").classes(
                        "text-[var(--accent-primary)]"
                    )
                    with (
                        ui.button(
                            "Application Settings",
                            on_click=lambda: _switch_tab("application"),
                        )
                        .props("flat dense no-caps size=xs")
                        .classes(
                            "font-semibold text-xs "
                            + (
                                "text-[var(--text-primary)] border-b-2 "
                                "border-[var(--accent-primary)]"
                                if active_tab[0] == "application"
                                else "text-[var(--text-secondary)]"
                            )
                        )
                        .mark("settings_tab_app")
                    ):
                        pass
                    with (
                        ui.button(
                            "Project Workspace Settings",
                            on_click=lambda: _switch_tab("workspace"),
                        )
                        .props("flat dense no-caps size=xs")
                        .classes(
                            "font-semibold text-xs "
                            + (
                                "text-[var(--text-primary)] border-b-2 "
                                "border-[var(--accent-primary)]"
                                if active_tab[0] == "workspace"
                                else "text-[var(--text-secondary)]"
                            )
                        )
                        .mark("settings_tab_workspace")
                    ):
                        pass
                ui.button(
                    icon="close",
                    on_click=lambda: dialog.close(),
                ).props("flat dense round text-color=grey-5 size=sm")

        header_tabs()

        api_key_input_holder: list[ui.input | None] = [None]
        project_name_input_holder: list[ui.input | None] = [None]

        @ui.refreshable
        def content_view() -> None:
            with ui.column().classes("w-full p-4 gap-4 max-h-[70vh] overflow-auto"):
                if active_tab[0] == "application":
                    with ui.row().classes("w-full gap-4 settings-form-row"):
                        with ui.column().classes("flex-1 gap-1 min-w-0"):
                            ui.label("API Key").classes(
                                "text-xs text-[var(--text-secondary)]"
                            )
                            inp = (
                                ui.input(
                                    value=str(edited["api_key"]),
                                    on_change=lambda e: edited.__setitem__(
                                        "api_key", e.value
                                    ),
                                    password=True,
                                    password_toggle_button=True,
                                )
                                .props("dense outlined")
                                .classes("w-full")
                                .mark("api_key_input")
                            )
                            api_key_input_holder[0] = inp

                        with ui.column().classes("flex-1 gap-1 min-w-0"):
                            ui.label("Default Model").classes(
                                "text-xs text-[var(--text-secondary)]"
                            )
                            ui.select(
                                options=get_model_options(),
                                value=str(edited["default_model"]),
                                on_change=lambda e: edited.__setitem__(
                                    "default_model", e.value
                                ),
                                with_input=True,
                            ).props("dense outlined").classes(
                                "w-full model-select"
                            ).mark("model_select")

                    with ui.row().classes("w-full gap-4 settings-form-row"):
                        with ui.column().classes("flex-1 gap-1 min-w-0"):
                            ui.label("Mana Limit (max_tokens)").classes(
                                "text-xs text-[var(--text-secondary)]"
                            )
                            ui.input(
                                value=str(edited["mana_limit"]),
                                on_change=lambda e: edited.__setitem__(
                                    "mana_limit", e.value
                                ),
                            ).props("dense outlined type=number").classes(
                                "w-full"
                            ).mark("mana_limit_input")

                        with ui.column().classes("flex-1 gap-1 min-w-0"):
                            ui.label("Temperature").classes(
                                "text-xs text-[var(--text-secondary)]"
                            )
                            ui.input(
                                value=str(edited["temperature"]),
                                on_change=lambda e: edited.__setitem__(
                                    "temperature", e.value
                                ),
                            ).props("dense outlined type=number step=0.1").classes(
                                "w-full"
                            ).mark("temperature_input")

                    with (
                        ui.row().classes("w-full gap-4 settings-form-row"),
                        ui.column().classes("flex-1 gap-1 min-w-0"),
                    ):
                        ui.label("Theme").classes(
                            "text-xs text-[var(--text-secondary)]"
                        )
                        ui.select(
                            AVAILABLE_THEMES,
                            value=str(edited["theme"]),
                            on_change=lambda e: edited.__setitem__("theme", e.value),
                        ).props("dense outlined").classes("w-full").mark("theme_select")
                else:
                    # Workspace Tab
                    with ui.row().classes("w-full gap-4"):
                        with ui.column().classes("flex-1 gap-1 min-w-0"):
                            ui.label("Project Name").classes(
                                "text-xs text-[var(--text-secondary)]"
                            )
                            p_inp = (
                                ui.input(
                                    value=str(ws_edited["project_name"]),
                                    on_change=lambda e: ws_edited.__setitem__(
                                        "project_name", e.value
                                    ),
                                )
                                .props("dense outlined")
                                .classes("w-full")
                                .mark("project_name_input")
                            )
                            project_name_input_holder[0] = p_inp

                        with ui.column().classes("flex-1 gap-1 min-w-0"):
                            ui.label("Project Directory").classes(
                                "text-xs text-[var(--text-secondary)]"
                            )
                            ui.label(str(project_dir)).classes(
                                "text-xs text-[var(--text-muted)] font-mono p-2 "
                                "bg-[var(--bg-sunken)] rounded border "
                                "border-[var(--border-subtle)] truncate"
                            )

                    with (
                        ui.row().classes("w-full gap-4"),
                        ui.column().classes("flex-1 gap-1 min-w-0"),
                    ):
                        ui.label("Contemplation Level").classes(
                            "text-xs text-[var(--text-secondary)]"
                        )
                        supported = (
                            state.get_contemplation_levels_for_selected_model()
                            if hasattr(
                                state,
                                "get_contemplation_levels_for_selected_model",
                            )
                            else []
                        )
                        options = (
                            list(supported) if supported else list(CONTEMPLATION_LEVELS)
                        )
                        current_val = str(ws_edited["contemplation_level"])
                        if current_val and current_val not in options:
                            options = [current_val, *options]
                        ui.select(
                            options,
                            value=current_val,
                            on_change=lambda e: ws_edited.__setitem__(
                                "contemplation_level", e.value
                            ),
                        ).props("dense outlined").classes("w-full").mark(
                            "contemplation_select"
                        )

        content_view()

        def _switch_tab(tab: str) -> None:
            active_tab[0] = tab
            header_tabs.refresh()
            content_view.refresh()

        with ui.row().classes(
            "w-full px-4 py-3 items-center justify-end gap-2 "
            "border-t border-[var(--border-subtle)] bg-[var(--bg-raised)]"
        ):
            ui.button("Cancel", on_click=lambda: dialog.close()).props(
                "flat dense no-caps text-color=grey-5"
            )
            ui.button("Save", on_click=_save).props(
                "unelevated dense no-caps mvge-glow-btn text-white"
            )

    install_focus_trap(dialog)
    dialog.open()
    if active_tab[0] == "application" and api_key_input_holder[0]:
        api_key_input_holder[0].run_method("focus")
    elif active_tab[0] == "workspace" and project_name_input_holder[0]:
        project_name_input_holder[0].run_method("focus")
