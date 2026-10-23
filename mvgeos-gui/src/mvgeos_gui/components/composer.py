"""Composer: prompt input with autocomplete, send/stop, and model selectors."""

from __future__ import annotations

import contextlib
from typing import Any

from mvgeos_provider.realms import REALM_RUNES
from nicegui import ui
from nicegui.elements.label import Label

from mvgeos_gui.autocomplete import AutocompleteService
from mvgeos_gui.state import AppState, format_channeling_elapsed


def _render_missing_rune_badge(state: AppState) -> None:
    """Render the amber badge for a missing realm rune, if applicable.

    Shown when the selected Realm has a Rune we ship but it is not installed. The
    badge names that Rune, derived from the Realm rather than hardcoded.

    Only Realms we actually publish a Rune for get a badge. Offering to install
    ``<realm>-realm`` for a Realm nothing provides would send a Summoner to a
    Rune that does not exist, which is worse than showing no badge at all.
    """
    missing_rune = REALM_RUNES.get(state.selected_realm)
    if missing_rune is None:
        return
    if state.is_rune_installed(missing_rune):
        return
    (
        ui.badge(f"Missing rune: {missing_rune}", color="amber-8")
        .props("rounded dense size=xs")
        .classes("cursor-pointer")
        .mark("missing_rune_badge")
        .tooltip(f"Install {missing_rune} from the Marketplace")
        .on("click", lambda: state.set_current_view("packages"))
    )


