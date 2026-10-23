"""Message and chat models for conversation threading."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from mvgeos_gui.models.artifact import Artifact
from mvgeos_gui.models.execution import ExecutionStep


class MessagePartType(StrEnum):
    """Categorized type of a content block in an assistant message."""

    CONTEMPLATION = "contemplation"
    STEP = "step"
    TEXT = "text"
    ARTIFACT = "artifact"


@dataclass
class MessagePart:
    """A single sequential content block in an assistant message."""

    part_type: MessagePartType
    text: str = ""
    step: ExecutionStep | None = None
    artifact: Artifact | None = None


@dataclass
class ChatMessage:
    """A single chat message in the conversation thread.

    ``parts`` is authoritative; ``content``, ``contemplation``, and ``steps``
    are projections assembled by InvocationTranscript.
    """

    role: str
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).strftime("%H:%M"))
    model: str | None = None
    mana_used: int = 0
    is_streaming: bool = False
    timeline: list[dict[str, Any]] = field(default_factory=list)
    feedback: str | None = None
    is_error: bool = False
    error_message: str | None = None
    attachments: list[str] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    parts: list[MessagePart] = field(default_factory=list)
    missing_rune: str | None = None
    is_steering: bool = False

    @property
    def content(self) -> str:
        """Plain-text projection of the TEXT parts."""
        return "".join(
            p.text for p in self.parts if p.part_type is MessagePartType.TEXT
        )

    @property
    def contemplation(self) -> list[str]:
        """Contemplation projection: texts of the CONTEMPLATION parts."""
        return [
            p.text for p in self.parts if p.part_type is MessagePartType.CONTEMPLATION
        ]

    @property
    def steps(self) -> list[ExecutionStep]:
        """Execution-step projection: steps carried by STEP parts."""
        return [
            p.step
            for p in self.parts
            if p.part_type is MessagePartType.STEP and p.step is not None
        ]
