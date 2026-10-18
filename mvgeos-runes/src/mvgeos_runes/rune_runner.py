from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import traceback
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

from mvgeos_core.approval import (
    ApprovalDecision,
    ApprovalOutcome,
    ApprovalPresenter,
    ApprovalReasonCode,
    ApprovalRequest,
    SpellGateHandler,
    allow,
    deny,
)

from mvgeos_runes.installer import read_or_create_install_id
from mvgeos_runes.loader import load_factory_from_manifest
from mvgeos_runes.manifest import load_manifest
from mvgeos_runes.rune_api import RuneAPI
from mvgeos_runes.skill_catalog import SkillCatalog
from mvgeos_runes.types import (
    Diagnostic,
    DiagnosticKind,
    RegisteredCommand,
    RuneContext,
    RuneLoad,
    RuneManifest,
    RuneShortcut,
    Sandbox,
    SigilHook,
    SpellDefinition,
)

logger = logging.getLogger(__name__)


def _call_handler(handler: Any, hook: SigilHook, data: Any) -> Any:
    if callable(handler):
        return handler(data)
    attr = getattr(handler, hook.value, None)
    if attr is not None:
        return attr(data)
    return None


async def _call_handler_async(handler: Any, hook: SigilHook, data: Any) -> Any:
    result = _call_handler(handler, hook, data)
    if isinstance(result, Awaitable):
        result = await result
    return result


async def _safe_call_handler_async(
    handler: Any,
    hook: SigilHook,
    data: Any,
    diagnostics: list[Diagnostic] | None = None,
) -> Any | None:
    try:
        return await _call_handler_async(handler, hook, data)
    except asyncio.CancelledError:
        raise
    except Exception as err:
        tb = traceback.format_exc()
        logger.warning(
            "Sigil handler for hook %s raised an exception: %s\n%s",
            hook.value,
            err,
            tb,
        )
        if diagnostics is not None:
            diagnostics.append(
                Diagnostic(
                    kind=DiagnosticKind.LOAD_FAILURE,
                    rune_name=getattr(handler, "__name__", "sigil_handler"),
                    message=f"Sigil handler error on {hook.value}: {err}\n{tb}",
                )
            )
        raise


def _register_item(
    registry: dict[str, Any],
    key: str,
    item: Any,
    item_type: str,
    override: bool = False,
) -> bool:
    if key in registry:
        if not override:
            logger.warning("Duplicate %s registration skipped: %s", item_type, key)
            return False
        logger.debug("Overwriting existing %s registration: %s", item_type, key)

    registry[key] = item
    return True


Handler = Callable[..., Any | None | Awaitable[Any | None]]


