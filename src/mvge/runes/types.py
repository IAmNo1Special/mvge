from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable

from mvge.core.abort import AbortSignal
from mvge.core.invocations import MvgeInvocation
from mvge.core.layers import RuneScope as RuneScope
from mvge.core.layers import Scope as Scope
from mvge.core.spell_schema import (
    find_context_parameter,
    invoke_spell_callable,
    unwrap_callable,
)
from mvge.core.spells import ExecutionMode, SpellContext


@runtime_checkable
class Sandbox(Protocol):
    """Structural type for the code-execution sandbox handed to runes.

    The agent layer supplies the concrete implementation (MvgeSandbox);
    runes and the runner depend only on this shape.
    """

    def execute_code(
        self,
        code_str: str,
        context_globals: dict[str, Any] | None = None,
        timeout_seconds: float = 5.0,
        allowed_modules: set[str] | None = None,
    ) -> dict[str, Any]: ...


class SigilHook(StrEnum):
    BEFORE_INVOCATION = "before_invocation"
    AFTER_INVOCATION = "after_invocation"
    BEFORE_SPELL_CAST = "before_spell_cast"
    AFTER_SPELL_RESULT = "after_spell_result"
    BEFORE_PROVIDER_REQUEST = "before_provider_request"
    AFTER_PROVIDER_RESPONSE = "after_provider_response"
    BEFORE_PROVIDER_HEADERS = "before_provider_headers"
    TURN_START = "turn_start"
    TURN_END = "turn_end"
    SESSION_START = "session_start"
    SESSION_SHUTDOWN = "session_shutdown"
    SESSION_BEFORE_SWITCH = "session_before_switch"
    SESSION_BEFORE_FORK = "session_before_fork"
    COMPACTION_START = "compaction_start"
    COMPACTION_END = "compaction_end"
    CONTEXT_TRANSFORM = "context_transform"
    AGENT_START = "agent_start"
    AGENT_END = "agent_end"
    BEFORE_MVGE_START = "before_mvge_start"
    INPUT = "input"
    SHOULD_STOP_AFTER_TURN = "should_stop_after_turn"
    PREPARE_NEXT_TURN = "prepare_next_turn"
    RESOURCES_DISCOVER = "resources_discover"
    SESSION_LEAF_CHANGED = "session_leaf_changed"
    WORKSPACE_UNTRUSTED = "workspace_untrusted"


@dataclass
class BeforeInvocationData:
    """Data passed to BEFORE_INVOCATION sigil hook."""

    invocation: MvgeInvocation


@dataclass
class AfterInvocationData:
    """Data passed to AFTER_INVOCATION sigil hook."""

    invocation: MvgeInvocation
    stop_reason: str


@dataclass
class BeforeSpellCastData:
    """Data passed to BEFORE_SPELL_CAST sigil hook."""

    spell_cast: dict[str, Any]
    spell_name: str


@dataclass
class AfterSpellResultData:
    """Data passed to AFTER_SPELL_RESULT sigil hook."""

    spell_name: str
    spell_cast_id: str
    result: Any


@dataclass
class BeforeProviderRequestData:
    """Data passed to BEFORE_PROVIDER_REQUEST sigil hook."""

    model: dict[str, Any]


@dataclass
class AfterProviderResponseData:
    """Data passed to AFTER_PROVIDER_RESPONSE sigil hook."""

    response: Any
    mana_used: int


@dataclass
class BeforeProviderHeadersData:
    """Data passed to BEFORE_PROVIDER_HEADERS sigil hook."""

    headers: dict[str, str]


@dataclass
class TurnStartData:
    """Data passed to TURN_START sigil hook."""

    model: dict[str, Any]


@dataclass
class TurnEndData:
    """Data passed to TURN_END sigil hook."""

    stop_reason: str
    mana_used: int


@dataclass
class SessionStartData:
    """Data passed to SESSION_START sigil hook."""

    session_name: str


@dataclass
class SessionShutdownData:
    """Data passed to SESSION_SHUTDOWN sigil hook."""

    session_name: str


@dataclass
class SessionBeforeSwitchData:
    """Data passed to SESSION_BEFORE_SWITCH sigil hook."""

    from_session: str
    to_session: str


