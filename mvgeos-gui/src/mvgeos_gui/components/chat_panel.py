"""Main chat panel: toolbar, message list, and side panel."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nicegui import ui

from mvgeos_gui.components.approval_dialog import render_approval_badge
from mvgeos_gui.components.composer import (
    _scroll_to_bottom,
    fill_composer,
    render_composer,
)
from mvgeos_gui.components.diff_viewer import render_diff_viewer
from mvgeos_gui.components.file_tree import render_file_tree
from mvgeos_gui.components.message_parts import render_assistant_message
from mvgeos_gui.state import AppState

EXAMPLE_PROMPTS = [
    "Explain this project structure",
    "Find all TODO comments",
    "Run the test suite",
    "Help me debug an error",
]


def render_chat_panel(state: AppState) -> ui.column:
    """Render the main chat panel with toolbar, messages, side panel, and composer."""
    # Drop the previous render pass's view listeners first: this render
    # replaces those elements, and stale refreshables would otherwise keep
    # firing against deleted elements on every state notification.
    state.clear_all_view_listeners()
    container = ui.column().classes(
        "flex-1 w-full h-full flex flex-col overflow-hidden"
    )

    with container:
        # Toolbar
        @ui.refreshable
        def toolbar_view() -> None:
            with (
                ui.row().classes(
                    "w-full h-11 items-center justify-between px-3 "
                    "border-b border-[var(--border-subtle)] shrink-0"
                ),
                ui.row().classes("items-center gap-2"),
            ):
                ui.icon("chat_bubble_outline", size="14px").classes(
                    "text-[var(--accent-primary)]"
                )
                ui.label(state.tome_title).classes(
                    "text-xs font-medium text-[var(--text-primary)] "
                    "max-w-[320px] truncate"
                )

                # Right toolbar actions
                with ui.row().classes("items-center gap-1 ml-auto"):

                    def _on_plan_mode_click() -> None:
                        active_spells = state.toggle_plan_mode()
                        if active_spells is None:
                            ui.notify(
                                "Plan mode needs an API key. "
                                "Set one in Settings first.",
                                type="warning",
                            )
                        elif state.plan_mode and not active_spells:
                            ui.notify(
                                "Plan mode is on, but no spells are marked read-only.",
                                type="warning",
                            )

                    _toolbar_button(
                        icon="search",
                        title="Command palette (Ctrl+Shift+P)",
                        on_click=lambda: state.set_command_palette_open(True),
                    )
                    _toolbar_button(
                        icon="edit_off",
                        title="Plan mode: read-only spells only",
                        active=state.plan_mode,
                        on_click=_on_plan_mode_click,
                        marker="chat_toolbar_plan_mode_btn",
                    )
                    _toolbar_button(
                        icon="folder_open",
                        title="Browse files",
                        active=state.review_open and state.active_canvas_tab == "files",
                        on_click=state.toggle_canvas_files,
                    )
                    is_review = (
                        state.review_open and state.active_canvas_tab == "review"
                    )
                    _toolbar_button(
                        icon="difference",
                        title="View changes",
                        active=is_review,
                        on_click=state.toggle_review,
                    )
                    _toolbar_button(
                        icon="tune",
                        title="Settings",
                        on_click=state.open_app_settings,
                        marker="chat_toolbar_settings_btn",
                    )

        toolbar_view()

        last_toolbar_state = [
            (
                state.review_open,
                state.chat_side_panel,
                state.plan_mode,
                str(state.project_path),
            )
        ]

        def _on_toolbar_check() -> None:
            cur = (
                state.review_open,
                state.chat_side_panel,
                state.plan_mode,
                str(state.project_path),
            )
            if cur != last_toolbar_state[0]:
                last_toolbar_state[0] = cur
                toolbar_view.refresh()

        state.subscribe_view("chat_panel_toolbar", _on_toolbar_check)

        # Persistent session approve-all indicator (Approval Rune).
        render_approval_badge(state)

        # Body: messages + side panel
        with ui.row().classes("flex-1 w-full overflow-hidden"):
            # Main chat column
            with ui.column().classes(
                "flex-1 h-full flex flex-col overflow-hidden relative"
            ):
                # Scrollable messages area
                with (
                    ui.column()
                    .classes("flex-1 w-full overflow-y-auto")
                    .props('id="chat-messages-area"')
                ) as messages_area:

                    @ui.refreshable
                    def static_messages_view() -> None:
                        if not state.messages:
                            return
                        with ui.column().classes("w-full space-y-4"):
                            msgs_to_render = (
                                state.messages[:-1]
                                if state.is_channeling
                                else state.messages
                            )
                            for idx, msg in enumerate(msgs_to_render):
                                if msg.role == "user":
                                    _render_user_message(msg)
                                else:
                                    render_assistant_message(msg, idx, state)
                            _scroll_to_bottom(messages_area.id, force=False)

                    @ui.refreshable
                    def streaming_bubble_view() -> None:
                        if state.messages and state.is_channeling:
                            last_idx = len(state.messages) - 1
                            render_assistant_message(
                                state.messages[last_idx], last_idx, state
                            )
                            _scroll_to_bottom(messages_area.id, force=False)

                    @ui.refreshable
                    def empty_state_view() -> None:
                        if state.messages:
                            return
                        with (
                            ui.column()
                            .classes(
                                "w-full min-h-full items-center justify-center "
                                "px-4 py-8"
                            )
                            .mark("empty-state")
                        ):
                            _render_empty_hero(state)
                            with ui.column().classes("w-full max-w-3xl mt-2"):
                                render_composer(state)

                    static_messages_view()
                    empty_state_view()
                    streaming_bubble_view()

                # Floating scroll-to-bottom button
                with (
                    ui.button(
                        icon="keyboard_arrow_down",
                        on_click=lambda: _scroll_to_bottom(
                            messages_area.id, force=True
                        ),
                    )
                    .props('round unelevated size=sm id="chat-scroll-bottom-btn"')
                    .classes(
                        "absolute right-4 bottom-4 z-30 bg-[var(--bg-card)] "
                        "hover:bg-[var(--border-subtle)] text-[var(--text-primary)] "
                        "border border-[var(--border-subtle)] "
                        "hover:border-[var(--accent-primary)] shadow-xl transition-all "
                        "duration-200 opacity-0 pointer-events-none"
                    )
                    .mark("chat-scroll-bottom-btn")
                ):
                    ui.tooltip("Scroll to bottom")

                # Subscribe to state changes to refresh views
                state.subscribe_view(
                    "chat_panel_messages", static_messages_view.refresh
                )
                state.subscribe_view(
                    "chat_panel_messages", streaming_bubble_view.refresh
                )
                state.subscribe_streaming_view(
                    "chat_panel_messages", streaming_bubble_view.refresh
                )

                # Composer dock: exactly one composer instance lives here while
                # the chat has messages; the empty state hosts it otherwise.
                @ui.refreshable
                def bottom_composer_view() -> None:
                    if state.messages:
                        render_composer(state)

                bottom_composer_view()

                # Move the single composer between the centered empty-state
                # slot and the bottom dock when the chat gains or loses
                # messages. The guard keeps every other notification from
                # re-rendering (and wiping) the textarea.
                last_had_messages = [bool(state.messages)]

                def _on_composer_placement_check() -> None:
                    cur = bool(state.messages)
                    if cur == last_had_messages[0]:
                        return
                    last_had_messages[0] = cur
                    for key in (
                        "composer_chips",
                        "composer_prompt",
                        "composer_actions",
                        "cascading_selector",
                        "empty_state_status",
                    ):
                        state.clear_view_listeners(key)
                    state.get_autocomplete_service().clear_items_changed_listeners()
                    empty_state_view.refresh()
                    bottom_composer_view.refresh()

                state.subscribe_view("composer_placement", _on_composer_placement_check)

            # Side panel
            @ui.refreshable
            def side_panel_view() -> None:
                if state.chat_side_panel in ("files", "diff"):
                    with ui.column().classes(
                        "w-80 border-l border-[var(--border-subtle)] "
                        "bg-[var(--bg-surface)] "
                        "shrink-0 overflow-hidden"
                    ):
                        with ui.row().classes(
                            "w-full h-11 items-center justify-between px-3 "
                            "border-b border-[var(--border-subtle)]"
                        ):
                            ui.label(
                                "Files" if state.chat_side_panel == "files" else "Diff"
                            ).classes(
                                "text-xs font-semibold text-[var(--text-primary)]"
                            )
                            ui.button(
                                icon="close",
                                on_click=lambda: state.set_chat_side_panel(None),
                            ).props("flat dense round text-color=grey-5 size=xs")
                        if state.chat_side_panel == "files":
                            render_file_tree(state)
                        else:
                            render_diff_viewer(state)

            side_panel_view()

            last_side_panel_state = [state.chat_side_panel]

            def _on_side_panel_check() -> None:
                if state.chat_side_panel != last_side_panel_state[0]:
                    last_side_panel_state[0] = state.chat_side_panel
                    side_panel_view.refresh()

            state.subscribe_view("chat_side_panel", _on_side_panel_check)

    return container


def _render_empty_hero(state: AppState) -> None:
    """Render the empty-chat hero: icon, title, live status, prompt chips.

    Layout is owned by the caller (empty_state_view centers this together
    with the composer); this renders the hero content only.
    """
    with ui.row().classes(
        "w-14 h-14 rounded-2xl bg-[var(--bg-card)] border "
        "border-[var(--border-subtle)] "
        "items-center justify-center shadow-lg mb-6"
    ):
        ui.icon("auto_awesome", size="28px").classes("text-[var(--accent-primary)]")

    ui.label("What should Mvge work on?").classes(
        "text-2xl font-semibold text-[var(--text-primary)] tracking-tight"
    )

    @ui.refreshable
    def empty_status_view() -> None:
        status_text = {
            "channeling": "Mvge is channeling a response…",
            "working": "Mvge is working…",
            "idle": "Choose a project — Mvge starts when you send.",
        }.get(state.mvge_status, "Choose a project — Mvge starts when you send.")
        ui.label(status_text).classes("text-sm text-[var(--text-secondary)] mt-1 mb-8")

    empty_status_view()

    last_empty_status = [state.mvge_status]

    def _on_empty_status_check() -> None:
        if state.mvge_status != last_empty_status[0]:
            last_empty_status[0] = state.mvge_status
            empty_status_view.refresh()

    state.subscribe_view("empty_state_status", _on_empty_status_check)

    with ui.row().classes("gap-2 flex-wrap justify-center mb-6"):
        for prompt in EXAMPLE_PROMPTS:
            ui.button(
                prompt,
                on_click=lambda p=prompt: fill_composer(state, p),
            ).props("unelevated dense no-caps").classes(
                "rounded-lg border border-[var(--border-subtle)] px-3 py-1.5 text-xs "
                "text-[var(--text-secondary)] hover:border-[var(--accent-primary)] "
                "hover:text-[var(--text-primary)] "
                "transition-colors"
            )


def _render_messages(state: AppState) -> None:
    """Render the message thread."""
    for idx, msg in enumerate(state.messages):
        if msg.role == "user":
            _render_user_message(msg)
        else:
            render_assistant_message(msg, idx, state)


def _render_user_message(msg: object) -> None:
    """Render a user message bubble."""
    content = getattr(msg, "content", "")
    timestamp = getattr(msg, "timestamp", "")
    attachments = getattr(msg, "attachments", None) or []
    with (
        ui.column().classes("w-full max-w-3xl mx-auto px-6 py-3 items-end"),
        ui.card().classes(
            "w-auto max-w-[85%] bg-[var(--bg-card)] border "
            "border-[var(--border-subtle)] "
            "rounded-2xl p-4 shadow-md"
        ),
    ):
        with ui.row().classes(
            "w-full items-center justify-between pb-1 border-b "
            "border-[var(--bg-tint)] mb-2"
        ):
            with ui.row().classes("items-center gap-1.5"):
                ui.icon("account_circle", size="16px").classes(
                    "text-[var(--accent-primary)]"
                )
                ui.label("Summoner").classes(
                    "text-xs font-semibold text-[var(--text-primary)]"
                )
                if getattr(msg, "is_steering", False):
                    ui.badge("Steering").props("rounded dense size=xs").classes(
                        "bg-[var(--accent-primary)] text-white text-[9px] "
                        "uppercase tracking-wider font-mono"
                    )
            if timestamp:
                ui.label(timestamp).classes(
                    "text-[10px] text-[var(--text-muted)] font-mono"
                )
        if content:
            ui.label(content).classes(
                "text-xs text-[var(--text-primary)] whitespace-pre-wrap leading-relaxed"
            )
        for name in attachments:
            with ui.row().classes("items-center gap-1.5 mt-1"):
                ui.icon("attach_file", size="14px").classes(
                    "text-[var(--text-secondary)]"
                )
                ui.label(str(name)).classes(
                    "text-xs text-[var(--text-secondary)] font-mono"
                )


def _toolbar_button(
    icon: str,
    title: str,
    active: bool = False,
    on_click: Callable[[], Any] | None = None,
    marker: str | None = None,
) -> None:
    """Render a toolbar icon button."""
    btn_cls = (
        "bg-card text-primary"
        if active
        else "text-dim hover:text-secondary hover:bg-highlight"
    )
    btn = (
        ui.button(icon=icon, on_click=on_click)
        .props("flat dense round size=xs")
        .classes(f"{btn_cls} p-0.5")
    )
    if marker:
        btn.mark(marker)
    with btn:
        ui.tooltip(title)
