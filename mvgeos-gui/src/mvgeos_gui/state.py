"""Application state management for mvgeos-gui."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import os
import shutil
import time
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from mvgeos_agent import (
    fetch_marketplace_mvges,
    install_mvge,
    list_installed_mvges,
    uninstall_mvge,
)
from mvgeos_core.approval import (
    ApprovalDecision,
    ApprovalOutcome,
    ApprovalReasonCode,
    ApprovalRequest,
    ApprovalScope,
)
from mvgeos_core.constants import DEFAULT_MODEL
from mvgeos_core.invocations import Attachment, build_content_parts
from mvgeos_core.layers import (
    agents_dir,
    extensions_dir,
    project_skills_dir,
    skills_dir,
)
from mvgeos_provider import (
    get_default_realm_registry,
    get_model_options,
    get_models_for_provider,
    get_providers_for_realm,
    get_supported_contemplation_levels,
    is_realm_router,
)
from mvgeos_provider.realms import DEFAULT_REALM, REALM_RUNES, realm_for_model_id
from mvgeos_runes import (
    RuneUICanvasTab,
    RuneUIView,
    fetch_marketplace_runes,
    install_rune,
    install_skill,
    list_installed_runes,
    set_rune_enabled,
    uninstall_rune,
)
from mvgeos_runes.types import SkillManifest
from mvgeos_tome.types import TomeEntry, TomeEntryType, TomeVersionError
from nicegui import app as nicegui_app
from nicegui.elements.dark_mode import DarkMode

from mvgeos_gui.approval.presenter import unbind_approval_presenter
from mvgeos_gui.approval.queue import ApprovalQueue
from mvgeos_gui.approval.types import PermissionsView
from mvgeos_gui.autocomplete import (
    AutocompleteService,
    CommandKind,
    MentionChip,
    MentionIndex,
    SlashCommandItem,
    SlashCommandRegistry,
)
from mvgeos_gui.git_workspace import ChangedFile, get_changed_files, get_diff_for_file
from mvgeos_gui.models import (
    Artifact,
    BackgroundTask,
    ChatMessage,
    SkillInfo,
    TaskStatus,
    User,
)
from mvgeos_gui.services.agent_service import AgentService
from mvgeos_gui.services.auth_service import AuthService
from mvgeos_gui.services.config_service import ConfigService
from mvgeos_gui.services.tome_service import TomeListEntry, TomeService
from mvgeos_gui.transcript import InvocationTranscript

logger = logging.getLogger(__name__)


def format_channeling_elapsed(seconds: float) -> str:
    """Format an elapsed channeling duration compactly ("45s", "2m 05s")."""
    total = max(0, int(seconds))
    if total < 60:
        return f"{total}s"
    return f"{total // 60}m {total % 60:02d}s"


def _resolve_skill_manifest_file(skill_dir: Path) -> Path | None:
    """Locate the skill manifest file inside a skill directory.

    Checks SKILL.md first, then falls back to lowercase skill.md per the
    .agents protocol. Returns None when neither exists (not a skill).
    """
    upper = skill_dir / "SKILL.md"
    if upper.is_file():
        return upper
    lower = skill_dir / "skill.md"
    if lower.is_file():
        return lower
    return None


def _scan_skill_manifests(project_path: Path) -> list[SkillManifest]:
    """Scan project and user skill directories for skill manifests.

    Shared by AppState.load_skills and the server-level @-mention index
    so both see the same skill set.
    """
    skills: list[SkillManifest] = []
    search_dirs = [
        project_skills_dir(project_path),
        skills_dir(),
    ]
    for sdir in search_dirs:
        if not sdir.is_dir():
            continue
        for item in sorted(sdir.iterdir()):
            if item.is_dir() and _resolve_skill_manifest_file(item) is not None:
                skills.append(
                    SkillManifest(
                        name=item.name,
                        description=f"Skill {item.name}",
                        path=str(item),
                    )
                )
    return skills


# AppState fields that are server-global rather than per-client. Reads of
# these names fall through to the owning ServerState via __getattr__ and
# writes pass through via __setattr__, so all sessions share one live
# configuration while every call site keeps working unchanged.
_SHARED_FIELDS = frozenset(
    {
        "project_path",
        "recent_projects",
        "api_key",
        "selected_realm",
        "selected_provider",
        "selected_model",
        "contemplation_level",
        "tome_service",
        "_config_service",
    }
)

# Per-browser storage key for the active session id.
_ACTIVE_TOME_STORAGE_KEY = "active-tome-id"


@dataclass
class ServerState:
    """Server-global configuration shared by every connected browser session.

    One ServerState exists per GUI process. Each browser session gets its
    own per-client AppState (see :meth:`new_client_state`); the client
    states delegate the fields named in ``_SHARED_FIELDS`` to this object,
    so every session reads and writes the same project, model selection,
    and credentials while all UI state stays per-client.
    """

    project_path: Path = field(default_factory=Path.cwd)
    recent_projects: list[Path] = field(default_factory=list)
    api_key: str | None = None
    # Neither the Realm nor the provider is a literal here. Both used to be, so
    # changing DEFAULT_MODEL alone left the cascading selector opening on a Realm
    # that could not serve the model it started on.
    #
    # The Realm comes from the Realm table, because the two are not the same
    # question: for a routed slug the id prefix is the *provider*. The provider
    # does come from the id, which is the rule the rest of the engine already
    # follows, and which is right for both shapes. A directly-served slug is
    # ``<realm>/<model>``, so the prefix is the Realm; a routed one is
    # ``<provider>/<model>``, so the prefix is the provider tier the cascading
    # selector has to open on. Copying the Realm instead turns a routed default
    # into "no provider tier", and the selector then offers the router's own
    # entry rather than the model's. Same for both the states above.
    selected_realm: str = DEFAULT_REALM
    selected_provider: str | None = realm_for_model_id(DEFAULT_MODEL)
    selected_model: str = DEFAULT_MODEL
    contemplation_level: str = "medium"
    tome_service: TomeService = field(
        default_factory=TomeService, repr=False, compare=False
    )
    _config_service: ConfigService = field(
        default_factory=ConfigService, repr=False, compare=False
    )
    _clients: list[AppState] = field(default_factory=list, repr=False, compare=False)
    # Server-shared @-mention index (one per server, not per client).
    # Built lazily on first use; dropped by refresh_mention_index() when
    # the project changes. The index itself notices tree changes via a
    # directory-mtime signature checked on every query (see MentionIndex),
    # so no file watcher or background thread is needed.
    _mention_index: MentionIndex | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        """Initialize server invariants now that constructor kwargs landed."""
        if self.project_path and self.project_path not in self.recent_projects:
            self.recent_projects.insert(0, self.project_path)
        if "/" in self.selected_model and self.selected_provider == "nvidia":
            self.selected_provider = self.selected_model.split("/")[0]

    @property
    def client_states(self) -> list[AppState]:
        """Snapshot of the currently connected per-client states."""
        return list(self._clients)

    def new_client_state(self) -> AppState:
        """Mint a fresh per-client AppState bound to this server.

        The state is built standalone first and then rebound: shared-field
        constructor defaults must never be written through to this
        server, which would clobber its live configuration.
        """
        state = AppState()
        state._server = self
        self._clients.append(state)
        return state

    def _notify_clients(self) -> None:
        """Refresh every connected client after a shared mutation.

        Shared configuration is server-global: when one client changes
        it, every session's UI must re-render where the change is
        visible (model switcher, project name, ...). Listeners are
        check-and-refresh callbacks, so unaffected clients are cheap.
        """
        for client in list(self._clients):
            with contextlib.suppress(Exception):
                client.notify()

    @property
    def mention_index(self) -> MentionIndex:
        """The server-shared @-mention index.

        One index per server, shared by every client's
        AutocompleteService. Built lazily for the current project;
        per-client services must always go through this property rather
        than constructing their own MentionIndex.
        """
        if self._mention_index is None:
            self._mention_index = MentionIndex(
                self.project_path,
                skills=_scan_skill_manifests(self.project_path),
            )
        return self._mention_index

    def refresh_mention_index(self) -> None:
        """Drop the shared @-mention index so it rebuilds on next query.

        Called when the active project changes: the next query builds a
        fresh index bound to the new project path. Idempotent.
        """
        self._mention_index = None

    def drop_client_state(self, state: AppState) -> None:
        """Detach a client session: fail closed and release its resources.

        Pending approval casts are denied (their decision surface is
        gone), the agent task is cancelled, UI listeners are dropped, and
        the client's cached agent is closed in the background so its rune
        watchers stop firing after disconnect. Background close is fine
        here because the server keeps running.
        """
        self._clients = [s for s in self._clients if s is not state]
        self._fail_closed_sync(state)
        with contextlib.suppress(Exception):
            service = state.agent_service
            if service is not None:
                service.close_agent_in_background()

    @staticmethod
    def _fail_closed_sync(state: AppState) -> None:
        """Synchronous fail-closed steps shared by disconnect and shutdown."""
        with contextlib.suppress(Exception):
            state.stop_channeling()
        with contextlib.suppress(Exception):
            state.clear_listeners()
        with contextlib.suppress(Exception):
            unbind_approval_presenter(state)

    async def ashutdown(self) -> None:
        """Awaited server shutdown: fail closed for every connected client.

        Unlike :meth:`drop_client_state` (which must not block a live
        server), shutdown awaits each cached agent's close, so rune
        watchers are guaranteed stopped before the event loop tears down.
        NiceGUI awaits async ``app.on_shutdown`` handlers inside
        ``App.stop()`` before uvicorn cancels pending tasks; a shutdown
        that merely scheduled background closes would be cancelled first.
        Close failures are logged and suppressed: teardown must not raise.
        """
        for client in list(self._clients):
            self._fail_closed_sync(client)
            with contextlib.suppress(Exception):
                service = client.agent_service
                if service is not None:
                    await service.close_agent()
            self._clients = [s for s in self._clients if s is not client]


@dataclass
class AppState:
    """Per-client reactive UI state for one MvgeOS browser session.

    Server-global configuration (project path, model selection, API key,
    and the config/tome services) lives on the owning ServerState. The
    fields named in ``_SHARED_FIELDS`` are delegated to it transparently:
    reads fall through via ``__getattr__`` and writes pass through via
    ``__setattr__``, so every call site keeps working unchanged while all
    sessions share one live configuration. Everything else on this object
    (dialog flags, current view, sidebar, transcript, plan mode, auth) is
    strictly per-client and never leaks across sessions.
    """

    # Owning server for the shared configuration. Not a constructor
    # argument: it is assigned by ServerState.new_client_state(), or built
    # lazily as a private server for standalone AppState() use.
    _server: ServerState | None = field(
        default=None, init=False, repr=False, compare=False
    )
    # NOTE: the fields below are server-global (see _SHARED_FIELDS). They
    # stay declared so AppState(...) keeps its constructor signature, but
    # their values live on the owning ServerState: __setattr__ buffers them
    # during __init__ and __getattr__ delegates reads to the server.
    project_path: Path = field(default_factory=Path.cwd)
    active_tome_id: str | None = None
    tome_title: str = "New Conversation"
    inspector_expanded: bool = True
    # Neither the Realm nor the provider is a literal here. Both used to be, so
    # changing DEFAULT_MODEL alone left the cascading selector opening on a Realm
    # that could not serve the model it started on.
    #
    # The Realm comes from the Realm table, because the two are not the same
    # question: for a routed slug the id prefix is the *provider*. The provider
    # does come from the id, which is the rule the rest of the engine already
    # follows, and which is right for both shapes. A directly-served slug is
    # ``<realm>/<model>``, so the prefix is the Realm; a routed one is
    # ``<provider>/<model>``, so the prefix is the provider tier the cascading
    # selector has to open on. Copying the Realm instead turns a routed default
    # into "no provider tier", and the selector then offers the router's own
    # entry rather than the model's. Same for both the states above.
    selected_realm: str = DEFAULT_REALM
    selected_provider: str | None = realm_for_model_id(DEFAULT_MODEL)
    selected_model: str = DEFAULT_MODEL
    contemplation_level: str = "medium"
    recent_projects: list[Path] = field(default_factory=list)
    is_channeling: bool = False
    channeling_started_at: float | None = None
    tome_service: TomeService = field(
        default_factory=TomeService, repr=False, compare=False
    )
    loaded_tomes: list[TomeListEntry] = field(default_factory=list)
    messages: list[ChatMessage] = field(default_factory=list)
    total_mana_used: int = 0
    # Latest provider-reported token usage for the context gauge. Distinct
    # from cumulative Mana: these are raw token counts off the last provider
    # response (input + output). None until a response reports real usage.
    context_input_tokens: int | None = None
    context_output_tokens: int | None = None
    active_prompt: str = ""
    # Draft text typed into the composer. The composer re-renders when it moves
    # between the centered empty-state slot and the bottom dock; the draft
    # restores the textarea so unsent text survives the move.
    composer_draft: str = ""
    # Plan mode: the engine only offers spells marked read_only. Toggled from
    # the chat toolbar or the command palette.
    plan_mode: bool = False
    api_key: str | None = None
    pending_attachments: list[str] = field(default_factory=list)
    pending_attachment_contents: dict[str, bytes] = field(
        default_factory=dict, repr=False
    )
    selected_mentions: list[MentionChip] = field(default_factory=list)
    queued_follow_ups: list[str] = field(default_factory=list)
    active_skills: list[SkillInfo] = field(default_factory=list)
    agent_service: AgentService | None = field(default=None, repr=False, compare=False)
    active_task: asyncio.Task[Any] | None = field(
        default=None, repr=False, compare=False
    )
    background_tasks: list[BackgroundTask] = field(default_factory=list)
    _autocomplete_service: AutocompleteService | None = field(
        default=None, repr=False, compare=False
    )
    # The page's Quasar DarkMode element, created by inject_theme() in
    # build_page. Settings reuses it via apply_theme() so a theme change
    # never mints a competing second element. Per-client: it belongs to
    # this browser session's page.
    _dark_mode: DarkMode | None = field(
        default=None, init=False, repr=False, compare=False
    )
    _change_listeners: list[Callable[[], Any]] = field(
        default_factory=list, repr=False, compare=False
    )
    _streaming_listeners: list[Callable[[], Any]] = field(
        default_factory=list, repr=False, compare=False
    )
    _view_listeners: dict[str, list[Callable[[], Any]]] = field(
        default_factory=dict, repr=False, compare=False
    )
    _streaming_view_listeners: dict[str, list[Callable[[], Any]]] = field(
        default_factory=dict, repr=False, compare=False
    )
    _selected_diff_path: str | None = field(default=None, repr=False, compare=False)
    changed_files: list[ChangedFile] = field(default_factory=list)
    _selected_artifact_id: str | None = field(default=None, repr=False, compare=False)
    artifacts: list[Artifact] = field(default_factory=list)
    _config_service: ConfigService = field(
        default_factory=ConfigService, repr=False, compare=False
    )
    _show_app_settings: bool = False
    _show_rename_dialog: bool = False
    _preview_file: Path | None = field(default=None, repr=False, compare=False)
    _show_workspace_settings: bool = False
    _show_login: bool = False
    _auth_service: AuthService = field(
        default_factory=AuthService, repr=False, compare=False
    )
    current_user: User | None = field(default=None, repr=False, compare=False)
    current_view: str = "chat"
    sidebar_open: bool = True
    review_open: bool = False
    active_canvas_tab: str = "review"
    inspected_sub_tome_id: str | None = None
    inspected_sub_tome_title: str | None = None
    canvas_docked: bool = True
    mvge_status: str = "idle"
    chat_side_panel: str | None = None
    _sidebar_width: int = 260
    _review_width: int = 320
    _command_palette_open: bool = False
    _expanded_cards: set[str] = field(default_factory=set, repr=False, compare=False)
    _collapsed_cards: set[str] = field(default_factory=set, repr=False, compare=False)
    # Approval queue (Approval Rune presenter). AppState owns the queue: the
    # active modal resolves one future at a time, and parallel batches are
    # presented strictly one after another.
    _approval_queue: ApprovalQueue = field(
        default_factory=ApprovalQueue, repr=False, compare=False
    )
    # The GUI presenter bound to the engine's presenter slot (set by the
    # host at startup via bind_approval_presenter). None until bound.
    _approval_presenter: Any = field(default=None, repr=False, compare=False)
    # Persistent chat badge: True while session approve-all is active.
    approval_session_approve_all: bool = False
    # Deep-link flag: the approval popup's "manage permissions" link sets
    # this; the overlay opens the rune's settings dialog directly.
    _approval_settings_requested: bool = False
    # Approval popup dialog step: "main" or "confirm" (persistent-grant
    # confirmation screen). Reset whenever the active request changes.
    _approval_dialog_step: str = "main"
    # Which persistent grant the confirmation screen is confirming:
    # "spell-allow" | "spell-deny" | "session" | "project".
    _approval_confirm_kind: str = ""

    def __getattr__(self, name: str) -> Any:
        """Fall through to the owning ServerState for shared fields.

        Only fires when normal lookup fails, so per-client fields and
        methods are never affected. Names outside _SHARED_FIELDS raise
        AttributeError as usual.
        """
        if name in _SHARED_FIELDS:
            return getattr(self._ensure_server(), name)
        raise AttributeError(f"{type(self).__name__} has no attribute {name!r}")

    def __setattr__(self, name: str, value: Any) -> None:
        """Write shared fields through to the owning ServerState.

        While the dataclass __init__ is still running there is no server
        yet: shared values are buffered and the server is constructed from
        them in __post_init__, so constructor kwargs seed the server
        instead of being clobbered by its defaults.
        """
        if name in _SHARED_FIELDS:
            if self.__dict__.get("_server") is None:
                self.__dict__.setdefault("_pending_shared", {})[name] = value
            else:
                server = self._ensure_server()
                setattr(server, name, value)
                # Shared configuration changed: every connected client
                # sees the same value, so refresh them all where visible.
                server._notify_clients()
            return
        object.__setattr__(self, name, value)

    def _ensure_server(self) -> ServerState:
        """Return the owning ServerState, building a private one if needed."""
        server = self.__dict__.get("_server")
        if server is None:
            pending = self.__dict__.pop("_pending_shared", {})
            server = ServerState(**pending)
            self.__dict__["_server"] = server
        return server

    def __post_init__(self) -> None:
        """Build the backing server from any buffered shared kwargs."""
        self._ensure_server()

    def subscribe(self, listener: Callable[[], Any]) -> None:
        """Subscribe a listener callback to state changes."""
        if listener not in self._change_listeners:
            self._change_listeners.append(listener)

    def unsubscribe(self, listener: Callable[[], Any]) -> None:
        """Unsubscribe a listener callback from state changes."""
        if listener in self._change_listeners:
            self._change_listeners.remove(listener)

    def clear_listeners(self) -> None:
        """Clear all registered change listeners."""
        self._change_listeners.clear()

    def notify(self) -> None:
        """Notify all change listeners."""
        for listener in list(self._change_listeners):
            with contextlib.suppress(Exception):
                result = listener()
                if inspect.iscoroutine(result):
                    coro = cast(Coroutine[Any, Any, None], result)
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(coro)
                    except RuntimeError:
                        coro.close()

    def subscribe_streaming(self, listener: Callable[[], Any]) -> None:
        """Subscribe a listener callback to streaming token updates."""
        if listener not in self._streaming_listeners:
            self._streaming_listeners.append(listener)

    def unsubscribe_streaming(self, listener: Callable[[], Any]) -> None:
        """Unsubscribe a listener callback from streaming token updates."""
        if listener in self._streaming_listeners:
            self._streaming_listeners.remove(listener)

    def subscribe_view(self, key: str, listener: Callable[[], Any]) -> None:
        """Subscribe a view-scoped listener tracked under *key*.

        View listeners render a specific panel; they are dropped via
        :meth:`clear_view_listeners` when the panel re-renders so stale
        refreshables on deleted elements never accumulate.
        """
        tracked = self._view_listeners.setdefault(key, [])
        if listener not in tracked:
            tracked.append(listener)
        self.subscribe(listener)

    def subscribe_streaming_view(self, key: str, listener: Callable[[], Any]) -> None:
        """Subscribe a streaming view-scoped listener tracked under *key*."""
        tracked = self._streaming_view_listeners.setdefault(key, [])
        if listener not in tracked:
            tracked.append(listener)
        self.subscribe_streaming(listener)

    def clear_view_listeners(self, key: str) -> None:
        """Detach all view-scoped listeners tracked under *key*."""
        for listener in self._view_listeners.pop(key, []):
            self.unsubscribe(listener)
        for listener in self._streaming_view_listeners.pop(key, []):
            self.unsubscribe_streaming(listener)

    def clear_all_view_listeners(self) -> None:
        """Detach every view-scoped listener from all keys.

        Call at the top of a panel render: the previous render pass owned
        the tracked listeners, and its elements are about to be replaced.
        """
        for key in list(self._view_listeners):
            self.clear_view_listeners(key)
        for key in list(self._streaming_view_listeners):
            self.clear_view_listeners(key)
        autocomplete = self._autocomplete_service
        if autocomplete is not None:
            autocomplete.clear_items_changed_listeners()

    def view_listener_count(self, key: str | None = None) -> int:
        """Return the number of tracked view-scoped listeners."""
        if key is not None:
            return len(self._view_listeners.get(key, [])) + len(
                self._streaming_view_listeners.get(key, [])
            )
        total = sum(len(v) for v in self._view_listeners.values())
        total += sum(len(v) for v in self._streaming_view_listeners.values())
        autocomplete = self._autocomplete_service
        if autocomplete is not None:
            total += autocomplete.items_changed_listener_count
        return total

    def notify_streaming(self) -> None:
        """Notify streaming listeners of rapid token updates.

        If streaming listeners are registered (e.g. active streaming bubble),
        notify only them to prevent whole-thread DOM re-renders. If no streaming
        listeners are registered (e.g. headless tests), fall back to general
        change listeners.
        """
        targets = (
            self._streaming_listeners
            if self._streaming_listeners
            else self._change_listeners
        )
        for listener in list(targets):
            with contextlib.suppress(Exception):
                result = listener()
                if inspect.iscoroutine(result):
                    coro = cast(Coroutine[Any, Any, None], result)
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(coro)
                    except RuntimeError:
                        coro.close()

    def get_agent_service(self) -> AgentService:
        """Retrieve or initialize the active AgentService instance."""
        if self.agent_service is None:
            self.agent_service = AgentService(
                project_path=self.project_path,
                api_key=self.api_key,
            )
        return self.agent_service

    def reset_agent(self) -> None:
        """Reset the cached agent in AgentService if one exists.

        The discarded agent is closed (stopping its rune watchers) rather
        than merely dereferenced, so a replaced agent never keeps firing
        reloads or holding its tome open.
        """
        if self.agent_service is not None:
            self.agent_service.close_agent_in_background()

    def get_autocomplete_service(self) -> AutocompleteService:
        """Retrieve or initialize the AutocompleteService for this session.

        The service (popup mode, selection) is per-client, but the
        MentionIndex it queries is server-shared: one index per server,
        refreshed when the project tree changes. Rune slash commands are
        read from installed, enabled rune manifests so autocomplete and
        the command dispatcher agree on what exists.
        """
        if self._autocomplete_service is None:
            command_registry = SlashCommandRegistry(
                rune_commands=self._rune_slash_commands()
            )
            self._autocomplete_service = AutocompleteService(
                self._ensure_server().mention_index, command_registry
            )
        return self._autocomplete_service

    @staticmethod
    def _rune_slash_commands() -> list[SlashCommandItem]:
        """Build autocomplete items from installed, enabled rune manifests.

        Disabled runes contribute nothing. A rune command that collides
        with an engine-owned CLI command is dropped so it can never
        shadow it (mirrors the dispatcher: e.g. /reload stays engine).
        First claimant wins across runes.
        """
        try:
            installed = list_installed_runes()
        except Exception:
            return []
        cli_names = set(SlashCommandRegistry.CLI_SLASH_COMMANDS)
        seen: set[str] = set()
        items: list[SlashCommandItem] = []
        for rune in installed:
            if not rune.get("enabled", True):
                continue
            description = str(rune.get("description", "") or "")
            commands = rune.get("commands", [])
            if not isinstance(commands, list):
                continue
            for command in commands:
                if not isinstance(command, str) or not command:
                    continue
                name = "/" + command.lstrip("/")
                if name in cli_names or name in seen:
                    continue
                seen.add(name)
                items.append(
                    SlashCommandItem(
                        kind=CommandKind.RUNE,
                        name=name,
                        description=description or f"Run extension command {command}",
                        value=name,
                    )
                )
        return items

    def _drop_autocomplete_cache(self) -> None:
        """Drop cached autocomplete services so rune commands rebuild.

        Rune install/uninstall/enable/disable changes what the registry
        should offer; the next keystroke rebuilds it. Propagates to other
        connected clients the same way project changes do.
        """
        self._autocomplete_service = None
        server = self.__dict__.get("_server")
        if server is not None:
            for client in server.client_states:
                if client is not self:
                    client._autocomplete_service = None

    def load_skills(self) -> list[SkillManifest]:
        """Load skill manifests from project and user skill directories."""
        return _scan_skill_manifests(self.project_path)

    @staticmethod
    def skill_info_from_manifest(manifest: SkillManifest) -> SkillInfo:
        """Convert a SkillManifest into the inspector-friendly SkillInfo shape."""
        return SkillInfo(
            name=manifest.name,
            description=manifest.description,
            scope=manifest.scope.value if manifest.scope else "",
            path=manifest.path,
        )

    def add_skill(self, manifest: SkillManifest) -> bool:
        """Add a skill to active_skills if not already present.

        Returns True when the skill was newly added (state changed).
        """
        info = self.skill_info_from_manifest(manifest)
        for existing in self.active_skills:
            if existing.name == info.name:
                return False
        self.active_skills.append(info)
        self.notify()
        return True

    def remove_skill(self, name: str) -> bool:
        """Remove a skill from active_skills by name.

        Returns True when a skill was actually removed.
        """
        for index, existing in enumerate(self.active_skills):
            if existing.name == name:
                del self.active_skills[index]
                self.notify()
                return True
        return False

    def clear_skills(self) -> None:
        """Remove all tracked skills."""
        if self.active_skills:
            self.active_skills.clear()
            self.notify()

    def add_attachment(self, name: str, content: bytes | None = None) -> None:
        """Add a file to the pending attachments bound to next submission."""
        if name and name not in self.pending_attachments:
            self.pending_attachments.append(name)
            if content is not None:
                self.pending_attachment_contents[name] = content
            self.notify()

    def remove_attachment(self, index: int) -> None:
        """Remove a pending attachment by index (safe no-op if out of range)."""
        if 0 <= index < len(self.pending_attachments):
            name = self.pending_attachments.pop(index)
            self.pending_attachment_contents.pop(name, None)
            self.notify()

    def add_mention(self, chip: MentionChip) -> None:
        """Add a mention chip to the current input."""
        self.selected_mentions.append(chip)
        self.notify()

    def remove_selected_mention(self, index: int) -> None:
        """Remove a selected mention chip by index."""
        if 0 <= index < len(self.selected_mentions):
            del self.selected_mentions[index]
            self.notify()

    def remove_last_mention(self) -> None:
        """Remove the most recently added mention chip."""
        if self.selected_mentions:
            self.selected_mentions.pop()
            self.notify()

    def clear_mentions(self) -> None:
        """Remove all mention chips."""
        if self.selected_mentions:
            self.selected_mentions.clear()
            self.notify()

    def clear_selected_mentions(self) -> None:
        """Remove all selected mention chips."""
        self.clear_mentions()

    def clear_attachments(self) -> None:
        """Remove all pending attachments."""
        if self.pending_attachments:
            self.pending_attachments.clear()
            self.pending_attachment_contents.clear()
            self.notify()

    def add_background_task(
        self,
        task_id: str,
        name: str,
        *,
        parent_id: str | None = None,
        progress: float = 0.0,
    ) -> BackgroundTask:
        """Register a new background task (idempotent on duplicate id)."""
        existing = self.get_background_task(task_id)
        if existing is not None:
            return existing
        task = BackgroundTask(
            id=task_id,
            name=name,
            parent_id=parent_id,
            progress=progress,
        )
        self.background_tasks.append(task)
        self.notify()
        return task

    def update_background_task(
        self,
        task_id: str,
        *,
        status: TaskStatus | str | None = None,
        progress: float | None = None,
        result: str | None = None,
        error: str | None = None,
    ) -> BackgroundTask | None:
        """Update fields on an existing background task (no-op if unknown)."""
        task = self.get_background_task(task_id)
        if task is None:
            return None
        if status is not None:
            task.status = TaskStatus(status)
        if progress is not None:
            task.progress = progress
        if result is not None:
            task.result = result
        if error is not None:
            task.error = error
        if task.status in (TaskStatus.COMPLETE, TaskStatus.ERROR):
            task.ended_at = time.monotonic()
        self.notify()
        return task

    def get_background_task(self, task_id: str) -> BackgroundTask | None:
        """Look up a background task by id."""
        for task in self.background_tasks:
            if task.id == task_id:
                return task
        return None

    def toggle_inspector(self) -> None:
        """Toggle right context inspector visibility."""
        self.inspector_expanded = not self.inspector_expanded
        self.notify()

    def open_app_settings(self) -> None:
        """Open the Application Settings modal."""
        self._show_app_settings = True
        self._show_workspace_settings = False
        self.notify()

    def close_app_settings(self) -> None:
        """Close the Application Settings modal."""
        self._show_app_settings = False
        self.notify()

    def open_rename_dialog(self) -> None:
        """Open the Rename session dialog."""
        self._show_rename_dialog = True
        self.notify()

    @property
    def preview_file(self) -> Path | None:
        """File currently shown in the in-browser preview dialog, if any."""
        return self._preview_file

    def open_file_preview(self, path: Path) -> None:
        """Preview a workspace file in the browser.

        The file tree calls this instead of spawning a server-side editor:
        a browser session has no use for an ``$EDITOR`` process on the
        server, so the file opens read-only inside the GUI.
        """
        self._preview_file = path
        self.notify()

    def close_file_preview(self) -> None:
        """Close the file preview dialog."""
        self._preview_file = None
        self.notify()

    def close_rename_dialog(self) -> None:
        """Close the Rename session dialog."""
        self._show_rename_dialog = False
        self.notify()

    def open_workspace_settings(self) -> None:
        """Open the Project Workspace Settings modal."""
        self._show_workspace_settings = True
        self._show_app_settings = False
        self.notify()

    def close_workspace_settings(self) -> None:
        """Close the Project Workspace Settings modal."""
        self._show_workspace_settings = False
        self.notify()

    @property
    def command_palette_open(self) -> bool:
        """Whether the command palette is visible."""
        return self._command_palette_open

    def set_command_palette_open(self, open: bool) -> None:
        """Set command palette visibility."""
        self._command_palette_open = open
        self.notify()

    def toggle_command_palette(self) -> None:
        """Toggle command palette visibility."""
        self._command_palette_open = not self._command_palette_open
        self.notify()

    def set_project(self, path: Path) -> None:
        """Change the active workspace project path.

        The project is server-global: every connected client's
        project-bound caches (agent service, autocomplete) are dropped,
        their pending approvals are denied, and their tome lists are
        reloaded for the new project.
        """
        self._on_approval_context_change()
        self.project_path = path
        # The project changed: drop the server-shared @-mention index so it
        # rebuilds bound to the new path on next query. Per-client
        # autocomplete services are dropped in _invalidate_project_caches
        # and pick up the fresh index when recreated.
        self._ensure_server().refresh_mention_index()
        self._invalidate_project_caches()
        self.add_recent_project(path)
        server = self.__dict__.get("_server")
        if server is not None:
            for client in server.client_states:
                if client is not self:
                    client._on_approval_context_change()
                    client._invalidate_project_caches()
        self.notify()

    def _invalidate_project_caches(self) -> None:
        """Drop project-bound caches and reload the tome list."""
        self.agent_service = None
        self._autocomplete_service = None
        self.clear_attachments()
        self.clear_mentions()
        self.load_tomes()

    def add_recent_project(self, path: Path) -> None:
        """Add or move a project path to the front of recent projects."""
        if path in self.recent_projects:
            self.recent_projects.remove(path)
        self.recent_projects.insert(0, path)
        if len(self.recent_projects) > 10:
            self.recent_projects = self.recent_projects[:10]
        self.notify()

    def new_conversation(self) -> None:
        """Reset conversation session to empty new state."""
        # A new session ends the old one: pending approvals die with it.
        self._on_approval_context_change()
        self.active_tome_id = None
        self.tome_title = "New Conversation"
        self.is_channeling = False
        self.channeling_started_at = None
        self.messages = []
        self.total_mana_used = 0
        self.reset_context_usage()
        self.pending_attachments.clear()
        self.pending_attachment_contents.clear()
        self.selected_mentions.clear()
        self.background_tasks.clear()
        self.clear_skills()
        # Drop the cached agent: it is bound to the old tome and would
        # otherwise resume it on the next send instead of starting fresh.
        self.reset_agent()
        self.load_tomes()
        # No active session: clear the persisted id so a page reload does
        # not resurrect the ended conversation.
        self.persist_active_tome()

    def record_context_usage(self, input_tokens: int, output_tokens: int) -> None:
        """Record the latest provider-reported token usage for the gauge."""
        self.context_input_tokens = input_tokens
        self.context_output_tokens = output_tokens
        self.notify()

    def reset_context_usage(self) -> None:
        """Clear recorded token usage; the gauge hides until new usage arrives."""
        self.context_input_tokens = None
        self.context_output_tokens = None

    def get_verified_context_window(self) -> int | None:
        """Return the selected model's context window, or None if unverified.

        Only a registry entry with a real context window counts. The registry
        silently substitutes 4096 when an entry has no ``context_length``
        (see model_registry.py), so exactly 4096 is treated as "unknown"
        rather than displayed as fact.
        """
        try:
            reg = get_default_realm_registry()
            model = reg.model_registry.get(self.selected_model)
        except Exception:
            return None
        if model is None:
            return None
        if model.context_window <= 0 or model.context_window == 4096:
            return None
        return model.context_window

    def load_tomes(self) -> None:
        """Load and index all Tomes for the active project workspace."""
        self.loaded_tomes = self.tome_service.list_tomes_for_project(
            self.project_path, active_tome_id=self.active_tome_id
        )
        self.notify()

    def load_messages_for_tome(self, tome_id: str) -> None:
        """Load existing messages from a Tome's JSONL transcript."""
        try:
            entries = self.tome_service.factory.get_entries(tome_id)
        except (FileNotFoundError, TomeVersionError, ValueError, KeyError):
            self.messages = []
            return

        reconstructed: list[ChatMessage] = []
        for entry in entries:
            if entry.type == TomeEntryType.MESSAGE:
                payload = entry.payload
                role = str(payload.get("role", "assistant"))
                if role in ("user", "assistant"):
                    transcript = InvocationTranscript.from_tome_content(
                        payload.get("content", ""),
                        role=role,
                        model=payload.get("model"),
                    )
                    reconstructed.append(transcript.message)
        self.messages = reconstructed
        self.notify()

    def switch_to_tome(self, tome_id: str) -> None:
        """Load a Tome session and switch the active conversation."""
        try:
            meta = self.tome_service.factory.open_tome(tome_id)
        except TomeVersionError:
            return
        if meta is None:
            return
        # A tome created in another workspace must never become the active
        # session here. restore_active_tome() delegates to this method, and
        # a stale browser-storage id can name a foreign tome. The same
        # normalization as TomeService.list_tomes_for_project applies.
        tome_cwd = os.path.normcase(os.path.normpath(meta.cwd))
        project = os.path.normcase(os.path.normpath(str(self.project_path)))
        if tome_cwd != project:
            return
        # The tome is changing: deny pending approvals before the state
        # change completes.
        self._on_approval_context_change()
        self.active_tome_id = meta.id
        self.tome_title = self.tome_service.get_tome_title(meta.id)
        self.is_channeling = False
        self.channeling_started_at = None
        self.reset_context_usage()
        # Drop the cached agent: it is bound to the previous tome and would
        # otherwise keep running against the wrong session.
        self.reset_agent()
        self.load_messages_for_tome(meta.id)
        self.load_tomes()
        self.persist_active_tome()

    @property
    def project_display_name(self) -> str:
        """Human name for the workspace: persisted name or directory name."""
        try:
            name = self._config_service.load_workspace_settings(
                self.project_path
            ).project_name
        except Exception:
            name = ""
        return name.strip() or self.project_path.name or str(self.project_path)

    def persist_active_tome(self) -> None:
        """Remember the active session id in per-browser storage.

        A fresh page load restores it via :meth:`restore_active_tome`.
        Best-effort: storage failures are ignored.
        """
        with contextlib.suppress(Exception):
            nicegui_app.storage.user[_ACTIVE_TOME_STORAGE_KEY] = self.active_tome_id

    def restore_active_tome(self) -> bool:
        """Re-select the session persisted by an earlier page load.

        Returns True when a stored session id still resolves to a tome.
        Best-effort: unknown ids and storage errors restore nothing.
        """
        try:
            stored = nicegui_app.storage.user.get(_ACTIVE_TOME_STORAGE_KEY)
        except Exception:
            return False
        if not isinstance(stored, str) or not stored:
            return False
        try:
            self.switch_to_tome(stored)
        except Exception:
            return False
        return self.active_tome_id == stored

    def get_realms(self) -> list[str]:
        """Return available realm identifiers.

        Every Realm the engine ships a Rune for, plus any a loaded Rune
        registered under. Anchoring on that table rather than on a literal is
        what lets a Realm be selectable before its Rune is installed, which is
        the state every fresh install is in -- and it is the state the default
        Realm has to be selectable in, or the selector opens on a value its own
        options do not contain.
        """
        try:
            realms = list(REALM_RUNES)
            for r in get_default_realm_registry().get_registered_realm_factories():
                if r not in realms:
                    realms.append(r)
            return realms
        except Exception:
            return [DEFAULT_REALM]

    def is_router_realm(self, realm: str | None = None) -> bool:
        """Return True if the realm is a router requiring provider selection."""
        target_realm = realm or self.selected_realm
        try:
            return is_realm_router(target_realm)
        except Exception:
            return target_realm.lower() == "openrouter"

    def get_providers_for_selected_realm(self) -> list[str]:
        """Return list of providers available for the current realm."""
        if not self.is_router_realm(self.selected_realm):
            return []
        try:
            providers = get_providers_for_realm(self.selected_realm)
            if not providers and self.selected_realm == "openrouter":
                providers = [
                    "anthropic",
                    "google",
                    "meta-llama",
                    "mistralai",
                    "nvidia",
                    "openai",
                    "qwen",
                ]
            return providers
        except Exception:
            return ["nvidia"]

    def get_models_for_selection(self) -> list[str]:
        """Return model IDs matching the current realm and provider selection."""
        try:
            if self.is_router_realm(self.selected_realm):
                if self.selected_provider:
                    models = get_models_for_provider(
                        self.selected_provider, self.selected_realm
                    )
                    if models:
                        return [m.id for m in models]
                    return [self.selected_model]
            else:
                reg = get_default_realm_registry()
                all_models = reg.model_registry.list_all()
                matching = [
                    m.id
                    for m in all_models
                    if (
                        m.realm == self.selected_realm
                        or m.provider_prefix == self.selected_realm
                    )
                ]
                if matching:
                    return matching
                return [f"{self.selected_realm}/default"]
        except Exception:
            pass
        return [self.selected_model]

    def get_model_options_for_selection(self) -> dict[str, str]:
        """Return dict of model IDs to display names for current selection.

        For router realms, repeated provider prefixes followed by ': ' are stripped.
        Direct providers retain their model names without modification.
        """
        all_options = get_model_options()
        model_ids = self.get_models_for_selection()
        model_options = {mid: all_options.get(mid, mid) for mid in model_ids}
        if self.selected_model not in model_options:
            model_options[self.selected_model] = all_options.get(
                self.selected_model, self.selected_model
            )
        if self.is_router_realm(self.selected_realm):
            model_options = {
                mid: (name.split(": ", 1)[1] if ": " in name else name)
                for mid, name in model_options.items()
            }
        return model_options

    def get_contemplation_levels_for_selected_model(self) -> list[str]:
        """Return contemplation levels supported by the selected model."""
        try:
            levels = get_supported_contemplation_levels(self.selected_model)
            if levels:
                return levels
            reg = get_default_realm_registry()
            model = reg.model_registry.get(self.selected_model)
            if model is not None:
                if model.supported_contemplation_levels:
                    return list(model.supported_contemplation_levels)
                if model.supports_contemplation:
                    return ["none", "low", "medium", "high", "x-high"]
        except Exception:
            pass
        return []

    def supports_contemplation_for_selected_model(self) -> bool:
        """Return True if the selected model supports contemplation / reasoning."""
        levels = self.get_contemplation_levels_for_selected_model()
        if levels:
            return True
        try:
            reg = get_default_realm_registry()
            model = reg.model_registry.get(self.selected_model)
            return bool(model.supports_contemplation) if model is not None else False
        except Exception:
            return False

    def _cascade_contemplation(self) -> None:
        """Ensure contemplation level matches supported levels if any."""
        if self.supports_contemplation_for_selected_model():
            levels = self.get_contemplation_levels_for_selected_model()
            if levels and self.contemplation_level not in levels:
                self.contemplation_level = "medium" if "medium" in levels else levels[0]

    def switch_realm(self, realm: str) -> None:
        """Switch realm and cascade provider, model, and contemplation."""
        self.selected_realm = realm
        if self.is_router_realm(realm):
            providers = self.get_providers_for_selected_realm()
            if not self.selected_provider or self.selected_provider not in providers:
                self.selected_provider = providers[0] if providers else None
        else:
            self.selected_provider = None

        models = self.get_models_for_selection()
        if self.selected_model not in models:
            self.selected_model = models[0] if models else self.selected_model

        self._cascade_contemplation()
        self.notify()

    def switch_provider(self, provider: str | None) -> None:
        """Switch provider and cascade model and contemplation."""
        self.selected_provider = provider
        models = self.get_models_for_selection()
        if self.selected_model not in models:
            self.selected_model = models[0] if models else self.selected_model

        self._cascade_contemplation()
        self.notify()

    def switch_model(self, model_id: str) -> None:
        """Switch the selected Realm model and cascade contemplation."""
        self.selected_model = model_id
        if "/" in model_id and self.is_router_realm(self.selected_realm):
            prefix = model_id.split("/")[0]
            providers = self.get_providers_for_selected_realm()
            if prefix in providers:
                self.selected_provider = prefix

        self._cascade_contemplation()
        self.notify()

    def set_contemplation_level(self, level: str) -> None:
        """Set contemplation level for the active session."""
        self.contemplation_level = level
        self.notify()

    def set_message_feedback(self, index: int, feedback: str | None) -> None:
        """Set or toggle feedback (thumbs up / thumbs down) on a message."""
        if 0 <= index < len(self.messages):
            msg = self.messages[index]
            if msg.feedback == feedback:
                msg.feedback = None
            else:
                msg.feedback = feedback
            self.notify()

    def submit_prompt(self, prompt: str) -> None:
        """Submit a new user prompt and start channeling Mvge response."""
        if self.is_channeling:
            return

        mention_text = " ".join(chip.text for chip in self.selected_mentions)
        text = prompt.strip()
        if not mention_text and not text and not self.pending_attachments:
            return

        if mention_text:
            text = f"{mention_text} {text}" if text else mention_text
        self.selected_mentions.clear()

        attachments = list(self.pending_attachments)
        contents = {
            name: self.pending_attachment_contents.pop(name, b"")
            for name in attachments
        }
        self.pending_attachments.clear()
        content = build_content_parts(
            text,
            [Attachment(filename=name, data=contents[name]) for name in attachments],
        )
        user_msg = InvocationTranscript.for_summoner(text, attachments)
        self.messages.append(user_msg)

        # Append assistant response bubble
        assistant_msg = ChatMessage(
            role="assistant",
            model=self.selected_model,
            is_streaming=True,
        )
        self.messages.append(assistant_msg)
        self.is_channeling = True
        self.channeling_started_at = time.monotonic()
        self.mvge_status = "channeling"
        self.notify()

        # Start agent task
        service = self.get_agent_service()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(
                "submit_prompt called without a running event loop — prompt dropped"
            )
            self.messages.pop()
            self.is_channeling = False
            self.channeling_started_at = None
            self.mvge_status = "idle"
            self.notify()
            return
        if loop is not None:
            task = asyncio.ensure_future(
                service.run_prompt(content, self, assistant_msg)
            )

            def _on_done(future: asyncio.Task[Any]) -> None:
                with contextlib.suppress(asyncio.CancelledError):
                    exc = future.exception()
                    if exc is not None:
                        assistant_msg.is_error = True
                        assistant_msg.error_message = str(exc)
                        assistant_msg.is_streaming = False
                        self.is_channeling = False
                        self.channeling_started_at = None
                        self.mvge_status = "idle"
                        self.notify()

            task.add_done_callback(_on_done)
            self.active_task = task

    def stop_channeling(self) -> None:
        """Cancel and halt active agent channeling."""
        if self.active_task is not None:
            with contextlib.suppress(Exception):
                self.active_task.cancel()
        if self.agent_service is not None:
            self.agent_service.cancel()
        # An aborted agent cannot receive decisions: deny pending approvals.
        self.cancel_pending_approvals()

        for msg in self.messages:
            if msg.is_streaming:
                msg.is_streaming = False

        self.is_channeling = False
        self.channeling_started_at = None
        self.mvge_status = "idle"
        self.notify()

    def steer_prompt(self, text: str) -> None:
        """Inject mid-flight steering guidance into the actively channeling Mvge."""
        stripped = text.strip()
        if not stripped:
            return
        service = self.get_agent_service()
        service.steer(stripped)
        steering_msg = InvocationTranscript.for_steering(stripped)
        self.messages.append(steering_msg)
        self.notify()

    def queue_follow_up(self, text: str) -> None:
        """Queue a follow-up prompt for execution when the active run settles."""
        stripped = text.strip()
        if not stripped:
            return
        self.queued_follow_ups.append(stripped)
        service = self.get_agent_service()
        service.follow_up(stripped)
        self.notify()

    def remove_follow_up(self, index: int) -> None:
        """Remove a queued follow-up by index."""
        if 0 <= index < len(self.queued_follow_ups):
            self.queued_follow_ups.pop(index)
            self.notify()

    def elapsed_channeling_seconds(self) -> float | None:
        """Seconds since channeling started, or None when not channeling."""
        if not self.is_channeling or self.channeling_started_at is None:
            return None
        return max(0.0, time.monotonic() - self.channeling_started_at)

    def fork_tome(self) -> str | None:
        """Fork the active Tome and switch to the new branch."""
        if self.active_tome_id is None:
            return None
        parent_title = self.tome_service.get_tome_title(self.active_tome_id)
        factory = self.tome_service.factory
        leaf_id = factory.get_leaf_id(self.active_tome_id)
        if leaf_id is None:
            return None
        forked = factory.create_branched_tome(
            parent_tome_id=self.active_tome_id,
            cwd=str(self.project_path),
            fork_from_leaf_id=leaf_id,
        )
        self.switch_to_tome(forked.tome_id)
        # The engine fork copies the message chain up to the leaf, which
        # drops the parent_id-less TOME_INFO title entry. Re-apply the
        # parent's title so the branch keeps its name.
        if self.tome_title != parent_title:
            self.rename_tome(parent_title)
        return forked.tome_id

    def export_tome(self) -> Path | None:
        """Export the active Tome's JSONL transcript to the project directory."""
        if self.active_tome_id is None:
            return None
        src = self.tome_service.factory.tome_file(self.active_tome_id)
        if not src.exists():
            return None
        dest = self.project_path / f"{self.active_tome_id[:8]}.jsonl"
        shutil.copy2(src, dest)
        return dest

    def rename_tome(self, new_title: str) -> bool:
        """Rename the active Tome by appending a TOME_INFO title entry.

        get_tome_title() reads entries newest-first, so the latest rename
        always wins. Returns False when there is no active Tome, the title
        is blank, or the Tome file cannot be written.
        """
        title = new_title.strip()
        if self.active_tome_id is None or not title:
            return False
        entry = TomeEntry(
            id=uuid.uuid4().hex[:12],
            parent_id=None,
            type=TomeEntryType.TOME_INFO,
            timestamp=time.time(),
            payload={"title": title},
        )
        try:
            self.tome_service.factory.open_write(self.active_tome_id).append(entry)
        except (FileNotFoundError, ValueError, RuntimeError):
            return False
        self.tome_title = title
        self.load_tomes()
        return True

    def clear_history(self) -> None:
        """Clear the current conversation history."""
        self.new_conversation()

    def refresh_changed_files(self) -> None:
        """Query git for changed files and update reactive state."""
        self.changed_files = get_changed_files(self.project_path)
        self.notify()

    def open_diff_review(self, path: str) -> None:
        """Open the Diff Review modal for the given file path."""
        self._selected_diff_path = path
        self.notify()

    def get_selected_diff_view(self) -> Any | None:
        """Return the DiffView for the currently selected diff file."""
        if not self._selected_diff_path:
            return None
        return get_diff_for_file(self.project_path, self._selected_diff_path)

    def clear_diff_selection(self) -> None:
        """Clear the selected diff path."""
        self._selected_diff_path = None
        self.notify()

    def add_artifact(self, artifact: Artifact) -> None:
        """Add an artifact to the session and notify listeners."""
        self.artifacts.append(artifact)
        self.notify()

    def get_artifact(self, artifact_id: str) -> Artifact | None:
        """Look up an artifact by id."""
        for artifact in self.artifacts:
            if artifact.id == artifact_id:
                return artifact
        return None

    def open_artifact(self, artifact_id: str) -> None:
        """Select an artifact for preview in the drawer/canvas."""
        if self.get_artifact(artifact_id) is not None:
            self._selected_artifact_id = artifact_id
            self.active_canvas_tab = "artifact"
            self.review_open = True
            self.notify()

    def close_artifact(self) -> None:
        """Clear the selected artifact preview."""
        self._selected_artifact_id = None
        if self.active_canvas_tab == "artifact":
            self.active_canvas_tab = "review"
        self.notify()

    def inspect_sub_tome(self, child_tome_id: str, title: str | None = None) -> None:
        """Project a child Mvge's Tome into the side Canvas."""
        self.inspected_sub_tome_id = child_tome_id
        self.inspected_sub_tome_title = title or "Sub-Mvge"
        self.active_canvas_tab = "sub_mvge"
        self.review_open = True
        self.notify()

    def close_sub_tome(self) -> None:
        """Clear the inspected child Mvge Tome from the Canvas."""
        self.inspected_sub_tome_id = None
        self.inspected_sub_tome_title = None
        if self.active_canvas_tab == "sub_mvge":
            self.active_canvas_tab = "review"
        self.notify()

    def set_canvas_tab(self, tab: str) -> None:
        """Switch active canvas tab ('review', 'artifact', 'sub_mvge')."""
        self.active_canvas_tab = tab
        self.notify()

    def toggle_canvas_docked(self) -> None:
        """Toggle Canvas between docked split-pane and floating drawer."""
        self.canvas_docked = not self.canvas_docked
        self.notify()

    def get_selected_artifact(self) -> Artifact | None:
        """Return the currently selected artifact for preview."""
        if not self._selected_artifact_id:
            return None
        return self.get_artifact(self._selected_artifact_id)

    def set_current_view(self, view: str) -> None:
        """Switch the main content view."""
        self.current_view = view
        self.notify()

    def toggle_sidebar(self) -> None:
        """Toggle left sidebar collapsed state.

        ``sidebar_open`` is the single source of truth: the chevron
        reflects it and this toggle flips it. The per-browser cookie only
        persists the choice across sessions (seeded once in
        ``app.index_page``); it is never read back to derive the rendered
        state, so a stale cookie can no longer make a click a no-op.
        """
        self.sidebar_open = not self.sidebar_open
        with contextlib.suppress(Exception):
            nicegui_app.storage.user["sidebar-collapsed"] = not self.sidebar_open
        self.notify()

    def toggle_review(self) -> None:
        """Toggle right review rail / canvas visibility."""
        self.review_open = not self.review_open
        if self.review_open:
            self.chat_side_panel = None
        self.notify()

    def toggle_canvas_files(self) -> None:
        """Toggle Canvas workspace files surface."""
        if self.review_open and self.active_canvas_tab == "files":
            self.review_open = False
        else:
            self.active_canvas_tab = "files"
            self.review_open = True
            self.chat_side_panel = None
        self.notify()

    def set_plan_mode(self, enabled: bool) -> list[str] | None:
        """Enable or disable plan mode (read-only spells only).

        Drives the engine agent's plan-mode filter, then mirrors the flag
        locally. Returns the spell names still active after the toggle so
        callers can warn when plan mode leaves the agent without tools, or
        None when the toggle was refused: enabling plan mode needs an
        engine agent, which cannot be created without an API key. A
        refusal never raises and leaves plan mode unchanged. Disabling
        with no agent is a silent no-op.
        """
        service = self.get_agent_service()
        if not service.can_create_agent():
            if enabled:
                logger.warning("Plan mode toggle refused: no API key configured")
                return None
            self.plan_mode = False
            self.notify()
            return []
        agent = service.get_or_create_agent(self)
        agent.set_plan_mode(enabled)
        self.plan_mode = enabled
        self.notify()
        return list(agent.enabled_spells)

    def toggle_plan_mode(self) -> list[str] | None:
        """Flip plan mode. Returns the spell names active after the toggle,
        or None when the toggle was refused for a missing API key."""
        return self.set_plan_mode(not self.plan_mode)

    def show_login(self) -> None:
        """Open the login dialog."""
        self._show_login = True
        self.notify()

    def hide_login(self) -> None:
        """Close the login dialog."""
        self._show_login = False
        self.notify()

    def set_mvge_status(self, status: str) -> None:
        """Update the Mvge activity status (idle, channeling, working)."""
        self.mvge_status = status
        self.notify()

    def set_chat_side_panel(self, panel: str | None) -> None:
        """Set the active side panel in chat (files, diff, or None)."""
        self.chat_side_panel = panel
        if panel is not None:
            self.review_open = False
        self.notify()

    # ------------------------------------------------------------------
    # Approval queue (Approval Rune presenter)
    # ------------------------------------------------------------------

    def enqueue_approval(
        self, request: ApprovalRequest
    ) -> asyncio.Future[ApprovalDecision]:
        """Queue an approval request and activate the head of the queue.

        Returns the future the presenter awaits; it resolves with the
        Summoner's decision once the modal for this cast is answered.
        """
        future = self._approval_queue.enqueue(request)
        self._approval_queue.activate_next()
        self.notify()
        return future

    @property
    def approval_active_request(self) -> ApprovalRequest | None:
        """The request currently shown in the approval modal, if any."""
        return self._approval_queue.active_request

    @property
    def approval_pending_count(self) -> int:
        """Number of approval requests waiting behind the active one."""
        return self._approval_queue.pending_count

    def resolve_active_approval(
        self,
        outcome: ApprovalOutcome,
        scope: ApprovalScope = ApprovalScope.ONCE,
        reason_code: ApprovalReasonCode = ApprovalReasonCode.USER,
    ) -> ApprovalDecision | None:
        """Resolve the active approval request with the Summoner's verdict.

        Builds the canonical decision bound to the active request's
        argument digest. Applies the stale-response rule against the live
        tome/project context: a decision that arrived after the context
        moved is forced to deny. Advances the queue and returns the
        effective decision.
        """
        effective = self._approval_queue.resolve_active(
            outcome,
            scope,
            reason_code,
            project_root=str(self.project_path),
            tome_id=self.active_tome_id,
        )
        self._reset_approval_dialog()
        if effective is not None:
            self._approval_queue.activate_next()
        self.notify()
        return effective

    def deny_active_approval(
        self, reason_code: ApprovalReasonCode = ApprovalReasonCode.FAILURE
    ) -> None:
        """Deny the active approval request (dialog closed / Escape)."""
        if self._approval_queue.deny_active(reason_code) is not None:
            self._approval_queue.activate_next()
        self._reset_approval_dialog()
        self.notify()

    def deny_approval_if_active(
        self, cast_id: str, reason_code: ApprovalReasonCode = ApprovalReasonCode.FAILURE
    ) -> None:
        """Deny the active request only when it is still the given cast.

        Dialog ``close`` events are bound to the cast id shown when the
        dialog was rendered: a stale close from a dialog destroyed by
        queue advancement must not deny the newly activated cast.
        """
        active = self._approval_queue.active_request
        if active is None or active.cast_id != cast_id:
            return
        self.deny_active_approval(reason_code)

    def cancel_pending_approvals(self) -> None:
        """Deny every outstanding approval request (fail closed).

        Used for disconnect, agent abort, and shutdown: no pending cast
        may survive the loss of its decision surface.
        """
        denied = self._approval_queue.cancel_all()
        self._reset_approval_dialog()
        if denied:
            self.notify()

    def _on_approval_context_change(self) -> None:
        """Deny pending approvals and clear session state on context change.

        Project or tome changes cancel all pending requests before the
        state change completes, and session approve-all never survives a
        transition.
        """
        denied = self._approval_queue.cancel_all()
        self._reset_approval_dialog()
        badge_was_on = self.approval_session_approve_all
        self.approval_session_approve_all = False
        if denied or badge_was_on:
            self.notify()

    def set_approval_session_badge(self, active: bool) -> None:
        """Toggle the persistent session approve-all chat badge."""
        if self.approval_session_approve_all != active:
            self.approval_session_approve_all = active
            self.notify()

    def sync_approval_session_badge(self, view: PermissionsView | None) -> None:
        """Reconcile the badge with the rune's session state.

        The presenter sets the badge when it issues a session-scope
        decision; the permissions screen re-syncs it in case the grant
        changed through another path.
        """
        self.set_approval_session_badge(
            bool(view is not None and view.session_approve_all)
        )

    def request_approval_permissions_open(self) -> None:
        """Deep-link: open the Approval Rune's settings dialog directly."""
        self._approval_settings_requested = True
        self.notify()

    def take_approval_permissions_open_request(self) -> bool:
        """Consume the deep-link flag (True when a dialog open was requested)."""
        requested = self._approval_settings_requested
        self._approval_settings_requested = False
        return requested

    def set_approval_dialog_step(self, step: str, kind: str = "") -> None:
        """Move the approval popup between its main and confirmation steps."""
        self._approval_dialog_step = step
        self._approval_confirm_kind = kind
        self.notify()

    def _reset_approval_dialog(self) -> None:
        """Reset the popup to its main step for the next request."""
        self._approval_dialog_step = "main"
        self._approval_confirm_kind = ""

    def is_card_expanded(self, card_id: str, default: bool = False) -> bool:
        """Check whether a card/expansion is currently expanded."""
        if card_id in self._expanded_cards:
            return True
        if card_id in self._collapsed_cards:
            return False
        return default

    def set_card_expansion(self, card_id: str, expanded: bool) -> None:
        """Record expansion state without triggering notification re-renders."""
        if expanded:
            self._expanded_cards.add(card_id)
            self._collapsed_cards.discard(card_id)
        else:
            self._collapsed_cards.add(card_id)
            self._expanded_cards.discard(card_id)

    def is_rune_installed(self, rune_name: str) -> bool:
        """Check if a rune extension is installed in the global extensions layer."""
        target = extensions_dir() / rune_name
        return target.is_dir()

    def is_selected_realm_rune_installed(self) -> bool:
        """Is the Rune that provides the selected Realm installed?

        Asks about the selected Realm rather than taking a name, so the empty
        case is handled once. ``is_rune_installed("")`` would join the extensions
        directory onto nothing and test the directory itself, which exists -- so
        an unknown Realm would report its Rune installed, and the badge that
        exists to warn about a missing Rune would never appear.
        """
        rune_name = REALM_RUNES.get(self.selected_realm)
        return rune_name is not None and self.is_rune_installed(rune_name)

    async def fetch_marketplace_runes_async(self) -> dict[str, Any]:
        """Fetch available marketplace runes asynchronously."""
        return await asyncio.to_thread(fetch_marketplace_runes)

    async def list_installed_runes_async(self) -> list[dict[str, Any]]:
        """List installed runes asynchronously."""
        return await asyncio.to_thread(list_installed_runes)

    async def install_rune_async(self, source: str) -> bool:
        """Install a rune from marketplace, git, or path asynchronously."""
        try:
            await asyncio.to_thread(install_rune, source)
            if self.agent_service and self.agent_service._agent:
                load_runes = getattr(self.agent_service._agent, "_load_runes", None)
                if callable(load_runes):
                    res = load_runes()
                    if inspect.isawaitable(res):
                        await res
            self._drop_autocomplete_cache()
            self.notify()
            return True
        except Exception as exc:
            logger.warning("Failed to install rune '%s': %s", source, exc)
            return False

    async def uninstall_rune_async(self, rune_name: str) -> bool:
        """Uninstall a rune and unregister its realm factory if registered."""
        result = bool(await asyncio.to_thread(uninstall_rune, rune_name))
        get_default_realm_registry().unregister_realm_factory(rune_name)
        if result:
            self._drop_autocomplete_cache()
        self.notify()
        return result

    async def set_rune_enabled_async(self, rune_name: str, enabled: bool) -> bool:
        """Enable or disable an installed rune by updating its manifest."""
        result = bool(await asyncio.to_thread(set_rune_enabled, rune_name, enabled))
        if result:
            self._drop_autocomplete_cache()
        self.notify()
        return result

    def get_contributed_views(self) -> list[RuneUIView]:
        """Collect and sort UI views contributed by installed, enabled runes."""
        try:
            installed = list_installed_runes()
        except Exception:
            return []

        views: list[RuneUIView] = []
        for rune in installed:
            if not rune.get("enabled", True):
                continue
            ui_block = rune.get("ui")
            if not isinstance(ui_block, dict):
                continue
            raw_views = ui_block.get("views", [])
            if not isinstance(raw_views, list):
                continue
            for item in raw_views:
                if isinstance(item, dict) and "id" in item and "title" in item:
                    views.append(
                        RuneUIView(
                            id=str(item["id"]),
                            title=str(item["title"]),
                            icon=str(item.get("icon", "extension")),
                            order=int(item.get("order", 100)),
                            description=str(item.get("description", "")),
                        )
                    )

        views.sort(key=lambda v: (v.order, v.title))
        return views

    def has_contributed_view(self, view_id: str) -> bool:
        """Check if a specific view id is contributed by an active rune."""
        return any(v.id == view_id for v in self.get_contributed_views())

    def get_contributed_canvas_tabs(self) -> list[RuneUICanvasTab]:
        """Collect and sort canvas tabs contributed by installed, enabled runes."""
        try:
            installed = list_installed_runes()
        except Exception:
            return []

        tabs: list[RuneUICanvasTab] = []
        for rune in installed:
            if not rune.get("enabled", True):
                continue
            ui_block = rune.get("ui")
            if not isinstance(ui_block, dict):
                continue
            raw_tabs = ui_block.get("canvas_tabs", [])
            if not isinstance(raw_tabs, list):
                continue
            for item in raw_tabs:
                if isinstance(item, dict) and "id" in item and "title" in item:
                    tabs.append(
                        RuneUICanvasTab(
                            id=str(item["id"]),
                            title=str(item["title"]),
                            icon=str(item.get("icon", "extension")),
                            order=int(item.get("order", 100)),
                        )
                    )

        tabs.sort(key=lambda t: (t.order, t.title))
        return tabs

    def has_contributed_canvas_tab(self, tab_id: str) -> bool:
        """Check if a specific canvas tab id is contributed by an active rune."""
        return any(t.id == tab_id for t in self.get_contributed_canvas_tabs())

    async def install_skill_async(
        self, source: str, name: str | None = None
    ) -> str | None:
        """Install a skill from a local path or git URL.

        Returns the installed skill name on success, None on failure.
        """
        try:
            dest = await asyncio.to_thread(install_skill, source, name)
            self.notify()
            return dest.name
        except Exception as exc:
            logger.warning("Failed to install skill from '%s': %s", source, exc)
            return None

    def is_mvge_installed(self, mvge_name: str) -> bool:
        """Check if an mvge agent is installed in the global agents layer."""
        normalized = mvge_name.replace("-", "_")
        target = agents_dir() / normalized
        return target.is_dir()

    async def fetch_marketplace_mvges_async(self) -> dict[str, Any]:
        """Fetch available marketplace mvges asynchronously."""
        return await asyncio.to_thread(fetch_marketplace_mvges)

    async def list_installed_mvges_async(self) -> list[dict[str, Any]]:
        """List installed mvges asynchronously."""
        return await asyncio.to_thread(list_installed_mvges)

    async def install_mvge_async(self, source: str) -> bool:
        """Install an mvge from marketplace, git, or path asynchronously."""
        try:
            await asyncio.to_thread(install_mvge, source)
            self.notify()
            return True
        except Exception as exc:
            logger.warning("Failed to install mvge '%s': %s", source, exc)
            return False

    async def uninstall_mvge_async(self, mvge_name: str) -> bool:
        """Uninstall an mvge asynchronously."""
        try:
            result = bool(await asyncio.to_thread(uninstall_mvge, mvge_name))
            self.notify()
            return result
        except Exception as exc:
            logger.warning("Failed to uninstall mvge '%s': %s", mvge_name, exc)
            return False


# Dataclass fields declared with a plain default keep that default as a
# class attribute, which would shadow __getattr__ and break delegation for
# the shared fields. Remove those class attributes: the dataclass machinery
# (init defaults, repr, eq) already captured them in __dataclass_fields__.
for _shared_name in _SHARED_FIELDS:
    with contextlib.suppress(AttributeError):
        delattr(AppState, _shared_name)
del _shared_name
