# MvgeOS Specification (SPEC.md)

## Overview

MvgeOS is a Python-based AI coding agent inspired by the pi project, built with clean-room implementation using Mvge-specific terminology. It follows TDD strictly and targets Python 3.13+ with `uv` for dependency management.

## Architecture

### Monorepo Structure (uv workspaces)

```text
mvgeos/
├── mvgeos-core/              # Canonical loop vocabulary: abort, spells, events, turn loop
├── mvgeos-agent/             # Core agent loop, state, environment, harness
├── mvgeos-provider/          # Realm protocol + model registry (OpenRouter realm is a marketplace rune)
├── mvgeos-tome/              # JSONL session persistence with file locking
├── mvgeos-runes/             # Extension system (manifest, loader, sigils)
├── mvgeos-cli/               # CLI entry point
├── mvgeos-gui/               # Desktop GUI application (NiceGUI + PyWebView)
├── coding-mvge/              # Coding agent package (BaseMvge subclass)
└── pyproject.toml            # Root workspace config
```

### Package Details

| Package | Module | Purpose |
| --------- | --------- | --------- |
| mvgeos-core | `loop.py` | Canonical turn loop (`run_loop`), `LoopContext`, `LoopCallbacks` |
| mvgeos-core | `dispatcher.py` | `SpellDispatcher` - parallel/sequential spell execution |
| mvgeos-core | `spells.py` | `MvgeSpell`, `SpellResult`, `SpellResultMessage`, `ExecutionMode` |
| mvgeos-core | `events.py` | `MvgeEvent`, `MvgeEventType`, `ContemplationLevel` |
| mvgeos-core | `invocations.py` | `MvgeInvocation`, `SummonerRequest` |
| mvgeos-core | `channel.py` | `Model`, `ChannelConfig`, `MvgeResponse`, `RealmResponse`, `StopReason` |
| mvgeos-core | `abort.py` | `AbortController`, `AbortSignal`, `AbortError` |
| mvgeos-agent | `types.py` | `MvgeState` session state container |
| mvgeos-agent | `harness/harness.py` | MvgeHarness - operational turn driver, compaction, and lifecycle |
| mvgeos-provider | `base.py` | Realm protocol (abstract base) |
| mvgeos-marketplace | `runes/openrouter-realm/` | OpenRouterRealm implementation (marketplace rune) |
| mvgeos-marketplace | `runes/opencode-realm/` | OpenCode Zen realm, the default (marketplace rune) |
| mvgeos-provider | `types.py` | Model, ChannelConfig, RealmResponse |
| mvgeos-tome | `ledger.py` | TomeLedger - session management |
| mvgeos-tome | `locking.py` | FileLock - cross-process locking |
| mvgeos-tome | `index.py` | Index - entry indexing |
| mvgeos-tome | `types.py` | TomeEntry, TomeMetadata, TomeEntryType |
| mvgeos-runes | `loader.py` | load_runes_from_paths, load_manifests |
| mvgeos-runes | `manifest.py` | load_manifest |
| mvgeos-runes | `rune_runner.py` | RuneRunner |
| mvgeos-runes | `types.py` | RuneManifest, SigilHook |
| mvgeos-cli | `main.py` | CLI entry point |
| mvgeos-cli | `commands/tome.py` | `mvgeos tome` command |
| mvgeos-cli | `commands/config.py` | `mvgeos config` command |
| mvgeos-cli | `commands/repl.py` | REPL/TUI implementation |
| mvgeos-cli | `commands/tui.py` | TUI implementation |
| mvgeos-gui | `main.py` | GUI CLI entry point (`mvgeos-gui`) |
| mvgeos-gui | `app.py` | Layout builder and event binding |
| mvgeos-gui | `state.py` | Reactive AppState UI state container |
| mvgeos-gui | `agent_service.py` | In-process agent execution bridge |
| mvgeos-gui | `components/` | Antigravity 1:1 UI components |
| coding-mvge | `mvge.py` | `root_mvge` instance with zero-config auto-discovery |
| coding-mvge | `spells/` | Modular built-in spells (`bash`, `read`, `write`, `edit`, `find`, `list_files`, `grep`) |

