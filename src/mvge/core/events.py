from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class PromptSource(StrEnum):
    """Where a resolved prompt resource came from."""

    CUSTOM_LITERAL = "custom_literal"
    CUSTOM_PATH = "custom_path"
    PROJECT_MD = "project_md"
    AGENT_MD = "agent_md"
    BUILTIN = "builtin"


class ContentType(StrEnum):
    TEXT = "text"
    SPELL_CAST = "spell_cast"
    CONTEMPLATION = "contemplation"


class ContemplationLevel(StrEnum):
    OFF = "none"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class QueueMode(StrEnum):
    ALL = "all"
    ONE_AT_A_TIME = "one-at-a-time"


class MvgeEventType(StrEnum):
    AGENT_START = "agent_start"
    AGENT_END = "agent_end"
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
    SPELL_CASTING_START = "spell_casting_start"
    SPELL_CASTING_END = "spell_casting_end"
    ARTIFACT_CREATED = "artifact_created"
    COMPACTION_START = "compaction_start"
    COMPACTION_END = "compaction_end"
    PROVIDER_ERROR = "provider_error"
    CONFIG_CHANGE = "config_change"
    SESSION_TITLE_UPDATED = "session_title_updated"


@dataclass
class MvgeEvent:
    type: MvgeEventType
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionSnapshot:
    """Read-only observability snapshot of the current execution configuration.

    Mirrors Pi's LaneExecutionInfo: exposes configured vs captured state
    without exposing internal mutation paths.
    """

    configured_model: str | None = None
    captured_model: str | None = None
    configured_realm: str | None = None
    captured_realm: str | None = None
    contemplation_budget: int | None = None
    active_spell_count: int = 0


__all__ = [
    "ContemplationLevel",
    "ContentType",
    "ExecutionSnapshot",
    "MvgeEvent",
    "MvgeEventType",
    "PromptSource",
    "QueueMode",
]
