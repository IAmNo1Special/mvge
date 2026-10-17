"""Internal session state (ADR-0012 §3).

``MvgeState`` is internal implementation, never in a protocol signature.
The sole writer of every field is the harness package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from mvgeos_core.event_bus import EventBus
from mvgeos_core.events import (
    ContemplationLevel,
    MvgeEvent,
    PromptSource,
    QueueMode,
)
from mvgeos_core.invocations import MvgeInvocation, SummonerRequest
from mvgeos_core.spells import MvgeSpell

if TYPE_CHECKING:
    from mvgeos_runes.rune_runner import RuneRunner

    from mvgeos_agent.agent_session import MvgeTome


@dataclass
class MvgeState:
    system_prompt: str = ""
    prompt_source: PromptSource = PromptSource.BUILTIN
    model: dict[str, Any] | None = None
    contemplation_level: ContemplationLevel | str = ContemplationLevel.MEDIUM
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

    def __post_init__(self) -> None:
        self._spell_index = {s.name: s for s in self.spells}


__all__ = [
    "MvgeState",
]
