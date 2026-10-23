"""Installed rune settings dialog (the settings cog on the rune card)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nicegui import ui

from mvgeos_gui.state import AppState


def render_rune_settings_dialog(
    state: AppState,
    r: dict[str, Any],
    on_saved: Callable[[], Any],
    extra_section: Callable[[], None] | None = None,
    wide: bool = False,
) -> None:
    """Render a settings dialog for an installed rune.

    The base dialog exposes the rune's enabled flag, persisted to the
    rune's manifest.json. ``extra_section`` lets a rune (the Approval
    Rune) append its own settings content below the enabled switch, and
    ``wide`` gives that content a broader card.
    """
    name = r["name"]
    version = r["version"]
    desc = r.get("description", "")
    path = r.get("path", "")
    # Default to enabled when the manifest omits the flag.
    enabled = r.get("enabled", True)
    width = "w-[640px]" if wide else "w-[420px]"

    with (
        ui.dialog() as dialog,
        ui.card().classes(
            f"{width} max-w-[90vw] bg-[var(--bg-card)] "
            "border border-[var(--border-subtle)] "
            "rounded-xl p-5 gap-4"
        ),
    ):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label(f"{name} Settings").classes(
                "text-base font-semibold text-[var(--text-primary)]"
            )
            ui.button(icon="close", on_click=dialog.close).props(
                "flat dense round text-color=grey-5 size=sm"
            )

        ui.label(f"v{version}").classes(
            "text-xs text-[var(--text-secondary)] font-mono -mt-3"
        )
        if desc:
            ui.label(desc).classes("text-sm text-[var(--text-violet-soft)]")

        ui.separator().classes("bg-[var(--border-subtle)]")

        with ui.row().classes("w-full items-center justify-between"):
            with ui.column().classes("gap-0"):
                ui.label("Enabled").classes(
                    "text-sm font-medium text-[var(--text-primary)]"
                )
                ui.label("Disabled runes are not loaded by the Mvge.").classes(
                    "text-xs text-[var(--text-muted)]"
                )
            enabled_switch = ui.switch(value=bool(enabled)).props("color=purple-6")

        if extra_section is not None:
            ui.separator().classes("bg-[var(--border-subtle)]")
            extra_section()

        if path:
            ui.label(path).classes(
                "text-[11px] text-[var(--text-muted)] font-mono break-all"
            )

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("Cancel", on_click=dialog.close).props(
                "flat dense text-color=grey-5"
            )

            async def _save_settings() -> None:
                new_enabled = bool(enabled_switch.value)
                ui.notify(
                    f"Saving {name} settings...",
                    type="info",
                )
                success = await state.set_rune_enabled_async(name, new_enabled)
                if success:
                    ui.notify(
                        f"{name} {'enabled' if new_enabled else 'disabled'}.",
                        type="positive",
                    )
                    dialog.close()
                    await on_saved()
                else:
                    ui.notify(
                        f"Failed to save {name} settings.",
                        type="negative",
                    )

            ui.button("Save", on_click=_save_settings).props(
                "unelevated dense color=purple-7"
            ).classes("text-white text-sm font-medium").mark("rune_settings_save_btn")

    dialog.open()
