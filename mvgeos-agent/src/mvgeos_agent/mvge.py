from __future__ import annotations

import asyncio
import contextlib
import copy
import dataclasses
import importlib.util
import inspect
import logging
import os
import re
import sys
from collections.abc import AsyncIterator, Callable, Sequence
from pathlib import Path
from typing import Any, cast

from dotenv import load_dotenv
from mvgeos_core.abort import (
    AbortController,
    AbortSignal,
)
from mvgeos_core.channel import (
    ChannelConfig,
    Model,
    RealmResponse,
)
from mvgeos_core.constants import DEFAULT_AGENT_NAME
from mvgeos_core.errors import MissingApiKeyError, TomeResumeError
from mvgeos_core.event_bus import EventBus
from mvgeos_core.events import (
    ContemplationLevel,
    MvgeEvent,
    MvgeEventType,
    QueueMode,
)
from mvgeos_core.invocations import (
    MvgeInvocation,
    SummonerRequest,
)
from mvgeos_core.layers import (
    ResolvedLayer,
    Scope,
    agents_dir,
    sessions_dir,
)
from mvgeos_core.loop import StreamFn
from mvgeos_core.spells import MvgeSpell
from mvgeos_provider.base import Realm, RealmFactory
from mvgeos_provider.model_registry import (
    ModelRegistry,
    model_requires_credential,
    serving_realm_for_model,
)
from mvgeos_provider.realms import (
    api_key_env_for_realm,
)
from mvgeos_provider.registry import RealmRegistry, get_default_realm_registry
from mvgeos_runes.codecs import load_session_codecs
from mvgeos_runes.rune_audit import (
    RuneAuditLog,
    default_rune_ops_dir,
    utcnow,
)
from mvgeos_runes.rune_runner import RuneRunner
from mvgeos_runes.types import (
    Diagnostic,
    DiagnosticKind,
    RegisteredCommand,
    RuneShortcut,
    SkillDiagnostic,
    SpellDefinition,
)
from mvgeos_tome.handle import TomeHandleFactory
from mvgeos_tome.types import TomeMetadata, TomeVersionError

from mvgeos_agent.agent_name import resolve_active_agent_name
from mvgeos_agent.agent_session import MvgeTome
from mvgeos_agent.compatibility import (
    SessionCompatibilityReport,
    validate_session_compatibility,
)
from mvgeos_agent.environment import (
    MvgeEnvironment,
    PromptSource,
    resolve_config_dir,
    resolve_system_prompt,
)
from mvgeos_agent.function_spell import (
    RuneSpellWrapper,
    SpellUnion,
    coerce_spell,
    discover_spells_from_dir,
)
from mvgeos_agent.harness import (
    DEFAULT_COMPACTION_SETTINGS,
    CompactionRunner,
    CompactionSettings,
    MvgeHarness,
)
from mvgeos_agent.protocol import ReloadResult
from mvgeos_agent.rune_lifecycle import RuneLifecycle
from mvgeos_agent.snapshot import RuntimeSnapshot
from mvgeos_agent.types import MvgeState

logger = logging.getLogger(__name__)

SPELL_NAME_REGEX = re.compile(r"^[a-zA-Z0-9_-]+$")


def _resolve_api_key(explicit_key: str | None = None, realm: str = "") -> str:
    """Read the credential for ``realm``, never for a different one.

    The lookup order is explicit argument, the Realm's own environment
    variable, then ``MVGEOS_API_KEY`` -- which is Realm-agnostic by name and so
    stays a valid fallback for whichever Realm is being served.

    There is deliberately no cross-Realm fallback. An earlier version read
    ``OPENROUTER_API_KEY`` first, unconditionally, so a Summoner whose only
    credential was an OpenRouter key had it sent to ``opencode.ai`` the moment
    ``opencode`` became the default Realm. That hands one Realm's key to another
    Realm's host: it authenticates at the wrong host, and the Summoner's key is
    exposed to a host that never asked for it. ``opencode`` is the default now,
    so this was not a corner case -- it was the first-run path.

    A Realm with no credential of its own resolves to ``""``, which is a correct
    answer rather than a failure: OpenCode Zen's free tier needs no key at all.
    """
    if explicit_key:
        return explicit_key
    env_key = os.environ.get(api_key_env_for_realm(realm)) if realm else None
    return env_key or os.environ.get("MVGEOS_API_KEY") or ""


def _validate_spell_name(name: Any) -> bool:
    """Validate spell name conforms to provider identifier standards."""
    return isinstance(name, str) and bool(SPELL_NAME_REGEX.match(name))


def _validate_spell_parameters(parameters: Any) -> bool:
    """Validate that parameters conform to a JSON Schema object dictionary."""
    if not isinstance(parameters, dict):
        return False
    if not parameters:
        return True

    schema_type = parameters.get("type")
    if schema_type is not None and schema_type != "object":
        return False

    properties = parameters.get("properties")
    if properties is not None:
        if not isinstance(properties, dict):
            return False
        for prop_name, prop_def in properties.items():
            if not isinstance(prop_name, str) or not isinstance(prop_def, dict):
                return False

    required = parameters.get("required")
    if required is not None:
        if not isinstance(required, (list, tuple, set)):
            return False
        if not all(isinstance(r, str) for r in required):
            return False

    return True


def _validate_spell_signature(spell: Any) -> bool:
    """Validate that spell.execute matches the MvgeSpell execution contract."""
    execute_fn = getattr(spell, "execute", None)
    if not callable(execute_fn):
        return False
    try:
        sig = inspect.signature(execute_fn)
    except (ValueError, TypeError):
        return False

    try:
        sig.bind("dummy_id", {}, signal=None, on_update=None)
        return True
    except TypeError:
        try:
            sig.bind("dummy_id", {}, signal=None)
            return True
        except TypeError:
            return False


def _apply_gateway_allowlist(runner: RuneRunner) -> str | None:
    """Engage spell-gateway mode when a gateway rune is loaded.

    When a loaded rune declares ``spell_gateway`` in its manifest, narrow the
    model's spell view to that rune's spells via the engine-owned global
    spell allowlist. Other runes' spells stay hidden until a rune reveals
    them (``RuneAPI.widen_global_allowlist``), e.g. as ``tool_search`` hits.

    An explicitly configured allowlist is never overridden: harness or user
    policy wins over the manifest declaration. Returns the gateway rune
    name when gateway mode was engaged, else ``None``.
    """
    if runner.get_global_spell_allowlist() is not None:
        return None
    gateway = runner.gateway_rune_name
    if gateway is None:
        return None
    names = runner.gateway_spell_names()
    if not names:
        return None
    runner.set_global_spell_allowlist(names)
    logger.info("Spell-gateway mode engaged via rune '%s'", gateway)
    return gateway


_ProviderSnapshot = tuple[dict[str, dict[str, Any]], dict[str, RealmFactory]]
"""Rune-registered provider configs and realm factories, for rollback."""


_RETIRED_SHUTDOWN_ATTEMPTS = 3
"""How many times reload retries stopping the retired lifecycle's watchers."""

_RETIRED_SHUTDOWN_BACKOFF_SECONDS = 0.25
"""Delay between retired-shutdown attempts: a transient observer-teardown
race may fail identically on immediate retry but succeed after a beat."""


def _snapshot_provider_registrations(
    registry: RealmRegistry,
) -> _ProviderSnapshot:
    """Capture rune-registered provider state using the public API."""
    providers: dict[str, dict[str, Any]] = {}
    for name in registry.get_registered_providers():
        config = registry.get_provider_config(name)
        if config is not None:
            providers[name] = dict(config)
    factories: dict[str, RealmFactory] = {}
    for prefix in registry.get_registered_realm_factories():
        factory = registry.get_realm_factory(prefix)
        if factory is not None:
            factories[prefix] = factory
    return providers, factories


def _restore_provider_registrations(
    registry: RealmRegistry, snapshot: _ProviderSnapshot
) -> None:
    """Roll provider state back to a snapshot taken before candidate load.

    Candidate rune factories execute against the shared registry; when the
    candidate build later fails, this removes or repairs anything the
    candidate added so the failed reload leaves no trace.
    """
    snap_providers, snap_factories = snapshot
    # Provider configs and realm factories live in separate namespaces;
    # one name may appear in both, so each is restored independently.
    for name in registry.get_registered_providers():
        wanted = snap_providers.get(name)
        current = registry.get_provider_config(name)
        if wanted is None:
            if current is not None:
                registry.unregister_provider(name)
        elif current != wanted:
            registry.unregister_provider(name)
            registry.register_provider(name, wanted)
    for prefix in registry.get_registered_realm_factories():
        wanted_factory = snap_factories.get(prefix)
        if wanted_factory is None:
            registry.unregister_realm_factory(prefix)
        elif registry.get_realm_factory(prefix) is not wanted_factory:
            registry.register_realm_factory(prefix, wanted_factory)


