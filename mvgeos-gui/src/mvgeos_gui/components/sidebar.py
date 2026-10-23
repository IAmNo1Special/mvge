"""Left navigation sidebar for MvgeOS desktop."""

from collections.abc import Callable

from nicegui import ui

from mvgeos_gui import __version__
from mvgeos_gui.components.sidebar_hint import sidebar_hint
from mvgeos_gui.state import AppState

# Mobile drawer bootstrap (Major #4). Injected once per page via
# ui.add_body_html; the window.__mvgeDrawerWired guard makes repeat
# injections from sidebar re-renders no-ops. The hamburger and scrim
# are created in <body> (not inside the sidebar container) because the
# container gets a translateX transform below the breakpoint, and a
# transformed ancestor would hijack position: fixed.
MOBILE_DRAWER_BOOTSTRAP = """
<script>
(function () {
    if (window.__mvgeDrawerWired) return;
    window.__mvgeDrawerWired = true;
    var btn = document.createElement('button');
    btn.className = 'mobile-menu-btn';
    btn.setAttribute('aria-label', 'Open navigation');
    btn.innerHTML = '<span class="material-icons">menu</span>';
    var scrim = document.createElement('div');
    scrim.className = 'sidebar-scrim';
    scrim.setAttribute('aria-hidden', 'true');
    document.body.appendChild(btn);
    document.body.appendChild(scrim);
    function isOpen() {
        var s = document.querySelector('.sidebar-container');
        return !!s && s.classList.contains('mobile-open');
    }
    function setOpen(open) {
        var s = document.querySelector('.sidebar-container');
        if (s) s.classList.toggle('mobile-open', open);
        scrim.classList.toggle('mobile-open', open);
    }
    document.addEventListener('click', function (ev) {
        var t = ev.target;
        if (t.closest && t.closest('.mobile-menu-btn')) {
            setOpen(!isOpen());
            return;
        }
        if (!isOpen()) return;
        if (t.closest && (t.closest('.sidebar-scrim') ||
                t.closest('.sidebar-container'))) {
            setOpen(false);
        }
    });
    document.addEventListener('keydown', function (ev) {
        if (ev.key === 'Escape') setOpen(false);
    });
})();
</script>
"""