## Core Types

### mvgeos-core/events.py and spells.py

```python
class ContemplationLevel(StrEnum):
    OFF = "none"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class SpellExecutionMode(StrEnum):
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"


class MvgeEventType(StrEnum):
    AGENT_START = "agent_start"
    AGENT_END = "agent_end"
    AGENT_SETTLED = "agent_settled"
    TURN_START = "turn_start"
    TURN_END = "turn_end"
    INPUT = "input"
    BEFORE_PROVIDER_REQUEST = "before_provider_request"
    AFTER_PROVIDER_RESPONSE = "after_provider_response"
    BEFORE_INVOCATION = "before_invocation"
    AFTER_INVOCATION = "after_invocation"
    MESSAGE_START = "message_start"
    MESSAGE_UPDATE = "message_update"
    MESSAGE_END = "message_end"
    TOOL_EXECUTION_START = "tool_execution_start"
    TOOL_EXECUTION_UPDATE = "tool_execution_update"
    TOOL_EXECUTION_END = "tool_execution_end"
    SPELL_CASTING_START = "spell_casting_start"
    SPELL_CASTING_UPDATE = "spell_casting_update"
    SPELL_CASTING_END = "spell_casting_end"
    ARTIFACT_CREATED = "artifact_created"
    COMPACTION_START = "compaction_start"
    COMPACTION_END = "compaction_end"
    ENTRY_APPENDED = "entry_appended"
    QUEUE_UPDATE = "queue_update"


class StopReason(StrEnum):
    PENDING = "pending"
    STOP = "stop"
    LENGTH = "length"
    SPELL_USE = "spellUse"
    ERROR = "error"
    ABORTED = "aborted"


@dataclass
class SummonerRequest:
    role: str = "user"
    content: str | list[dict[str, Any]] | None = None
    timestamp: float = 0.0


@dataclass
class MvgeResponse:
    role: str = "assistant"
    content: list[dict[str, Any]] = field(default_factory=list)
    realm: str = ""
    model: str = ""
    mana_usage: dict[str, float] = field(default_factory=dict)
    stop_reason: StopReason = StopReason.PENDING
    error_message: str | None = None
    timestamp: float = 0.0


class SpellStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    PARTIAL = "partial"


@dataclass
class SpellResult:
    spell_name: str
    status: SpellStatus = SpellStatus.SUCCESS
    content: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None
    terminate: bool = False


@dataclass
class SpellResultMessage:
    role: str = "spellResult"
    spell_cast_id: str = ""
    spell_name: str = ""
    content: list[dict[str, Any]] = field(default_factory=list)
    details: dict[str, Any] | None = None
    is_error: bool = False
    timestamp: float = 0.0
    terminate: bool = False


MvgeInvocation = SummonerRequest | MvgeResponse | SpellResultMessage


class AbortError(Exception):
    """Raised when an operation is cancelled via AbortSignal."""


class AbortSignal:
    """Cancellation signal mirroring the web AbortSignal API."""


class AbortController:
    """Controller that owns an AbortSignal and can abort it."""


@dataclass
class MvgeSpell:
    name: str
    description: str
    parameters: dict[str, Any]
    execution_mode: SpellExecutionMode = SpellExecutionMode.PARALLEL

    def prepare_arguments(self, args: dict[str, Any]) -> dict[str, Any]: ...

    async def execute(
        self,
        spell_cast_id: str,
        params: dict[str, Any],
        signal: AbortSignal | None = None,
        on_update: Any | None = None,
    ) -> dict[str, Any] | str: ...


@dataclass
class MvgeState:
    system_prompt: str = ""
    prompt_source: PromptSource = PromptSource.BUILTIN
    model: dict[str, Any] | None = None
    contemplation_level: ContemplationLevel = ContemplationLevel.MEDIUM
    spells: list[MvgeSpell] = field(default_factory=list)
    _spell_index: dict[str, MvgeSpell] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )
    invocations: list[MvgeInvocation] = field(default_factory=list)
    is_streaming: bool = False
    streaming_manifestation: MvgeInvocation | None = None
    pending_spell_casts: set[str] = field(default_factory=set)
    error_message: str | None = None
    mana_used: int = 0
    max_tokens: int | None = None
    temperature: float | None = None
    max_turns: int = 50
    max_events: int = 1000
    spell_timeout_ms: int = 30000
    contemplation_budget: int | None = None
    exclude_contemplation: bool = False
    queue_mode: QueueMode = QueueMode.ONE_AT_A_TIME
    rune_runner: RuneRunner | None = None
    agent_tome: MvgeTome | None = None
    event_bus: EventBus | None = None
    events: list[MvgeEvent] = field(default_factory=list)
    steer_queue: list[SummonerRequest] = field(default_factory=list)
    followup_queue: list[SummonerRequest] = field(default_factory=list)


@dataclass
class MvgeEvent:
    type: MvgeEventType
    data: dict[str, Any] = field(default_factory=dict)
```