def render_composer(state: AppState) -> None:
    """Render the bottom composer input area.

    Curvy glow styling adapted from Uiverse.io "curvy-earwig-22" by
    Lakshay-art (MIT License): fluid full-width version with no search
    icon; the filter slot is reused as the send/stop button.
    """
    ac_service = state.get_autocomplete_service()

    # Glow containment: px-8/pb-8 give the halo fringe room to decay before
    # the chat column's overflow:hidden walls (sides/bottom); pt-5 tops up
    # the open messages area. See .mvge-glow note in styles.py.
    with ui.column().classes("w-full px-8 pb-8 pt-5 shrink-0"):  # noqa: SIM117
        with ui.element("div").classes("mvge-poda").mark("composer-curvy"):
            ui.element("div").classes("mvge-glow")
            ui.element("div").classes("mvge-dark-border-bg")
            ui.element("div").classes("mvge-dark-border-bg")
            ui.element("div").classes("mvge-dark-border-bg")
            ui.element("div").classes("mvge-white")
            ui.element("div").classes("mvge-border")
            with ui.element("div").classes("mvge-main").mark("composer-main"):
                # Queued follow-ups tray
                @ui.refreshable
                def queued_tray_view() -> None:
                    if not state.queued_follow_ups:
                        return
                    with (
                        ui.row()
                        .classes("items-center gap-1.5 flex-wrap mb-2")
                        .mark("queued_follow_ups_tray")
                    ):
                        for idx, text in enumerate(state.queued_follow_ups):
                            with ui.row().classes(
                                "items-center gap-1 px-2 py-0.5 rounded-md "
                                "bg-[var(--accent-primary-a10)] border "
                                "border-[var(--accent-primary-a30)]"
                            ):
                                ui.icon("schedule", size="12px").classes(
                                    "text-[var(--accent-primary)]"
                                )
                                ui.label(f"Queued: {text}").classes(
                                    "text-xs text-[var(--text-primary)] truncate "
                                    "max-w-[200px]"
                                )
                                ui.icon("close", size="10px").classes(
                                    "text-[var(--text-muted)] cursor-pointer "
                                    "hover:text-[var(--text-primary)]"
                                ).on(
                                    "click",
                                    lambda _, i=idx: state.remove_follow_up(i),
                                )

                queued_tray_view()

                last_queued_tray_state = [len(state.queued_follow_ups)]

                def _on_queued_tray_check() -> None:
                    cur = len(state.queued_follow_ups)
                    if cur != last_queued_tray_state[0]:
                        last_queued_tray_state[0] = cur
                        queued_tray_view.refresh()

                state.subscribe_view("composer_queued_tray", _on_queued_tray_check)

                # Attachment and Mention chips
                @ui.refreshable
                def chips_view() -> None:
                    if state.pending_attachments:
                        with ui.row().classes("items-center gap-1.5 flex-wrap mb-2"):
                            for idx, name in enumerate(state.pending_attachments):
                                with ui.row().classes(
                                    "items-center gap-1 px-2 py-0.5 rounded-md "
                                    "bg-[var(--border-subtle)]"
                                ):
                                    ui.icon("insert_drive_file", size="12px").classes(
                                        "text-[var(--text-secondary)]"
                                    )
                                    ui.label(name).classes(
                                        "text-xs text-[var(--text-primary)] truncate "
                                        "max-w-[140px]"
                                    )
                                    ui.icon("close", size="10px").classes(
                                        "text-[var(--text-muted)] cursor-pointer"
                                    ).on(
                                        "click",
                                        lambda _, i=idx: state.remove_attachment(i),
                                    )

                    if state.selected_mentions:
                        with ui.row().classes("items-center gap-1.5 flex-wrap mb-2"):
                            for idx, chip in enumerate(state.selected_mentions):
                                with ui.row().classes(
                                    "items-center gap-1 px-2 py-0.5 rounded-md "
                                    "bg-[var(--border-subtle)] border "
                                    "border-[var(--accent-primary-a30)]"
                                ):
                                    ui.icon("insert_drive_file", size="12px").classes(
                                        "text-[var(--text-secondary)]"
                                    )
                                    ui.label(chip.text).classes(
                                        "text-xs text-[var(--text-primary)] truncate "
                                        "max-w-[160px]"
                                    )
                                    ui.icon("close", size="10px").classes(
                                        "text-[var(--text-muted)] cursor-pointer"
                                    ).on(
                                        "click",
                                        lambda _, i=idx: state.remove_selected_mention(
                                            i
                                        ),
                                    )

                chips_view()

                last_chips_state = [
                    (len(state.pending_attachments), len(state.selected_mentions))
                ]

                def _on_chips_check() -> None:
                    cur = (
                        len(state.pending_attachments),
                        len(state.selected_mentions),
                    )
                    if cur != last_chips_state[0]:
                        last_chips_state[0] = cur
                        chips_view.refresh()

                state.subscribe_view("composer_chips", _on_chips_check)

                # Input zone: textarea + glow masks + send button
                # (no search icon; filter slot is the send/stop button).
                with ui.column().classes("relative w-full mvge-input-zone"):
                    placeholder_text = (
                        "Ask Mvge anything… (@ to mention, / for commands)"
                    )
                    prompt_input = (
                        ui.textarea(
                            placeholder=placeholder_text,
                            value=state.composer_draft,
                        )
                        .props("autogrow borderless dense rows=2")
                        .classes(
                            "w-full bg-transparent text-sm "
                            "text-[var(--text-primary)] resize-none"
                        )
                        .mark("prompt_input")
                    )
                    ui.element("div").classes("mvge-input-mask")
                    ui.element("div").classes("mvge-pink-mask")
                    ui.element("div").classes("mvge-send-border")

                    # Sync active_prompt if set externally (e.g. example prompts)
                    last_active_prompt = [state.active_prompt]

                    def _on_prompt_check() -> None:
                        if (
                            state.active_prompt
                            and state.active_prompt != last_active_prompt[0]
                        ):
                            last_active_prompt[0] = state.active_prompt
                            prompt_input.value = state.active_prompt
                            state.composer_draft = state.active_prompt
                            state.active_prompt = ""

                    state.subscribe_view("composer_prompt", _on_prompt_check)

                    # Bind input to autocomplete service
                    def handle_input_change(e: Any) -> None:
                        val = getattr(e, "value", None)
                        if val is None and hasattr(e, "args"):
                            val = e.args
                        text = str(
                            val if val is not None else (prompt_input.value or "")
                        )
                        ac_service.process_input(text, len(text))
                        # Stash the draft: the composer re-renders when it moves
                        # between the empty-state slot and the bottom dock.
                        state.composer_draft = text

                    prompt_input.on("update:model-value", handle_input_change)

                    def handle_submit() -> None:
                        text = prompt_input.value or ""
                        if (
                            not text.strip()
                            and not state.selected_mentions
                            and not state.pending_attachments
                        ):
                            return
                        # Prepend mentions to text if any
                        if state.selected_mentions:
                            prefix = (
                                " ".join(m.text for m in state.selected_mentions) + " "
                            )
                            text = prefix + text
                            state.clear_selected_mentions()
                        prompt_input.value = ""
                        state.composer_draft = ""
                        ac_service.close()
                        _scroll_to_bottom(element_id=None, force=True)
                        state.submit_prompt(text)

                    def handle_steer() -> None:
                        text = prompt_input.value or ""
                        if not text.strip():
                            return
                        prompt_input.value = ""
                        state.composer_draft = ""
                        ac_service.close()
                        state.steer_prompt(text)

                    def handle_queue_follow_up() -> None:
                        text = prompt_input.value or ""
                        if not text.strip():
                            return
                        prompt_input.value = ""
                        state.composer_draft = ""
                        ac_service.close()
                        state.queue_follow_up(text)

                    def handle_enter(e: object) -> None:
                        # If autocomplete popup is open, enter selects the item
                        if ac_service.is_open:
                            item = ac_service.get_selected_item()
                            if item:
                                _apply_autocomplete_selection(
                                    ac_service, prompt_input, item, state
                                )
                            return
                        if state.is_channeling:
                            handle_steer()
                            return
                        # Shift+Enter inserts newline, normal Enter submits
                        handle_submit()

                    def handle_ctrl_enter(e: object) -> None:
                        if state.is_channeling:
                            handle_queue_follow_up()

                    def handle_arrow_down(e: object) -> None:
                        if ac_service.is_open:
                            ac_service.move_down()

                    def handle_arrow_up(e: object) -> None:
                        if ac_service.is_open:
                            ac_service.move_up()

                    prompt_input.on(
                        "keydown.tab.prevent",
                        lambda e: handle_tab(ac_service, prompt_input, state),
                    )
                    prompt_input.on("keydown.down.prevent", handle_arrow_down)
                    prompt_input.on("keydown.up.prevent", handle_arrow_up)

                    prompt_input.on(
                        "keydown.backspace",
                        lambda e: _handle_backspace(prompt_input, state),
                    )

                    prompt_input.on("keydown.enter.exact.prevent", handle_enter)
                    prompt_input.on("keydown.enter.ctrl.prevent", handle_ctrl_enter)
                    prompt_input.on("keydown.enter.meta.prevent", handle_ctrl_enter)
                    # Note: no element-level keydown.escape here. Escape is owned
                    # by the global keyboard dispatcher (components/keyboard.py),
                    # which closes autocomplete before palette before interrupt —
                    # a per-element handler would fire first during bubble and
                    # could trigger two actions on one press.

                    # Autocomplete popup
                    @ui.refreshable
                    def popup_view() -> None:
                        if not ac_service.is_open:
                            return
                        items = ac_service.get_visible_items()
                        if not items:
                            return
                        popup_id = f"autocomplete-popup-{id(ac_service)}"
                        selected_idx = ac_service.selected_index
                        with (
                            ui.element("div")
                            .classes(
                                "absolute z-50 w-full bg-[var(--border-subtle)] "
                                "border border-[var(--accent-primary)] rounded-lg "
                                "shadow-xl "
                                "max-h-48 overflow-y-auto bottom-full mb-1"
                            )
                            .props(f"id={popup_id}")
                        ):
                            for idx, item in enumerate(items):
                                is_selected = idx == selected_idx
                                item_cls = (
                                    "bg-[var(--accent-primary-a20)]"
                                    if is_selected
                                    else "hover:bg-[var(--border-subtle-a50)]"
                                )
                                with (
                                    ui.row()
                                    .classes(
                                        "items-center gap-2 px-2 py-1 cursor-pointer "
                                        f"{item_cls} rounded w-full"
                                    )
                                    .on(
                                        "click",
                                        lambda _, i=idx: _select_autocomplete_item(
                                            ac_service, prompt_input, i, state
                                        ),
                                    )
                                    .props(f'data-index="{idx}"')
                                ):
                                    ui.icon("help_outline", size="14px").classes(
                                        "text-[var(--text-secondary)]"
                                    )
                                    ui.label(
                                        str(ac_service.get_item_label(item))
                                    ).classes(
                                        "text-xs text-[var(--text-primary)] truncate"
                                    )

                        if items and 0 <= selected_idx < len(items):
                            ui.run_javascript(f"""
                                const container = document.getElementById('{popup_id}');
                                const item = container?.querySelector(
                                    '[data-index="{selected_idx}"]'
                                );
                                if (item) item.scrollIntoView({{ block: 'nearest' }});
                            """)

                    ac_service.subscribe_items_changed(popup_view.refresh)
                    popup_view()

                    elapsed_label: list[Label | None] = [None]

                    @ui.refreshable
                    def action_btn_view() -> None:
                        if state.is_channeling:
                            with ui.row().classes("items-center gap-1.5"):
                                with (
                                    ui.button(
                                        icon="stop",
                                        on_click=state.stop_channeling,
                                    )
                                    .props("unelevated dense size=sm")
                                    .classes("mvge-send-btn mvge-stop")
                                    .mark("stop_channeling_btn")
                                ):
                                    ui.tooltip("Stop generation")
                                with (
                                    ui.button(
                                        icon="bolt",
                                        on_click=handle_steer,
                                    )
                                    .props("unelevated dense size=sm")
                                    .classes("mvge-send-btn")
                                    .mark("steer_mvge_btn")
                                ):
                                    ui.tooltip("Steer Mvge immediately (Enter)")
                                with (
                                    ui.button(
                                        icon="playlist_add",
                                        on_click=handle_queue_follow_up,
                                    )
                                    .props("unelevated dense size=sm")
                                    .classes("mvge-send-btn")
                                    .mark("queue_follow_up_btn")
                                ):
                                    ui.tooltip("Queue Follow-up (Ctrl/Cmd+Enter)")
                                elapsed = state.elapsed_channeling_seconds()
                                elapsed_label[0] = (
                                    ui.label(
                                        format_channeling_elapsed(elapsed)
                                        if elapsed is not None
                                        else "0s"
                                    )
                                    .classes("text-xs text-[var(--text-secondary)]")
                                    .mark("channeling_elapsed")
                                )
                        else:
                            elapsed_label[0] = None
                            with (
                                ui.button(
                                    icon="arrow_forward",
                                    on_click=handle_submit,
                                )
                                .props("unelevated dense size=sm")
                                .classes("mvge-send-btn")
                                .mark("send_prompt_btn")
                            ):
                                ui.tooltip("Send prompt")

                    action_btn_view()

                    def _tick_channeling_timer() -> None:
                        label = elapsed_label[0]
                        if state.is_channeling and label is not None:
                            elapsed = state.elapsed_channeling_seconds()
                            label.text = (
                                format_channeling_elapsed(elapsed)
                                if elapsed is not None
                                else "0s"
                            )

                    ui.timer(1.0, _tick_channeling_timer)

                    last_action_state = [state.is_channeling]

                    def _on_action_check() -> None:
                        if state.is_channeling != last_action_state[0]:
                            last_action_state[0] = state.is_channeling
                            action_btn_view.refresh()

                    state.subscribe_view("composer_actions", _on_action_check)

                # Toolbar row below the input (model + context actions)
                with ui.row().classes(
                    "w-full items-center gap-2 pt-1 mvge-composer-toolbar pl-2"
                ):

                    async def _handle_upload(e: Any) -> None:
                        """Attach the uploaded file for the next prompt."""
                        # NiceGUI's UploadEventArguments carries a single .file
                        uploaded = getattr(e, "file", None)
                        name = getattr(uploaded, "name", "")
                        if not uploaded or not name:
                            return
                        content = await uploaded.read()
                        state.add_attachment(name, content)
                        ui.notify(f"Attached {name}.", type="positive")

                    ui.upload(
                        on_upload=_handle_upload,
                        label="",
                        auto_upload=True,
                    ).props("flat dense round").classes("mvge-upload-btn").mark(
                        "composer_upload_btn"
                    )

                    @ui.refreshable
                    def cascading_selector_view() -> None:
                        is_router = state.is_router_realm()
                        supports_contemplation = (
                            state.supports_contemplation_for_selected_model()
                        )

                        # Tier 1: Realm Select
                        realms = state.get_realms()
                        realm_labels = {
                            "openrouter": "OpenRouter",
                            "ollama": "Ollama",
                            "google": "Google Direct",
                        }
                        realm_options = {
                            r: realm_labels.get(r, r.capitalize()) for r in realms
                        }
                        with (
                            ui.select(
                                options=realm_options,
                                value=state.selected_realm,
                                on_change=lambda e: state.switch_realm(e.value),
                            )
                            .props("dense borderless rounded text-xs")
                            .classes("text-xs text-[var(--text-secondary)] font-mono")
                            .mark("realm_select")
                        ):
                            ui.tooltip("Realm")

                        _render_missing_rune_badge(state)

                        # Tier 2: Provider Select (Visible only if router)
                        if is_router:
                            providers = state.get_providers_for_selected_realm()
                            prov_options = {p: p for p in providers}
                            if (
                                state.selected_provider
                                and state.selected_provider not in prov_options
                            ):
                                prov_options[state.selected_provider] = (
                                    state.selected_provider
                                )
                            with (
                                ui.select(
                                    options=prov_options,
                                    value=state.selected_provider,
                                    on_change=lambda e: state.switch_provider(e.value),
                                )
                                .props("dense borderless rounded text-xs")
                                .classes(
                                    "text-xs text-[var(--text-secondary)] font-mono"
                                )
                                .mark("provider_select")
                            ):
                                ui.tooltip("Provider")

                        # Tier 3: Model Select
                        model_options = state.get_model_options_for_selection()
                        with (
                            ui.select(
                                options=model_options,
                                value=state.selected_model,
                                on_change=lambda e: state.switch_model(e.value),
                            )
                            .props("dense borderless rounded text-xs")
                            .classes("text-xs text-[var(--text-secondary)] font-mono")
                            .mark("model_select")
                        ):
                            ui.tooltip("Model")

                        # Tier 4: Contemplation Level Select (Dynamic, if supported)
                        if supports_contemplation:
                            levels = state.get_contemplation_levels_for_selected_model()
                            level_options = {lvl: f"Thinking: {lvl}" for lvl in levels}
                            if (
                                state.contemplation_level
                                and state.contemplation_level not in level_options
                            ):
                                level_options[state.contemplation_level] = (
                                    f"Thinking: {state.contemplation_level}"
                                )
                            with (
                                ui.select(
                                    options=level_options,
                                    value=state.contemplation_level,
                                    on_change=lambda e: state.set_contemplation_level(
                                        e.value
                                    ),
                                )
                                .props("dense borderless rounded text-xs")
                                .classes(
                                    "text-xs text-[var(--text-secondary)] font-mono"
                                )
                                .mark("contemplation_select")
                            ):
                                ui.tooltip("Contemplation Level")

                    cascading_selector_view()

                    last_selector_state = [
                        (
                            state.selected_realm,
                            state.selected_provider,
                            state.selected_model,
                            state.contemplation_level,
                            state.is_selected_realm_rune_installed(),
                        )
                    ]

                    def _on_selector_check() -> None:
                        current_sel = (
                            state.selected_realm,
                            state.selected_provider,
                            state.selected_model,
                            state.contemplation_level,
                            state.is_selected_realm_rune_installed(),
                        )
                        if current_sel != last_selector_state[0]:
                            last_selector_state[0] = current_sel
                            cascading_selector_view.refresh()

                    state.subscribe_view("cascading_selector", _on_selector_check)


