"""Collapsible execution step cards for Mvge execution operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

from mvgeos_gui.models import ExecutionStep, StepType

if TYPE_CHECKING:
    from mvgeos_gui.state import AppState

_PREVIEW_LINES = 20


def _truncate_text(text: str, max_lines: int = _PREVIEW_LINES) -> tuple[str, int]:
    """Truncate text to max_lines and return (displayed_text, hidden_lines)."""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text, 0
    return "\n".join(lines[:max_lines]), len(lines) - max_lines


def _bind_expansion(
    expansion: ui.expansion,
    card_id: str | None,
    state: AppState | None,
) -> None:
    """Bind expansion value changes to AppState without triggering notification
    loops.
    """
    if state is not None and card_id is not None:
        expansion.on_value_change(
            lambda e, cid=card_id: state.set_card_expansion(
                cid, bool(getattr(e, "value", e))
            )
        )


def render_contemplation_card(
    contemplation: str,
    is_streaming: bool = False,
    card_id: str | None = None,
    state: AppState | None = None,
) -> ui.expansion:
    """Render a collapsible 'Contemplation' reasoning card."""
    initial_val = (
        state.is_card_expanded(card_id, default=is_streaming)
        if (state is not None and card_id is not None)
        else is_streaming
    )
    with (
        ui.expansion(
            text="Contemplation",
            icon="psychology",
            value=initial_val,
        )
        .props("dense dense-toggle header-class=bg-[var(--bg-raised)]")
        .classes(
            "w-full rounded-xl bg-[var(--bg-sunken-alt)] border "
            "border-[var(--bg-card-hover)] "
            "text-xs text-[var(--text-secondary)] my-1"
        ) as expansion,
        ui.column().classes(
            "w-full p-3 bg-[var(--bg-well-deep)] rounded-b-xl border-t "
            "border-[var(--bg-tint)]"
        ),
    ):
        ui.markdown(contemplation).classes(
            "markdown-content markdown-contemplation italic max-w-none w-full"
        )
    _bind_expansion(expansion, card_id, state)
    return expansion


def render_worked_card(
    step: ExecutionStep,
    card_id: str | None = None,
    state: AppState | None = None,
) -> ui.expansion:
    """Render a collapsible 'Worked for Xs' execution card."""
    title = step.spell_name or step.title or "Worked for 0.0s"
    initial_val = (
        state.is_card_expanded(card_id, default=False)
        if (state is not None and card_id is not None)
        else False
    )
    with (
        ui.expansion(
            text=title,
            icon="schedule" if step.is_complete else "hourglass_top",
            value=initial_val,
        )
        .props("dense dense-toggle header-class=bg-[var(--bg-card)]")
        .classes(
            "w-full rounded-xl bg-[var(--bg-card)] border "
            "border-[var(--border-subtle)] "
            "text-xs text-[var(--text-secondary)] my-1"
        ) as expansion,
        ui.column().classes("w-full p-3 gap-2 bg-[var(--bg-well)] rounded-b-xl"),
    ):
        if step.details:
            for detail in step.details:
                with ui.row().classes(
                    "items-center gap-2 text-[11px] text-[var(--text-secondary)]"
                ):
                    ui.icon("check_circle", size="12px").classes(
                        "text-[var(--addition-green)]"
                    )
                    ui.label(detail).classes("font-mono")
        else:
            ui.label("Execution completed.").classes(
                "text-[11px] text-[var(--text-muted)] italic"
            )
        if step.params:
            params_id = f"{card_id}_params" if card_id else None
            params_val = (
                state.is_card_expanded(params_id, default=False)
                if (state is not None and params_id is not None)
                else False
            )
            with (
                ui.expansion("Parameters", icon="unfold_more", value=params_val)
                .props("dense dense-toggle")
                .classes("text-[10px] text-[var(--text-muted)]")
            ) as p_exp:
                ui.code(str(step.params)).classes(
                    "w-full text-[10px] bg-[var(--bg-sunken)] p-2 rounded max-h-32 "
                    "overflow-auto"
                )
            _bind_expansion(p_exp, params_id, state)
        if step.result:
            preview, hidden = _truncate_text(step.result)
            with ui.column().classes("w-full gap-1"):
                result_id = f"{card_id}_result" if card_id else None
                result_val = (
                    state.is_card_expanded(result_id, default=False)
                    if (state is not None and result_id is not None)
                    else False
                )
                with (
                    ui.expansion("Result", icon="unfold_more", value=result_val)
                    .props("dense dense-toggle")
                    .classes("text-[10px] text-[var(--text-muted)]")
                ) as r_exp:
                    ui.code(preview).classes(
                        "w-full text-[10px] bg-[var(--bg-sunken)] p-2 rounded "
                        "max-h-32 overflow-auto"
                    )
                _bind_expansion(r_exp, result_id, state)
                if hidden > 0:
                    ui.label(
                        f"... ({hidden} more lines, expand Result to view)"
                    ).classes("text-[10px] text-[var(--text-muted)] italic")
    _bind_expansion(expansion, card_id, state)
    return expansion


def render_files_card(
    step: ExecutionStep,
    card_id: str | None = None,
    state: AppState | None = None,
) -> ui.expansion:
    """Render a collapsible 'Explored N files' card."""
    count = len(step.files)
    title = step.title or f"Explored {count} file{'s' if count != 1 else ''}"
    initial_val = (
        state.is_card_expanded(card_id, default=False)
        if (state is not None and card_id is not None)
        else False
    )

    with (
        ui.expansion(
            text=title,
            icon="find_in_page",
            value=initial_val,
        )
        .props("dense dense-toggle header-class=bg-[var(--bg-card)]")
        .classes(
            "w-full rounded-xl bg-[var(--bg-card)] border "
            "border-[var(--border-subtle)] "
            "text-xs text-[var(--text-secondary)] my-1"
        ) as expansion,
        ui.column().classes("w-full p-3 gap-2 bg-[var(--bg-well)] rounded-b-xl"),
    ):
        for idx, f in enumerate(step.files):
            with ui.row().classes(
                "w-full items-center justify-between p-1.5 rounded "
                "bg-[var(--bg-surface)] border border-[var(--bg-tint)]"
            ):
                with ui.row().classes("items-center gap-2 overflow-hidden"):
                    ui.icon("description", size="14px").classes(
                        "text-[var(--accent-primary)] shrink-0"
                    )
                    ui.label(f.path).classes(
                        "text-xs text-[var(--text-primary)] font-mono truncate "
                        "max-w-[320px]"
                    )
                    if f.lines:
                        ui.badge(f.lines).props("rounded dense").classes(
                            "text-[10px] text-[var(--text-secondary)] font-mono px-1.5 "
                            "bg-[var(--bg-card)] border border-[var(--border-subtle)]"
                        )
                ui.badge(f.operation.upper(), color="deep-purple-9").props(
                    "rounded dense"
                ).classes("text-[9px] text-white font-mono px-1.5")

            if f.details:
                preview, hidden = _truncate_text(f.details)
                with ui.column().classes("w-full gap-1 ml-4"):
                    details_id = f"{card_id}_details_{idx}" if card_id else None
                    details_val = (
                        state.is_card_expanded(details_id, default=False)
                        if (state is not None and details_id is not None)
                        else False
                    )
                    with (
                        ui.expansion(
                            "File details", icon="unfold_more", value=details_val
                        )
                        .props("dense dense-toggle")
                        .classes("text-[10px] text-[var(--text-muted)]")
                    ) as d_exp:
                        ui.code(preview).classes(
                            "w-full text-[10px] bg-[var(--bg-sunken)] p-2 rounded "
                            "max-h-32 overflow-auto"
                        )
                    _bind_expansion(d_exp, details_id, state)
                    if hidden > 0:
                        ui.label(f"... ({hidden} more lines, expand to view)").classes(
                            "text-[10px] text-[var(--text-muted)] italic"
                        )
    _bind_expansion(expansion, card_id, state)
    return expansion


def render_commands_card(
    step: ExecutionStep,
    card_id: str | None = None,
    state: AppState | None = None,
) -> ui.expansion:
    """Render a collapsible 'Ran N commands' card with terminal container."""
    count = len(step.commands)
    title = step.title or f"Ran {count} command{'s' if count != 1 else ''}"
    initial_val = (
        state.is_card_expanded(card_id, default=False)
        if (state is not None and card_id is not None)
        else False
    )

    with (
        ui.expansion(
            text=title,
            icon="terminal",
            value=initial_val,
        )
        .props("dense dense-toggle header-class=bg-[var(--bg-card)]")
        .classes(
            "w-full rounded-xl bg-[var(--bg-card)] border "
            "border-[var(--border-subtle)] "
            "text-xs text-[var(--text-secondary)] my-1"
        ) as expansion,
        ui.column().classes("w-full p-3 gap-3 bg-[var(--bg-well)] rounded-b-xl"),
    ):
        for cmd in step.commands:
            with ui.column().classes(
                "w-full rounded-lg bg-[var(--bg-sunken)] border "
                "border-[var(--bg-tint)] "
                "p-3 gap-2 shadow-inner"
            ):
                # Terminal title row
                with ui.row().classes("w-full items-center justify-between"):
                    with ui.row().classes("items-center gap-1.5"):
                        ui.element("div").classes(
                            "w-2.5 h-2.5 rounded-full bg-[var(--deletion-red)]"
                        )
                        ui.element("div").classes(
                            "w-2.5 h-2.5 rounded-full bg-[var(--text-warn)]"
                        )
                        ui.element("div").classes(
                            "w-2.5 h-2.5 rounded-full bg-[var(--addition-green)]"
                        )
                        ui.label("Terminal").classes(
                            "text-[10px] text-[var(--text-muted)] font-mono ml-2"
                        )
                    if cmd.duration_seconds > 0:
                        ui.label(f"{cmd.duration_seconds:.2f}s").classes(
                            "text-[10px] text-[var(--text-muted)] font-mono"
                        )

                # Command Prompt
                with ui.row().classes("items-center gap-2"):
                    ui.label("$").classes(
                        "text-xs text-[var(--addition-green)] font-mono font-bold"
                    )
                    ui.label(cmd.command).classes(
                        "text-xs text-[var(--text-primary)] font-mono font-semibold"
                    )

                # Command Output
                if cmd.output:
                    preview, hidden = _truncate_text(cmd.output)
                    with ui.column().classes("w-full gap-1"):
                        with ui.scroll_area().classes(
                            "w-full max-h-48 bg-[var(--bg-canvas)] rounded p-2 "
                            "border border-[var(--bg-well-alt)]"
                        ):
                            ui.markdown(f"```text\n{preview}\n```").classes(
                                "markdown-content markdown-terminal m-0 "
                                "max-w-none w-full"
                            )
                        if hidden > 0:
                            ui.label(
                                f"... ({hidden} more lines, scroll to view)"
                            ).classes("text-[10px] text-[var(--text-muted)] italic")
    _bind_expansion(expansion, card_id, state)
    return expansion


def render_summoning_card(
    step: ExecutionStep,
    card_id: str | None = None,
    state: AppState | None = None,
) -> ui.expansion:
    """Render a collapsible Sub-Mvge summoning card."""
    title = step.title or step.sub_mvge_id or "Sub-Mvge"
    initial_val = (
        state.is_card_expanded(card_id, default=True)
        if (state is not None and card_id is not None)
        else True
    )
    with (
        ui.expansion(
            text=f"Summoned Sub-Mvge: {title}",
            icon="hub",
            value=initial_val,
        )
        .props("dense dense-toggle header-class=bg-[var(--bg-card)]")
        .classes(
            "w-full rounded-xl bg-[var(--bg-card)] border "
            "border-[var(--accent-primary-a30)] "
            "text-xs text-[var(--text-secondary)] my-1"
        ) as expansion,
        ui.column().classes("w-full p-3 bg-[var(--bg-card)] rounded-b-xl gap-2"),
    ):
        with ui.row().classes("items-center justify-between w-full"):
            with ui.row().classes("items-center gap-2"):
                # Live status indicator
                dot_color = (
                    "bg-[var(--addition-green)]"
                    if step.is_complete
                    else "bg-[var(--accent-primary)] animate-pulse"
                )
                ui.element("div").classes(f"w-2.5 h-2.5 rounded-full {dot_color}")
                ui.label(title).classes(
                    "text-xs font-semibold text-[var(--text-primary)]"
                )
                if step.spell_name:
                    ui.badge(f"Spell: {step.spell_name}", color="indigo-9").props(
                        "rounded dense size=xs"
                    )
            with ui.row().classes("items-center gap-2"):
                if step.mana_spent > 0:
                    ui.label(f"{step.mana_spent} Mana").classes(
                        "text-[10px] text-[var(--text-muted)] font-mono"
                    )

        if step.details:
            with ui.column().classes("w-full gap-1 pl-4"):
                for detail in step.details:
                    ui.label(f"• {detail}").classes(
                        "text-xs text-[var(--text-secondary)]"
                    )

        with ui.row().classes("items-center gap-2 pt-1"):
            if step.child_tome_id:
                (
                    ui.button(
                        "Inspect",
                        icon="visibility",
                        on_click=lambda: (
                            state.inspect_sub_tome(step.child_tome_id, title)
                            if state and step.child_tome_id
                            else None
                        ),
                    )
                    .props("unelevated dense size=xs color=primary")
                    .mark("inspect_sub_mvge_btn")
                )
                (
                    ui.button(
                        "Open as Full Tome",
                        icon="open_in_new",
                        on_click=lambda: (
                            state.switch_to_tome(step.child_tome_id)
                            if state and step.child_tome_id
                            else None
                        ),
                    )
                    .props("flat dense size=xs")
                    .mark("open_full_tome_btn")
                )

    _bind_expansion(expansion, card_id, state)
    return expansion


def render_step_card(
    step: ExecutionStep,
    card_id: str | None = None,
    state: AppState | None = None,
) -> ui.expansion:
    """Dispatch step rendering to the appropriate card renderer."""
    if step.step_type == StepType.SUMMONING:
        return render_summoning_card(step, card_id=card_id, state=state)
    if step.step_type == StepType.FILES:
        return render_files_card(step, card_id=card_id, state=state)
    if step.step_type == StepType.COMMANDS:
        return render_commands_card(step, card_id=card_id, state=state)
    return render_worked_card(step, card_id=card_id, state=state)