@dataclasses.dataclass
class _ReloadedState:
    """Validated candidate state for one reload; swapped in atomically.

    ``_build_reload_state`` constructs this fully before anything is
    swapped, so a failure anywhere in the build leaves the live
    last-known-good state untouched: the candidate lifecycle owns a
    fresh runner (factories re-executed, rehydrate applied), the
    candidate harness is built pre-swap, and the swap itself is
    synchronous.
    """

    environment: MvgeEnvironment
    prompt_source: PromptSource
    prompt: str
    discovered_spells: list[SpellUnion]
    built_spells: list[MvgeSpell]
    active_spells_dir: Path | None
    rune_diagnostics: list[Diagnostic | SkillDiagnostic]
    rehydrate_errors: list[str]
    spell_file_warnings: list[str]
    prompt_changed: bool
    spells_changed: bool
    summary: str
    runner: RuneRunner | None
    lifecycle: RuneLifecycle | None
    harness: MvgeHarness | None
    gateway_rune: str | None
    """Spell-gateway rune engaged on the candidate runner, if any."""


def discover_agent_rune_extras(
    name: str, caller_dir: Path | None = None
) -> list[ResolvedLayer]:
    """Rune directories this agent ships, beyond the standard stack.

    The colocated ``runes/`` beside the caller's code, the agent
    configuration directory's ``runes/`` and ``extensions/``, and the
    ``runes/`` shipped inside an importable agent package. None of these are
    standard protocol names, so each is a *declared* extra that states its
    own scope -- agent-scoped, because these ship with the agent -- rather
    than a layer the resolver infers from a path string.

    Ranking places them with the agent layer: ahead of a project directory,
    behind the agent's own standard layer. That is the same rule every other
    declared extra follows. The layer decides precedence; the caller only
    says what layer a directory is.

    Shared with the CLI's Rune command discovery. A Rune the agent loads but
    the CLI cannot see is installed successfully and then loads nowhere,
    which is the failure this whole resolver exists to prevent.
    """
    if not name or name == DEFAULT_AGENT_NAME:
        return []

    extras: list[ResolvedLayer] = []

    def _declare(path: Path) -> None:
        if path.is_dir() and not any(layer.path == path for layer in extras):
            extras.append(ResolvedLayer(Scope.AGENT, path))

    if caller_dir is not None:
        _declare(caller_dir / "runes")

    agent_dir = resolve_config_dir(name)
    for sub_name in ("runes", "extensions"):
        _declare(agent_dir / sub_name)

    # Built-in runes shipped inside an importable agent package.
    for try_name in (name, name.replace("-", "_"), name.replace("_", "-")):
        try:
            spec = importlib.util.find_spec(try_name)
            if spec is None:
                continue
            pkg_path = None
            if spec.origin and spec.origin not in (None, "namespace"):
                pkg_path = Path(spec.origin).parent
            elif spec.submodule_search_locations:
                for loc in spec.submodule_search_locations:
                    pkg_path = Path(loc)
                    break
            if pkg_path is not None:
                _declare(pkg_path / "runes")
                break
        except Exception:
            continue

    return extras


