"""InvocationTranscript: assembles one Mvge Invocation's rendered parts.

Deep module behind the Summoner-facing bubble: every fact that becomes a
MessagePart — contemplation deltas, inline <think>/<thought> tags, spell
execution steps, artifacts — is assembled here, whether it arrives live off
the event bus or replayed from a Tome. Parts are authoritative;
ChatMessage.content/.contemplation/.steps are projections of them.

Imports nothing from NiceGUI, AgentService, or AppState: pure dataclass
assembly, testable through the interface alone.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, TypedDict

from mvgeos_core.invocations import normalize_content_blocks

from mvgeos_gui.models import (
    Artifact,
    ChatMessage,
    CommandExecution,
    ExecutionStep,
    FileExploration,
    MessagePart,
    MessagePartType,
    StepType,
)

_THOUGHT_TAG_PATTERN = re.compile(
    r"<(?:think|thought)>(.*?)</(?:think|thought)>",
    re.DOTALL | re.IGNORECASE,
)
_UNCLOSED_THOUGHT_PATTERN = re.compile(
    r"<(?:think|thought)>(.*)$",
    re.DOTALL | re.IGNORECASE,
)

_READER_SPELLS = frozenset({"read", "grep", "find", "list"})


class TextDeltaPayload(TypedDict, total=False):
    """MESSAGE_UPDATE payload: an incremental text or contemplation chunk."""

    text: str
    kind: str


class SpellStartPayload(TypedDict, total=False):
    """SPELL_CASTING_START payload."""

    spellCastId: str
    spellName: str
    arguments: dict[str, Any]


class SpellEndPayload(TypedDict, total=False):
    """SPELL_CASTING_END payload."""

    spellCastId: str
    result: str
    error: str
    message: str


def format_duration(seconds: float) -> str:
    """Render an elapsed-seconds value as a compact human label."""
    if seconds < 1.0:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60.0:
        return f"{seconds:.1f}s"
    mins = int(seconds // 60)
    rem_secs = seconds % 60
    return f"{mins}m {rem_secs:.0f}s"


def _categorize_spell(spell_name: str) -> StepType:
    if spell_name == "bash":
        return StepType.COMMANDS
    if spell_name in _READER_SPELLS:
        return StepType.FILES
    return StepType.WORKED


def _extract_contemplation_tags(text: str) -> tuple[str, list[str]]:
    """Split <think>/<thought> tags out of a chunk of streamed text."""
    if not text:
        return "", []

    thoughts: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        thoughts.append(match.group(1).strip())
        return ""

    cleaned = _THOUGHT_TAG_PATTERN.sub(_replace, text)
    unclosed_match = _UNCLOSED_THOUGHT_PATTERN.search(cleaned)
    if unclosed_match:
        thoughts.append(unclosed_match.group(1).strip())
        cleaned = _UNCLOSED_THOUGHT_PATTERN.sub("", cleaned)

    return cleaned.strip(), [t for t in thoughts if t]


def _extract_ordered_content(text: str) -> list[dict[str, str]]:
    """Split a persisted string into chronological text/thought segments."""
    if not text:
        return []

    segments: list[dict[str, str]] = []
    last_end = 0
    for match in _THOUGHT_TAG_PATTERN.finditer(text):
        if match.start() > last_end:
            leading = text[last_end : match.start()].strip()
            if leading:
                segments.append({"type": "text", "text": leading})
        thought_text = match.group(1).strip()
        if thought_text:
            segments.append({"type": "thought", "text": thought_text})
        last_end = match.end()
    if last_end < len(text):
        trailing = text[last_end:].strip()
        if trailing:
            segments.append({"type": "text", "text": trailing})

    return segments


class InvocationTranscript:
    """Assembles the rendered structure of one Mvge Invocation.

    Live callers feed bus payloads (apply_message_update/begin_spell/
    end_spell/add_artifact); Tome replay feeds the same machinery via
    from_tome_content. The assembled ChatMessage carries authoritative
    ``parts``; text/contemplation/steps read off it are projections.
    """

    def __init__(self, role: str = "assistant", model: str | None = None) -> None:
        self._message = ChatMessage(role=role, model=model)
        self._steps_by_spell_id: dict[str, ExecutionStep] = {}

    @property
    def message(self) -> ChatMessage:
        """The assembled ChatMessage; parts on it are authoritative."""
        return self._message

    @classmethod
    def bind(cls, message: ChatMessage) -> InvocationTranscript:
        """Attach assembly machinery to an already-created ChatMessage."""
        transcript = cls(role=message.role, model=message.model)
        transcript._message = message
        return transcript

    @classmethod
    def for_summoner(
        cls, text: str, attachments: list[str] | None = None
    ) -> ChatMessage:
        """Build a Summoner bubble: one TEXT part carrying the prompt."""
        message = ChatMessage(role="user", attachments=list(attachments or []))
        message.parts.append(MessagePart(part_type=MessagePartType.TEXT, text=text))
        return message

    def apply_message_update(self, data: TextDeltaPayload | Mapping[str, Any]) -> None:
        """Consume a MESSAGE_UPDATE payload (text or contemplation delta)."""
        text = str(data.get("text", "") or "")
        kind = str(data.get("kind", "text"))
        if not text:
            return
        if kind == "contemplation":
            self._append_thought_delta(text)
        else:
            self._append_text_delta(text)

    def begin_spell(
        self,
        data: SpellStartPayload | Mapping[str, Any],
        *,
        elapsed_seconds: float | None = None,
    ) -> ExecutionStep | None:
        """Consume a SPELL_CASTING_START payload; create its step card.

        Returns None when nothing would be shown (a reader spell without a
        path produces no card).
        """
        spell_name = str(data.get("spellName", "tool"))
        spell_id = str(data.get("spellCastId", ""))
        raw_arguments: object = data.get("arguments", {})
        arguments = raw_arguments if isinstance(raw_arguments, dict) else {}

        if spell_name in _READER_SPELLS and not arguments.get("path"):
            return None

        step = ExecutionStep(
            step_type=_categorize_spell(spell_name),
            spell_name=spell_name,
            params=arguments,
        )
        if spell_name == "bash":
            command = str(arguments.get("command", "") or "")
            step.commands.append(
                CommandExecution(command=command or "$ (running command...)")
            )
            step.title = f"Ran {len(step.commands)} command(s)"
        elif spell_name in _READER_SPELLS:
            path = arguments.get("path")
            lines = arguments.get("lines")
            step.files.append(
                FileExploration(
                    path=str(path),
                    operation=spell_name,
                    lines=str(lines) if lines else None,
                )
            )
            step.title = f"Explored {len(step.files)} file(s)"
        else:
            step.details.append(f"Executing {spell_name}...")
            if elapsed_seconds is not None:
                step.title = f"Worked for {format_duration(elapsed_seconds)}"

        self._steps_by_spell_id[spell_id] = step
        self._message.parts.append(MessagePart(MessagePartType.STEP, step=step))
        return step

    def end_spell(
        self,
        data: SpellEndPayload | Mapping[str, Any],
        *,
        duration_seconds: float,
        elapsed_seconds: float | None = None,
    ) -> None:
        """Consume a SPELL_CASTING_END payload, paired by spell cast id."""
        step = self._steps_by_spell_id.get(str(data.get("spellCastId", "")))
        if step is None:
            return
        result = data.get("result")
        error = data.get("error")
        payload = str(result or error or "")

        if step.step_type is StepType.COMMANDS and step.commands:
            entry = step.commands[-1]
            if not entry.output and not entry.is_error:
                entry.output = payload
                entry.is_error = error is not None
                entry.duration_seconds = duration_seconds
        elif step.step_type is StepType.FILES and step.files:
            file_entry = step.files[-1]
            if not file_entry.details and not file_entry.is_error:
                file_entry.details = payload
                file_entry.is_error = error is not None
        elif step.step_type is StepType.WORKED:
            elapsed = (
                elapsed_seconds if elapsed_seconds is not None else duration_seconds
            )
            step.duration_seconds = elapsed
            step.title = f"Worked for {format_duration(elapsed)}"
            step.result = payload

    def add_artifact(self, artifact: Artifact) -> None:
        """Record a structured artifact as the next part of the bubble."""
        self._message.artifacts.append(artifact)
        self._message.parts.append(
            MessagePart(MessagePartType.ARTIFACT, artifact=artifact)
        )

    def set_text(self, text: str) -> None:
        """Replace the bubble's text projection with a single TEXT part."""
        self._message.parts = [MessagePart(part_type=MessagePartType.TEXT, text=text)]

    def append_text(self, text: str) -> None:
        """Append text to the bubble's text projection."""
        self._merge_or_append(MessagePartType.TEXT, text)

    def finish(self, *, elapsed_seconds: float | None = None) -> None:
        """Close out the invocation: complete worked steps, stop streaming."""
        for part in self._message.parts:
            if (
                part.part_type is MessagePartType.STEP
                and part.step is not None
                and part.step.step_type is StepType.WORKED
            ):
                if elapsed_seconds is not None:
                    part.step.duration_seconds = elapsed_seconds
                    part.step.title = f"Worked for {format_duration(elapsed_seconds)}"
                part.step.is_complete = True
        self._message.is_streaming = False

    @classmethod
    def from_tome_content(
        cls,
        content: Any,
        *,
        role: str = "assistant",
        model: str | None = None,
    ) -> InvocationTranscript:
        """Rebuild a transcript from a persisted Tome Invocation payload."""
        transcript = cls(role=role, model=model)
        if isinstance(content, str):
            cleaned, thoughts = _extract_contemplation_tags(content)
            ordered = _extract_ordered_content(content)
            if ordered:
                for segment in ordered:
                    segment_text = segment.get("text", "")
                    if not segment_text:
                        continue
                    if segment.get("type") == "thought":
                        transcript._append_part(
                            MessagePartType.CONTEMPLATION, segment_text
                        )
                    else:
                        transcript._append_part(MessagePartType.TEXT, segment_text)
            elif cleaned or thoughts:
                for thought in thoughts:
                    transcript._append_part(MessagePartType.CONTEMPLATION, thought)
                if cleaned:
                    transcript._append_part(MessagePartType.TEXT, cleaned)
            # Fall through to the final return below.

        if isinstance(content, list):
            for item in normalize_content_blocks(content):
                transcript._consume_replay_dict(item)
        return transcript

    def _consume_replay_dict(self, item: dict[str, Any]) -> None:
        item_type = item.get("type")
        if item_type in ("contemplation", "thinking"):
            thought = str(item.get("text", "") or item.get("thinking", ""))
            if thought:
                self._append_part(MessagePartType.CONTEMPLATION, thought)
        elif item_type == "text":
            cleaned, thoughts = _extract_contemplation_tags(str(item.get("text", "")))
            for thought in thoughts:
                self._append_part(MessagePartType.CONTEMPLATION, thought)
            if cleaned:
                self._append_part(MessagePartType.TEXT, cleaned)
        elif item_type in ("spell_cast", "tool_call", "tool_use"):
            spell_obj = (
                item.get("spell_cast", {})
                if isinstance(item.get("spell_cast"), dict)
                else item
            )
            spell_name = str(spell_obj.get("name", "") or item.get("name", ""))
            raw_params: object = spell_obj.get("arguments", item.get("arguments", {}))
            step = ExecutionStep(
                step_type=_categorize_spell(spell_name),
                spell_name=spell_name,
                params=raw_params if isinstance(raw_params, dict) else {},
                is_complete=True,
            )
            self._message.parts.append(MessagePart(MessagePartType.STEP, step=step))
        elif "text" in item:
            self._append_part(MessagePartType.TEXT, str(item["text"]))

    def _append_thought_delta(self, text: str) -> None:
        self._merge_or_append(MessagePartType.CONTEMPLATION, text)

    def _append_text_delta(self, text: str) -> None:
        if "<think>" in text or "<thought>" in text:
            cleaned, thoughts = _extract_contemplation_tags(text)
            for thought in thoughts:
                self._append_part(MessagePartType.CONTEMPLATION, thought)
            if cleaned:
                self._merge_or_append(MessagePartType.TEXT, cleaned)
        else:
            self._merge_or_append(MessagePartType.TEXT, text)

    def _append_part(self, part_type: MessagePartType, text: str) -> None:
        self._message.parts.append(MessagePart(part_type=part_type, text=text))

    def _merge_or_append(self, part_type: MessagePartType, text: str) -> None:
        parts = self._message.parts
        if parts and parts[-1].part_type == part_type:
            parts[-1].text += text
        else:
            parts.append(MessagePart(part_type=part_type, text=text))
