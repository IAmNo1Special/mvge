from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

#: Realms that spell their free tier with a ``-free`` suffix rather than the
#: ``:free`` suffix OpenRouter uses.
#:
#: This lives beside ``Model.free`` rather than in the provider layer's shared
#: Realm table, because it is the one Realm fact core needs and core sits below
#: provider in the dependency graph.
_FREE_SUFFIX_REALMS = frozenset({"opencode"})


class StopReason(StrEnum):
    PENDING = "pending"
    STOP = "stop"
    LENGTH = "length"
    SPELL_USE = "spellUse"
    ERROR = "error"
    ABORTED = "aborted"


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
    supported_contemplation_levels: list[str] = field(default_factory=list)
    is_free: bool = False

    @property
    def free(self) -> bool:
        """Return True if the model is free of charge.

        Three realms are reachable today and they do not agree on how to spell
        "free", so all of the conventions are recognised rather than one:

        - ``is_free``, set by whoever authored the catalog entry.
        - A ``:free`` suffix. OpenRouter's convention.
        - A ``-free`` suffix, but only for a Realm that uses it. OpenCode Zen
          spells its free tier this way (``space-bunny-free``), and treating the
          suffix as universal would let a paid model be called free on the
          strength of a name -- which is the one mistake a cost guard must not
          make. Gate it on the Realm rather than guessing from the id.
        - ``openrouter/free``, which routes to whatever free model is available
          and so has no suffix to read.
        """
        if self.is_free or self.id.endswith(":free"):
            return True
        if self.id == "openrouter/free":
            return True
        return self.realm in _FREE_SUFFIX_REALMS and self.id.endswith("-free")

    @property
    def provider(self) -> str:
        """The organization that provides this model (derived from the model ID)."""
        return self.id.split("/")[0] if "/" in self.id else self.realm

    @property
    def provider_prefix(self) -> str:
        """The provider prefix before '/' in id, or realm, or id."""
        return self.id.split("/")[0] if "/" in self.id else (self.realm or self.id)

    @property
    def supports_contemplation(self) -> bool:
        """Return True if the model supports contemplation/reasoning."""
        return (
            bool(self.supported_contemplation_levels)
            or "reasoning" in self.supported_parameters
            or "thinking" in self.supported_parameters
        )


@dataclass
class ChannelConfig:
    model: Model
    temperature: float = 0.7
    max_tokens: int = 4096
    max_output_mana: int | None = None
    timeout_ms: int = 120000
    max_retries: int = 3
    contemplation_level: str = "medium"
    contemplation_budget: int | None = None
    exclude_contemplation: bool = False
    spells: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    meta_data: dict[str, Any] = field(default_factory=dict)
    system_prompt: str = ""

    def __post_init__(self) -> None:
        if self.tools and not self.spells:
            self.spells = self.tools
        elif self.spells and not self.tools:
            self.tools = self.spells


@dataclass
class RealmResponse:
    model: Model
    invocation: Any | None = None
    mana_used: int = 0
    stop_reason: str = "stop"
    error_message: str | None = None
    error_code: str | None = None
    retry_after: float | None = None
    limit_source: str | None = None
    remedy_hint: str | None = None
    reset_at: float | None = None
    quota_limit: int | None = None
    quota_remaining: int | None = None


__all__ = [
    "ChannelConfig",
    "Model",
    "MvgeResponse",
    "RealmResponse",
    "StopReason",
]