@dataclass
class SessionBeforeForkData:
    """Data passed to SESSION_BEFORE_FORK sigil hook."""

    parent_session: str
    # The child session does not exist yet when this hook fires; it is
    # populated only if the fork completes. entry_id names the entry the
    # fork starts from.
    child_session: str | None = None
    entry_id: str | None = None


@dataclass
class CompactionStartData:
    """Data passed to COMPACTION_START sigil hook."""

    session_name: str
    message_count: int


@dataclass
class CompactionEndData:
    """Data passed to COMPACTION_END sigil hook."""

    session_name: str
    original_count: int
    compacted_count: int
    # Set when summarization failed and no compaction happened.
    error: str | None = None


@dataclass
class ContextTransformData:
    """Data passed to CONTEXT_TRANSFORM sigil hook."""

    invocations: list[MvgeInvocation]


@dataclass
class AgentStartData:
    """Data passed to AGENT_START sigil hook."""

    pass


@dataclass
class AgentEndData:
    """Data passed to AGENT_END sigil hook."""

    stop_reason: str


@dataclass
class BeforeMvgeStartData:
    """Data passed to BEFORE_MVGE_START sigil hook."""

    base_prompt: str
    spell_names: list[str]
    config_dir: str
    custom_prompt: str
    agent_name: str
    cwd: str
    runes_paths: list[str] = field(default_factory=list)
    system_path: str | None = None
    spells_dir: str | None = None


@dataclass
class InputData:
    """Data passed to INPUT sigil hook."""

    content: str | list[dict[str, Any]] | None


@dataclass
class ShouldStopAfterTurnData:
    """Data passed to SHOULD_STOP_AFTER_TURN sigil hook."""

    pass


@dataclass
class PrepareNextTurnData:
    """Data passed to PREPARE_NEXT_TURN sigil hook."""

    # This will be populated with LoopContext fields
    system_prompt: str = ""
    prompt_source: str = "builtin"
    invocations: list[MvgeInvocation] = field(default_factory=list)
    spell_names: list[str] = field(default_factory=list)
    contemplation_level: str = "medium"
    max_tokens: int | None = None
    temperature: float | None = None
    spell_timeout_ms: int = 30000
    contemplation_budget: int | None = None
    exclude_contemplation: bool = False
    max_turns: int = 50
    queue_mode: str = "one-at-a-time"


@dataclass
class ResourcesDiscoverData:
    """Data passed to RESOURCES_DISCOVER sigil hook."""

    cwd: str
    reason: str = "startup"
    skill_paths: list[str | Path] = field(default_factory=list)
    prompt_paths: list[str | Path] = field(default_factory=list)


@dataclass
class SessionLeafChangedData:
    """Data passed to SESSION_LEAF_CHANGED sigil hook."""

    tome_id: str
    old_leaf_id: str | None
    new_leaf_id: str | None
    reason: str  # "undo", "checkout", "fork"
    workspace_root: str | None = None


@dataclass
class WorkspaceUntrustedData:
    """Data passed to WORKSPACE_UNTRUSTED sigil hook."""

    workspace_root: str
    status: str


# Mapping from SigilHook to its typed data class
SIGIL_HOOK_DATA_CLASSES: dict[SigilHook, type] = {
    SigilHook.BEFORE_INVOCATION: BeforeInvocationData,
    SigilHook.AFTER_INVOCATION: AfterInvocationData,
    SigilHook.BEFORE_SPELL_CAST: BeforeSpellCastData,
    SigilHook.AFTER_SPELL_RESULT: AfterSpellResultData,
    SigilHook.BEFORE_PROVIDER_REQUEST: BeforeProviderRequestData,
    SigilHook.AFTER_PROVIDER_RESPONSE: AfterProviderResponseData,
    SigilHook.BEFORE_PROVIDER_HEADERS: BeforeProviderHeadersData,
    SigilHook.TURN_START: TurnStartData,
    SigilHook.TURN_END: TurnEndData,
    SigilHook.SESSION_START: SessionStartData,
    SigilHook.SESSION_SHUTDOWN: SessionShutdownData,
    SigilHook.SESSION_BEFORE_SWITCH: SessionBeforeSwitchData,
    SigilHook.SESSION_BEFORE_FORK: SessionBeforeForkData,
    SigilHook.COMPACTION_START: CompactionStartData,
    SigilHook.COMPACTION_END: CompactionEndData,
    SigilHook.CONTEXT_TRANSFORM: ContextTransformData,
    SigilHook.AGENT_START: AgentStartData,
    SigilHook.AGENT_END: AgentEndData,
    SigilHook.BEFORE_MVGE_START: BeforeMvgeStartData,
    SigilHook.INPUT: InputData,
    SigilHook.SHOULD_STOP_AFTER_TURN: ShouldStopAfterTurnData,
    SigilHook.PREPARE_NEXT_TURN: PrepareNextTurnData,
    SigilHook.RESOURCES_DISCOVER: ResourcesDiscoverData,
    SigilHook.SESSION_LEAF_CHANGED: SessionLeafChangedData,
    SigilHook.WORKSPACE_UNTRUSTED: WorkspaceUntrustedData,
}


