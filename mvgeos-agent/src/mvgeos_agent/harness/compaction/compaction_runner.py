"""Decides when to compact a Mana Pool and carries it out.

The pure measuring and splitting live in `compaction`. This module owns the
side effects: calling a Realm for the summary, emitting lifecycle events, and
recording the compaction on the Tome.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from mvgeos_core.channel import (
    ChannelConfig,
    Model,
    RealmResponse,
)
from mvgeos_provider.base import Realm
from mvgeos_provider.retry import DEFAULT_RETRY_POLICY, RetryPolicy, retry_invocation

if TYPE_CHECKING:
    from mvgeos_core.loop import EmitSink

    from mvgeos_agent.agent_session import MvgeTome

from mvgeos_core.abort import AbortSignal
from mvgeos_core.events import (
    ContentType,
    MvgeEvent,
    MvgeEventType,
)
from mvgeos_core.invocations import (
    MvgeInvocation,
    SummonerRequest,
)

from mvgeos_agent.harness.compaction.compaction import (
    DEFAULT_COMPACTION_SETTINGS,
    CompactionSettings,
    estimate_context_mana,
    generate_summary,
    prepare_compaction,
    should_compact,
)

logger = logging.getLogger(__name__)

_SUMMARY_PREFIX = "Summary of earlier conversation:\n\n"

_SKILL_CONTENT_REGEX = re.compile(
    r"(<skill_content name=\"[^\"]+\".*?</skill_content>)", re.DOTALL
)
_SKILL_NAME_REGEX = re.compile(r"<skill_content name=\"([^\"]+)\"")


def _extract_skill_contents(invocations: list[MvgeInvocation]) -> dict[str, str]:
    """Map skill name -> latest <skill_content> block in invocations."""
    skills: dict[str, str] = {}
    for inv in invocations:
        content = getattr(inv, "content", "")
        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") in ("text", ContentType.TEXT):
                        text += block.get("text", "")
                elif isinstance(block, str):
                    text += block
        for match in _SKILL_CONTENT_REGEX.finditer(text):
            full_block = match.group(1)
            name_match = _SKILL_NAME_REGEX.search(full_block)
            if name_match:
                skills[name_match.group(1)] = full_block
    return skills


def _preserve_skill_contents(
    to_summarize: list[MvgeInvocation],
    retained_tail: list[MvgeInvocation],
) -> list[str]:
    """Extract <skill_content> blocks from summarized invocations
    not present in retained tail.
    """
    summarized_skills = _extract_skill_contents(to_summarize)
    retained_skills = _extract_skill_contents(retained_tail)
    return [
        block
        for name, block in summarized_skills.items()
        if name not in retained_skills
    ]


class CompactionRunner:
    """Compacts a transcript once it crowds the Mana Pool."""

    def __init__(
        self,
        realm: Realm,
        model: Model,
        emit: EmitSink | None = None,
        settings: CompactionSettings = DEFAULT_COMPACTION_SETTINGS,
        tome: MvgeTome | None = None,
        retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    ) -> None:
        self._realm = realm
        self._model = model
        self._emit = emit
        self._settings = settings
        self._tome = tome
        self._retry_policy = retry_policy
        self._previous_summary: str | None = None

    async def _send_event(self, event: MvgeEvent) -> None:
        if self._emit is not None:
            await self._emit(event)

    @property
    def _session_name(self) -> str:
        """Tome id for the sigil contract; empty when no tome is bound."""
        if self._tome is None:
            return ""
        return self._tome.tome_id

    async def maybe_compact(
        self,
        invocations: list[MvgeInvocation],
        signal: AbortSignal | None = None,
    ) -> list[MvgeInvocation] | None:
        """Compact if the Mana Pool is crowded, else return None.

        Returns the replacement transcript on success. On any failure it
        returns None and the run continues uncompacted: compaction is a
        recovery mechanism and must not become a new failure mode.
        """
        if not self._settings.enabled:
            return None

        context_mana = estimate_context_mana(invocations).mana
        if not should_compact(context_mana, self._model.context_window, self._settings):
            return None

        prepared = prepare_compaction(
            invocations, self._settings, self._previous_summary
        )
        if prepared is None:
            return None

        await self._send_event(
            MvgeEvent(
                type=MvgeEventType.COMPACTION_START,
                data={
                    # session_name/message_count satisfy the sigil contract;
                    # the mana keys stay for event-bus observers.
                    "session_name": self._session_name,
                    "message_count": len(invocations),
                    "mana_before": prepared.mana_before,
                    "mana_pool": self._model.context_window,
                },
            )
        )

        summary = await generate_summary(
            prepared.to_summarize, self._summarize, prepared.previous_summary, signal
        )
        if summary is None:
            await self._send_event(
                MvgeEvent(
                    type=MvgeEventType.COMPACTION_END,
                    data={
                        "session_name": self._session_name,
                        "original_count": len(prepared.to_summarize),
                        "compacted_count": 0,
                        "error": "Summarization failed",
                    },
                )
            )
            return None

        preserved_skills = _preserve_skill_contents(
            prepared.to_summarize, prepared.retained_tail
        )
        preserved_invocations: list[MvgeInvocation] = [
            SummonerRequest(role="user", content=block) for block in preserved_skills
        ]
        replacement: list[MvgeInvocation] = [
            SummonerRequest(role="user", content=f"{_SUMMARY_PREFIX}{summary}"),
            *preserved_invocations,
            *prepared.retained_tail,
        ]
        self._previous_summary = summary

        self._record(summary, prepared.mana_before, prepared.retained_tail)

        await self._send_event(
            MvgeEvent(
                type=MvgeEventType.COMPACTION_END,
                data={
                    "session_name": self._session_name,
                    "original_count": len(prepared.to_summarize),
                    "compacted_count": len(replacement),
                    "mana_before": prepared.mana_before,
                    "mana_after": estimate_context_mana(replacement).mana,
                    "summarized": len(prepared.to_summarize),
                    "retained": len(prepared.retained_tail),
                },
            )
        )
        return replacement

    async def force_compact(
        self,
        invocations: list[MvgeInvocation],
        signal: AbortSignal | None = None,
    ) -> list[MvgeInvocation] | None:
        """Force manual compaction of invocations regardless of
        current Mana pressure.
        """
        prepared = prepare_compaction(
            invocations, self._settings, self._previous_summary
        )
        if prepared is None:
            return None

        await self._send_event(
            MvgeEvent(
                type=MvgeEventType.COMPACTION_START,
                data={
                    # session_name/message_count satisfy the sigil contract;
                    # the mana keys stay for event-bus observers.
                    "session_name": self._session_name,
                    "message_count": len(invocations),
                    "mana_before": prepared.mana_before,
                    "mana_pool": self._model.context_window,
                },
            )
        )

        summary = await generate_summary(
            prepared.to_summarize, self._summarize, prepared.previous_summary, signal
        )
        if summary is None:
            await self._send_event(
                MvgeEvent(
                    type=MvgeEventType.COMPACTION_END,
                    data={
                        "session_name": self._session_name,
                        "original_count": len(prepared.to_summarize),
                        "compacted_count": 0,
                        "error": "Summarization failed",
                    },
                )
            )
            return None

        preserved_skills = _preserve_skill_contents(
            prepared.to_summarize, prepared.retained_tail
        )
        preserved_invocations = [
            SummonerRequest(role="user", content=block) for block in preserved_skills
        ]
        replacement = [
            SummonerRequest(role="user", content=f"{_SUMMARY_PREFIX}{summary}"),
            *preserved_invocations,
            *prepared.retained_tail,
        ]
        self._previous_summary = summary

        self._record(summary, prepared.mana_before, prepared.retained_tail)

        await self._send_event(
            MvgeEvent(
                type=MvgeEventType.COMPACTION_END,
                data={
                    "session_name": self._session_name,
                    "original_count": len(prepared.to_summarize),
                    "compacted_count": len(replacement),
                    "mana_before": prepared.mana_before,
                    "mana_after": estimate_context_mana(replacement).mana,
                    "summarized": len(prepared.to_summarize),
                    "retained": len(prepared.retained_tail),
                },
            )
        )
        return replacement

    async def _summarize(
        self,
        messages: list[dict[str, str]],
        signal: AbortSignal | None = None,
    ) -> str:
        """Ask the Realm for a summary. Spells are deliberately not offered."""
        config = ChannelConfig(
            model=self._model,
            temperature=0.3,
            max_tokens=self._model.max_tokens,
        )

        async def produce() -> RealmResponse:
            return await self._realm.complete(self._model, messages, config, signal)  # type: ignore[no-any-return]

        response = await retry_invocation(
            produce,
            self._retry_policy,
            signal=signal,
        )

        if response.error_message or response.invocation is None:
            raise RuntimeError(response.error_message or "Realm returned no summary")

        return "".join(
            block.get("text", "")
            for block in response.invocation.content or []
            if block.get("type") == ContentType.TEXT
        )

    def _record(
        self,
        summary: str,
        mana_before: int,
        retained_tail: list[MvgeInvocation],
    ) -> None:
        if self._tome is None:
            return
        try:
            self._tome.record_compaction(
                summary=summary,
                mana_before=mana_before,
                retained_tail=retained_tail,
            )
        except Exception:
            logger.exception("Failed to record compaction on the Tome")


__all__ = ["CompactionRunner"]