def _scroll_to_bottom(element_id: int | None = None, force: bool = False) -> None:
    """Smart auto-scroll chat messages to bottom or preserve user scroll position.

    If force is True or the user is already near the bottom, scrolls to bottom.
    If the user has manually scrolled up to read history, preserves scroll position.
    Shows/hides the floating down-arrow button based on bottom-pinning status.
    """
    force_js = "true" if force else "false"
    target_el = f"getElement({element_id})" if element_id is not None else "null"
    js_cmd = f"""
        const el = typeof getElement === 'function'
            ? {target_el}
            : document.getElementById('chat-messages-area');
        const updateScrollBtn = (isPinned) => {{
            const btn = document.getElementById('chat-scroll-bottom-btn');
            if (btn) {{
                if (!isPinned) {{
                    btn.classList.remove('opacity-0', 'pointer-events-none');
                    btn.classList.add('opacity-100', 'pointer-events-auto');
                }} else {{
                    btn.classList.remove('opacity-100', 'pointer-events-auto');
                    btn.classList.add('opacity-0', 'pointer-events-none');
                }}
            }}
        }};
        if (el) {{
            if (!el._scrollListenerAttached) {{
                el._scrollListenerAttached = true;
                el._userScrolledUp = false;
                el._savedScrollTop = el.scrollTop;

                el.addEventListener('wheel', (e) => {{
                    if (e.deltaY < 0) {{
                        el._userScrolledUp = true;
                        el._savedScrollTop = el.scrollTop;
                        updateScrollBtn(false);
                    }} else if (e.deltaY > 0) {{
                        const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
                        if (dist < 80) {{
                            el._userScrolledUp = false;
                            updateScrollBtn(true);
                        }} else {{
                            el._savedScrollTop = el.scrollTop;
                        }}
                    }}
                }}, {{ passive: true }});

                el.addEventListener('touchmove', () => {{
                    const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
                    if (dist >= 80) {{
                        el._userScrolledUp = true;
                        el._savedScrollTop = el.scrollTop;
                        updateScrollBtn(false);
                    }} else {{
                        el._userScrolledUp = false;
                        updateScrollBtn(true);
                    }}
                }}, {{ passive: true }});

                el.addEventListener('scroll', () => {{
                    if (el._isProgrammaticScroll) {{
                        el._isProgrammaticScroll = false;
                        return;
                    }}
                    if (el.scrollHeight > el.clientHeight + 50) {{
                        const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
                        if (dist >= 80) {{
                            el._userScrolledUp = true;
                            el._savedScrollTop = el.scrollTop;
                            updateScrollBtn(false);
                        }} else {{
                            el._userScrolledUp = false;
                            updateScrollBtn(true);
                        }}
                    }}
                }}, {{ passive: true }});
            }}

            const forceScroll = {force_js};
            if (forceScroll) {{
                el._userScrolledUp = false;
            }}

            if (forceScroll || !el._userScrolledUp) {{
                updateScrollBtn(true);
                el._isProgrammaticScroll = true;
                el.scrollTop = el.scrollHeight;
                requestAnimationFrame(() => {{
                    el.scrollTop = el.scrollHeight;
                }});
            }} else {{
                updateScrollBtn(false);
                if (el._savedScrollTop !== undefined) {{
                    el._isProgrammaticScroll = true;
                    el.scrollTop = el._savedScrollTop;
                }}
            }}
        }}
    """
    with contextlib.suppress(Exception):
        ui.run_javascript(js_cmd)