#: ``Scope`` is defined in core because the resolver that ranks layers owns
#: it: a scope is the name of a layer of the ``.agents`` stack, and core is
#: the only package that may derive one. Re-exported here because Runes and
#: Skills both address their origin through it, and this is the module that
#: already speaks for both vocabularies. A type moving down the dependency
#: direction -- not a path-derivation shim.
SkillScope = Scope


@dataclass
class SkillManifest:
    name: str
    description: str
    scope: SkillScope = SkillScope.PROJECT
    path: str = ""
    location: str = ""
    version: str = ""
    license: str = ""
    compatibility: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    allowed_tools: str = ""
    disable_model_invocation: bool = False
    body: str | None = None

    def __post_init__(self) -> None:
        if not self.location and self.path:
            if self.path.endswith("SKILL.md"):
                self.location = self.path
            else:
                self.location = (Path(self.path) / "SKILL.md").as_posix()
        elif self.location and not self.path:
            self.path = self.location

    @property
    def base_dir(self) -> Path:
        loc = self.location or self.path
        if loc.endswith("SKILL.md"):
            return Path(loc).parent
        return Path(loc)


@dataclass
class SkillLoad:
    manifest: SkillManifest
    source_rune: str | None = None


class SkillDiagnosticKind(StrEnum):
    SHADOWED_SKILL = "shadowed_skill"
    PARSE_WARNING = "parse_warning"
    MALFORMED_YAML = "malformed_yaml"
    INVALID_PLUGIN = "invalid_plugin"
    PATH_ESCAPE = "path_escape"


@dataclass
class SkillDiagnostic:
    kind: SkillDiagnosticKind
    skill_name: str
    message: str
    scope: SkillScope | None = None
    path: str = ""


@dataclass
class PluginManifest:
    name: str
    schema: str
    version: str = ""
    description: str = ""
    author: dict[str, str] | str = field(default_factory=dict)
    homepage: str = ""
    repository: str = ""
    license: str = ""
    keywords: list[str] = field(default_factory=list)
    extensions: dict[str, Any] = field(default_factory=dict)
    path: str = ""


@dataclass(frozen=True)
class RuneUIView:
    """A sidebar view navigation entry contributed by a rune."""

    id: str
    title: str
    icon: str = "extension"
    order: int = 100
    description: str = ""


@dataclass(frozen=True)
class RuneUICanvasTab:
    """An adaptive canvas surface/tab contributed by a rune."""

    id: str
    title: str
    icon: str = "extension"
    order: int = 100


@dataclass(frozen=True)
class RuneUIContributions:
    """UI entry points declared in manifest.json."""

    views: list[RuneUIView] = field(default_factory=list)
    canvas_tabs: list[RuneUICanvasTab] = field(default_factory=list)


@dataclass
class RuneManifest:
    name: str
    version: str
    description: str
    scope: RuneScope = RuneScope.PROJECT
    path: str = ""
    hooks: list[SigilHook] = field(default_factory=list)
    entry_point: str = ""
    shortcuts: list[RuneShortcut] = field(default_factory=list)
    system_deps: list[str] = field(default_factory=list)
    python_deps: list[str] = field(default_factory=list)
    execution_mode: ExecutionMode = ExecutionMode.PARALLEL
    enabled: bool = True
    runtime: str = "python"
    types: list[str] = field(default_factory=list)
    spell_gateway: bool = False
    commands: list[str] = field(default_factory=list)
    session_codecs: list[str] = field(default_factory=list)
    ui: RuneUIContributions = field(default_factory=RuneUIContributions)


