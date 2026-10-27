from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

CURRENT_SESSION_VERSION: int = 1


class TomeEntryType(StrEnum):
    MESSAGE = "message"
    LABEL = "label"
    COMPACTION = "compaction"
    CUSTOM = "custom"
    LEAF = "leaf"
    TOME_INFO = "tome_info"


@dataclass
class TomeEntry:
    id: str
    parent_id: str | None
    type: TomeEntryType
    timestamp: float
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "parentId": self.parent_id,
            "type": self.type.value,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }


class TomeVersionError(Exception):
    """Raised when encountering an unsupported or invalid session schema version."""

    def __init__(self, version: Any, message: str | None = None) -> None:
        self.version = version
        msg = message or f"Unsupported session version: {version}"
        super().__init__(msg)


@dataclass
class TomeMetadata:
    id: str
    created_at: str
    cwd: str
    parent_tome_id: str | None = None
    active_leaf_id: str | None = None
    schema_version: str = "1.0"
    version: int = 1
    model: str | None = None
    contemplation_level: str | None = None
    spells: list[str] = field(default_factory=list)


@dataclass
class TomeIntegrityIssue:
    line_number: int
    message: str
    raw_line: str | None = None


@dataclass
class TomeIntegrityReport:
    valid: bool
    tome_id: str
    issues: list[TomeIntegrityIssue] = field(default_factory=list)
    total_lines: int = 0
    valid_entries_count: int = 0

    def __bool__(self) -> bool:
        return self.valid


@dataclass
class ATIFTrajectoryStep:
    step_id: str
    role: str
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_call_id: str | None = None
    timestamp: float = 0.0
    latency_ms: float = 0.0
    model: str | None = None
    reasoning_content: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "step_id": self.step_id,
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "latency_ms": self.latency_ms,
        }
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.model:
            d["model"] = self.model
        if self.reasoning_content:
            d["reasoning_content"] = self.reasoning_content
        return d


@dataclass
class ATIFMetrics:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "reasoning_tokens": self.reasoning_tokens,
        }


@dataclass
class ATIFTrajectory:
    trajectory_id: str
    agent_name: str
    model: str
    created_at: str
    steps: list[ATIFTrajectoryStep] = field(default_factory=list)
    metrics: ATIFMetrics = field(default_factory=ATIFMetrics)
    completed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "trajectory_id": self.trajectory_id,
            "agent_name": self.agent_name,
            "model": self.model,
            "created_at": self.created_at,
            "steps": [s.to_dict() for s in self.steps],
            "metrics": self.metrics.to_dict(),
            "completed": self.completed,
        }