def render_sidebar(state: AppState) -> ui.column:
    """Render the collapsible left sidebar."""
    collapsed = not state.sidebar_open
    collapsed_width = 56
    expanded_width = 260

    container = (
        ui.column()
        .classes(
            "h-full bg-[var(--bg-surface)] border-r border-[var(--border-subtle)] "
            "shrink-0 flex flex-col no-wrap overflow-hidden sidebar-container"
        )
        .style(f"width: {collapsed_width if collapsed else expanded_width}px")
    )

    with container:
        # Header
        if collapsed:
            with (
                ui.row().classes(
                    "w-full h-12 items-center justify-center "
                    "border-b border-[var(--border-subtle)] shrink-0"
                ),
                ui.button(
                    icon="chevron_right",
                    on_click=state.toggle_sidebar,
                )
                .props("flat dense round text-color=grey-5 size=xs")
                .classes("collapse-btn-icon")
                .mark("expand_sidebar_btn"),
            ):
                ui.tooltip("Expand sidebar")
        else:
            with ui.row().classes(
                "w-full h-12 items-center justify-between px-3 "
                "border-b border-[var(--border-subtle)] shrink-0 no-wrap"
            ):
                with ui.row().classes("items-center gap-2 no-wrap"):
                    ui.icon("auto_awesome", size="16px").classes(
                        "text-[var(--accent-primary)]"
                    )
                    ui.label("MvgeOS").classes(
                        "text-sm font-semibold text-[var(--text-primary)] tracking-wide"
                    )
                with (
                    ui.button(
                        icon="chevron_left",
                        on_click=state.toggle_sidebar,
                    )
                    .props("flat dense round text-color=grey-5 size=xs")
                    .classes("collapse-btn-icon")
                    .mark("collapse_sidebar_btn")
                ):
                    ui.tooltip("Collapse sidebar")

        # Workspace / Project display
        project_name = state.project_display_name
        if collapsed:
            with (
                ui.row().classes("w-full justify-center py-2 shrink-0"),
                ui.element("div").classes(
                    "cursor-pointer p-1.5 rounded-md hover:bg-[var(--bg-card-a60)]"
                ),
            ):
                ui.icon("folder_open", size="16px").classes(
                    "text-[var(--accent-primary)]"
                )
                ui.tooltip(f"Project: {project_name}")
        else:
            with ui.row().classes(
                "w-full px-3 py-2 items-center gap-2 shrink-0 no-wrap"
            ):
                ui.icon("folder_open", size="14px").classes(
                    "text-[var(--accent-primary)] shrink-0"
                )
                ui.label(project_name).classes(
                    "text-xs text-[var(--text-primary)] truncate font-medium flex-1"
                )

        # New Conversation button / Sign In
        if state.current_user:
            if collapsed:
                with (
                    ui.row().classes("w-full justify-center py-1 shrink-0"),
                    ui.button(
                        icon="add",
                        on_click=state.new_conversation,
                    )
                    .props("flat dense round size=sm")
                    .classes(
                        "bg-[var(--bg-card)] hover:bg-[var(--bg-card-hover)] "
                        "text-[var(--text-primary)] "
                        "border border-[var(--border-subtle)]"
                    )
                    .mark("new_conversation_btn"),
                ):
                    ui.tooltip("New Conversation")
            else:
                with ui.row().classes("w-full px-3 py-1 shrink-0"):
                    ui.button(
                        "+ New Conversation",
                        on_click=state.new_conversation,
                    ).props("unelevated no-caps").classes(
                        "w-full bg-[var(--bg-card)] hover:bg-[var(--bg-card-hover)] "
                        "text-[var(--text-primary)] "
                        "border border-[var(--border-subtle)] text-xs font-medium py-2 "
                        "rounded-lg text-left pl-3"
                    ).mark("new_conversation_btn")
        else:
            if collapsed:
                with (
                    ui.row().classes("w-full justify-center py-1 shrink-0"),
                    ui.button(
                        icon="login",
                        on_click=state.show_login,
                    )
                    .props("unelevated round size=sm")
                    .classes("mvge-glow-btn text-white")
                    .mark("sign_in_btn"),
                ):
                    ui.tooltip("Sign In")
            else:
                with ui.row().classes("w-full px-3 py-1 shrink-0"):
                    ui.button(
                        "Sign In",
                        on_click=state.show_login,
                    ).props("unelevated no-caps").classes(
                        "w-full mvge-glow-btn "
                        "text-white text-xs font-medium py-2 rounded-lg"
                    ).mark("sign_in_btn")

        # Navigation items: Core items + Rune-contributed items + Footer items
        core_nav_items = [
            ("Home", "home", "home"),
            ("Chat", "chat", "chat_bubble_outline"),
            ("Sessions", "sessions", "history"),
            ("Marketplace", "packages", "storefront"),
        ]
        contributed_nav_items = [
            (v.title, v.id, v.icon) for v in state.get_contributed_views()
        ]
        footer_nav_items = [
            ("Diagnostics", "diagnostics", "troubleshoot"),
            ("Settings", "settings", "settings"),
        ]
        nav_items = core_nav_items + contributed_nav_items + footer_nav_items

        with ui.column().classes("w-full px-2 py-1 gap-0.5 shrink-0"):
            for label, view, icon in nav_items:
                if view == "settings":
                    # Settings is a modal, not a view: open it directly.
                    # (There is no "settings" view branch in shell.py.)
                    _sidebar_item(
                        label,
                        view,
                        icon,
                        False,
                        state,
                        collapsed,
                        on_click=state.open_app_settings,
                    )
                else:
                    _sidebar_item(
                        label,
                        view,
                        icon,
                        state.current_view == view,
                        state,
                        collapsed,
                    )

        # Current Session & Recent Sessions
        if not collapsed:
            # Current Session
            if state.active_tome_id is not None:
                with (
                    ui.row().classes("mx-3 mt-3 shrink-0"),
                    ui.card().classes(
                        "w-full p-3 bg-[var(--bg-card)] border "
                        "border-[var(--border-subtle)] rounded-lg"
                    ),
                ):
                    ui.label("Current Session").classes(
                        "text-[10px] font-semibold uppercase "
                        "tracking-wider text-[var(--text-muted)]"
                    )
                    ui.label(state.tome_title).classes(
                        "text-sm text-[var(--text-primary)] truncate mt-1"
                    )
                    if state.active_tome_id:
                        ui.label(state.active_tome_id[:8]).classes(
                            "text-[10px] text-[var(--text-muted)] font-mono mt-0.5"
                        )

            # Recent Sessions
            with ui.column().classes("mt-4 flex-1 min-h-0 overflow-y-auto px-2 w-full"):
                with ui.row().classes("w-full items-center justify-between px-2 py-1"):
                    ui.label("Recent Sessions").classes(
                        "text-[10px] font-semibold uppercase tracking-wider "
                        "text-[var(--text-muted)]"
                    )

                search_query = [""]

                @ui.refreshable
                def _render_recent_tomes() -> None:
                    q = search_query[0].strip().lower()
                    tomes = state.loaded_tomes
                    if q:
                        tomes = [t for t in tomes if q in t.title.lower()]
                    if not tomes:
                        if q:
                            ui.label(f"No sessions match '{search_query[0]}'").classes(
                                "text-[11px] text-[var(--text-muted)] px-2 py-2"
                            )
                        else:
                            ui.label("No sessions yet").classes(
                                "text-[11px] text-[var(--text-muted)] px-2 py-2"
                            )
                    else:
                        for entry in tomes:
                            bg = (
                                "bg-[var(--bg-card-a70)] "
                                "border "
                                "border-[var(--border-subtle-a60)]"
                                if entry.is_active
                                else "hover:bg-[var(--bg-card-a40)]"
                            )
                            with (
                                ui.row()
                                .classes(
                                    f"w-full items-center px-2 py-1.5 rounded-md "
                                    f"cursor-pointer text-xs {bg} no-wrap"
                                )
                                .on(
                                    "click",
                                    lambda _, e=entry: state.switch_to_tome(e.tome_id),
                                )
                            ):
                                ui.icon("chat_bubble_outline", size="12px").classes(
                                    "text-[var(--text-secondary)] shrink-0"
                                )
                                ui.label(entry.title).classes(
                                    "text-[var(--text-primary)] truncate "
                                    "text-[11px] flex-1"
                                )
                                with ui.row().classes("items-center gap-1 shrink-0"):
                                    ui.label(entry.relative_time).classes(
                                        "text-[10px] text-[var(--text-muted)]"
                                    )
                                    if entry.git_branch:
                                        ui.label(entry.git_branch).classes(
                                            "text-[10px] px-1 py-0.5 rounded "
                                            "bg-[var(--accent-primary-a10)] "
                                            "text-[var(--accent-primary)] "
                                            "border border-[var(--accent-primary-a30)] "
                                            "font-mono"
                                        )

                if len(state.loaded_tomes) > 0:
                    ui.input(
                        placeholder="Search tomes...",
                        on_change=lambda e: (
                            search_query.__setitem__(0, e.value or ""),
                            _render_recent_tomes.refresh(),
                        ),
                    ).props("dense outlined rounded").classes(
                        "w-full text-xs px-1 mb-1"
                    ).mark("tomes_search_input")

                _render_recent_tomes()
        else:
            ui.element("div").classes("flex-1")

        # Bottom: version
        if collapsed:
            with ui.row().classes(
                "w-full border-t border-[var(--border-subtle)] py-2 "
                "justify-center items-center shrink-0"
            ):
                ui.label(f"v{__version__}").classes(
                    "text-[9px] text-[var(--text-muted)]"
                )
        else:
            with ui.row().classes(
                "w-full border-t border-[var(--border-subtle)] px-3 py-2 items-center "
                "justify-end shrink-0 no-wrap"
            ):
                ui.label(f"v{__version__}").classes(
                    "text-[10px] text-[var(--text-muted)]"
                )

    # Mobile drawer wiring (Major #4): idempotent, see module constant.
    ui.add_body_html(MOBILE_DRAWER_BOOTSTRAP)

    return container