@dataclass(frozen=True)
class RuneContext:
    cwd: str = ""
    project_root: str = ""
    mode: str = "cli"
    has_ui: bool = False
    agent_name: str = ""
    api_key: str = ""
    session_id: str = ""
    tome_dir: str = ""
    model_id: str = ""


@dataclass
class RegisteredCommand:
    name: str
    description: str = ""
    handler: Any = None


@dataclass
class RuneShortcut:
    key: str
    description: str = ""
    handler: Any = None


@dataclass
class RuneDisposableScope:
    """Disposable lifecycle registration scope for one rune."""

    rune_name: str
    spell_names: set[str] = field(default_factory=set)
    sigil_handlers: list[tuple[SigilHook, Any]] = field(default_factory=list)
    commands: set[str] = field(default_factory=set)
    shortcuts: set[str] = field(default_factory=set)
    providers: set[str] = field(default_factory=set)
    realm_factories: set[str] = field(default_factory=set)
    installed_spell_gate: bool = False
    cleanup_callbacks: list[Callable[[], Any]] = field(default_factory=list)


class DiagnosticKind(StrEnum):
    SHADOWED_RUNE = "shadowed_rune"
    LOAD_FAILURE = "load_failure"
    LOAD_WARNING = "load_warning"
    PARSE_WARNING = "parse_warning"
    MISSING_DEP = "missing_dep"
    INCOMPATIBLE_SESSION = "incompatible_session"
    MODEL_MISMATCH = "model_mismatch"
    MISSING_SPELL = "missing_spell"
    CONTEMPLATION_MISMATCH = "contemplation_mismatch"
    INCOMPATIBLE_RUNTIME = "incompatible_runtime"


@dataclass
class Diagnostic:
    kind: DiagnosticKind
    rune_name: str
    message: str
    scope: RuneScope | None = None
    path: str = ""


@dataclass
class RuneLoad:
    manifest: RuneManifest
    factory: Any = None


class SpellDefinition:
    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any] | None = None,
        execution_mode: ExecutionMode = ExecutionMode.PARALLEL,
        prompt_snippet: str | None = None,
        prompt_guidelines: list[str] | None = None,
        source_rune: str | None = None,
        handler: Any | None = None,
        read_only: bool = False,
        timeout_ms: int | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters or {}
        self.execution_mode = execution_mode
        self.prompt_snippet = prompt_snippet
        self.prompt_guidelines = prompt_guidelines or []
        self.source_rune = source_rune
        self._handler = handler
        self.read_only = read_only
        # Dispatcher timeout override, same semantics as MvgeSpell.timeout_ms:
        # None inherits the loop timeout, 0 disables the dispatcher timeout.
        self.timeout_ms = timeout_ms

    async def execute(
        self,
        spell_cast_id: str,
        params: dict[str, Any],
        signal: AbortSignal | None = None,
        on_update: Any | None = None,
        ctx: SpellContext | None = None,
    ) -> dict[str, Any]:
        if self._handler is not None:
            unwrapped = unwrap_callable(self._handler)
            sig = inspect.signature(unwrapped)
            func_params = sig.parameters
            has_var_kw = any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in func_params.values()
            )
            ctx_param = find_context_parameter(unwrapped)

            call_kwargs: dict[str, Any] = {}
            if "signal" in func_params or has_var_kw:
                call_kwargs["signal"] = signal
            if "on_update" in func_params or has_var_kw:
                call_kwargs["on_update"] = on_update
            if ctx_param is not None and ctx is not None:
                call_kwargs[ctx_param] = ctx

            call_params = dict(params)
            if not has_var_kw:
                if "spell_action" not in func_params:
                    call_params.pop("spell_action", None)
                if "spell_summary" not in func_params:
                    call_params.pop("spell_summary", None)

            param_keys = list(func_params.keys())
            if len(param_keys) >= 2 and param_keys[0] in (
                "spell_cast_id",
                "id",
                "cast_id",
            ):
                res = await invoke_spell_callable(
                    self._handler, spell_cast_id, call_params, **call_kwargs
                )
            else:
                res = await invoke_spell_callable(
                    self._handler, call_params, **call_kwargs
                )
            return cast(dict[str, Any], res)
        raise NotImplementedError
