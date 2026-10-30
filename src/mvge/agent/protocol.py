from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from mvge.agent.environment import MvgeEnvironment
from mvge.agent.snapshot import RuntimeSnapshot
from mvge.core.events import (
    ContemplationLevel,
    MvgeEvent,
    MvgeEventType,
    QueueMode,
)
from mvge.core.invocations import MvgeInvocation
from mvge.provider.model_registry import ModelRegistry
from mvge.runes.rune_runner import RuneRunner


@dataclass
class ReloadResult:
    """Outcome of an engine reload (``Mvge.reload()``)."""

    ok: bool
    """Whether the reload took effect (or was cleanly queued)."""

    message: str
    """Human-readable outcome detail, including failure diagnostics."""

    queued: bool = False
    """True when the reload was deferred to the next turn boundary."""

    prompt_changed: bool = False
    """True when the rebuild produced a different system prompt."""

    spells_changed: bool = False
    """True when the rebuilt spell set differs from the previous one."""


@runtime_checkable
class MvgeAgent(Protocol):
    """Protocol defining the core interface for an Mvge agent.

    Decouples summoning layers (CLI, GUI, runners) from concrete agent
    implementations (such as Mvge).
    """

    @property
    def tome_id(self) -> str | None:
        """The active tome (session) identifier."""
        ...

    @property
    def session_id(self) -> str | None:
        """Alias for tome_id adhering to standard agent protocol vocabulary."""
        ...

    @property
    def model_id(self) -> str:
        """Active model identifier."""
        ...

    @property
    def model_registry(self) -> ModelRegistry:
        """The model registry bound to this agent."""
        ...

    @property
    def contemplation_level(self) -> ContemplationLevel | str:
        """Active contemplation (reasoning effort) level."""
        ...

    @property
    def mana_used(self) -> int | None:
        """Total mana consumed in the current session/state, if available."""
        ...

    @property
    def queue_mode(self) -> QueueMode:
        """Current queue processing mode."""
        ...

    @queue_mode.setter
    def queue_mode(self, mode: QueueMode | str) -> None: ...

    @property
    def registered_providers(self) -> list[str]:
        """List of registered provider/realm names."""
        ...

    @property
    def enabled_spells(self) -> list[str]:
        """List of currently enabled spell names."""
        ...

    @property
    def available_spells(self) -> list[str]:
        """List of all available spell names (builtin + rune-registered)."""
        ...

    @property
    def runner(self) -> RuneRunner | None:
        """The engine-owned rune runner, once runes have loaded.

        Read-only host access so the host can bind host-privileged slots
        (e.g. the approval presenter) on the engine-owned runner object.
        """
        ...

    def set_enabled_spells(self, spell_names: Sequence[str]) -> None:
        """Filter which spells are enabled for execution."""
        ...

    def set_plan_mode(self, enabled: bool) -> None:
        """Enable or disable plan mode (read-only spells only)."""
        ...

    @property
    def environment(self) -> MvgeEnvironment:
        """The resolved environment configuration."""
        ...

    def on(
        self,
        event_type: MvgeEventType | str,
        callback: Callable[[MvgeEvent], None],
    ) -> Callable[[], None]:
        """Subscribe to agent lifecycle and channeling events."""
        ...

    def steer(self, text: str) -> None:
        """Inject user steering request mid-flight."""
        ...

    def follow_up(self, text: str) -> None:
        """Queue follow-up request after current turn completes."""
        ...

    def abort(self) -> None:
        """Abort active channeling / tool execution."""
        ...

    async def initialize(self) -> None:
        """Wire collaborators, runes, and tome session."""
        ...

    async def run(self, prompt: str | list[dict[str, Any]]) -> MvgeInvocation:
        """Run an invocation turn."""
        ...

    async def switch_model(self, model_id: str) -> None:
        """Switch active model for the running agent."""
        ...

    async def set_contemplation_level(self, level: ContemplationLevel | str) -> None:
        """Switch the contemplation level in-flight."""
        ...

    async def reset_session(self, *, resume_tome_id: str | None = None) -> None:
        """Reset or resume session lifecycle."""
        ...

    async def reload(self) -> ReloadResult:
        """Rebuild runes, system prompt, spells, skills, and harness mid-session.

        Transactional: a failed reload preserves last-known-good state and
        surfaces the exact diagnostic. When a turn is in flight the reload
        queues until the turn boundary.
        """
        ...

    def build_snapshot(self) -> RuntimeSnapshot:
        """Assemble runtime snapshot for introspection."""
        ...

    async def fork_tome(self, entry_id: str | None = None) -> str:
        """Branch current Tome from entry_id (or active leaf) and
        switch to the new Tome.
        """
        ...

    async def checkout_leaf(self, leaf_id: str) -> None:
        """Switch active position in the Tome to a specific leaf entry."""
        ...

    async def list_leaves(self) -> list[str]:
        """List all active leaf entry IDs in the current Tome."""
        ...

    async def undo(self) -> str | None:
        """Revert the most recent summoner invocation by pointing active
        leaf to its parent.
        """
        ...

    async def compact(self) -> str:
        """Trigger mana pool compaction on the current Tome branch."""
        ...

    def get_skills_catalog(self) -> list[dict[str, str]]:
        """List registered skills with metadata."""
        ...

    async def activate_skill(self, name: str) -> str:
        """Activate a registered skill by name and return its activation content."""
        ...

    def get_registered_commands(self) -> list[Any]:
        """List registered extension commands."""
        ...

    async def close(self) -> None:
        """Shut down resources, runes, and sessions."""
        ...


class AgentFactory(Protocol):
    """Factory protocol for producing MvgeAgent instances."""

    def __call__(self, *args: Any, **kwargs: Any) -> MvgeAgent: ...