def _sidebar_item(
    label: str,
    view: str,
    icon: str,
    active: bool,
    state: AppState,
    collapsed: bool,
    on_click: Callable[[], None] | None = None,
) -> None:
    """Render a single sidebar navigation item."""
    active_cls = (
        "nav-link-active text-primary"
        if active
        else (
            "text-[var(--text-secondary)] hover:text-[var(--text-primary)] "
            "hover:bg-[var(--bg-card-a60)]"
        )
    )
    handle_click = (
        on_click if on_click is not None else lambda: state.set_current_view(view)
    )
    if collapsed:
        with (
            ui.row()
            .classes(
                f"w-full justify-center py-2 rounded-md "
                f"cursor-pointer transition-colors {active_cls}"
            )
            .on("click", lambda _: handle_click())
            .mark(f"sidebar_nav_{view}")
        ):
            ui.icon(icon, size="16px").classes(
                "shrink-0 "
                + ("nav-icon-active" if active else "text-[var(--text-secondary)]")
            )
            sidebar_hint(label)
    else:
        with (
            ui.row()
            .classes(
                f"w-full items-center gap-2.5 px-3 py-2 rounded-md "
                f"cursor-pointer text-xs transition-colors {active_cls} no-wrap"
            )
            .on("click", lambda _: handle_click())
            .mark(f"sidebar_nav_{view}")
        ):
            ui.icon(icon, size="14px").classes(
                "shrink-0 "
                + ("nav-icon-active" if active else "text-[var(--text-secondary)]")
            )
            ui.label(label).classes("font-normal truncate flex-1")
