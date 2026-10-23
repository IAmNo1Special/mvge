"""Sessions panel: search, filter, tags, archive/delete."""

from nicegui import ui

from mvgeos_gui.state import AppState


def render_sessions_panel(state: AppState) -> None:
    """Render the sessions management view."""
    with ui.column().classes("w-full h-full overflow-y-auto p-6 gap-4"):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label("Sessions").classes(
                "text-2xl font-semibold text-[var(--text-primary)]"
            )

            def _on_new_session() -> None:
                state.new_conversation()
                state.set_current_view("chat")
                ui.notify("New session started", type="positive")

            ui.button(
                "New Session",
                on_click=_on_new_session,
            ).props("unelevated").classes("mvge-glow-btn text-white")

        @ui.refreshable
        def sessions_list() -> None:
            query = (search_input.value or "").strip().lower()
            tomes = state.loaded_tomes
            if query:
                tomes = [t for t in tomes if query in t.title.lower()]

            if not tomes:
                if query:
                    ui.label(
                        f"No sessions match '{search_input.value.strip()}'"
                    ).classes("text-xs text-[var(--text-muted)] mt-4")
                else:
                    ui.label("No sessions yet").classes(
                        "text-xs text-[var(--text-muted)] mt-4"
                    )
            else:
                for entry in tomes:
                    with (
                        ui.card()
                        .classes(
                            "w-full p-3 bg-[var(--bg-card)] border "
                            "border-[var(--border-subtle)] "
                            "rounded-lg cursor-pointer "
                            "hover:border-[var(--accent-primary)] "
                            "transition-colors"
                        )
                        .on("click", lambda e=entry: state.switch_to_tome(e.tome_id))
                    ):
                        with ui.row().classes("w-full items-center justify-between"):
                            with ui.row().classes("items-center gap-2 flex-1 min-w-0"):
                                ui.icon("chat_bubble_outline", size="16px").classes(
                                    "text-[var(--text-secondary)] shrink-0"
                                )
                                ui.label(entry.title).classes(
                                    "text-sm text-[var(--text-primary)] truncate"
                                )
                            with ui.row().classes("items-center gap-2 shrink-0"):
                                ui.label(entry.relative_time).classes(
                                    "text-[10px] text-[var(--text-muted)]"
                                )
                                if entry.git_branch:
                                    ui.label(entry.git_branch).classes(
                                        "text-[10px] px-1 py-0.5 rounded "
                                        "mvge-glow-btn/10 text-[var(--accent-primary)] "
                                        "border border-[var(--accent-primary-a30)] "
                                        "font-mono"
                                    )
                        if entry.is_active:
                            ui.label("Active").classes(
                                "text-[10px] text-[var(--accent-primary)] mt-1"
                            )

        search_input = (
            ui.input(
                placeholder="Search sessions...",
                on_change=lambda: sessions_list.refresh(),
            )
            .props("dense dark outlined rounded")
            .classes("w-full text-xs")
            .mark("sessions_search_input")
        )

        sessions_list()

        # Back button
        with ui.row().classes("mt-4"):
            ui.button(
                "Back to Chat",
                on_click=lambda: state.set_current_view("chat"),
            ).props("flat no-caps text-color=grey-5")
