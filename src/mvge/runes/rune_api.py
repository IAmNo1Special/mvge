from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mvge.runes.rune_runner import RuneRunner

from mvge.core.approval import (
    ApprovalDecision,
    ApprovalRequest,
    SpellGateHandler,
)
from mvge.core.proxy import sanitize_no_proxy_env
from mvge.core.spells import MvgeSpell
from mvge.runes.rune_audit import (
    AuditError,
    RuneAuditLog,
    default_rune_ops_dir,
    utcnow,
)
from mvge.runes.types import (
    RegisteredCommand,
    RuneContext,
    RuneDisposableScope,
    RuneShortcut,
    Sandbox,
    SigilHook,
    SkillManifest,
    SpellDefinition,
)

# A rune factory is called with its own RuneAPI. Most factories return
# None (registration side effects are enough), but a factory may return its
# public rune instance object; the runner records non-None results under the
# manifest name so the host can reach live rune state through the
# host-privileged ``RuneRunner.get_rune`` accessor (never via RuneAPI).
RuneFactory = Callable[["RuneAPI"], None | Awaitable[None] | Any]


class RuneAPI:
    def __init__(
        self,
        runner: RuneRunner,
        rune_name: str | None = None,
        override: bool = False,
        install_id: str | None = None,
        audit_log: RuneAuditLog | None = None,
    ) -> None:
        self._runner = runner
        self._rune_name = rune_name
        self._override = override
        self._install_id = install_id
        # Engine-owned: the rune-op audit log. Defaults to the user-scope
        # ``.agents/extensions/audit.jsonl``; injectable for tests.
        self._audit_log = audit_log or RuneAuditLog(default_rune_ops_dir())
        self._disposable_scope = (
            runner.get_or_create_scope(rune_name)
            if rune_name is not None
            else RuneDisposableScope(rune_name="anonymous")
        )

    @property
    def scope(self) -> RuneDisposableScope:
        return self._disposable_scope

    def on_dispose(self, callback: Callable[[], Any]) -> None:
        """Register a cleanup callback invoked when this rune is unloaded."""
        self._disposable_scope.cleanup_callbacks.append(callback)

    def unregister_spell(self, name: str) -> bool:
        """Unregister a single spell previously registered by this rune."""
        self._disposable_scope.spell_names.discard(name)
        return self._runner.unregister_spell(name, rune_name=self._rune_name)

    @property
    def install_id(self) -> str | None:
        """Installer-owned id of the loaded rune directory.

        The host stamps this from ``<rune-dir>/.install-id`` when the rune
        is loaded through the installer/manifest path. ``None`` when the
        API was created outside a rune load (tests, ad-hoc use). Runes
        must treat it as opaque: it binds user-owned policy to one
        installation and must never be chosen by the rune itself.
        """
        return self._install_id

    @property
    def sandbox(self) -> Sandbox:
        return self._runner.sandbox

    @property
    def context(self) -> RuneContext:
        return self._runner.context

    def sanitize_no_proxy_env(self) -> bool:
        """Strip bracketed IPv6 tokens from NO_PROXY before building a client.

        A rune that builds its own httpx client bypasses every engine
        construction site, so the engine cannot protect that client.
        Call this first, in the factory, before any ``httpx.Client`` or
        ``httpx.AsyncClient`` is constructed -- sanitizing after a
        client failed to build cannot repair it. Returns True when
        anything changed; idempotent, and a no-op on a clean
        environment. Only bracketed tokens are removed.
        """
        return sanitize_no_proxy_env()

    def on(self, hook: SigilHook, handler: Any) -> None:
        self._disposable_scope.sigil_handlers.append((hook, handler))
        self._runner.register_handler(hook, handler, rune_name=self._rune_name)

    def register_spell_gate(self, handler: SpellGateHandler) -> None:
        """Register a security-critical spell gate.

        Gates are evaluated separately from ordinary sigils with AND
        semantics and fail closed: any denial, exception, cancellation, or
        malformed response denies the cast. This is the approval path; the
        fail-open ``BEFORE_SPELL_CAST`` hook cannot substitute for it.
        """
        self._disposable_scope.installed_spell_gate = True
        self._runner.register_spell_gate(handler, rune_name=self._rune_name)

    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        """Await the host-bound presenter for one approval request.

        The rune never imports UI code. With no presenter bound
        (headless/CI) the request is denied; the presenter slot itself is
        host-privileged and is never exposed through this API.
        """
        return await self._runner.request_approval(request)

    def register_spell(
        self, spell: SpellDefinition | MvgeSpell, override: bool = False
    ) -> bool:
        ok = self._runner.register_spell(
            spell, self._rune_name, override=override or self._override
        )
        if ok:
            self._disposable_scope.spell_names.add(spell.name)
        return ok

    def register_command(
        self,
        name: str,
        description: str = "",
        handler: Any = None,
        override: bool = False,
    ) -> bool:
        ok = self._runner.register_command(
            RegisteredCommand(name=name, description=description, handler=handler),
            override=override or self._override,
        )
        if ok:
            self._disposable_scope.commands.add(name)
        return ok

    def register_shortcut(
        self,
        key: str,
        description: str = "",
        handler: Any = None,
        override: bool = False,
    ) -> bool:
        ok = self._runner.register_shortcut(
            RuneShortcut(key=key, description=description, handler=handler),
            override=override or self._override,
        )
        if ok:
            self._disposable_scope.shortcuts.add(key)
        return ok

    def register_provider(
        self,
        name: str,
        config: dict[str, Any],
        override: bool = False,
    ) -> bool:
        ok = self._runner.register_provider(
            name, config, override=override or self._override
        )
        if ok:
            self._disposable_scope.providers.add(name)
        return ok

    def get_registered_providers(
        self,
    ) -> dict[str, dict[str, Any]]:
        return self._runner.get_registered_providers()

    def register_realm_factory(
        self,
        prefix: str,
        factory: Any,
        override: bool = False,
    ) -> bool:
        ok = self._runner.register_realm_factory(
            prefix,
            factory,
            rune_name=self._rune_name,
            override=override or self._override,
        )
        if ok:
            self._disposable_scope.realm_factories.add(prefix)
        return ok

    def get_registered_realm_factories(
        self,
    ) -> dict[str, Any]:
        return self._runner.get_registered_realm_factories()

    def get_active_spells(self) -> list[str]:
        return self._runner.get_active_spells()

    def set_active_spells(self, spell_names: list[str]) -> None:
        self._runner.set_active_spells(spell_names, self._rune_name)

    def get_global_spell_allowlist(self) -> list[str] | None:
        """Get the engine-owned global spell allowlist, or None if unset.

        Read-only: runes cannot rewrite the engine's global filter. The
        filter is owned by the embedding layer (agent/benchmarks), which
        configures it directly on the runner. Runes narrow the model's
        view of their own spells via ``set_active_spells``.
        """
        return self._runner.get_global_spell_allowlist()

    def widen_global_allowlist(self, spell_names: list[str]) -> None:
        """Widen the engine's global spell filter (additive-only).

        Reveals spells to the model as the rune discovers them (e.g.
        ``tool_search`` hits). Any rune may widen; widening can only ever
        add visibility, never remove it.

        No-op when no allowlist is active: widening cannot create the
        filter, so a rune can never narrow the model's view through this
        method. The filter itself is engine-owned -- runes cannot replace
        or drop it (see ``get_global_spell_allowlist``).
        """
        if self._runner.get_global_spell_allowlist() is None:
            return
        self._runner.widen_global_allowlist(spell_names)

    def narrow_global_allowlist(self, spell_names: Iterable[str]) -> list[str]:
        """Narrow the engine's global spell filter by removing names.

        Names belonging to the designated gateway rune cannot be removed.
        """
        return self._runner.narrow_global_allowlist(spell_names)

    @property
    def has_spell_gateway(self) -> bool:
        """Whether a spell-gateway rune has been designated on the runner."""
        return self._runner.has_spell_gateway

    def get_discoverable_spells(self) -> list[Any]:
        """All non-gateway builtin spells + all enabled non-gateway Rune spells."""
        return self._runner.get_discoverable_spells()

    def get_all_spells(self) -> list[SpellDefinition | MvgeSpell]:
        return self._runner.get_all_registered_spells()

    def get_shortcuts(self) -> list[RuneShortcut]:
        return self._runner.get_shortcuts()

    def get_skills(self) -> list[SkillManifest]:
        """Get all registered skills."""
        return self._runner.skill_catalog.get_skills()

    def register_skill_path(self, path: Path | str) -> None:
        """Dynamically register an additional skill directory path."""
        self._runner.skill_catalog.register_skill_path(path)

    def register_skill(self, manifest: SkillManifest) -> None:
        """Dynamically register an in-memory SkillManifest."""
        self._runner.skill_catalog.register_skill(manifest, source_rune=self._rune_name)

    def send_message(self, content: str) -> None:
        self._runner.send_message(content)

    def set_session_name(self, name: str) -> None:
        self._runner.set_session_name(name)

    def on_event(self, channel: str, handler: Any) -> None:
        self._runner.on_event(channel, handler)

    def emit_event(self, channel: str, data: Any) -> None:
        self._runner.emit_event(channel, data)

    def audit(
        self,
        op: str,
        *,
        outcome: str,
        code: str,
        message: str,
        target: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Append a rune-op audit event to the engine-owned audit log.

        The engine stamps the rune's identity and the timestamp — ``extra``
        can never override them, so a rune cannot forge another rune's
        entries. The log is append-only: there is no read/rewrite path, so
        history cannot be rewritten through this API.

        Args:
            op: The mutating operation name (e.g. ``"revise_persona"``).
            outcome: ``"ok"`` or ``"failed"``.
            code: Machine-readable outcome code (``"ok"`` or the op's
                failure code, e.g. ``"snapshot_failed"``).
            message: Human-readable outcome detail.
            target: The mutated target (path, name, or snapshot id).
            extra: Additional record fields; stamped fields always win.

        Raises:
            AuditError: If the durable append fails. Callers must surface
                this loudly — an op that cannot prove it happened must
                never report silent success.
            ValueError: If ``op`` is empty.
        """
        if not op:
            raise ValueError("audit op name must be non-empty")
        record: dict[str, Any] = dict(extra) if extra else {}
        record.update(
            {
                "timestamp": utcnow(),
                "rune": self._rune_name or "unknown",
                "op": op,
                "outcome": outcome,
                "code": code,
                "target": target,
                "message": message,
            }
        )
        try:
            self._audit_log.append_event(record)
        except AuditError:
            raise
        except OSError as exc:
            raise AuditError(f"audit append failed: {exc}") from exc