def fill_composer(state: AppState, text: str) -> None:
    """Fill the composer with example text without sending."""
    state.active_prompt = text
    state.notify()


def _handle_backspace(prompt_input: ui.textarea, state: AppState) -> None:
    """Remove the last mention chip if textarea is empty."""
    text = prompt_input.value or ""
    if not text and state.selected_mentions:
        state.remove_selected_mention(len(state.selected_mentions) - 1)


def handle_tab(
    ac_service: AutocompleteService,
    prompt_input: ui.textarea,
    state: AppState,
) -> None:
    """Apply the highlighted autocomplete item when the popup is open."""
    if ac_service.is_open:
        item = ac_service.get_selected_item()
        if item:
            _apply_autocomplete_selection(ac_service, prompt_input, item, state)


def _apply_autocomplete_selection(
    ac_service: AutocompleteService,
    prompt_input: ui.textarea,
    item: object,
    state: AppState,
) -> None:
    """Apply selected autocomplete item."""
    chip = ac_service.create_chip(item)
    if chip:
        state.add_mention(chip)
        text = prompt_input.value or ""
        start, end = ac_service.get_word_range(text)
        if start >= 0 and end >= 0:
            prompt_input.value = text[:start] + text[end:]
            state.composer_draft = prompt_input.value or ""
    ac_service.close()
    ac_service._notify_listeners()


def _select_autocomplete_item(
    ac_service: AutocompleteService,
    prompt_input: ui.textarea,
    idx: int,
    state: AppState,
) -> None:
    """Select an autocomplete item and insert as chip."""
    if idx < 0 or idx >= len(ac_service.items):
        return
    ac_service.selected_index = idx
    item = ac_service.items[idx]
    _apply_autocomplete_selection(ac_service, prompt_input, item, state)