class RuneRunner:
    def __init__(self, sandbox_factory: Callable[[], Sandbox] | None = None) -> None:
        self._sigil_handlers: dict[SigilHook, list[Handler]] = {}
        self._spells: dict[str, SpellDefinition] = {}
        self._commands: dict[str, RegisteredCommand] = {}
        self._shortcuts: dict[str, RuneShortcut] = {}
        self._providers: dict[str, dict[str, Any]] = {}
        self._realm_factories: dict[str, tuple[Any, str | None]] = {}
        self._context = RuneContext()
        self._message_queue: list[str] = []
        self._session_name: str | None = None
        self._event_handlers: dict[str, list[Any]] = {}
        # The sandbox is supplied by the embedding layer (the agent package
        # injects MvgeSandbox); runes depends only on the Sandbox protocol.
        self._sandbox_factory = sandbox_factory
        self._sandbox: Sandbox | None = None
        self._loaded_manifests: list[RuneManifest] = []
        self._diagnostics: list[Diagnostic] = []
        self._loaded_rune_names: set[str] = set()
        self._current_loading_rune: str | None = None
        self._spell_version: int = 0
        # SkillCatalog owns all skill-related state (loaded skills, diagnostics,
        # registered skill paths, active-skills set). RuneRunner composes it.
        self._skill_catalog = SkillCatalog()
        # The effective active-spell set is the *union* of each rune's own
        # contribution: ``_active_spells_by_rune`` maps a rune name (or ``None``
        # for spells registered outside any rune context) to the set of spell
        # names it has declared active. A rune that calls ``set_active_spells``
        # only narrows/freezes its own entry; it never locks out other runes.
        self._active_spells_by_rune: dict[str | None, set[str]] = {}
        self._pinned_runes: set[str | None] = set()
        self._rune_handlers: dict[str, dict[SigilHook, list[Handler]]] = {}
        self._global_spell_allowlist: list[str] | None = None
        # Designated spell-gateway rune (manifest-declared): when set, the
        # engine narrows the model's spell view to this rune's spells and the
        # gateway reveals discovered spells via widen_global_allowlist.
        self._gateway_rune_name: str | None = None
        # Host-privileged accessor registry: a rune factory may return its
        # public rune instance object; the runner records non-None results
        # here, keyed by manifest name, so the host can reach a loaded rune
        # (e.g. the Approval Rune's permissions surface) through
        # ``get_rune``. This registry lives on the engine-owned runner and
        # is never exposed through RuneAPI.
        self._rune_instances: dict[str, Any] = {}
        # Security-critical spell gates, registered separately from ordinary
        # sigils via RuneAPI.register_spell_gate. Each entry is
        # (handler, rune_name); all must allow for a cast to proceed.
        self._spell_gates: list[tuple[SpellGateHandler, str | None]] = []
        # Host-bound approval presenter slot. Owned by the engine and set only
        # by the host (never through RuneAPI); None means headless.
        self._approval_presenter: ApprovalPresenter | None = None
        # Presenter generation: bumped every time the slot changes. A pending
        # request is honored only by the exact binding it started under; any
        # rebind or unbind fails it as denied so hot reload and host
        # shutdown can never orphan a pending approval future.
        self._presenter_generation: int = 0
        self._pending_approvals: set[asyncio.Future[None]] = set()

    @property
    def context(self) -> RuneContext:
        return self._context

    @property
    def sandbox(self) -> Sandbox:
        if self._sandbox is None:
            if self._sandbox_factory is None:
                raise RuntimeError(
                    "No sandbox configured; pass sandbox_factory to RuneRunner"
                )
            self._sandbox = self._sandbox_factory()
        return self._sandbox

    @property
    def loaded_manifests(self) -> list[RuneManifest]:
        return list(self._loaded_manifests)

    def get_rune(self, name: str) -> Any | None:
        """Return a loaded rune's public instance object, if any.

        Host-privileged: the engine-owned runner records the object a
        rune's factory returned (keyed by manifest name) so the host can
        reach live rune state — e.g. the Approval Rune's permissions
        surface — without runes exposing each other. Never exposed
        through RuneAPI. Returns None when no loaded rune by that name
        returned an instance.
        """
        return self._rune_instances.get(name)

    @property
    def diagnostics(self) -> list[Diagnostic]:
        return list(self._diagnostics)

    @property
    def skill_catalog(self) -> SkillCatalog:
        """The SkillCatalog owning all skill-related state for this runner."""
        return self._skill_catalog

    def extend_diagnostics(self, diagnostics: list[Diagnostic]) -> None:
        """Append loader diagnostics for paths that produced no runes."""
        self._diagnostics.extend(diagnostics)

    def bind_context(self, context: RuneContext) -> None:
        # has_ui is derived from the host-bound presenter slot, never from
        # the incoming context: a rebind must neither drop a live binding
        # nor fabricate UI capability when no presenter is bound.
        self._context = dataclasses.replace(
            context, has_ui=self._approval_presenter is not None
        )

    def register_handler(
        self, hook: SigilHook, handler: Any, rune_name: str | None = None
    ) -> None:
        if hook not in self._sigil_handlers:
            self._sigil_handlers[hook] = []
        self._sigil_handlers[hook].append(handler)
        if rune_name is not None:
            self._rune_handlers.setdefault(rune_name, {}).setdefault(hook, []).append(
                handler
            )

    def clear_rune(self, rune_name: str) -> None:
        """Clear registrations and handlers associated with a rune before reload."""
        to_remove_spells = [
            k
            for k, v in self._spells.items()
            if getattr(v, "source_rune", None) == rune_name
        ]
        for k in to_remove_spells:
            del self._spells[k]
        if rune_name in self._active_spells_by_rune:
            del self._active_spells_by_rune[rune_name]
        self._pinned_runes.discard(rune_name)

        to_remove_commands = [
            k
            for k, v in self._commands.items()
            if getattr(v, "source_rune", None) == rune_name
        ]
        for k in to_remove_commands:
            del self._commands[k]

        to_remove_factories = [
            k for k, v in self._realm_factories.items() if v[1] == rune_name
        ]
        for k in to_remove_factories:
            del self._realm_factories[k]

        if rune_name in self._rune_handlers:
            for hook, handlers in self._rune_handlers[rune_name].items():
                if hook in self._sigil_handlers:
                    for h in handlers:
                        if h in self._sigil_handlers[hook]:
                            self._sigil_handlers[hook].remove(h)
            del self._rune_handlers[rune_name]
        self._spell_gates = [
            (handler, owner)
            for handler, owner in self._spell_gates
            if owner != rune_name
        ]
        self._rune_instances.pop(rune_name, None)
        self._spell_version += 1

    async def refresh_runes(self, extensions_dirs: Sequence[Path]) -> list[Diagnostic]:
        """Clear and re-execute every rune factory across the given dirs.

        This is the runner half of ``Mvge.reload()``: one refresh path for
        all rune code, replacing the old per-rune watcher hot-reload. New
        rune directories are loaded; changed ones are cleared and
        re-executed (override semantics, so registrations never
        duplicate); broken manifests or factories become diagnostics
        instead of aborting the refresh.
        """
        diagnostics: list[Diagnostic] = []
        for extensions_dir in extensions_dirs:
            if not extensions_dir.is_dir():
                continue
            for child in sorted(extensions_dir.iterdir()):
                manifest_path = child / "manifest.json"
                if not child.is_dir() or not manifest_path.is_file():
                    continue
                manifest = load_manifest(child)
                if manifest is None:
                    diagnostics.append(
                        Diagnostic(
                            kind=DiagnosticKind.LOAD_FAILURE,
                            rune_name=child.name,
                            message=f"Manifest invalid or missing: {manifest_path}",
                            path=str(manifest_path),
                        )
                    )
                    continue
                factory = load_factory_from_manifest(
                    manifest, child, diagnostics=diagnostics
                )
                if factory is None:
                    continue
                self.clear_rune(manifest.name)
                self._loaded_manifests = [
                    m for m in self._loaded_manifests if m.name != manifest.name
                ]
                self._loaded_manifests.append(manifest)
                self._loaded_rune_names.add(manifest.name)
                for sc in manifest.shortcuts:
                    self.register_shortcut(sc, override=True)
                self._current_loading_rune = manifest.name
                api = self.create_api(rune_name=manifest.name, override=True)
                try:
                    result = factory(api)
                    if isinstance(result, Awaitable):
                        result = await result
                except Exception as exc:
                    diagnostics.append(
                        Diagnostic(
                            kind=DiagnosticKind.LOAD_FAILURE,
                            rune_name=manifest.name,
                            message=f"Factory raised during refresh: {exc}",
                            path=str(child),
                        )
                    )
                    continue
                finally:
                    self._current_loading_rune = None
                if result is not None:
                    self._rune_instances[manifest.name] = result
        self._designate_spell_gateway()
        return diagnostics

    async def rehydrate_runes(self, payload: Any) -> list[str]:
        """Refresh hook-derived rune instance state from a fresh payload.

        This is the dedicated rehydrate path: it is NOT the
        prompt-mutating ``BEFORE_MVGE_START`` hook. Each loaded rune
        instance defining ``rehydrate(payload)`` receives the fresh
        payload (awaited when awaitable). Per-rune failures are isolated
        into the returned error strings; the reload continues.
        """
        errors: list[str] = []
        for name, instance in list(self._rune_instances.items()):
            rehydrate = getattr(instance, "rehydrate", None)
            if not callable(rehydrate):
                continue
            try:
                result = rehydrate(payload)
                if isinstance(result, Awaitable):
                    await result
            except Exception as exc:
                errors.append(f"{name}: {exc}")
                logger.warning("Rune %s rehydrate failed: %s", name, exc)
        return errors

    def register_spell_gate(
        self, handler: SpellGateHandler, rune_name: str | None = None
    ) -> None:
        """Register a security-critical spell gate.

        Gates are evaluated separately from ordinary sigils with AND
        semantics: any denial, exception, cancellation, or malformed response
        denies the cast. Unlike ``BEFORE_SPELL_CAST`` (fail-open), this path
        is fail-closed.
        """
        if rune_name is None:
            rune_name = self._current_loading_rune
        self._spell_gates.append((handler, rune_name))

    async def evaluate_spell_gates(self, request: ApprovalRequest) -> ApprovalDecision:
        """Evaluate every registered gate for one cast (AND semantics).

        Returns allow only when every gate allows with a fresh, well-formed
        decision bound to this request. With no gates registered the cast is
        allowed: the engine alone does not fail closed.
        """
        if not self._spell_gates:
            return allow(request)
        for handler, _rune_name in self._spell_gates:
            try:
                result = handler(request)
                if isinstance(result, Awaitable):
                    result = await result
            except asyncio.CancelledError:
                return deny(request, ApprovalReasonCode.FAILURE)
            except Exception:
                logger.exception("Spell gate handler raised; denying cast")
                return deny(request, ApprovalReasonCode.FAILURE)
            if (
                not isinstance(result, ApprovalDecision)
                or result.request_digest != request.argument_digest
            ):
                return deny(request, ApprovalReasonCode.FAILURE)
            if result.outcome is ApprovalOutcome.DENY:
                return result
        return allow(request)

    def set_approval_presenter(self, presenter: ApprovalPresenter) -> None:
        """Bind the host-owned approval presenter (host-privileged).

        Never exposed through RuneAPI: if any rune could write this slot it
        could install an auto-approver. Binding marks the rune context as
        UI-capable (``RuneContext.has_ui``). Rebinding fails every request
        that is still waiting on the previous presenter as denied.
        """
        self._approval_presenter = presenter
        self._context = dataclasses.replace(self._context, has_ui=True)
        self._invalidate_pending_approvals()

    def clear_approval_presenter(self) -> None:
        """Unbind the presenter; pending requests resolve as denied."""
        self._approval_presenter = None
        self._context = dataclasses.replace(self._context, has_ui=False)
        self._invalidate_pending_approvals()

    def _invalidate_pending_approvals(self) -> None:
        """Fail every in-flight approval request as denied.

        The presenter slot changed (bind, rebind, or unbind): a request may
        only be authorized by the exact binding it started under.
        """
        self._presenter_generation += 1
        pending = list(self._pending_approvals)
        self._pending_approvals.clear()
        for future in pending:
            if not future.done():
                future.set_result(None)

    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        """Await the host-bound presenter for one approval request.

        The rune never imports UI code; it goes through this engine-owned
        slot. With no presenter bound (headless/CI) the request is denied.
        If the host rebinds or unbinds the presenter while a request is
        pending, the request resolves as denied immediately instead of
        waiting on the orphaned presenter.
        """
        presenter = self._approval_presenter
        if presenter is None:
            return deny(request, ApprovalReasonCode.FAILURE)
        generation = self._presenter_generation
        invalidated = asyncio.get_running_loop().create_future()
        self._pending_approvals.add(invalidated)
        task = asyncio.ensure_future(presenter(request))
        try:
            try:
                done, _ = await asyncio.wait(
                    {task, invalidated}, return_when=asyncio.FIRST_COMPLETED
                )
            except asyncio.CancelledError:
                task.cancel()
                return deny(request, ApprovalReasonCode.FAILURE)
            if invalidated in done:
                # The slot changed mid-flight: the orphaned presenter task
                # cannot authorize this request.
                if not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task
                return deny(request, ApprovalReasonCode.FAILURE)
            if (
                self._presenter_generation != generation
                or self._approval_presenter is not presenter
            ):
                return deny(request, ApprovalReasonCode.FAILURE)
            try:
                decision = task.result()
            except asyncio.CancelledError:
                return deny(request, ApprovalReasonCode.FAILURE)
            except Exception:
                logger.exception("Approval presenter raised; denying request")
                return deny(request, ApprovalReasonCode.FAILURE)
            if (
                not isinstance(decision, ApprovalDecision)
                or decision.request_digest != request.argument_digest
            ):
                return deny(request, ApprovalReasonCode.FAILURE)
            if decision.outcome is ApprovalOutcome.ALLOW and (
                self._context.session_id != request.tome_id
                or self._context.project_root != request.project_root
            ):
                # The active Tome or project changed while the prompt was
                # open: a late decision bound to the old context must not
                # authorize the new one.
                logger.warning(
                    "Approval context changed mid-prompt; denying stale decision"
                )
                return deny(request, ApprovalReasonCode.FAILURE)
            return decision
        finally:
            self._pending_approvals.discard(invalidated)

    def get_sigil_handlers(self, hook: SigilHook) -> list[Handler]:
        return list(self._sigil_handlers.get(hook, []))

    @property
    def sigil_handlers(self) -> dict[SigilHook, list[Handler]]:
        return {k: list(v) for k, v in self._sigil_handlers.items()}

    def register_spell(
        self,
        spell: SpellDefinition,
        rune_name: str | None = None,
        override: bool = False,
    ) -> bool:
        if rune_name is None:
            rune_name = self._current_loading_rune
        if spell.name in self._spells:
            if not override:
                logger.warning("Duplicate spell registration skipped: %s", spell.name)
                return False
            logger.debug("Overwriting existing spell registration: %s", spell.name)
            old_spell = self._spells[spell.name]
            old_rune = getattr(old_spell, "source_rune", None)
            if old_rune != rune_name and old_rune in self._active_spells_by_rune:
                self._active_spells_by_rune[old_rune].discard(spell.name)

        if getattr(spell, "source_rune", None) is None and rune_name is not None:
            with contextlib.suppress(AttributeError):
                spell.source_rune = rune_name
        self._spells[spell.name] = spell
        # Seed the rune's own active set with every registered rune spell by
        # default, unless that rune has already pinned an explicit set. Other
        # runes' pinned sets are left untouched (composable per-rune model).
        if rune_name not in self._pinned_runes:
            self._active_spells_by_rune.setdefault(rune_name, set()).add(spell.name)
        self._spell_version += 1
        return True

    def register_command(
        self, command: RegisteredCommand, override: bool = False
    ) -> bool:
        return _register_item(
            self._commands, command.name, command, "command", override
        )

    def register_shortcut(self, shortcut: RuneShortcut, override: bool = False) -> bool:
        return _register_item(
            self._shortcuts, shortcut.key, shortcut, "shortcut", override
        )

    def register_provider(
        self, name: str, config: dict[str, Any], override: bool = False
    ) -> bool:
        return _register_item(self._providers, name, config, "provider", override)

    def get_registered_providers(self) -> dict[str, dict[str, Any]]:
        return dict(self._providers)

    def register_realm_factory(
        self,
        prefix: str,
        factory: Any,
        rune_name: str | None = None,
        override: bool = False,
    ) -> bool:
        """Register a realm factory for a provider prefix attributed to a rune."""
        if rune_name is None:
            rune_name = self._current_loading_rune
        if prefix in self._realm_factories and not override:
            logger.warning(
                "Duplicate realm factory registration skipped for prefix: %s", prefix
            )
            return False
        self._realm_factories[prefix] = (factory, rune_name)
        return True

    def get_registered_realm_factories(self) -> dict[str, Any]:
        """Return dict of prefix -> factory without rune attribution tuple."""
        return {k: v[0] for k, v in self._realm_factories.items()}

    def get_all_registered_spells(self) -> list[SpellDefinition]:
        return list(self._spells.values())

    def get_spell(self, name: str) -> SpellDefinition | None:
        return self._spells.get(name)

    def get_commands(self) -> list[RegisteredCommand]:
        return list(self._commands.values())

    def get_shortcuts(self) -> list[RuneShortcut]:
        return list(self._shortcuts.values())

    def get_active_spells(self) -> list[str]:
        active: set[str] = set()
        for rune_spells in self._active_spells_by_rune.values():
            active |= rune_spells
        return sorted(active)

    def set_active_spells(
        self, spell_names: list[str], rune_name: str | None = None
    ) -> None:
        """Declare the rune-owned active-spell set for a single rune.

        Unlike a global replace and freeze, this only sets the calling rune's own
        contribution; the engine's effective active set is the union across all
        runes (``get_active_spells``). A rune that wants to narrow its own
        surface does so without affecting other runes' spells.

        ``rune_name`` defaults to the rune currently being loaded so that
        factory-time calls (before sigils fire) are attributed correctly. When
        invoked through a ``RuneAPI`` tied to a rune, the rune name is carried
        on the API instance, surviving past the loading phase into
        ``SESSION_START`` handlers, so later spell registrations from other
        runes still join the unioned active set.
        """
        if rune_name is None:
            rune_name = self._current_loading_rune
        self._active_spells_by_rune[rune_name] = set(spell_names)
        self._pinned_runes.add(rune_name)
        self._spell_version += 1

    def get_global_spell_allowlist(self) -> list[str] | None:
        """Get the engine-owned global spell allowlist.

        Returns ``None`` when no global filtering is active (all spells
        visible). When set to a list of spell names, only those spells
        are exposed to the model regardless of per-rune active sets.
        """
        return self._global_spell_allowlist

    def set_global_spell_allowlist(self, spell_names: list[str] | None) -> None:
        """Set the global spell allowlist.

        Pass ``None`` to disable global filtering (all active spells visible).
        Pass a list to restrict the model's view to only those spell names.
        """
        if spell_names is None:
            self._global_spell_allowlist = None
        else:
            self._global_spell_allowlist = list(spell_names)
        self._spell_version += 1

    def widen_global_allowlist(self, spell_names: list[str]) -> None:
        """Add spell names to the existing global allowlist.

        If no global allowlist is active (``None``), one is created with the
        given names. Otherwise the names are merged into the existing set.
        """
        if self._global_spell_allowlist is None:
            self._global_spell_allowlist = list(spell_names)
        else:
            existing: set[str] = set(self._global_spell_allowlist)
            existing.update(spell_names)
            self._global_spell_allowlist = list(existing)
        self._spell_version += 1

    def send_message(self, content: str) -> None:
        self._message_queue.append(content)

    def set_session_name(self, name: str) -> None:
        self._session_name = name

    def create_api(
        self, rune_name: str | None = None, override: bool = False
    ) -> RuneAPI:
        if rune_name is None:
            rune_name = self._current_loading_rune
        return RuneAPI(
            self,
            rune_name,
            override=override,
            install_id=self._install_id_for_rune(rune_name),
        )

    def _install_id_for_rune(self, rune_name: str | None) -> str | None:
        """Installer-owned id for a loaded rune's directory.

        Resolved from the loaded manifest's directory via the installer's
        read-or-create: the host mints the id at load when the installer
        never did (or the file was lost), so policy binding always has an
        id to stamp — a fresh mint simply invalidates prior grants via
        mismatch, which is the fail-closed direction. ``None`` only when
        the rune was not loaded through the manifest path at all.
        """
        if rune_name is None:
            return None
        for manifest in self._loaded_manifests:
            if manifest.name == rune_name and manifest.path:
                return read_or_create_install_id(Path(manifest.path))
        return None

    async def load_rune_loads(
        self,
        loads: list[RuneLoad],
        diagnostics: list[Diagnostic] | None = None,
    ) -> None:
        if diagnostics is not None:
            self._diagnostics.extend(diagnostics)
        for load in loads:
            if load.manifest.name in self._loaded_rune_names:
                continue
            self._loaded_manifests.append(load.manifest)
            self._loaded_rune_names.add(load.manifest.name)
            for sc in load.manifest.shortcuts:
                self.register_shortcut(sc)
            if load.factory is not None:
                self._current_loading_rune = load.manifest.name
                api = self.create_api()
                result = load.factory(api)
                if isinstance(result, Awaitable):
                    result = await result
                if result is not None:
                    self._rune_instances[load.manifest.name] = result
                self._current_loading_rune = None
        self._designate_spell_gateway()

    def _designate_spell_gateway(self) -> None:
        """Designate the spell-gateway rune from loaded manifests.

        The first loaded rune declaring ``spell_gateway`` wins; additional
        claimants are reported via diagnostics and ignored, so the model's
        narrowed spell view stays predictable.
        """
        for manifest in self._loaded_manifests:
            if not manifest.spell_gateway:
                continue
            if self._gateway_rune_name is None:
                self._gateway_rune_name = manifest.name
            elif manifest.name != self._gateway_rune_name:
                self._diagnostics.append(
                    Diagnostic(
                        kind=DiagnosticKind.PARSE_WARNING,
                        rune_name=manifest.name,
                        message=(
                            f"Rune '{manifest.name}' declares spell_gateway, "
                            f"but '{self._gateway_rune_name}' already claimed "
                            "it; ignoring."
                        ),
                    )
                )

    @property
    def gateway_rune_name(self) -> str | None:
        """Name of the designated spell-gateway rune, if any."""
        return self._gateway_rune_name

    def gateway_spell_names(self) -> list[str]:
        """Spell names registered by the designated gateway rune.

        Returns an empty list when no gateway rune is designated.
        """
        if self._gateway_rune_name is None:
            return []
        return sorted(
            spell.name
            for spell in self._spells.values()
            if getattr(spell, "source_rune", None) == self._gateway_rune_name
        )

    async def emit_async(self, hook: SigilHook, data: Any) -> None:
        # Payloads arrive typed from mvgeos_agent.sigil_payloads; the runner
        # fans them out unchanged. Rune *returns* are never re-validated.
        for handler in self.get_sigil_handlers(hook):
            await _safe_call_handler_async(handler, hook, data)

    async def emit_chain(self, hook: SigilHook, initial: Any) -> Any:
        current = initial
        for handler in self.get_sigil_handlers(hook):
            result = await _safe_call_handler_async(handler, hook, current)
            if result is not None:
                current = result
        return current

    async def emit_first(self, hook: SigilHook, data: Any) -> Any | None:
        for handler in self.get_sigil_handlers(hook):
            result = await _safe_call_handler_async(handler, hook, data)
            if result is not None:
                return result
        return None

    async def emit_block(self, hook: SigilHook, data: Any) -> dict[str, Any] | None:
        for handler in self.get_sigil_handlers(hook):
            result = await _safe_call_handler_async(handler, hook, data)
            if isinstance(result, dict) and result.get("block"):
                return result
        return None

    def on_event(self, channel: str, handler: Any) -> None:
        if channel not in self._event_handlers:
            self._event_handlers[channel] = []
        self._event_handlers[channel].append(handler)

    def emit_event(self, channel: str, data: Any) -> None:
        for handler in self._event_handlers.get(channel, []):
            try:
                handler(data)
            except Exception:
                logger.exception(
                    "Event handler for channel %s raised an exception", channel
                )

    def get_event_channels(self) -> list[str]:
        return list(self._event_handlers.keys())

    @property
    def spell_version(self) -> int:
        """Monotonic counter incremented on spell registration/removal/activation
        changes. Used by MvgeHarness to skip redundant spell resolution rebuilds.
        """
        return self._spell_version
