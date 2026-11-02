"""Restricted runner facade handed to rune factories via RuneAPI.

Phase 2A narrowing: a factory must never receive a live RuneRunner.
This facade is the only runner-shaped object rune code can reach
through RuneAPI, and it exposes exactly the narrow methods runes
need. It is a narrowing step, not a sandbox: determined in-process
code can still reach the wrapped runner through Python object
introspection, and the docs must never claim otherwise. What it
does close is the casual, documented escape path — ``api._runner``
and ``api.context().api_key`` — and it gives every migrated rune
a typed method instead of a private attribute.

The facade also owns the engine-held model-call capability:
``stream_model_text`` builds a realm from the runner-registered
factories using the key the embedding layer bound on the runner
(see ``RuneRunner.bind_model_key``). The key never crosses into
rune code, the realm object never crosses either — only the
returned text does.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mvge.core.approval import (
    ApprovalDecision,
    ApprovalRequest,
    SpellGateHandler,
)
from mvge.core.channel import ChannelConfig
from mvge.core.invocations import SummonerRequest
from mvge.core.spells import MvgeSpell
from mvge.provider.registry import RealmRegistry
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

if TYPE_CHECKING:
    from mvge.runes.rune_runner import RuneRunner


class RunnerFacade:
    """Narrow, engine-owned view of a RuneRunner for one rune."""

    def __init__(self, runner: RuneRunner) -> None:
        self._wrapped_runner = runner

    # -- identity / scope -------------------------------------------------
    def get_or_create_scope(self, rune_name: str | None) -> RuneDisposableScope:
        return self._wrapped_runner.get_or_create_scope(rune_name)

    # -- context / session ------------------------------------------------
    @property
    def context(self) -> RuneContext:
        # A copy, so a rune mutating its context view cannot rewrite
        # the runner's live context. The context carries no credential:
        # RuneContext has no api_key field (Phase 2A).
        return dataclasses.replace(self._wrapped_runner.context)

    @property
    def sandbox(self) -> Sandbox:
        return self._wrapped_runner.sandbox

    def get_session_name(self) -> str | None:
        return self._wrapped_runner.session_name

    def set_session_name(self, name: str) -> None:
        self._wrapped_runner.set_session_name(name)

    def send_message(self, content: str) -> None:
        self._wrapped_runner.send_message(content)

    # -- spells / commands / shortcuts / providers / realms --------------
    def register_spell(
        self, spell: SpellDefinition | MvgeSpell, rune_name: str | None, override: bool
    ) -> bool:
        return self._wrapped_runner.register_spell(spell, rune_name, override=override)

    def unregister_spell(self, name: str, rune_name: str | None) -> bool:
        return self._wrapped_runner.unregister_spell(name, rune_name=rune_name)

    def get_spell(self, name: str) -> SpellDefinition | MvgeSpell | None:
        return self._wrapped_runner.get_spell(name)

    def get_all_registered_spells(self) -> list[SpellDefinition | MvgeSpell]:
        return self._wrapped_runner.get_all_registered_spells()

    def get_discoverable_spells(self) -> list[Any]:
        return self._wrapped_runner.get_discoverable_spells()

    def register_command(
        self, command: RegisteredCommand, override: bool
    ) -> bool:
        return self._wrapped_runner.register_command(command, override=override)

    def register_shortcut(
        self, shortcut: RuneShortcut, override: bool
    ) -> bool:
        return self._wrapped_runner.register_shortcut(shortcut, override=override)

    def get_shortcuts(self) -> list[RuneShortcut]:
        return self._wrapped_runner.get_shortcuts()

    def register_provider(
        self, name: str, config: dict[str, Any], override: bool
    ) -> bool:
        return self._wrapped_runner.register_provider(name, config, override=override)

    def get_registered_providers(self) -> dict[str, dict[str, Any]]:
        return self._wrapped_runner.get_registered_providers()

    def register_realm_factory(
        self, prefix: str, factory: Any, rune_name: str | None, override: bool
    ) -> bool:
        return self._wrapped_runner.register_realm_factory(
            prefix, factory, rune_name=rune_name, override=override
        )

    def get_registered_realm_factories(self) -> dict[str, Any]:
        return self._wrapped_runner.get_registered_realm_factories()

    # -- spell visibility --------------------------------------------------
    def get_active_spells(self) -> list[str]:
        return self._wrapped_runner.get_active_spells()

    def set_active_spells(self, spell_names: list[str], rune_name: str | None) -> None:
        self._wrapped_runner.set_active_spells(spell_names, rune_name)

    def get_global_spell_allowlist(self) -> list[str] | None:
        return self._wrapped_runner.get_global_spell_allowlist()

    def widen_global_allowlist(self, spell_names: list[str]) -> None:
        self._wrapped_runner.widen_global_allowlist(spell_names)

    def narrow_global_allowlist(self, spell_names: Iterable[str]) -> list[str]:
        return self._wrapped_runner.narrow_global_allowlist(spell_names)

    @property
    def has_spell_gateway(self) -> bool:
        return self._wrapped_runner.has_spell_gateway

    def gateway_spell_names(self) -> list[str]:
        return self._wrapped_runner.gateway_spell_names()

    # -- sigils / gates / approval / events --------------------------------
    def register_handler(
        self, hook: SigilHook, handler: Any, rune_name: str | None
    ) -> None:
        self._wrapped_runner.register_handler(hook, handler, rune_name=rune_name)

    def register_spell_gate(
        self, handler: SpellGateHandler, rune_name: str | None
    ) -> None:
        self._wrapped_runner.register_spell_gate(handler, rune_name=rune_name)

    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        return await self._wrapped_runner.request_approval(request)

    def on_event(self, channel: str, handler: Any) -> None:
        self._wrapped_runner.on_event(channel, handler)

    def emit_event(self, channel: str, data: Any) -> None:
        self._wrapped_runner.emit_event(channel, data)

    # -- skills --------------------------------------------------------------
    def get_skills(self) -> list[SkillManifest]:
        # A fresh list of manifests: mutating the returned list cannot
        # rewrite the catalog.
        return self._wrapped_runner.skill_catalog.get_skills()

    def register_skill_path(self, path: Path | str) -> None:
        self._wrapped_runner.skill_catalog.register_skill_path(path)

    def register_skill(self, manifest: SkillManifest, source_rune: str | None) -> None:
        self._wrapped_runner.skill_catalog.register_skill(
            manifest, source_rune=source_rune
        )

    def clear_skills_for_rune(self, rune_name: str | None) -> None:
        if rune_name is not None:
            self._wrapped_runner.skill_catalog.clear_rune(rune_name)

    # -- approval-presenter sharing (submvge) --------------------------------
    def approval_presenter_source(self) -> Callable[[Any], None]:
        """Bound callback a submvge manager uses to share the parent
        approval presenter into a child runner.

        The callable takes a child RuneRunner (an engine object the
        manager already holds) and copies the parent presenter into
        it. It is for manager use only: rune code must pass it
        through, never call it with a runner of its own making.
        """
        runner = self._wrapped_runner

        def _share(child_runner: Any) -> None:
            child_runner.share_approval_presenter_from(runner)

        return _share

    # -- engine-held model call ----------------------------------------------
    async def stream_model_text(
        self,
        model_id: str,
        prompt: str,
        *,
        temperature: float = 0.1,
        max_tokens: int = 1024,
        timeout_seconds: float = 30.0,
    ) -> str:
        """Stream one model completion and return its text.

        The realm is built inside the engine from the runner-registered
        factories, using the key bound on the runner by the embedding
        layer. Neither the key nor the realm object is returned: only
        the collected text crosses back to the caller. This is the
        Phase 2A replacement for runes reading ``context.api_key``
        and building their own realm.
        """
        registry = RealmRegistry()
        factories = self._wrapped_runner.get_registered_realm_factories()
        for prefix, factory in factories.items():
            registry.register_realm_factory(prefix, factory)
        for name, config in self._wrapped_runner.get_registered_providers().items():
            registry.register_provider(name, config)
        model = registry.compose_model(
            model_id=model_id,
            api_key=self._wrapped_runner.model_key,
            provider_name=None,
        )
        if model is None:
            raise RuntimeError(f"No Realm factory registered for model '{model_id}'")
        realm = registry.create_realm(
            model, api_key=self._wrapped_runner.model_key
        )
        config = ChannelConfig(
            model=model, temperature=temperature, max_tokens=max_tokens
        )
        invocations = [SummonerRequest(role="user", content=prompt)]

        async def _collect() -> str:
            collected = ""
            async for resp in realm.stream(model, invocations, config):
                if resp.invocation and resp.invocation.content:
                    for item in resp.invocation.content:
                        if item.get("type") == "text":
                            collected += item.get("text", "")
            return collected

        try:
            return await asyncio.wait_for(_collect(), timeout=timeout_seconds)
        finally:
            await realm.close()