### mvgeos-provider/types.py

```python
@dataclass
class Model:
    id: str
    name: str
    realm: str
    base_url: str
    api_key: str
    max_completion_mana: int = 0
    context_window: int = 128000
    max_tokens: int = 4096
    headers: dict[str, str] = field(default_factory=dict)
    supported_parameters: list[str] = field(default_factory=list)
    is_free: bool = False

    @property
    def free(self) -> bool:
        return self.is_free or self.id.endswith(":free") or self.id == "openrouter/free"

    @property
    def provider(self) -> str:
        return self.id.split("/")[0] if "/" in self.id else self.realm


@dataclass
class ChannelConfig:
    model: Model
    temperature: float = 0.7
    max_tokens: int = 4096
    max_output_mana: int | None = None
    timeout_ms: int = 60000
    max_retries: int = 3
    contemplation_level: str = "medium"
    contemplation_budget: int | None = None
    exclude_contemplation: bool = False
    tools: list[dict[str, Any]] = field(default_factory=list)
    meta_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class RealmResponse:
    model: Model
    invocation: Any | None = None
    mana_used: int = 0
    stop_reason: str = "stop"
    error_message: str | None = None
    error_code: str | None = None
```

### mvgeos-tome/types.py

```python
class TomeEntryType(StrEnum):
    INVOCATION = "invocation"
    SPELL_RESULT = "spellResult"
    MODEL_CHANGE = "modelChange"
    CONTEMPLATION_LEVEL_CHANGE = "contemplationLevelChange"
    SPELL_CALLS_CHANGE = "spellCallsChange"
    LABEL = "label"
    BRANCH_SUMMARY = "branchSummary"
    COMPACTION = "compaction"
    CUSTOM = "custom"
    CUSTOM_MESSAGE = "customMessage"
    LEAF = "leaf"
    TOME_INFO = "tome_info"
    MESSAGE = "message"


@dataclass
class TomeEntry:
    id: str
    parent_id: str | None
    type: TomeEntryType
    timestamp: float
    payload: dict[str, Any]


@dataclass
class TomeMetadata:
    id: str
    created_at: str
    cwd: str
    parent_tome_id: str | None = None
    active_leaf_id: str | None = None
    schema_version: str = "1.0"
```

### mvgeos-runes/types.py

```python
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
```

## Implemented Components

### MvgeHarness (mvgeos-agent/harness/harness.py)

Main operational turn driver with:

