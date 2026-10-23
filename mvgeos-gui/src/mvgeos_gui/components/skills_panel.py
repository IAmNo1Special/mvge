"""Skills panel: manage active skills, add available ones, install new ones."""

from __future__ import annotations

from nicegui import ui

from mvgeos_gui.state import AppState


def render_skills_panel(state: AppState) -> None:
    """Render the skills management view."""
    with ui.column().classes("w-full h-full overflow-y-auto p-6 gap-4"):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label("Skills").classes(
                "text-2xl font-semibold text-[var(--text-primary)]"
            )
            ui.button(
                "Install Skill",
                icon="add",
                on_click=lambda: _render_install_dialog(state, skills_view.refresh),
            ).props("unelevated dense no-caps").classes(
                "mvge-glow-btn text-white text-sm font-medium"
            ).mark("skill_install_btn")

        @ui.refreshable
        def skills_view() -> None:
            _render_skills_lists(state, skills_view.refresh)

        skills_view()

        with ui.row().classes("mt-4"):
            ui.button(
                "Back to Chat",
                on_click=lambda: state.set_current_view("chat"),
            ).props("flat no-caps text-color=grey-5")


def _render_skills_lists(state: AppState, refresh: object) -> None:
    """Render the active and available skill sections."""
    # Active skills
    ui.label("Active Skills").classes(
        "text-sm font-semibold text-[var(--text-violet-soft)] uppercase "
        "tracking-wide mt-2"
    )
    skills = state.active_skills
    if not skills:
        with ui.column().classes("gap-1 mt-1"):
            ui.label("No skills loaded").classes("text-xs text-[var(--text-muted)]")
            ui.label(
                "Skills are reusable capabilities the Mvge can invoke. "
                "Use Install Skill above to install one from a git URL or "
                "local folder."
            ).classes("text-[11px] text-[var(--text-muted-a70)]")
    else:
        for skill in skills:
            with ui.card().classes(
                "w-full p-4 bg-[var(--bg-card)] border border-[var(--border-subtle)] "
                "rounded-lg"
            ):
                with ui.row().classes("w-full items-center justify-between"):
                    with ui.row().classes("items-center gap-2"):
                        ui.label(skill.name).classes(
                            "text-sm font-semibold text-[var(--text-primary)]"
                        )
                        if skill.invoked:
                            ui.badge("Invoked", color="green").props(
                                "rounded dense"
                            ).classes("text-[10px]")
                    ui.button(
                        icon="delete",
                        on_click=lambda s=skill: _remove_skill(state, s.name, refresh),
                    ).props("flat dense round text-color=grey-5 size=sm").mark(
                        f"skill_remove_{skill.name}"
                    )
                ui.label(skill.description or "No description").classes(
                    "text-xs text-[var(--text-secondary)] mt-1"
                )
                ui.label(skill.scope).classes(
                    "text-[10px] text-[var(--text-muted)] mt-1 font-mono"
                )

    # Available (discovered but not active) skills
    ui.label("Available Skills").classes(
        "text-sm font-semibold text-[var(--text-violet-soft)] uppercase "
        "tracking-wide mt-4"
    )
    try:
        discovered = state.load_skills()
    except Exception:
        discovered = []
    active_names = {s.name for s in state.active_skills}
    available = [m for m in discovered if m.name not in active_names]
    if not available:
        with ui.column().classes("gap-1 mt-1"):
            ui.label("No additional skills found").classes(
                "text-xs text-[var(--text-muted)]"
            )
            ui.label(
                "Discovered skills appear here. Use Install Skill above to "
                "install one from a git URL or local folder."
            ).classes("text-[11px] text-[var(--text-muted-a70)]")
    else:
        for manifest in available:
            with ui.card().classes(
                "w-full p-4 bg-[var(--bg-card)] border border-[var(--border-subtle)] "
                "rounded-lg"
            ):
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label(manifest.name).classes(
                        "text-sm font-semibold text-[var(--text-primary)]"
                    )
                    ui.button(
                        "Add",
                        on_click=lambda m=manifest: _add_skill(state, m, refresh),
                    ).props("unelevated dense no-caps size=sm").classes(
                        "mvge-glow-btn text-white text-xs font-medium"
                    ).mark(f"skill_add_{manifest.name}")
                ui.label(manifest.description or "No description").classes(
                    "text-xs text-[var(--text-secondary)] mt-1"
                )
                if manifest.path:
                    ui.label(manifest.path).classes(
                        "text-[10px] text-[var(--text-muted)] mt-1 font-mono break-all"
                    )


def _remove_skill(state: AppState, name: str, refresh: object) -> None:
    """Remove a skill from the active list."""
    if state.remove_skill(name):
        ui.notify(f"Removed skill '{name}'.", type="positive")
    else:
        ui.notify(f"Skill '{name}' was not active.", type="warning")
    if callable(refresh):
        refresh()


def _add_skill(state: AppState, manifest: object, refresh: object) -> None:
    """Add a discovered skill to the active list."""
    # manifest is a SkillManifest; typed as object to avoid a hard import cycle.
    from mvgeos_runes.types import SkillManifest

    assert isinstance(manifest, SkillManifest)
    if state.add_skill(manifest):
        ui.notify(f"Added skill '{manifest.name}'.", type="positive")
    else:
        ui.notify(f"Skill '{manifest.name}' is already active.", type="warning")
    if callable(refresh):
        refresh()


def _render_install_dialog(state: AppState, refresh: object) -> None:
    """Render a dialog to install a skill from a git URL or local path."""
    with (
        ui.dialog() as dialog,
        ui.card().classes(
            "w-[440px] max-w-[90vw] bg-[var(--bg-card)] border "
            "border-[var(--border-subtle)] "
            "rounded-xl p-5 gap-4"
        ),
    ):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label("Install Skill").classes(
                "text-base font-semibold text-[var(--text-primary)]"
            )
            ui.button(icon="close", on_click=dialog.close).props(
                "flat dense round text-color=grey-5 size=sm"
            )

        ui.label(
            "Install from a git repository or a local folder containing SKILL.md."
        ).classes("text-xs text-[var(--text-secondary)]")

        ui.label("Git URL or local path").classes(
            "text-xs text-[var(--text-secondary)]"
        )
        source_input = (
            ui.input(
                placeholder="https://github.com/... or /path/to/skill",
            )
            .classes("w-full")
            .props("dark dense outlined")
        )
        ui.label("Name (optional)").classes("text-xs text-[var(--text-secondary)]")
        name_input = (
            ui.input(
                placeholder="Defaults to folder or repo name",
            )
            .classes("w-full")
            .props("dark dense outlined")
        )

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("Cancel", on_click=dialog.close).props(
                "flat dense text-color=grey-5"
            )

            async def _install() -> None:
                source = (source_input.value or "").strip()
                if not source:
                    ui.notify("Enter a git URL or local path.", type="warning")
                    return
                name = (name_input.value or "").strip() or None
                ui.notify(f"Installing skill from {source}...", type="info")
                installed = await state.install_skill_async(source, name)
                if installed:
                    ui.notify(f"Installed skill '{installed}'.", type="positive")
                    dialog.close()
                    if callable(refresh):
                        refresh()
                else:
                    ui.notify(
                        "Failed to install skill. Check the source and try again.",
                        type="negative",
                    )

            ui.button("Install", on_click=_install).props(
                "unelevated dense no-caps"
            ).classes("mvge-glow-btn text-white text-sm font-medium")

    dialog.open()
