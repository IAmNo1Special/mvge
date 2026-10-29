"""Capability protocols for the Harness seam (ADR-0012 §2, Split Capabilities).

The seam is three capability protocols plus a thin composing facade.
Callers take the capability they need: the TUI takes a ``TurnDriver``,
the file watcher takes a ``SessionLifecycle``, the settings UI takes an
``AgentConfiguration``. ``MvgeState`` never appears in a protocol
signature — it is internal implementation.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from mvge.agent.agent_session import MvgeTome
from mvge.agent.protocol import ReloadResult
from mvge.core.abort import AbortSignal
from mvge.core.channel import Model
from mvge.core.events import ContemplationLevel, ExecutionSnapshot, QueueMode
from mvge.core.invocations import MvgeInvocation, SummonerRequest
from mvge.provider.base import Realm


@runtime_checkable
class TurnDriver(Protocol):
    """Owns the hot path: exactly one turn at a time."""

    async def run_turn(
        self, invocation: SummonerRequest, *, signal: AbortSignal | None = None
    ) -> MvgeInvocation: ...
    def steer(self, text: str) -> None: ...  # enqueue-and-drain, sync
    def follow_up(self, text: str) -> None: ...  # enqueue-and-drain, sync
    def abort(self) -> None: ...  # idempotent, sync
    @property
    def turn_in_flight(self) -> bool: ...


@runtime_checkable
class SessionLifecycle(Protocol):
    """Owns slow-path session transactions. Never runs concurrently
    with itself (internal lock); defers to the TurnDriver at turn
    boundaries."""

    async def reload(self) -> ReloadResult: ...
    def reload_spells(self) -> None: ...
    async def switch_tome(self, tome: MvgeTome) -> None: ...
    async def fork_tome(self, entry_id: str | None = None) -> str: ...
    async def checkout_leaf(self, leaf_id: str) -> None: ...
    async def undo(self) -> str | None: ...
    async def compact(self) -> str: ...
    async def close(self) -> None: ...


@runtime_checkable
class AgentConfiguration(Protocol):
    """Owns session configuration. Writes never touch an in-flight turn;
    every mutation emits CONFIG_CHANGE. The in-flight turn's captured
    values are unreachable from here by construction."""

    def set_model_and_realm(self, model: Model, realm: Realm) -> None: ...
    def set_contemplation_level(self, level: ContemplationLevel | str) -> None: ...
    def set_plan_mode(self, enabled: bool) -> None: ...
    def set_enabled_spells(self, spell_names: Sequence[str] | None) -> None: ...
    @property
    def queue_mode(self) -> QueueMode: ...
    @queue_mode.setter
    def queue_mode(self, mode: QueueMode | str) -> None: ...
    @property
    def snapshot(self) -> ExecutionSnapshot: ...  # read-only observability