class Mvge:
    """Core concrete agent implementation in MvgeOS.

    Configured with tools (spells), prompt, model, environment, and persistence.
    """

    @staticmethod
    def _default_tome_factory(
        tome_dir: Path, rune_dirs: Sequence[Path]
    ) -> TomeHandleFactory:
        """Build the default session factory with rune-provided codecs.

        Runes opt in through ``session_codecs`` in manifest.json; ordinary
        runes are never imported here. The built-in Tome v1 codec always
        stays first regardless of rune codecs.
        """
        codecs, diagnostics = load_session_codecs(rune_dirs)
        for diag in diagnostics:
            logger.warning(
                "Session codec issue in rune %s: %s",
                diag.rune_name,
                diag.message,
            )
        return TomeHandleFactory(tome_dir, codecs=codecs)

    def __init__(
        self,
        api_key: str | None = None,
        *,
        name: str = DEFAULT_AGENT_NAME,
        spells: Sequence[SpellUnion] | None = None,
        custom_system_prompt: str = "",
        extension_dir: str | None = None,
        tome_dir: Path | None = None,
        tome_resume: str | None = None,
        tome_factory: TomeHandleFactory | None = None,
        provider_name: str | None = None,
        runes_paths: Sequence[str] | None = None,
        compaction: CompactionSettings = DEFAULT_COMPACTION_SETTINGS,
        environment: MvgeEnvironment | None = None,
        strict_resume: bool = False,
        force_fork_resume: bool = False,
        caller_dir: Path | None = None,
        provider_registry: RealmRegistry | None = None,
    ) -> None:
        resolved_caller_dir: Path | None = (
            caller_dir.resolve() if caller_dir is not None else None
        )
        if resolved_caller_dir is None:
            try:
                caller_frame = inspect.stack()[1]
                caller_file = caller_frame.filename
                if caller_file:
                    resolved_caller_dir = Path(caller_file).resolve().parent
            except Exception:
                pass
        if resolved_caller_dir is not None:
            env_path = resolved_caller_dir / ".env"
            if env_path.is_file():
                load_dotenv(env_path)
        caller_dir = resolved_caller_dir

        # Held explicitly here and resolved from the environment once the Realm
        # is known -- reading a credential before that would mean reading a
        # Realm-blind one, which is the bug this ordering exists to prevent.
        self._api_key = api_key or ""
        # Which Mvge runs is decided once, here, and every later consumer --
        # spells, rune extras, the environment, `name` -- reads the answer.
        # Deciding it again downstream is the second precedence pass ADR-0014
        # was written to end, and it is what let one marketplace artifact's
        # slug into five engine packages.
        self._name = resolve_active_agent_name(name)
        self._caller_dir = caller_dir

        agents_root = str(agents_dir())
        if agents_root not in sys.path:
            sys.path.insert(0, agents_root)

        self._spells_arg = spells
        self._spells, self._active_spells_dir = self._discover_spells()

        self._plan_mode = False

        agent_rune_extras = discover_agent_rune_extras(self._name, caller_dir)

        self._custom_system_prompt = custom_system_prompt
        self._extension_dir = extension_dir
        self._tome_dir = tome_dir or sessions_dir()
        self._tome_resume = tome_resume
        self._provider_name = provider_name
        self._compaction_settings = compaction
        self._strict_resume = strict_resume
        self._force_fork_resume = force_fork_resume
        self._resume_diagnostics: list[Diagnostic | SkillDiagnostic] = []

        if environment is None:
            environment = MvgeEnvironment.resolve(
                self._name,
                config_dir=resolve_config_dir(self._name),
                caller_dir=caller_dir,
                extension_dir=extension_dir,
                runes_paths=list(runes_paths) if runes_paths is not None else None,
                rune_extras=agent_rune_extras,
                active_spells_dir=self._active_spells_dir,
            )

        self._environment = environment
        self._config_manager = environment.config_manager

        self._model_id = environment.model_id
        # Now that the model is known, its Realm is too, and only then is it
        # safe to read a credential. An explicit key still wins: the Summoner
        # handing one over is not a guess about which Realm they meant.
        #
        # The Realm that serves the model rather than the slug's prefix, which
        # differ for a routed slug -- see serving_realm_for_model.
        if not self._api_key:
            self._api_key = _resolve_api_key(
                realm=serving_realm_for_model(self._model_id)
            )
        self._temperature = environment.temperature
        self._max_tokens = environment.max_tokens
        self._contemplation_level = environment.contemplation_level
        self._contemplation_budget = environment.contemplation_budget
        self._exclude_contemplation = environment.exclude_contemplation
        self._queue_mode: QueueMode = environment.queue_mode
        self._spell_names = environment.spell_names
        # The environment resolved the full stack exactly once, layers and
        # all. Nothing is concatenated here: a second precedence pass is how
        # installation and discovery came to disagree about where a Rune
        # lives.
        self._rune_layers: list[ResolvedLayer] = list(environment.rune_layers)
        self._runes_paths = [layer.path for layer in self._rune_layers]

        # Built after the environment, because a Rune-provided session codec
        # may ship in *any* layer -- not just the directories this agent
        # discovered for itself. Scanning only the agent's own extras would
        # silently stop loading a user's codec.
        self._tome_factory = tome_factory or self._default_tome_factory(
            self._tome_dir, self._runes_paths
        )

        self._provider_registry = (
            provider_registry
            if provider_registry is not None
            else get_default_realm_registry()
        )
        self._runner: RuneRunner | None = None
        self._rune_lifecycle: RuneLifecycle | None = None
        self._prompt_source = environment.resolved_prompt.source
        # Inputs for re-resolving the base SYSTEM.md chain on reload: the
        # exact values MvgeEnvironment.resolve used, so reload re-discovers
        # identically whether the environment was built here or handed in
        # (CLI/GUI).
        self._prompt_resolve_kwargs: dict[str, Any] = dict(
            environment.prompt_resolve_kwargs
        ) or {"agent_name": name}
        # Turn-boundary reload: a reload requested mid-turn queues here and
        # the in-flight turn keeps its LoopContext untouched.
        self._run_in_flight = False
        self._reload_pending = False
        # Serializes reload() executions: concurrent callers must never
        # interleave their builds over the shared provider registry.
        self._reload_lock = asyncio.Lock()
        self._reload_diagnostics: list[Diagnostic | SkillDiagnostic] = []
        # Spell-gateway tracking: the rune name when the global spell
        # allowlist was engaged via a rune's ``spell_gateway`` manifest
        # declaration, else None (no allowlist, or an explicitly
        # configured policy that gateway mode must never override).
        self._gateway_engaged_rune: str | None = None
        self._model: Model | None = None
        self._realm: Realm | None = None
        self._agent_tome: MvgeTome | None = None
        self._harness: MvgeHarness | None = None
        self._compaction: CompactionRunner | None = None
        self._state: MvgeState | None = None
        self._event_bus = EventBus()
        self._abort_controller: AbortController | None = None
        self._enabled_spells_filter: set[str] | None = None
        self._initialized = False

    def _discover_spells(self) -> tuple[list[SpellUnion], Path | None]:
        """Run the construction-time spell discovery branches (strict)."""
        return self._discover_spells_impl(on_file_error=None)

    def _discover_spells_impl(
        self,
        on_file_error: Callable[[Path, Exception], None] | None,
    ) -> tuple[list[SpellUnion], Path | None]:
        """Spell discovery branching shared by construction and reload.

        Returns the discovered spells and the winning spells directory
        (``None`` when spells were injected directly or no spells directory
        exists). ``on_file_error`` selects tolerant mode: per-file import
        or discovery failures are reported through it and skipped instead
        of raising (used by reload); ``None`` keeps strict construction
        behavior.
        """
        # The agent scope was resolved once in __init__. Resolving it again
        # here would be a second precedence pass; reading the answer already
        # taken is what keeps agent and spells pointing at the same Mvge.
        name = self._name
        caller_dir = self._caller_dir
        spells_arg = self._spells_arg

        def _from_dir(spells_dir: Path) -> list[SpellUnion]:
            return list(
                discover_spells_from_dir(spells_dir, on_file_error=on_file_error)
            )

        if spells_arg is not None:
            if spells_arg and all(isinstance(s, str) for s in spells_arg):
                agent_config_dir = resolve_config_dir(name)
                discovered: list[SpellUnion] = []
                active_dir: Path | None = None
                config_spells = agent_config_dir / "spells"
                if config_spells.is_dir():
                    discovered = _from_dir(config_spells)
                    active_dir = config_spells
                filter_set = set(spells_arg)
                return (
                    [s for s in discovered if getattr(s, "name", "") in filter_set],
                    active_dir,
                )
            return list(spells_arg), None
        if caller_dir is not None and (caller_dir / "spells").is_dir():
            caller_spells = caller_dir / "spells"
            return _from_dir(caller_spells), caller_spells
        agent_config_dir = resolve_config_dir(name)
        config_spells = agent_config_dir / "spells"
        if config_spells.is_dir():
            return _from_dir(config_spells), config_spells
        return [], None

    @property
    def tome_id(self) -> str | None:
        if self._agent_tome is not None:
            return self._agent_tome.tome_id
        return None

    @property
    def session_id(self) -> str | None:
        """Alias for tome_id adhering to standard agent protocol vocabulary."""
        return self.tome_id

    @property
    def tome_dir(self) -> Path:
        return self._tome_dir

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def contemplation_level(self) -> ContemplationLevel | str:
        return self._contemplation_level

    @property
    def mana_used(self) -> int | None:
        if self._state is not None:
            return getattr(self._state, "mana_used", None)
        return None

    @property
    def system_prompt(self) -> str:
        """The active system prompt: last-known-good after a failed reload."""
        if self._state is not None:
            return self._state.system_prompt
        return ""

    @property
    def enabled_spells(self) -> list[str]:
        spells = self._build_spells()
        if spells:
            return [s.name for s in spells]
        if self._enabled_spells_filter is not None:
            return list(self._spell_names or self._enabled_spells_filter)
        return []

    @property
    def available_spells(self) -> list[str]:
        """List of all available spell names (builtin + rune-registered).

        In plan mode this reflects the plan-mode filter: only spells
        marked read_only are reported as available.
        """
        if self._plan_mode:
            return [s.name for s in self._build_spells()]
        names: list[str] = [coerce_spell(s).name for s in self._spells]
        if self._runner is not None:
            names.extend([s.name for s in self._runner.get_all_registered_spells()])
        return list(dict.fromkeys(names))

    def set_plan_mode(self, enabled: bool) -> None:
        """Enable or disable plan mode (read-only spells only).

        When enabled, _build_spells() drops every spell not marked
        read_only=True. MvgeState.spells refreshes immediately and the
        harness is rebuilt through the normal construction path, so the
        next run's LoopContext carries the new spell set. A run already
        in flight keeps the LoopContext it was built with. Enabling plan
        mode with no read-only spells leaves the agent without tools and
        logs a warning.
        """
        self._plan_mode = enabled
        spells = self._build_spells()
        if self._state is not None:
            self._state.spells = spells
            if self._harness is not None:
                self._harness = self._build_harness()
                self._compaction = self._harness.compaction
        if enabled and not spells:
            logger.warning(
                "Plan mode enabled but no spells are marked read-only; "
                "the agent has no tools available in this mode.",
            )

    def set_enabled_spells(self, spell_names: Sequence[str]) -> None:
        """Filter which spells are enabled for execution."""
        self._enabled_spells_filter = set(spell_names)
        self._spell_names = list(spell_names)
        if self._state is not None:
            self._state.spells = self._build_spells()

    def reload_spells(self) -> None:
        """Re-run spell resolution with full validation and update state.

        Called after Rune watcher reload, manual load_runes(), or any event
        that may have changed the rune-registered spell set. Re-validates all
        spells (name, schema, signature) and applies collision renaming,
        then refreshes MvgeState.spells and the internal spell index.
        """
        if self._state is not None:
            self._state.spells = self._build_spells()
            # Refresh the spell index used by the dispatcher
            self._state._spell_index = {s.name: s for s in self._state.spells}

    async def reload(self) -> ReloadResult:
        """Rebuild the runtime mid-session: runes, prompt, spells, harness.

        The sole engine-owned reload mechanism; the file watcher is only a
        trigger that calls this. Steps, in order:

        1. Turn boundary: a reload requested mid-turn queues until the turn
           completes; the in-flight turn keeps its LoopContext.
        2. Prompt rebuild: the base SYSTEM.md discovery chain is
           re-resolved from pristine pre-sigil content, and the
           prompt-mutating BEFORE_MVGE_START hook fires exactly once for
           the rebuild.
        3. Spell rediscovery: per-file import failures are diagnosed and
           the file dropped, never aborting the reload.
        4. Rune rehydrate: hook-derived instance state is refreshed from a
           fresh payload without refiring the prompt-mutating hook.
        5. Harness rebuild when the prompt or spell set changed.
        6. Transactional: every fallible step validates on a candidate
           before the swap; a failed reload preserves last-known-good
           state and surfaces the exact diagnostic. The swap itself is
           synchronous.
        7. Watcher handover: the candidate lifecycle's watchers start
           after the swap (covering trigger dirs that appeared since
           initialization); a handover failure is audited as a loud
           failure with the new state live.

        Every executed reload writes exactly one record to the user-scope
        audit log (``$MVGEOS_GLOBAL_DIR/extensions/audit.jsonl`` when set,
        else ``~/.agents/extensions/audit.jsonl``). A reload queued
        mid-turn is audited when it executes at the turn boundary, and
        concurrent queued requests coalesce into that single execution.
        Concurrent direct calls serialize on a lock: the second caller
        waits for the in-progress execution, then runs its own — builds
        never interleave over the shared provider registry.
        A reload rejected before initialization performs no work and
        writes no record.
        """
        if self._run_in_flight:
            self._reload_pending = True
            return ReloadResult(
                ok=True,
                queued=True,
                message=(
                    "Reload queued: a turn is in flight; it applies at the "
                    "next turn boundary."
                ),
            )
        if not self._initialized:
            return ReloadResult(
                ok=False, message="Agent not initialized; nothing to reload."
            )
        # Serialize executions: concurrent reload() calls must never
        # interleave their builds over the shared provider registry —
        # a failed build restores the registry snapshot, which would
        # clobber a concurrent successful build. The mid-turn queue
        # check and the pre-init rejection above stay outside the lock:
        # they touch no shared build state. A second caller waits for
        # the in-progress execution, then runs its own and receives
        # its own truthful outcome.
        async with self._reload_lock:
            return await self._reload_locked()

    async def _reload_locked(self) -> ReloadResult:
        """Execute one reload; the caller holds ``self._reload_lock``.

        Split out of :meth:`reload` so the lock wraps the whole
        execution without re-indenting the body. Every return here is
        an audited outcome of exactly one execution.
        """
        # Provider registrations are the one shared-mutable step of the
        # build (rune factories re-execute against the shared registry):
        # snapshot first so any build failure rolls them back.
        provider_snapshot = _snapshot_provider_registrations(self._provider_registry)
        try:
            rebuilt = await self._build_reload_state()
        except Exception as exc:
            _restore_provider_registrations(self._provider_registry, provider_snapshot)
            diagnostic = f"Reload failed: {exc}"
            logger.exception("Reload failed; last-known-good state preserved")
            self._reload_diagnostics = [
                Diagnostic(
                    kind=DiagnosticKind.LOAD_FAILURE,
                    rune_name="engine",
                    message=diagnostic,
                )
            ]
            audit_error = self._audit_reload(
                ok=False, code="reload_failed", message=diagnostic
            )
            message = diagnostic + "; last-known-good state preserved."
            if audit_error is not None:
                message += f" ({audit_error})"
            return ReloadResult(ok=False, message=message)
        old_lifecycle = self._apply_reload_state(rebuilt)
        # Watcher handover: the candidate lifecycle (already swapped in)
        # starts its watchers — rune paths plus the trigger dirs computed
        # from the NEW active spells dir, so a spells dir that appeared
        # since initialization is picked up. The retired lifecycle's
        # watchers stop afterwards. There is a brief overlap where both
        # sets are live: both fire the same engine reload, which is
        # idempotent, so duplicate triggers only mean duplicate reloads.
        watcher_error: str | None = None
        if self._rune_lifecycle is not None:
            try:
                await self._rune_lifecycle.start()
            except Exception as exc:
                watcher_error = f"watcher re-sync failed: {exc}"
                logger.exception(
                    "Reload applied but watcher re-sync failed; "
                    "file-triggered reloads are paused until the next "
                    "successful reload"
                )
        retired_shutdown_error: str | None = None
        if old_lifecycle is not None:
            # Bounded retry: a transient observer-teardown failure should
            # not leave zombie watchers behind without trying again.
            # Only when every attempt fails is the error recorded — the
            # new state is still live either way.
            for attempt in range(_RETIRED_SHUTDOWN_ATTEMPTS):
                try:
                    await old_lifecycle.shutdown()
                    retired_shutdown_error = None
                    break
                except Exception as exc:
                    retired_shutdown_error = f"retired watcher shutdown failed: {exc}"
                    if attempt == _RETIRED_SHUTDOWN_ATTEMPTS - 1:
                        logger.exception(
                            "Retired rune watchers did not shut down "
                            "cleanly after reload"
                        )
                    else:
                        await asyncio.sleep(_RETIRED_SHUTDOWN_BACKOFF_SECONDS)
        if watcher_error is not None:
            # The swap succeeded — the new state IS live — but the
            # post-swap watcher handover failed. Per the §4.6 audit
            # pattern the result is a loud failure that states plainly
            # what is live and what is not.
            message = f"{rebuilt.summary}; reload IS live but {watcher_error}."
            audit_error = self._audit_reload(
                ok=False,
                code="watcher_sync_failed",
                message=message,
                extra={
                    "prompt_changed": rebuilt.prompt_changed,
                    "spells_changed": rebuilt.spells_changed,
                    "rune_diagnostics": len(rebuilt.rune_diagnostics),
                    "rehydrate_errors": rebuilt.rehydrate_errors,
                    "spell_file_warnings": rebuilt.spell_file_warnings,
                },
            )
            if audit_error is not None:
                message += f" ({audit_error})"
            self._reload_diagnostics = [
                Diagnostic(
                    kind=DiagnosticKind.LOAD_FAILURE,
                    rune_name="engine",
                    message=message,
                )
            ]
            return ReloadResult(ok=False, message=message)
        audit_error = self._audit_reload(
            ok=True,
            code="reload_ok",
            message=rebuilt.summary,
            extra={
                "prompt_changed": rebuilt.prompt_changed,
                "spells_changed": rebuilt.spells_changed,
                "rune_diagnostics": len(rebuilt.rune_diagnostics),
                "rehydrate_errors": rebuilt.rehydrate_errors,
                "spell_file_warnings": rebuilt.spell_file_warnings,
                # None unless the retired lifecycle's watchers failed to
                # stop; the new state is still live in that case.
                "retired_shutdown_error": retired_shutdown_error,
            },
        )
        if audit_error is not None:
            # §4.6, normative: an audit append failure turns the result
            # into audit_failed (ok=False), never a silent ok. The
            # mutation succeeded — the message states plainly that the
            # reload IS live but unrecorded.
            message = (
                f"audit_failed: {audit_error}; {rebuilt.summary} — "
                "the reload IS live but unrecorded."
            )
            self._reload_diagnostics = [
                Diagnostic(
                    kind=DiagnosticKind.LOAD_FAILURE,
                    rune_name="engine",
                    message=message,
                )
            ]
            return ReloadResult(
                ok=False,
                message=message,
                prompt_changed=rebuilt.prompt_changed,
                spells_changed=rebuilt.spells_changed,
            )
        self._reload_diagnostics = [
            *rebuilt.rune_diagnostics,
            *(
                Diagnostic(
                    kind=DiagnosticKind.LOAD_FAILURE,
                    rune_name="engine",
                    message=warning,
                )
                for warning in rebuilt.spell_file_warnings
            ),
            *(
                Diagnostic(
                    kind=DiagnosticKind.LOAD_FAILURE,
                    rune_name="engine",
                    message=error,
                )
                for error in rebuilt.rehydrate_errors
            ),
            *(
                [
                    Diagnostic(
                        kind=DiagnosticKind.LOAD_FAILURE,
                        rune_name="engine",
                        message=retired_shutdown_error,
                    )
                ]
                if retired_shutdown_error is not None
                else []
            ),
        ]
        return ReloadResult(
            ok=True,
            message=rebuilt.summary,
            prompt_changed=rebuilt.prompt_changed,
            spells_changed=rebuilt.spells_changed,
        )

    async def _build_reload_state(self) -> _ReloadedState:
        """Build the validated candidate state for a reload.

        Raises on any failure; the caller preserves last-known-good state.
        Nothing here mutates the live agent: rune factories are
        re-executed on a candidate runner owned by a candidate lifecycle,
        the prompt-mutating hook fires on that candidate, rehydrate
        touches only candidate instances, and the candidate harness is
        built before the swap. Rune-declared providers are re-registered
        on the shared provider registry as at construction, but the
        pre-load state is snapshotted and restored if the build fails, so
        a failed reload leaves no provider trace behind.
        """
        # 1. Rediscover spells, tolerantly: per-file failures become
        #    warnings and the file is dropped, never aborting the reload.
        spell_file_warnings: list[str] = []

        def _on_file_error(path: Path, exc: Exception) -> None:
            spell_file_warnings.append(f"{path.name}: {exc}")

        discovered, active_dir = self._discover_spells_impl(
            on_file_error=_on_file_error
        )

        # 2. Re-resolve the pristine base prompt (pre-sigil content) using
        #    the exact discovery inputs recorded at construction.
        fresh_prompt = resolve_system_prompt(**self._prompt_resolve_kwargs)
        new_environment = dataclasses.replace(
            self._environment,
            resolved_prompt=fresh_prompt,
            active_spells_dir=active_dir,
        )

        # 3. Candidate rune lifecycle: a fresh runner with every factory
        #    re-executed from current disk state. The live runner and its
        #    instances stay untouched until the swap, so a later failure
        #    still preserves last-known-good rune state.
        candidate_lifecycle: RuneLifecycle | None = None
        candidate_runner: RuneRunner | None = self._runner
        rune_diagnostics: list[Diagnostic | SkillDiagnostic] = []
        if self._rune_lifecycle is not None:
            candidate_lifecycle = RuneLifecycle(
                agent_name=self._name,
                api_key=self._api_key,
                rune_layers=self._rune_layers,
                environment=new_environment,
                provider_registry=self._provider_registry,
                reload_callback=self.reload,
                extra_watch_dirs=self._reload_trigger_dirs(active_dir),
            )
            await candidate_lifecycle.load()
            candidate_runner = candidate_lifecycle.runner
            if candidate_runner is not None:
                rune_diags = list(candidate_runner.diagnostics)
                rune_diagnostics = [
                    *rune_diags,
                    *candidate_runner.skill_catalog.skill_diagnostics,
                ]
                # A previously working rune that no longer loads (broken
                # manifest or factory, reported as a diagnostic) fails the
                # whole candidate: the spec grants rune refresh no tolerant
                # drop semantics, so last-known-good is preserved. A rune
                # deleted from disk (no diagnostic) is a legitimate
                # removal and proceeds.
                if self._runner is not None:
                    live_names = {m.name for m in self._runner.loaded_manifests}
                    candidate_names = {
                        m.name for m in candidate_runner.loaded_manifests
                    }
                    lost = live_names - candidate_names
                    broken = sorted(lost & {d.rune_name for d in rune_diags})
                    if broken:
                        raise RuntimeError(
                            "previously loaded rune(s) no longer load: "
                            + ", ".join(broken)
                        )
            if candidate_lifecycle.environment is not None:
                new_environment = candidate_lifecycle.environment

        # Gateway policy on the candidate, before spell/prompt assembly
        # (the startup order): an explicitly configured live allowlist is
        # transferred so user/harness policy survives the reload; a
        # gateway-engaged view is re-derived from the candidate's runes so
        # a removed or changed gateway rune cannot leave a stale view
        # behind. Ephemeral widenings are re-discoverable after reload.
        gateway_rune = self._gateway_engaged_rune
        if candidate_runner is not None and candidate_lifecycle is not None:
            live_allowlist = (
                self._runner.get_global_spell_allowlist()
                if self._runner is not None
                else None
            )
            if live_allowlist is not None and self._gateway_engaged_rune is None:
                candidate_runner.set_global_spell_allowlist(live_allowlist)
            else:
                gateway_rune = _apply_gateway_allowlist(candidate_runner)

        new_spells = self._build_spells_from(discovered, runner=candidate_runner)
        # The prompt-mutating hook fires exactly once for this rebuild,
        # on the candidate runner — never on the live instances.
        new_prompt = await self._assemble_prompt_async(
            new_environment, new_spells, active_dir, runner=candidate_runner
        )

        # 4. Fresh sigil payload for rehydration: hook-derived rune
        #    instance state is restored on the candidate WITHOUT refiring
        #    the prompt-mutating hook.
        payload = new_environment.build_sigil_payload(
            base_prompt=fresh_prompt.text,
            spell_names=[s.name for s in new_spells],
            config_dir=self.config_dir,
            custom_prompt=self._custom_system_prompt,
            cwd=self._effective_cwd(),
            active_spells_dir=active_dir,
        )
        rehydrate_errors: list[str] = []
        if candidate_runner is not None:
            rehydrate_errors = await candidate_runner.rehydrate_runes(payload)

        old_prompt = self._state.system_prompt if self._state is not None else None
        old_spell_names = (
            sorted(s.name for s in self._state.spells)
            if self._state is not None
            else []
        )
        new_spell_names = sorted(s.name for s in new_spells)
        prompt_changed = old_prompt is not None and new_prompt != old_prompt
        spells_changed = new_spell_names != old_spell_names
        summary = (
            f"Reload complete: prompt "
            f"{'changed' if prompt_changed else 'unchanged'}, "
            f"{len(new_spell_names)} spell(s) "
            f"({'changed' if spells_changed else 'unchanged'})"
        )
        if rune_diagnostics:
            summary += f", {len(rune_diagnostics)} rune diagnostic(s)"
        if rehydrate_errors:
            summary += f", {len(rehydrate_errors)} rehydrate error(s)"
        if spell_file_warnings:
            summary += f", {len(spell_file_warnings)} spell file(s) skipped"

        # 5. Candidate harness, built BEFORE the swap: a construction
        #    failure here still preserves last-known-good state. The
        #    harness holds the live state object by reference and reads
        #    prompt/spells per turn, so building it pre-swap is exact.
        #    Rebuilt only when the reload changed something the harness
        #    captured (prompt, spell set), per the set_plan_mode
        #    precedent.
        candidate_harness: MvgeHarness | None = None
        if self._harness is not None and (prompt_changed or spells_changed):
            candidate_harness = self._build_harness()

        return _ReloadedState(
            environment=new_environment,
            prompt_source=fresh_prompt.source,
            prompt=new_prompt,
            discovered_spells=discovered,
            built_spells=new_spells,
            active_spells_dir=active_dir,
            rune_diagnostics=rune_diagnostics,
            rehydrate_errors=rehydrate_errors,
            spell_file_warnings=spell_file_warnings,
            prompt_changed=prompt_changed,
            spells_changed=spells_changed,
            summary=summary,
            runner=candidate_runner,
            lifecycle=candidate_lifecycle,
            harness=candidate_harness,
            gateway_rune=gateway_rune,
        )

    def _commit_candidate_providers(
        self,
        live_runner: RuneRunner | None,
        candidate_runner: RuneRunner,
    ) -> None:
        """Replace rune-owned provider registrations with the candidate's exact state.

        ``RealmRegistry.register_provider()`` merges configs (``dict.update``),
        so a successful reload must *replace* — not merge — the entries the
        candidate build touched, and drop entries whose rune is gone.
        Ownership is determined from the runners: names registered on the
        live or candidate runner are rune-owned; anything else in the
        registry is unrelated non-rune state and is left alone.
        """
        registry = self._provider_registry
        candidate_providers = candidate_runner.get_registered_providers()
        candidate_factories = candidate_runner.get_registered_realm_factories()
        live_provider_names = (
            set(live_runner.get_registered_providers())
            if live_runner is not None
            else set()
        )
        live_factory_prefixes = (
            set(live_runner.get_registered_realm_factories())
            if live_runner is not None
            else set()
        )
        for name, config in candidate_providers.items():
            # Replace, not merge: keys the rune dropped must not survive.
            registry.unregister_provider(name)
            registry.register_provider(name, dict(config))
        for name in live_provider_names - set(candidate_providers):
            # The providing rune is gone; its registration must not survive.
            registry.unregister_provider(name)
        for prefix, factory in candidate_factories.items():
            # Realm factories already replace on register; re-register to
            # make the candidate's factory the live one.
            registry.register_realm_factory(prefix, factory)
        for prefix in live_factory_prefixes - set(candidate_factories):
            registry.unregister_realm_factory(prefix)

    def _apply_reload_state(self, rebuilt: _ReloadedState) -> RuneLifecycle | None:
        """Swap in validated reload state.

        Synchronous so the swap cannot interleave with another task: the
        candidate was fully validated before this runs. Returns the
        retired lifecycle so the caller can hand its watchers over to
        the candidate's after the swap.
        """
        old_lifecycle = self._rune_lifecycle
        self._environment = rebuilt.environment
        self._prompt_source = rebuilt.prompt_source
        self._spells = rebuilt.discovered_spells
        self._active_spells_dir = rebuilt.active_spells_dir
        if self._state is not None:
            self._state.system_prompt = rebuilt.prompt
            self._state.spells = rebuilt.built_spells
            self._state._spell_index = {s.name: s for s in rebuilt.built_spells}
        if rebuilt.lifecycle is not None and rebuilt.runner is not None:
            # A genuine candidate: swap the runner with its wiring and
            # retire the old lifecycle. (Without a candidate lifecycle the
            # live runner stays exactly as it was.)
            self._commit_candidate_providers(self._runner, rebuilt.runner)
            self._runner = rebuilt.runner
            self._runner.on_event(
                "mvge_event",
                lambda ev: self._event_bus.emit(ev.type, ev.data),
            )
            self._gateway_engaged_rune = rebuilt.gateway_rune
            self._rune_lifecycle = rebuilt.lifecycle
        if rebuilt.harness is not None:
            self._harness = rebuilt.harness
            self._compaction = rebuilt.harness.compaction
        return old_lifecycle

    def _audit_reload(
        self,
        *,
        ok: bool,
        code: str,
        message: str,
        extra: dict[str, Any] | None = None,
    ) -> str | None:
        """Append one reload record to the user-scope audit log.

        The ``rune`` field carries the engine's own identity (``"engine"``):
        reload is harness machinery, and no rune can emit it —
        ``RuneAPI.audit`` always stamps the calling rune's manifest name.

        Returns an error description when the durable append fails, else
        None. Audit-write failure is never silent: the caller surfaces it
        loudly in the result message and diagnostics.
        """
        record: dict[str, Any] = dict(extra) if extra else {}
        record.update(
            {
                "timestamp": utcnow(),
                "rune": "engine",
                "op": "reload",
                "outcome": "ok" if ok else "failed",
                "code": code,
                "target": None,
                "message": message,
            }
        )
        try:
            RuneAuditLog(default_rune_ops_dir()).append_event(record)
        except Exception as exc:
            # Audit failure is never silent and never fatal to the reload:
            # log it loudly and return a description so the caller turns the
            # result into a diagnosed audit_failed outcome.
            logger.exception("Reload audit append failed")
            return f"audit append failed: {type(exc).__name__}: {exc}"
        return None

    @property
    def reload_pending(self) -> bool:
        """Whether a reload is queued for the next turn boundary."""
        return self._reload_pending

    @property
    def registered_commands(self) -> list[str]:
        if self._runner is None:
            return []
        return [c.name for c in self._runner.get_commands()]

    def get_registered_commands(self) -> list[RegisteredCommand]:
        """Return all RegisteredCommand objects from the runner."""
        if self._runner is None:
            return []
        return self._runner.get_commands()

    @property
    def registered_shortcuts(self) -> list[RuneShortcut]:
        if self._runner is None:
            return []
        return self._runner.get_shortcuts()

    @property
    def registered_providers(self) -> list[str]:
        return self._provider_registry.get_registered_providers()

    @property
    def model_registry(self) -> ModelRegistry:
        """Active model registry bound to the agent's realm registry."""
        return self._provider_registry.model_registry

    @property
    def name(self) -> str:
        return self._name

    @property
    def config_dir(self) -> Path:
        if self._config_manager is not None and getattr(
            self._config_manager, "agent_config_path", None
        ):
            return self._config_manager.agent_config_path.parent
        return resolve_config_dir(self._name)

    @property
    def environment(self) -> MvgeEnvironment:
        return self._environment

    @property
    def spells(self) -> list[SpellUnion]:
        return list(self._spells)

    @property
    def event_bus(self) -> EventBus:
        return self._event_bus

    @property
    def runner(self) -> RuneRunner | None:
        """The engine-owned rune runner, once runes have loaded.

        Read-only host access so the host can bind host-privileged slots
        (e.g. the approval presenter) on the engine-owned runner object.
        The runner itself stays engine-owned; ``set_runner`` remains the
        injection point.
        """
        return self._runner

    @property
    def harness(self) -> MvgeHarness | None:
        """Access to the deepened Harness seam for observability (snapshot, events)."""
        return self._harness

    @property
    def diagnostics(self) -> list[Diagnostic | SkillDiagnostic]:
        diags: list[Diagnostic | SkillDiagnostic] = list(self._resume_diagnostics)
        diags.extend(self._reload_diagnostics)
        if self._environment is not None and self._environment.diagnostics:
            for d in self._environment.diagnostics:
                if d not in diags:
                    diags.append(d)
        return diags

    def set_environment(self, environment: MvgeEnvironment) -> None:
        """Replace the environment and update config_manager reference."""
        self._environment = environment
        self._config_manager = environment.config_manager

    def set_config_manager(self, config_manager: Any) -> None:
        """Replace the config manager on both agent and environment."""
        self._config_manager = config_manager
        if self._environment is not None:
            self._environment = dataclasses.replace(
                self._environment, config_manager=config_manager
            )

    def set_runner(self, runner: RuneRunner | None) -> None:
        """Inject an explicit RuneRunner."""
        self._runner = runner
        if runner is not None:
            runner.on_event(
                "mvge_event", lambda ev: self._event_bus.emit(ev.type, ev.data)
            )
        if self._rune_lifecycle is not None:
            self._rune_lifecycle._runner = runner

    async def load_runes(self) -> None:
        """Explicitly initialize runes and skills."""
        await self._load_runes()

    def _reload_trigger_dirs(self, active_spells_dir: Path | None) -> list[Path]:
        """Directories whose changes must trigger an engine reload.

        The configured rune extension directories are watched by the
        lifecycle itself; this adds the resolved active spells directory
        (new/changed spell files) and the agent config directory
        (SYSTEM.md edits by revise_persona/teach). Only existing
        directories can be watched. The active dir is passed explicitly
        so reload can compute the candidate set before swapping.
        """
        dirs = [self.config_dir]
        if active_spells_dir is not None:
            dirs.append(active_spells_dir)
        return [d for d in dirs if d.is_dir()]

    async def _load_runes(self) -> None:
        """Initialize the RuneLifecycle collaborator."""
        if self._rune_lifecycle is None:
            self._rune_lifecycle = RuneLifecycle(
                agent_name=self._name,
                api_key=self._api_key,
                rune_layers=self._rune_layers,
                environment=self._environment,
                provider_registry=self._provider_registry,
                runner=self._runner,
                reload_callback=self.reload,
                extra_watch_dirs=self._reload_trigger_dirs(self._active_spells_dir),
            )
        await self._rune_lifecycle.load()
        await self._rune_lifecycle.start()
        self._runner = self._rune_lifecycle.runner
        if self._runner is not None:
            self._runner.on_event(
                "mvge_event", lambda ev: self._event_bus.emit(ev.type, ev.data)
            )
            self._gateway_engaged_rune = _apply_gateway_allowlist(self._runner)
        if self._rune_lifecycle.environment is not None:
            self._environment = self._rune_lifecycle.environment
        if self._environment.diagnostics:
            self._resume_diagnostics = list(self._environment.diagnostics)

    def validate_tome_compatibility(
        self,
        tome_id_or_meta: str | TomeMetadata,
    ) -> SessionCompatibilityReport:
        """Inspect compatibility of a target tome against active configuration."""
        if isinstance(tome_id_or_meta, TomeMetadata):
            meta = tome_id_or_meta
        else:
            try:
                loaded = self._tome_factory.open_tome(tome_id_or_meta)
            except TomeVersionError as e:
                raise TomeResumeError(tome_id_or_meta) from e
            if loaded is None:
                raise TomeResumeError(tome_id_or_meta)
            meta = loaded

        active_spells = [s.name for s in self._build_spells()]
        if self._runner is not None:
            active_spells.extend(
                [s.name for s in self._runner.get_all_registered_spells()]
            )
        active_spells = list(dict.fromkeys(active_spells))

        if self._model is not None:
            model_id = self._model.id
        elif self._model_id:
            try:
                resolved_model, _ = self._provider_registry.resolve(
                    self._model_id, self._api_key, self._provider_name
                )
                model_id = resolved_model.id
            except Exception:
                model_id = self._model_id
        else:
            model_id = None

        entries = self._tome_factory.get_entries(meta.id)
        return validate_session_compatibility(
            meta,
            expected_model=model_id,
            expected_contemplation=str(self._contemplation_level),
            expected_spells=active_spells,
            entries=entries,
        )

    def build_snapshot(self) -> RuntimeSnapshot:
        """Assemble a resolved runtime snapshot of the agent's surface."""
        spells: list[MvgeSpell | SpellDefinition] = cast(
            list[MvgeSpell | SpellDefinition], self._build_spells()
        )
        env = MvgeEnvironment.resolve(
            self._name,
            config_dir=self.config_dir,
            project_dir=getattr(self._config_manager, "_project_dir", None),
            custom_prompt=getattr(self, "_custom_system_prompt", ""),
            spells=spells,
            runner=self._runner,
            config_manager=self._config_manager,
            has_config_manager=(self._config_manager is not None),
        )
        return env.build_snapshot()

    def on(
        self,
        event_type: MvgeEventType | str,
        callback: Callable[[MvgeEvent], None],
    ) -> Callable[[], None]:
        if isinstance(event_type, str):
            event_type = MvgeEventType(event_type)
        return self._event_bus.on(event_type, callback)

    @property
    def queue_mode(self) -> QueueMode:
        return self._queue_mode

    @queue_mode.setter
    def queue_mode(self, mode: QueueMode | str) -> None:
        if isinstance(mode, str):
            mode = QueueMode(mode)
        self._queue_mode = mode

    def steer(self, text: str) -> None:
        if self._state is not None:
            self._state.steer_queue.append(SummonerRequest(role="user", content=text))

    def follow_up(self, text: str) -> None:
        if self._state is not None:
            self._state.followup_queue.append(
                SummonerRequest(role="user", content=text)
            )

    def abort(self) -> None:
        if self._abort_controller is not None:
            self._abort_controller.abort()

    def _build_spells(self) -> list[MvgeSpell]:
        """Convert injected callables and rune spells to executable MvgeSpells."""
        return self._build_spells_from(self._spells)

    def _build_spells_from(
        self,
        spells: Sequence[SpellUnion],
        runner: RuneRunner | None = None,
    ) -> list[MvgeSpell]:
        """Build executable spells from an explicit spell list.

        Lets reload validate a rebuilt spell set before swapping it in;
        the normal path passes the live spell list. ``runner`` selects
        which runner's registered spells are merged: the live runner by
        default, the candidate runner during a reload build.
        """
        built: list[MvgeSpell] = []
        for s in spells:
            spell = coerce_spell(s)
            if not _validate_spell_name(spell.name):
                logger.warning(
                    "Spell '%s' has an invalid name (must match ^[a-zA-Z0-9_-]+$) "
                    "and will be skipped.",
                    spell.name,
                )
                continue
            built.append(spell)

        seen_names: set[str] = {s.name for s in built}

        active_runner = runner if runner is not None else self._runner
        if active_runner is not None:
            active = set(active_runner.get_active_spells())
            for rs in active_runner.get_all_registered_spells():
                if rs.name not in active:
                    continue

                if not _validate_spell_name(rs.name):
                    logger.warning(
                        "Rune spell '%s' has an invalid name "
                        "(must match ^[a-zA-Z0-9_-]+$) and will be skipped.",
                        rs.name,
                    )
                    continue

                if not _validate_spell_parameters(getattr(rs, "parameters", None)):
                    logger.warning(
                        "Rune spell '%s' has an invalid parameter schema "
                        "and will be skipped.",
                        rs.name,
                    )
                    continue

                if not _validate_spell_signature(rs):
                    logger.warning(
                        "Rune spell '%s' execution signature does not conform "
                        "to the execution contract and will be skipped.",
                        rs.name,
                    )
                    continue

                spell_to_add: MvgeSpell = RuneSpellWrapper(rs, runner_origin=True)
                spell_name = rs.name
                if spell_name in seen_names:
                    source_rune = getattr(rs, "source_rune", None) or "rune"
                    prefixed_name = f"{source_rune}_{spell_name}"
                    logger.warning(
                        "Rune spell '%s' collides with existing spell; "
                        "renaming to '%s'.",
                        spell_name,
                        prefixed_name,
                    )
                    if not _validate_spell_name(prefixed_name):
                        logger.warning(
                            "Prefixed rune spell '%s' has an invalid name "
                            "and will be skipped.",
                            prefixed_name,
                        )
                        continue
                    if prefixed_name in seen_names:
                        logger.warning(
                            "Prefixed rune spell '%s' still collides with an "
                            "existing spell and will be skipped.",
                            prefixed_name,
                        )
                        continue
                    spell_to_add = copy.copy(spell_to_add)
                    spell_to_add.name = prefixed_name
                    spell_name = prefixed_name

                seen_names.add(spell_name)
                built.append(spell_to_add)

        if self._enabled_spells_filter is not None:
            built = [s for s in built if s.name in self._enabled_spells_filter]

        if active_runner is not None:
            global_allowlist = active_runner.get_global_spell_allowlist()
            if global_allowlist is not None:
                allowlist_set = set(global_allowlist)
                built = [s for s in built if s.name in allowlist_set]

        if self._plan_mode:
            built = [s for s in built if s.read_only]

        return built

    def _build_system_prompt(self) -> str:
        """Build the agent system prompt string."""
        return self._environment.resolved_prompt.text

    async def _build_system_prompt_async(self) -> str:
        """Async version that supports rune prompt injection via sigil hooks."""
        return await self._assemble_prompt_async(
            self._environment, self._build_spells(), self._active_spells_dir
        )

    def _effective_cwd(self) -> Path:
        if (
            self._config_manager is not None
            and self._config_manager.project_dir is not None
        ):
            return self._config_manager.project_dir
        return Path.cwd()

    async def _assemble_prompt_async(
        self,
        environment: MvgeEnvironment,
        spells: Sequence[MvgeSpell],
        active_spells_dir: Path | None,
        runner: RuneRunner | None = None,
    ) -> str:
        """Assemble the system prompt against an explicit environment.

        Lets reload validate a fully rebuilt prompt before swapping it in;
        the normal path passes the live environment and spell set.
        ``runner`` selects which runner's BEFORE_MVGE_START chain fires:
        the live runner by default, the candidate runner during a reload
        build (so the hook never touches live rune instances pre-swap).
        """
        active_names = [s.name for s in spells]
        return await environment.assemble_system_prompt(
            runner=runner if runner is not None else self._runner,
            base_prompt=environment.resolved_prompt.text,
            custom_prompt=getattr(self, "_custom_system_prompt", ""),
            cwd=self._effective_cwd(),
            spell_names=active_names,
            config_dir=self.config_dir,
            active_spells_dir=active_spells_dir,
        )

    async def _run_impl(self) -> MvgeInvocation:
        """Turn-processing logic using the harness."""
        assert self._model is not None
        assert self._realm is not None
        assert self._state is not None
        assert self._harness is not None

        stream_fn = self._make_stream_fn(
            self._model,
            self._realm,
            self._state,
            self._temperature,
            self._max_tokens,
        )

        if self._abort_controller is not None:
            self._abort_controller.abort()
        self._abort_controller = AbortController()
        signal = self._abort_controller.signal

        self._run_in_flight = True
        try:
            return await self._harness.run(
                stream_fn,
                model=dataclasses.asdict(self._model),
                contemplation_level=self._contemplation_level,
                signal=signal,
            )
        finally:
            self._run_in_flight = False
            if self._reload_pending:
                # Turn boundary: the in-flight turn kept its LoopContext;
                # the queued reload applies now. The flag clears only after
                # reload() returns an audited outcome — if it raises, the
                # request stays pending so the next boundary retries it
                # instead of silently dropping it.
                try:
                    await self.reload()
                except Exception:
                    logger.exception("Queued reload failed at turn boundary")
                else:
                    self._reload_pending = False

    def _make_stream_fn(
        self,
        model: Model,
        realm: Realm,
        state: MvgeState,
        temperature: float,
        max_tokens: int,
    ) -> StreamFn:
        spells = [
            {
                "type": "function",
                "function": {
                    "name": spell.name,
                    "description": spell.description,
                    "parameters": spell.parameters,
                },
            }
            for spell in state.spells
            if spell.parameters
        ]

        def stream_fn(
            invocations: list[MvgeInvocation],
            signal: AbortSignal | None = None,
        ) -> AsyncIterator[RealmResponse]:
            return realm.stream(
                model=model,
                invocations=invocations,
                config=ChannelConfig(
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    contemplation_level=(
                        state.contemplation_level.value
                        if isinstance(state.contemplation_level, ContemplationLevel)
                        else str(state.contemplation_level)
                    ),
                    contemplation_budget=state.contemplation_budget,
                    exclude_contemplation=state.exclude_contemplation,
                    spells=spells,
                    system_prompt=state.system_prompt,
                ),
                signal=signal,
            )

        return stream_fn

    async def initialize(self) -> None:
        """Wire lifecycle modules into a running agent."""
        if self._initialized:
            return

        if not self._api_key:
            self._api_key = _resolve_api_key(
                realm=serving_realm_for_model(self._model_id)
            )
        realm = serving_realm_for_model(self._model_id)
        if not self._api_key and model_requires_credential(self._model_id):
            # Named from the Realm being served, not from a default. The
            # message this replaces named OPENROUTER_API_KEY regardless of which
            # Realm wanted a credential, so a Summoner on `opencode` was sent to
            # set a key that Realm never reads. Read off the serving Realm rather
            # than the slug, so a routed slug names the Realm that will actually
            # be dispatched to.
            raise MissingApiKeyError(
                realm=realm, api_key_env=api_key_env_for_realm(realm)
            )

        await self._load_runes()

        final_prompt = await self._build_system_prompt_async()
        self._model, self._realm = self._provider_registry.resolve(
            self._model_id, self._api_key, self._provider_name
        )
        active_spells = [s.name for s in self._build_spells()]
        if self._runner is not None:
            active_spells.extend(
                [s.name for s in self._runner.get_all_registered_spells()]
            )
        active_spells = list(dict.fromkeys(active_spells))

        model_id = self._model.id if self._model is not None else self._model_id
        self._agent_tome = await MvgeTome.open_or_create(
            self._tome_factory,
            self._tome_resume,
            # Record the agent's project directory, not the host process cwd:
            # embedders (e.g. the GUI) run the engine in-process with a
            # project path that differs from os.getcwd(). The project filter
            # in session listings matches on this value.
            cwd=self._effective_cwd(),
            runner=self._runner,
            model=model_id,
            contemplation_level=str(self._contemplation_level),
            spells=active_spells,
            strict=self._strict_resume,
            force_fork=self._force_fork_resume,
        )

        self._rebind_runner_context()

        if (
            self._agent_tome is not None
            and self._agent_tome.compatibility_report is not None
        ):
            self._resume_diagnostics = list(
                self._agent_tome.compatibility_report.diagnostics
            )

        self._wire_runtime(final_prompt)
        self._initialized = True

    def _rebind_runner_context(self) -> None:
        """Synchronize active session/tome context into the bound RuneRunner."""
        if self._runner is not None and self._agent_tome is not None:
            project_root = self._runner.context.project_root
            config_manager = getattr(self, "_config_manager", None)
            configured = getattr(config_manager, "project_dir", None)
            if configured is not None:
                project_root = str(Path(configured).resolve())
            new_ctx = dataclasses.replace(
                self._runner.context,
                project_root=project_root,
                session_id=self._agent_tome.tome_id,
                tome_dir=str(self._tome_dir),
                model_id=self._model_id,
            )
            self._runner.bind_context(new_ctx)

    def _wire_runtime(self, final_prompt: str) -> None:
        """Construct MvgeState and MvgeHarness from the wired collaborators."""
        assert self._model is not None
        assert self._realm is not None
        assert self._agent_tome is not None

        initial_invocations: list[MvgeInvocation] = []
        if self._tome_resume:
            initial_invocations = self._agent_tome.reconstruct_invocations()

        self._state = MvgeState(
            system_prompt=final_prompt,
            prompt_source=self._prompt_source,
            model=dataclasses.asdict(self._model),
            contemplation_level=ContemplationLevel(self._contemplation_level),
            spells=self._build_spells(),
            invocations=initial_invocations,
            max_tokens=self._max_tokens,
            temperature=self._temperature,
            contemplation_budget=self._contemplation_budget,
            exclude_contemplation=self._exclude_contemplation,
            queue_mode=self._queue_mode,
            rune_runner=self._runner,
            agent_tome=self._agent_tome,
            event_bus=self._event_bus,
        )

        self._harness = self._build_harness()
        self._compaction = self._harness.compaction

    def _build_harness(self) -> MvgeHarness:
        """Construct the MvgeHarness from the wired collaborators.

        Single construction path used both at wire-up and when plan mode
        retires the harness for a fresh one carrying the new spell set.

        Pure: the caller owns assigning ``self._harness`` and syncing
        ``self._compaction`` from the result, so reload can build a
        candidate harness before swapping anything live.
        """
        assert self._state is not None
        harness = MvgeHarness(
            state=self._state,
            tome=self._agent_tome,
            realm=self._realm,
            model=self._model,
            compaction=self._compaction,
            compaction_settings=self._compaction_settings,
            refresh_spells=self._build_spells,
        )
        return harness

    async def run(self, prompt: str | list[dict[str, Any]]) -> MvgeInvocation:
        """Template method for processing a turn."""
        await self.initialize()
        assert self._state is not None

        user_msg = SummonerRequest(role="user", content=prompt)
        self._state.invocations.append(user_msg)

        if not self._state.system_prompt:
            final_prompt = await self._build_system_prompt_async()
            self._state.system_prompt = final_prompt

        return await self._run_impl()

    async def switch_model(self, model_id: str) -> None:
        """Switch the model in-flight."""
        if self._model is not None and self._model.id == model_id:
            return

        self._model_id = model_id
        if self._initialized:
            new_model, new_realm = self._provider_registry.resolve(
                model_id, self._api_key, self._provider_name
            )
            self._model = new_model
            self._realm = new_realm
            if self._state is not None:
                self._state.model = dataclasses.asdict(new_model)
            if self._harness is not None:
                self._harness.set_model_and_realm(new_model, new_realm)
            if self._agent_tome is not None:
                await self._agent_tome.record_custom_async(
                    "model_switch", {"model": new_model.id}
                )

    async def set_contemplation_level(self, level: ContemplationLevel | str) -> None:
        """Switch the contemplation level in-flight."""
        if isinstance(level, str):
            with contextlib.suppress(ValueError):
                level = ContemplationLevel(level)
        if self._contemplation_level == level:
            return

        self._contemplation_level = level
        if self._state is not None:
            self._state.contemplation_level = level
        if self._agent_tome is not None:
            lvl_val = (
                level.value if isinstance(level, ContemplationLevel) else str(level)
            )
            await self._agent_tome.record_custom_async(
                "contemplation_switch", {"level": lvl_val}
            )

    async def reset_session(self, *, resume_tome_id: str | None = None) -> None:
        """Reset or resume session lifecycle."""
        await self.close()
        self._tome_resume = resume_tome_id
        self._state = None
        self._agent_tome = None
        self._harness = None
        self._initialized = False
        await self.initialize()

    async def fork_tome(self, entry_id: str | None = None) -> str:
        """Branch current Tome from entry_id (or active leaf) and
        switch to the new Tome.
        """
        if not self._initialized or self._agent_tome is None:
            raise RuntimeError("Agent not initialized")
        new_tome = await self._agent_tome.fork(entry_id)
        if new_tome is None:
            raise RuntimeError("Failed to fork tome")
        self._agent_tome = new_tome
        self._tome_resume = new_tome.tome_id
        if self._harness is not None:
            self._harness.switch_tome(new_tome)
            self._compaction = self._harness.compaction
        if self._state is not None:
            self._state.invocations = self._agent_tome.reconstruct_invocations()
        self._rebind_runner_context()
        return new_tome.tome_id

    async def checkout_leaf(self, leaf_id: str) -> None:
        """Switch active position in the Tome to a specific leaf entry."""
        if not self._initialized or self._agent_tome is None:
            raise RuntimeError("Agent not initialized")
        if self._state is not None and getattr(self._state, "is_streaming", False):
            raise RuntimeError("Cannot checkout leaf while invocation is streaming")
        self._agent_tome.set_leaf(leaf_id)
        if self._state is not None:
            self._state.invocations = self._agent_tome.reconstruct_invocations()

    async def list_leaves(self) -> list[str]:
        """List all active leaf entry IDs in the current Tome."""
        if not self._initialized or self._agent_tome is None:
            return []
        return self._tome_factory.list_leaves(self._agent_tome.tome_id)

    async def undo(self) -> str | None:
        """Revert the most recent summoner invocation by pointing active
        leaf to its parent.
        """
        if not self._initialized or self._agent_tome is None:
            raise RuntimeError("Agent not initialized")
        if self._state is not None and getattr(self._state, "is_streaming", False):
            raise RuntimeError("Cannot undo while invocation is streaming")
        target = self._tome_factory.get_parent_summoner_entry(
            self._agent_tome.tome_id, self._agent_tome.active_leaf_id
        )
        if target is None:
            raise ValueError("Cannot undo: at root invocation")
        self._agent_tome.set_leaf(target.id)
        if self._state is not None:
            self._state.invocations = self._agent_tome.reconstruct_invocations()
        return target.id

    async def compact(self) -> str:
        """Trigger mana pool compaction on the current Tome branch."""
        compaction = self._compaction or (
            self._harness.compaction if self._harness is not None else None
        )
        if (
            not self._initialized
            or self._agent_tome is None
            or compaction is None
            or self._state is None
        ):
            raise RuntimeError("Agent not initialized")
        if getattr(self._state, "is_streaming", False):
            raise RuntimeError("Cannot compact while invocation is streaming")
        if not self._state.invocations:
            return "No invocations to compact"
        replacement = await compaction.force_compact(self._state.invocations)
        if replacement is not None:
            self._state.invocations = replacement
            return "Compaction completed"
        return "Nothing to compact or compaction skipped"

    def get_skills_catalog(self) -> list[dict[str, str]]:
        """List registered skills with metadata."""
        if self._runner is None:
            return []
        skills = self._runner.skill_catalog.get_skills()
        return [
            {
                "name": s.name,
                "description": s.description,
                "scope": s.scope.value if s.scope else "unknown",
                "path": str(s.path),
            }
            for s in skills
        ]

    async def activate_skill(self, name: str) -> str:
        """Activate a registered skill by name and return its activation content."""
        if self._runner is None:
            raise ValueError("No runner available to activate skills.")
        spell = self._runner.get_spell("activate_skill")
        if spell is None:
            raise ValueError(
                "No activate_skill spell registered. "
                "Ensure skills-bridge rune is installed."
            )

        result = await spell.execute(f"cast_activate_{name}", {"name": name})
        content = result.get("content", "")
        if not content and "error" in result:
            raise ValueError(result["error"])
        return str(content)

    async def close(self) -> None:
        """Teardown the agent session and release resources."""
        if self._abort_controller is not None:
            self._abort_controller.abort()

        if self._rune_lifecycle is not None:
            await self._rune_lifecycle.shutdown()
            self._rune_lifecycle = None
            self._runner = None

        if self._realm is not None:
            close_fn = getattr(self._realm, "close", None)
            if callable(close_fn):
                res = close_fn()
                if inspect.isawaitable(res):
                    await res
            self._realm = None

        if self._agent_tome is not None:
            await self._agent_tome.shutdown()

        self._initialized = False

    async def __aenter__(self) -> Mvge:
        await self.initialize()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        await self.close()
