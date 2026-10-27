"""Pure helpers for deciding when and where to compact a Mana Pool.

Nothing here performs I/O or calls a Realm. The caller decides what to do with
a cut point; these functions only measure and locate.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from mvge.core.abort import AbortSignal
from mvge.core.channel import (
    MvgeResponse,
    StopReason,
)
from mvge.core.events import ContentType
from mvge.core.invocations import (
    MvgeInvocation,
    SummonerRequest,
    extract_text_blocks,
)
from mvge.core.spells import SpellResultMessage

logger = logging.getLogger(__name__)

_CHARS_PER_MANA = 4

SummarizeFn = Callable[[list[dict[str, str]], "AbortSignal | None"], Awaitable[str]]

SUMMARIZATION_SYSTEM_PROMPT = (
    "You are a context summarization assistant. Your task is to read a "
    "conversation between a Summoner and a Mvge, then produce a structured "
    "summary following the exact format specified.\n\n"
    "Do NOT continue the conversation. Do NOT respond to any questions in the "
    "conversation. ONLY output the structured summary."
)

_SUMMARIZATION_PROMPT = """The messages above are a conversation to summarize. \
Create a structured context checkpoint summary that another Mvge will use to \
continue the work.

Use this EXACT format:

## Goal
[What is the Summoner trying to accomplish?]

## Constraints & Preferences
- [Any constraints, preferences, or requirements mentioned]
- [Or "(none)" if none were mentioned]

## Progress
### Done
- [x] [Completed tasks/changes]

### In Progress
- [ ] [Current work]

### Blocked
- [Issues preventing progress, if any]

## Key Decisions
- **[Decision]**: [Brief rationale]

## Next Steps
1. [Ordered list of what should happen next]

## Critical Context
- [Any data, examples, or references needed to continue]
- [Or "(none)" if not applicable]

Keep each section concise. Preserve exact file paths, function names, and error \
messages."""

_UPDATE_SUMMARIZATION_PROMPT = """The messages above are NEW conversation \
messages to incorporate into the existing summary provided in \
<previous-summary> tags.

Update the existing structured summary with new information. RULES:
- PRESERVE all existing information from the previous summary
- ADD new progress, decisions, and context from the new messages
- UPDATE the Progress section: move items from "In Progress" to "Done" when \
completed
- UPDATE "Next Steps" based on what was accomplished
- PRESERVE exact file paths, function names, and error messages
- If something is no longer relevant, you may remove it