- Direct driving of `run_loop()` with streaming response channeling
- Compaction management (pre-turn when crowded and post-turn via `after_invocation`)
- Event fan-out (`_emit`) to state reduction, event bus, Sigils, and Tome
- Spell/tool call execution with event emission
- Error handling and queue drainage (steering and follow-up)

Session persistence:

- `create_tome(cwd, parent_tome_id, tome_id)` - Create new tome
- `open_tome(tome_id)` - Open existing tome
- `append(tome_id, entry)` - Append entry to tome
- `get_entries(tome_id, entry_type, limit)` - Retrieve entries
- `get_leaf_id(tome_id)` - Get current leaf for branching
- `create_branched_tome(parent_tome_id, cwd, fork_from_leaf_id)` - Fork a tome
- Uses `FileLock` for cross-process safety
- Single JSONL per tome (`{tome_id}.jsonl`, Pi-inspired but not Pi-compatible) with header + entries
- In-memory index rebuilt on startup

All 7 spells implemented:

- `cast_bash` - Shell command execution with timeout
- `cast_read` - File reading
- `cast_write` - File writing with parent dir creation
- `cast_edit` - String replacement in files
- `cast_find` - Glob pattern file finding
- `cast_list` - Single-directory listing (recursion lives in `cast_find`)
- `cast_grep` - Regex pattern matching in files

### CodingMvge (coding-mvge/mvge.py)

Concrete `BaseMvge` subclass with:

- Built-in spell registry (bash, read, write, edit, find, list, grep)
- Configurable system prompt via `load_system_prompt()`
- Turn loop with steer/follow-up queue handling
- Template method pattern via `BaseMvge`

## Configuration

### dotagents Protocol

Config at `.agents/.mvgeos/`:

- `config.json` - Main config (model, max_tokens, spells_enabled, etc.)
- `tomes/` - Tome directories (managed by TomeLedger)
- `runes/` - Project-level runes (each rune in its own subdirectory with manifest.json)
- `runes/manifest.json` - Rune manifest

### Config Schema

```json
{
  "model": "opencode/space-bunny-free",
  "max_tokens": 4096,
  "temperature": 0.7,
  "contemplation_level": "medium",
  "spells_enabled": ["bash", "read", "write", "edit", "find", "list", "grep"],
  "runes_paths": [
    "~/.agents/.mvgeos/runes",
    "~/.agents/.mvgeos/{agent_name}/runes",
    ".agents/.mvgeos/runes"
  ]
}
```

## Quality Gates

```bash
# All must pass before committing
uv run pytest --cov
uv run mypy
uv run ruff check
uv run ruff format --check
```

Coverage target: **90%+** (CI enforced)

## TDD Rules

1. **Write failing test first** - Red
2. **Implement minimal code** - Green
3. **Refactor** - Clean up, ensure types pass
4. **Run all quality gates** - Verify

Test naming: `test_<function>_<scenario>`

- `test_loop_single_turn_no_spells`
- `test_loop_with_spell_cast`
- `test_loop_mana_exhaustion`
- `test_cast_bash_success`
- `test_cast_bash_timeout`

## MvgeOS Terminology Reference

| Standard Term | MvgeOS Term |
| --------------- | ------------- |
| Agent | Mvge |
| User | Summoner |
| Tool | Spell |
| Token | Mana |
| Context Window | Mana Pool |
| Provider | Realm |
| Session | Tome |
| Message | Invocation |
| Streaming | Channeling |
| Extension | Rune |
| Callback | Sigil |
| Reasoning Effort | Contemplation |

Use these terms consistently in code, docs, and comments.

## Git Workflow

- Branch per feature
- Conventional commits: `feat(mvgeos-agent): add spell execution`
- **Changelog**: git-cliff at `cliff.toml` — generates CHANGELOG.md from commits
- **Release**: `.github/workflows/release.yml` — push `v*` tag or manual dispatch to create release
- Pre-commit: ruff, mypy, pytest
- No direct commits to main without PR

## License

MIT - See `LICENSE` file.
