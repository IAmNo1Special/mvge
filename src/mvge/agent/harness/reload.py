"""Reload transaction types (ADR-0012 §2.1).

The reload candidate is built by the construction adapter (Mvge) and
applied by the harness (SessionLifecycle). The candidate is fully
validated before the swap; the swap itself is synchronous.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mvge.agent.environment import MvgeEnvironment
from mvge.agent.function_spell import SpellUnion
from mvge.core.events import PromptSource
from mvge.core.spells import MvgeSpell
from mvge.provider.base import RealmFactory
from mvge.provider.registry import RealmRegistry
from mvge.runes.types import Diagnostic, SkillDiagnostic

if TYPE_CHECKING:
    from mvge.agent.rune_lifecycle import RuneLifecycle
    from mvge.runes.rune_runner import RuneRunner


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
class ReloadCandidate:
    """Validated candidate state for one reload; swapped in atomically.

    Built fully before anything is swapped, so a failure anywhere in the
    build leaves the live last-known-good state untouched.
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
    gateway_rune: str | None
    """Spell-gateway rune engaged on the candidate runner, if any."""


#: Builds a validated reload candidate. Provided by the construction
#: adapter (Mvge); consumed by SessionLifecycle.reload().
ReloadBuilder = Callable[[], Awaitable[ReloadCandidate]]


__all__ = [
    "ReloadBuilder",
    "ReloadCandidate",
]