Keep the same format as the previous summary. Keep each section concise."""


@dataclass(frozen=True)
class CompactionSettings:
    """Thresholds governing when the Mana Pool is compacted."""

    enabled: bool = True
    reserve_mana: int = 16384
    keep_recent_mana: int = 20000


DEFAULT_COMPACTION_SETTINGS = CompactionSettings()


@dataclass(frozen=True)
class ContextManaEstimate:
    """Estimated Mana occupied by a list of Invocations."""

    mana: int
    usage_mana: int
    trailing_mana: int
    last_usage_index: int | None


@dataclass(frozen=True)
class CutPoint:
    """Where a compaction should split the transcript."""

    first_kept_index: int
    turn_start_index: int
    is_split_turn: bool


def calculate_context_mana(mana_usage: dict[str, float]) -> int:
    """Total Mana reported by a Realm usage block."""
    if not mana_usage:
        return 0
    total = mana_usage.get("total")
    if total:
        return int(total)
    return int(mana_usage.get("input", 0) + mana_usage.get("output", 0))


def should_compact(
    context_mana: int,
    mana_pool: int,
    settings: CompactionSettings = DEFAULT_COMPACTION_SETTINGS,
) -> bool:
    """Whether context Mana has crossed the compaction threshold."""
    if not settings.enabled:
        return False
    return context_mana > mana_pool - settings.reserve_mana


def _text_chars(content: Any) -> int:
    if isinstance(content, str):
        return len(content)
    if not isinstance(content, list):
        return 0
    chars = 0
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == ContentType.TEXT:
            chars += len(block.get("text", ""))
    return chars


def estimate_invocation_mana(invocation: MvgeInvocation) -> int:
    """Conservative character-based Mana estimate for one Invocation."""
    chars = 0
    if isinstance(invocation, SummonerRequest):
        chars = _text_chars(invocation.content)
    elif isinstance(invocation, MvgeResponse):
        for block in invocation.content or []:
            if block.get("type") == ContentType.TEXT:
                chars += len(block.get("text", ""))
            elif block.get("type") == ContentType.SPELL_CAST:
                call = block.get("spell_cast", {})
                chars += len(call.get("name", ""))
                chars += len(json.dumps(call.get("arguments", {})))
    elif isinstance(invocation, SpellResultMessage):
        chars = _text_chars(invocation.content)
    return math.ceil(chars / _CHARS_PER_MANA)


def _usage_of(invocation: MvgeInvocation) -> dict[str, float] | None:
    if not isinstance(invocation, MvgeResponse):
        return None
    if invocation.stop_reason in (StopReason.ERROR, StopReason.ABORTED):
        return None
    if not invocation.mana_usage:
        return None
    if calculate_context_mana(invocation.mana_usage) <= 0:
        return None
    return invocation.mana_usage


def estimate_context_mana(invocations: list[MvgeInvocation]) -> ContextManaEstimate:
    """Estimate context Mana, preferring Realm-reported usage over heuristics."""
    last_index: int | None = None
    last_usage: dict[str, float] | None = None
    for index in range(len(invocations) - 1, -1, -1):
        usage = _usage_of(invocations[index])
        if usage is not None:
            last_index = index
            last_usage = usage
            break

    if last_usage is None or last_index is None:
        estimated = sum(estimate_invocation_mana(inv) for inv in invocations)
        return ContextManaEstimate(
            mana=estimated,
            usage_mana=0,
            trailing_mana=estimated,
            last_usage_index=None,
        )

    usage_mana = calculate_context_mana(last_usage)
    trailing = sum(
        estimate_invocation_mana(inv) for inv in invocations[last_index + 1 :]
    )
    return ContextManaEstimate(
        mana=usage_mana + trailing,
        usage_mana=usage_mana,
        trailing_mana=trailing,
        last_usage_index=last_index,
    )


def _valid_cut_indices(
    invocations: list[MvgeInvocation], start: int, end: int
) -> list[int]:
    # A Spell result must stay with the Invocation that requested it, so it is
    # never a valid place to cut.
    return [
        index
        for index in range(start, end)
        if not isinstance(invocations[index], SpellResultMessage)
    ]


def find_turn_start_index(
    invocations: list[MvgeInvocation], index: int, start: int
) -> int:
    """Index of the SummonerRequest that opened the turn containing `index`."""
    for candidate in range(index, start - 1, -1):
        if isinstance(invocations[candidate], SummonerRequest):
            return candidate
    return -1


def find_cut_point(
    invocations: list[MvgeInvocation],
    start: int,
    end: int,
    keep_recent_mana: int,
) -> CutPoint:
    """Locate the cut that retains roughly `keep_recent_mana` of recent context."""
    cut_indices = _valid_cut_indices(invocations, start, end)
    if not cut_indices:
        return CutPoint(
            first_kept_index=start, turn_start_index=-1, is_split_turn=False
        )

    cut_index = cut_indices[0]
    accumulated = 0
    for index in range(end - 1, start - 1, -1):
        accumulated += estimate_invocation_mana(invocations[index])
        if accumulated >= keep_recent_mana:
            for candidate in cut_indices:
                if candidate >= index:
                    cut_index = candidate
                    break
            break

    cut = invocations[cut_index]
    is_summoner_request = isinstance(cut, SummonerRequest)
    turn_start = (
        -1
        if is_summoner_request
        else find_turn_start_index(invocations, cut_index, start)
    )
    return CutPoint(
        first_kept_index=cut_index,
        turn_start_index=turn_start,
        is_split_turn=not is_summoner_request and turn_start != -1,
    )


@dataclass(frozen=True)
class CompactionPreparation:
    """The split of a transcript into the part to summarize and the part kept."""

    to_summarize: list[MvgeInvocation] = field(default_factory=list)
    retained_tail: list[MvgeInvocation] = field(default_factory=list)
    first_kept_index: int = 0
    mana_before: int = 0
    is_split_turn: bool = False
    previous_summary: str | None = None


def prepare_compaction(
    invocations: list[MvgeInvocation],
    settings: CompactionSettings = DEFAULT_COMPACTION_SETTINGS,
    previous_summary: str | None = None,
) -> CompactionPreparation | None:
    """Split a transcript at its cut point, or return None if there is nothing to do."""
    if not invocations:
        return None

    cut = find_cut_point(invocations, 0, len(invocations), settings.keep_recent_mana)
    if cut.first_kept_index <= 0:
        return None

    return CompactionPreparation(
        to_summarize=list(invocations[: cut.first_kept_index]),
        retained_tail=list(invocations[cut.first_kept_index :]),
        first_kept_index=cut.first_kept_index,
        mana_before=estimate_context_mana(invocations).mana,
        is_split_turn=cut.is_split_turn,
        previous_summary=previous_summary,
    )


def _render_invocation(invocation: MvgeInvocation) -> str | None:
    if isinstance(invocation, SummonerRequest):
        text = _render_content(invocation.content)
        return f"Summoner: {text}" if text else None
    if isinstance(invocation, MvgeResponse):
        parts: list[str] = []
        for block in invocation.content or []:
            if block.get("type") == ContentType.TEXT:
                parts.append(block.get("text", ""))
            elif block.get("type") == ContentType.SPELL_CAST:
                call = block.get("spell_cast", {})
                parts.append(f"[cast {call.get('name', '')}]")
        text = " ".join(part for part in parts if part)
        return f"Mvge: {text}" if text else None
    # MvgeInvocation is a closed union, so this is the last arm.
    text = _render_content(invocation.content)
    return f"Spell result ({invocation.spell_name}): {text}" if text else None


def _render_content(content: Any) -> str:
    return extract_text_blocks(content)


async def generate_summary(
    invocations: list[MvgeInvocation],
    summarize: SummarizeFn,
    previous_summary: str | None = None,
    signal: AbortSignal | None = None,
) -> str | None:
    """Ask a Realm to summarize a transcript.

    Returns None when summarization fails or yields nothing usable. Compaction
    is a recovery mechanism, so it must never become a new failure mode: the
    caller continues uncompacted.
    """
    transcript = "\n".join(
        rendered
        for rendered in (_render_invocation(inv) for inv in invocations)
        if rendered
    )
    if not transcript.strip():
        return None

    instruction = (
        _UPDATE_SUMMARIZATION_PROMPT if previous_summary else _SUMMARIZATION_PROMPT
    )
    user_content = transcript
    if previous_summary:
        user_content = (
            f"<previous-summary>\n{previous_summary}\n</previous-summary>\n\n"
            f"{transcript}"
        )

    messages = [
        {"role": "system", "content": SUMMARIZATION_SYSTEM_PROMPT},
        {"role": "user", "content": f"{user_content}\n\n{instruction}"},
    ]

    try:
        summary = await summarize(messages, signal)
    except Exception:
        logger.exception("Compaction summarization failed")
        return None

    return summary.strip() or None


__all__ = [
    "DEFAULT_COMPACTION_SETTINGS",
    "SUMMARIZATION_SYSTEM_PROMPT",
    "CompactionPreparation",
    "CompactionSettings",
    "ContextManaEstimate",
    "CutPoint",
    "calculate_context_mana",
    "estimate_context_mana",
    "estimate_invocation_mana",
    "find_cut_point",
    "find_turn_start_index",
    "generate_summary",
    "prepare_compaction",
    "should_compact",
]
