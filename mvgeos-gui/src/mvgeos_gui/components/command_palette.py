"""Command palette quick switcher (Ctrl/Cmd+K or Ctrl/Cmd+Shift+P)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from nicegui import ui
from nicegui.events import ValueChangeEventArguments

from mvgeos_gui.state import AppState


@dataclass
class PaletteCommand:
    """One actionable entry in the command palette."""

    label: str
    icon: str
    run: Callable[[AppState], Any]
    enabled: Callable[[AppState], bool] = field(default=lambda _state: True, repr=False)
    hint: str = ""


def _close(state: AppState) -> None:
    state.set_command_palette_open(False)


def _new_session(state: AppState) -> None:
    state.new_conversation()
    _close(state)


def _fork_session(state: AppState) -> None:
    forked_id = state.fork_tome()
    if forked_id is None:
        ui.notify("No active session to fork", type="warning")
    else:
        ui.notify(f"Forked session {forked_id[:8]}")
    _close(state)


def _export_session(state: AppState) -> None:
    dest = state.export_tome()
    if dest is None:
        ui.notify("No active session to export", type="warning")
    else:
        ui.notify(f"Exported to {dest.name}")
    _close(state)


def _rename_session(state: AppState) -> None:
    _close(state)
    state.open_rename_dialog()


def render_rename_dialog(state: AppState) -> None:
    """Render the Rename session dialog when its state flag is set.

    Rendered from the overlay refreshable (like the settings modals), not
    from inside the palette: the palette's refreshable clears its own slot
    when it closes, which would destroy a dialog created there.
    """
    if not getattr(state, "_show_rename_dialog", False):
        return

    def _on_close() -> None:
        state.close_rename_dialog()

    def _confirm(title: str) -> None:
        if state.rename_tome(title):
            ui.notify("Session renamed")
        else:
            ui.notify("Could not rename session", type="warning")
        state.close_rename_dialog()

    with (
        ui.dialog().on("close", _on_close) as dialog,
        ui.card().classes(
            "p-4 gap-3 min-w-[320px] bg-[var(--bg-surface)] border "
            "border-[var(--border-subtle)] rounded-xl"
        ),
    ):
        ui.label("Rename session").classes(
            "text-sm font-semibold text-[var(--text-primary)]"
        )
        title_input = (
            ui.input(value=state.tome_title, placeholder="Session title")
            .props("dense dark outlined autofocus")
            .classes("w-full")
            .mark("rename_session_input")
        )
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=_on_close).props("flat dense")
            ui.button(
                "Rename", on_click=lambda: _confirm(title_input.value or "")
            ).props("unelevated dense").classes("mvge-glow-btn text-white").mark(
                "rename_confirm_btn"
            )
    dialog.open()


async def _compact_session(state: AppState) -> None:
    service = state.get_agent_service()
    result = await service.compact_active_tome(state)
    # Notify while the palette slot is still alive, then close it: the
    # Quasar notification outlives the palette, but creating it needs a slot.
    ui.notify(result)
    _close(state)


def _toggle_sidebar(state: AppState) -> None:
    state.toggle_sidebar()
    _close(state)


def _toggle_review(state: AppState) -> None:
    state.toggle_review()
    _close(state)


def _toggle_plan_mode(state: AppState) -> None:
    active_spells = state.toggle_plan_mode()
    if active_spells is None:
        ui.notify(
            "Plan mode needs an API key. Set one in Settings first.",
            type="warning",
        )
    elif state.plan_mode and not active_spells:
        ui.notify(
            "Plan mode is on, but no spells are marked read-only.",
            type="warning",
        )
    _close(state)


def _open_settings(state: AppState) -> None:
    # Settings is a modal, not a view: open it directly.
    # (There is no "settings" view branch in shell.py.)
    state.open_app_settings()
    _close(state)


def _make_navigator(view_name: str) -> Callable[[AppState], None]:
    def _go(state: AppState) -> None:
        state.set_current_view(view_name)
        _close(state)

    return _go


def _has_active_tome(state: AppState) -> bool:
    return state.active_tome_id is not None


def _can_compact(state: AppState) -> bool:
    return state.active_tome_id is not None and not state.is_channeling


def get_palette_commands() -> list[PaletteCommand]:
    """Every command the palette offers. Labels double as the filter text."""
    commands = [
        PaletteCommand(
            "New session", "add", _new_session, hint="Start a fresh conversation"
        ),
        PaletteCommand(
            "Fork session",
            "call_split",
            _fork_session,
            enabled=_has_active_tome,
            hint="Branch the active session",
        ),
        PaletteCommand(
            "Export session",
            "download",
            _export_session,
            enabled=_has_active_tome,
            hint="Save transcript as JSONL",
        ),
        PaletteCommand(
            "Rename session",
            "edit",
            _rename_session,
            enabled=_has_active_tome,
            hint="Rename the active session",
        ),
        PaletteCommand(
            "Compact session",
            "compress",
            _compact_session,
            enabled=_can_compact,
            hint="Compact the session context",
        ),
        PaletteCommand("Toggle sidebar", "menu", _toggle_sidebar),
        PaletteCommand("Toggle review rail", "preview", _toggle_review),
        PaletteCommand(
            "Toggle plan mode",
            "edit_off",
            _toggle_plan_mode,
            hint="Read-only spells only",
        ),
    ]
    for label, view, icon in (
        ("Go to Chat", "chat", "chat_bubble_outline"),
        ("Go to Sessions", "sessions", "history"),
        ("Go to Marketplace", "marketplace", "storefront"),
        ("Go to Diagnostics", "diagnostics", "troubleshoot"),
    ):
        commands.append(PaletteCommand(label, icon, _make_navigator(view)))
    commands.append(
        PaletteCommand(
            "Go to Skills",
            "auto_awesome",
            _make_navigator("skills"),
            enabled=lambda s: s.has_contributed_view("skills"),
            hint="Agent skills (requires skills-bridge)",
        )
    )
    commands.append(
        PaletteCommand("Settings", "settings", _open_settings, hint="App settings")
    )
    return commands


def filter_commands(commands: list[PaletteCommand], query: str) -> list[PaletteCommand]:
    """Case-insensitive substring filter over command labels."""
    q = query.strip().lower()
    if not q:
        return list(commands)
    return [cmd for cmd in commands if q in cmd.label.lower()]


def render_command_palette(state: AppState) -> None:
    """Render the command palette dialog."""
    if not state.command_palette_open:
        return

    query: list[str] = [""]

    with (
        ui.dialog().props("maximized") as dialog,
        ui.card().classes(
            "w-full max-w-2xl mx-auto mt-20 bg-[var(--bg-surface)] border "
            "border-[var(--border-subtle)] rounded-xl shadow-2xl"
        ),
    ):
        ui.label("Quick Switcher").classes(
            "text-sm font-semibold text-[var(--text-primary)] mb-3 px-4 pt-4"
        )

        @ui.refreshable
        def results_list() -> None:
            commands = filter_commands(get_palette_commands(), query[0])
            with ui.column().classes("px-4 pb-4 gap-1 max-h-96 overflow-y-auto"):
                if not commands:
                    ui.label(f'No commands matching "{query[0]}"').classes(
                        "text-xs text-[var(--text-dim-deep)] px-3 py-2"
                    )
                    return
                for cmd in commands:
                    _render_command_row(cmd, state)

        def _on_query(e: ValueChangeEventArguments[str | None]) -> None:
            query[0] = e.value or ""
            results_list.refresh()

        # Note: no element-level keydown.escape here. Escape is owned by the
        # global keyboard dispatcher (components/keyboard.py): autocomplete
        # popup first, then this palette, then channeling interrupt.
        (
            ui.input(
                placeholder="Type a command, session, or file...", on_change=_on_query
            )
            .props(
                "dense dark outlined rounded borderless bg-[var(--bg-card)] "
                "text-white autofocus"
            )
            .classes("w-full mb-3 mx-4")
            .mark("palette_search_input")
        )

        results_list()

    dialog.open()


def _render_command_row(cmd: PaletteCommand, state: AppState) -> None:
    available = cmd.enabled(state)
    row = ui.row().classes(
        "w-full items-center gap-3 px-3 py-2 rounded-lg text-xs "
        "text-[var(--text-primary)] "
        + (
            "cursor-pointer hover:bg-[var(--bg-card)]"
            if available
            else "opacity-40 pointer-events-none"
        )
    )
    if available:
        row.on("click", lambda: cmd.run(state))
    with row:
        ui.icon(cmd.icon, size="16px").classes("text-[var(--text-secondary)]")
        ui.label(cmd.label).classes("flex-1")
        if cmd.hint:
            ui.label(cmd.hint).classes("text-[var(--text-dim-deep)]")
