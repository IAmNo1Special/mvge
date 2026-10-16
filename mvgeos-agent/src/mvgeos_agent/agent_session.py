from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mvgeos_core.channel import (
    MvgeResponse,
    StopReason,
)
from mvgeos_core.errors import (
    TomeIncompatibleError,
    TomeResumeError,
)
from mvgeos_core.events import ContentType
from mvgeos_core.invocations import (
    MvgeInvocation,
    SummonerRequest,
)
from mvgeos_core.spells import SpellResultMessage
from mvgeos_runes.rune_runner import RuneRunner
from mvgeos_runes.types import SigilHook
from mvgeos_tome.handle import TomeHandle, TomeHandleFactory
from mvgeos_tome.types import (
    TomeEntry,
    TomeEntryType,
    TomeMetadata,
    TomeVersionError,
)

from mvgeos_agent.compatibility import (
    SessionCompatibilityReport,
    validate_session_compatibility,
)
from mvgeos_agent.sigil_payloads import payload_for

logger = logging.getLogger(__name__)

_TOME_FILE_PATTERN = re.compile(r"([a-f0-9]{32})\.jsonl$")


def _generate_short_id() -> str:
    return uuid.uuid4().hex[:8]


def _timestamp_now() -> float:
    return datetime.now(UTC).timestamp()


def _serialise_invocation(invocation: Any) -> dict[str, Any]:
    """Flatten an Invocation to JSON-safe primitives for the Tome file."""
    if dataclasses.is_dataclass(invocation) and not isinstance(invocation, type):
        return dataclasses.asdict(invocation)
    return {"role": getattr(invocation, "role", "unknown")}


class MvgeTome:
    @classmethod
    async def open(
        cls,
        factory: TomeHandleFactory,
        tome_id: str,
        runner: RuneRunner | None = None,
        *,
        expected_model: str | None = None,
        expected_contemplation: str | None = None,
        expected_spells: Sequence[str] | None = None,
        strict: bool = False,
        force_fork: bool = False,
        entries: Sequence[TomeEntry] | None = None,
    ) -> MvgeTome:
        """Resume an existing Tome, validating compatibility with active config.

        Raises:
            TomeResumeError: when the tome cannot be opened or version is unsupported.
            TomeIncompatibleError: when strict=True and compatibility checks fail.
        """
        try:
            read_handle = factory.open_read(tome_id)
            metadata = read_handle.get_metadata()
        except (FileNotFoundError, TomeVersionError) as e:
            raise TomeResumeError(tome_id) from e
        if metadata is None:
            raise TomeResumeError(tome_id)

        write_handle = factory.open_write(metadata.id)

        truncated = write_handle.repair_torn_tail()
        if truncated:
            logger.warning(
                "Repaired torn tail of tome %s (%d bytes)", metadata.id, truncated
            )

        if entries is None:
            entries = read_handle.get_entries()

        report = validate_session_compatibility(
            metadata,
            expected_model=expected_model,
            expected_contemplation=expected_contemplation,
            expected_spells=expected_spells,
            entries=entries,
        )

        if not report.compatible:
            if force_fork:
                logger.info(
                    "Force-forking incompatible tome %s with active configuration",
                    metadata.id,
                )
                try:
                    fork_leaf_id = (
                        cls._leaf_of(factory, metadata.id) or metadata.active_leaf_id
                    )
                    forked_write = factory.create_branched_tome(
                        parent_tome_id=metadata.id,
                        cwd=metadata.cwd,
                        fork_from_leaf_id=fork_leaf_id,
                        model=expected_model or metadata.model,
                        contemplation_level=expected_contemplation
                        or metadata.contemplation_level,
                        spells=expected_spells
                        if expected_spells is not None
                        else metadata.spells,
                    )
                except Exception as e:
                    logger.exception(
                        "Failed to force-fork incompatible tome %s: %s",
                        metadata.id,
                        e,
                    )
                    raise TomeIncompatibleError(
                        tome_id=metadata.id,
                        issues=[d.message for d in report.diagnostics],
                        model_mismatch=report.model_mismatch,
                        missing_spells=report.missing_spells,
                        contemplation_mismatch=report.contemplation_mismatch,
                    ) from e

                forked_read = factory.open_read(forked_write.tome_id)
                tome = cls(factory, forked_write, forked_read, runner)
                tome.last_compatibility_report = report
                await tome.start(reason="fork")
                return tome

            if strict:
                raise TomeIncompatibleError(
                    tome_id=metadata.id,
                    issues=[d.message for d in report.diagnostics],
                    model_mismatch=report.model_mismatch,
                    missing_spells=report.missing_spells,
                    contemplation_mismatch=report.contemplation_mismatch,
                )

            logger.warning(
                "Resuming tome %s with compatibility warnings: %s",
                metadata.id,
                [d.message for d in report.diagnostics],
            )

        tome = cls(factory, write_handle, read_handle, runner)
        tome.last_compatibility_report = report
        await tome.start(reason="resume")
        return tome

    @classmethod
    async def create(
        cls,
        factory: TomeHandleFactory,
        cwd: str | Path | None = None,
        runner: RuneRunner | None = None,
        *,
        model: str | None = None,
        contemplation_level: str | None = None,
        spells: Sequence[str] | None = None,
    ) -> MvgeTome:
        """Create and start a fresh Tome."""
        cwd_str = str(cwd) if cwd is not None else str(Path.cwd())
        write_handle = factory.create_tome(
            cwd_str,
            model=model,
            contemplation_level=contemplation_level,
            spells=list(spells) if spells is not None else None,
        )
        read_handle = factory.open_read(write_handle.tome_id)
        tome = cls(factory, write_handle, read_handle, runner)
        await tome.start(reason="startup")
        return tome

    @classmethod
    async def open_or_create(
        cls,
        factory: TomeHandleFactory,
        tome_resume: str | None = None,
        cwd: str | Path | None = None,
        runner: RuneRunner | None = None,
        *,
        model: str | None = None,
        contemplation_level: str | None = None,
        spells: Sequence[str] | None = None,
        strict: bool = False,
        force_fork: bool = False,
    ) -> MvgeTome:
        """Resume the given tome, or create a fresh one when no target given."""
        if tome_resume:
            return await cls.open(
                factory,
                tome_resume,
                runner,
                expected_model=model,
                expected_contemplation=contemplation_level,
                expected_spells=spells,
                strict=strict,
                force_fork=force_fork,
            )
        return await cls.create(
            factory,
            cwd,
            runner,
            model=model,
            contemplation_level=contemplation_level,
            spells=spells,
        )

    @staticmethod
    def _leaf_of(factory: TomeHandleFactory, tome_id: str) -> str | None:
        return factory.get_leaf_id(tome_id)

    def __init__(
        self,
        factory: TomeHandleFactory,
        write_handle: TomeHandle,
        read_handle: TomeHandle,
        rune_runner: RuneRunner | None = None,
    ) -> None:
        self._factory = factory
        self._write_handle = write_handle
        self._read_handle = read_handle
        self._tome_id = write_handle.tome_id
        self._rune_runner = rune_runner
        self._started = False
        self.last_compatibility_report: SessionCompatibilityReport | None = None

    @property
    def version(self) -> int:
        meta = self.metadata
        return meta.version

    @property
    def tome_id(self) -> str:
        return self._tome_id

    @property
    def tome_file(self) -> str:
        return str(self._factory.tome_file(self._tome_id))

    @property
    def factory(self) -> TomeHandleFactory:
        return self._factory

    @property
    def metadata(self) -> TomeMetadata:
        meta = self._read_handle.get_metadata()
        if meta is None:
            raise TomeResumeError(self._tome_id)
        return meta

    @property
    def compatibility_report(self) -> SessionCompatibilityReport | None:
        return self.last_compatibility_report

    @property
    def active_leaf_id(self) -> str | None:
        return self._factory.get_leaf_id(self._tome_id)

    def set_leaf(self, leaf_id: str) -> TomeEntry:
        """Point the Tome's Leaf at an existing entry."""
        with self._write_handle.locked():
            return self._write_handle.append_leaf(leaf_id)

    def get_entries(
        self,
        entry_type: TomeEntryType | None = None,
        limit: int | None = None,
    ) -> list[TomeEntry]:
        """Fetch entries recorded in this tome session."""
        return self._factory.get_entries(
            self._tome_id, entry_type=entry_type, limit=limit
        )

    def get_context_entries(
        self,
        leaf_id: str | None = None,
        max_entries: int | None = None,
    ) -> list[TomeEntry]:
        """Fetch entries along the branch ending at leaf_id (or active leaf)."""
        target_leaf = leaf_id or self.active_leaf_id
        return self._factory.get_entries_for_context(
            self._tome_id, leaf_id=target_leaf, max_entries=max_entries
        )

    def reconstruct_invocations(self) -> list[MvgeInvocation]:
        """Reconstruct prior conversation invocations from active session branch."""
        leaf_id = self.active_leaf_id
        entries = self._factory.get_entries_for_context(self._tome_id, leaf_id=leaf_id)
        invocations: list[MvgeInvocation] = []

        for entry in entries:
            payload = entry.payload or {}
            if entry.type == TomeEntryType.MESSAGE:
                role = payload.get("role")
                content = payload.get("content")
                if role == "user":
                    user_content = (
                        content if isinstance(content, (str, list)) else str(content)
                    )
                    invocations.append(
                        SummonerRequest(role="user", content=user_content)
                    )
                elif role == "assistant":
                    if isinstance(content, list):
                        content_blocks = content
                    elif isinstance(content, str):
                        content_blocks = [{"type": ContentType.TEXT, "text": content}]
                    else:
                        content_blocks = [
                            {"type": ContentType.TEXT, "text": str(content)}
                        ]
                    stop_reason = StopReason.STOP
                    if "stop_reason" in payload:
                        try:
                            stop_reason = StopReason(payload["stop_reason"])
                        except ValueError:
                            stop_reason = StopReason.STOP
                    invocations.append(
                        MvgeResponse(
                            role="assistant",
                            content=content_blocks,
                            stop_reason=stop_reason,
                        )
                    )
                elif role in ("spellResult", "tool"):
                    if isinstance(content, list):
                        content_blocks = content
                    elif isinstance(content, str):
                        content_blocks = [{"type": ContentType.TEXT, "text": content}]
                    else:
                        content_blocks = [
                            {"type": ContentType.TEXT, "text": str(content)}
                        ]
                    invocations.append(
                        SpellResultMessage(
                            role="spellResult",
                            content=content_blocks,
                            spell_name=str(payload.get("spell_name") or ""),
                            spell_cast_id=str(payload.get("spell_cast_id") or ""),
                        )
                    )
            elif entry.type == TomeEntryType.COMPACTION:
                summary = payload.get("summary", "")
                invocations.append(
                    SummonerRequest(
                        role="user",
                        content=f"Summary of earlier conversation:\n\n{summary}",
                    )
                )
                retained_tail = payload.get("retainedTail")
                if isinstance(retained_tail, list):
                    for item in retained_tail:
                        if isinstance(item, dict):
                            item_role = item.get("role")
                            item_content = item.get("content")
                            if item_role == "user":
                                invocations.append(
                                    SummonerRequest(
                                        role="user",
                                        content=(
                                            item_content
                                            if isinstance(item_content, (str, list))
                                            else str(item_content)
                                        ),
                                    )
                                )
                            elif item_role == "assistant":
                                if isinstance(item_content, list):
                                    c_blocks = item_content
                                elif isinstance(item_content, str):
                                    c_blocks = [
                                        {
                                            "type": ContentType.TEXT,
                                            "text": item_content,
                                        }
                                    ]
                                else:
                                    c_blocks = [
                                        {
                                            "type": ContentType.TEXT,
                                            "text": str(item_content),
                                        }
                                    ]
                                invocations.append(
                                    MvgeResponse(
                                        role="assistant",
                                        content=c_blocks,
                                        stop_reason=StopReason.STOP,
                                    )
                                )
                            elif item_role in ("spellResult", "tool"):
                                if isinstance(item_content, list):
                                    c_blocks = item_content
                                elif isinstance(item_content, str):
                                    c_blocks = [
                                        {
                                            "type": ContentType.TEXT,
                                            "text": item_content,
                                        }
                                    ]
                                else:
                                    c_blocks = [
                                        {
                                            "type": ContentType.TEXT,
                                            "text": str(item_content),
                                        }
                                    ]
                                invocations.append(
                                    SpellResultMessage(
                                        role="spellResult",
                                        content=c_blocks,
                                        spell_name=str(item.get("spell_name") or ""),
                                        spell_cast_id=str(
                                            item.get("spell_cast_id") or ""
                                        ),
                                    )
                                )

        return invocations

    def bind_runner(self, runner: RuneRunner) -> None:
        self._rune_runner = runner

    async def start(self, reason: str = "startup") -> None:
        if self._started:
            return
        self._started = True
        await self._safe_emit(
            SigilHook.SESSION_START,
            payload_for(SigilHook.SESSION_START, session_name=self._tome_id),
        )

    async def shutdown(
        self, reason: str = "quit", target_session_file: str | None = None
    ) -> None:
        if not self._started:
            return
        self._started = False
        await self._safe_emit(
            SigilHook.SESSION_SHUTDOWN,
            payload_for(SigilHook.SESSION_SHUTDOWN, session_name=self._tome_id),
        )

    async def before_switch(self, target_file: str) -> dict[str, Any] | None:
        result = await self._safe_emit_first(
            SigilHook.SESSION_BEFORE_SWITCH,
            payload_for(
                SigilHook.SESSION_BEFORE_SWITCH,
                from_session=self._tome_id,
                to_session=target_file,
            ),
        )
        if isinstance(result, dict) and result.get("cancel"):
            return {"cancelled": True}
        return {"cancelled": False}

    async def before_fork(self, entry_id: str) -> dict[str, Any] | None:
        result = await self._safe_emit_first(
            SigilHook.SESSION_BEFORE_FORK,
            payload_for(
                SigilHook.SESSION_BEFORE_FORK,
                parent_session=self._tome_id,
                entry_id=entry_id,
            ),
        )
        if isinstance(result, dict) and result.get("cancel"):
            return {"cancelled": True}
        return {"cancelled": False}

    async def fork(self, entry_id: str | None = None) -> MvgeTome | None:
        """Fork the Tome at an entry and start the branched tome.

        Returns None when a SESSION_BEFORE_FORK sigil cancels the fork or the
        branch cannot be created; failures leave the source session running.
        """
        target_id = entry_id or self.active_leaf_id or ""
        fork_result = await self.before_fork(target_id)
        if fork_result and fork_result.get("cancelled"):
            return None

        try:
            meta = self.metadata
            forked_write = self._factory.create_branched_tome(
                parent_tome_id=meta.id,
                cwd=meta.cwd,
                fork_from_leaf_id=target_id or None,
            )
        except (KeyError, ValueError, TomeResumeError) as e:
            logger.exception("Failed to fork tome: %s", e)
            return None

        new_tome_file = str(self._factory.tome_file(forked_write.tome_id))
        await self.shutdown(reason="fork", target_session_file=new_tome_file)
        forked_read = self._factory.open_read(forked_write.tome_id)
        new_tome = MvgeTome(self._factory, forked_write, forked_read, self._rune_runner)
        await new_tome.start(reason="fork")
        return new_tome

    async def switch(self, target_file: Path | str) -> MvgeTome | None:
        """Shut the source session down and resume the tome behind a file.

        Returns None when a SESSION_BEFORE_SWITCH sigil cancels the switch,
        the file name carries no tome id, or the target tome cannot be opened;
        failures leave the source session running.
        """
        target_str = str(target_file)
        switch_result = await self.before_switch(target_str)
        if switch_result and switch_result.get("cancelled"):
            return None

        match = _TOME_FILE_PATTERN.search(target_str)
        if not match:
            logger.error("Could not parse tome_id from target_file: %s", target_file)
            return None
        target_tome_id = match.group(1)

        try:
            target_read = self._factory.open_read(target_tome_id)
            if target_read.get_metadata() is None:
                logger.error("Failed to open target tome: %s", target_tome_id)
                return None
            target_write = self._factory.open_write(target_read.tome_id)
        except (ValueError, FileNotFoundError, TomeVersionError) as e:
            logger.exception("Failed to open target tome: %s", e)
            return None

        await self.shutdown(reason="resume", target_session_file=target_str)
        new_tome = MvgeTome(self._factory, target_write, target_read, self._rune_runner)
        await new_tome.start(reason="resume")
        return new_tome

    def _require_started(self) -> bool:
        if not self._started:
            logger.warning(
                "record dropped: tome %s not started",
                self._tome_id,
            )
            return False
        return True

    def record_message(
        self,
        role: str,
        content: Any,
        parent_id: str | None = None,
        model: str | None = None,
        provider: str | None = None,
    ) -> TomeEntry | None:
        if not self._require_started():
            return None
        if parent_id is None:
            parent_id = self.active_leaf_id
        entry = TomeEntry(
            id=_generate_short_id(),
            parent_id=parent_id,
            type=TomeEntryType.MESSAGE,
            timestamp=_timestamp_now(),
            payload={
                "role": role,
                "content": content,
                "model": model,
                "provider": provider,
            },
        )
        with self._write_handle.locked():
            self._write_handle.append(entry)
            self._advance_leaf(entry)
        return entry

    def _advance_leaf(self, entry: TomeEntry | None) -> None:
        """Move the Tome's Leaf to the entry just appended.

        The Leaf marks the current tip of the branch, recorded both as a
        LEAF entry and as the header's activeLeafId. Forking reads it to
        decide where to branch from.
        """
        if entry is None:
            return
        try:
            self._write_handle.append_leaf(entry.id)
        except Exception:
            logger.exception("Failed to advance the Leaf for tome %s", self._tome_id)

    def record_compaction(
        self,
        summary: str,
        mana_before: int,
        retained_tail: list[Any],
        first_kept_entry_id: str | None = None,
        parent_id: str | None = None,
    ) -> TomeEntry | None:
        """Record a compaction so the Tome can rebuild context without replaying
        the Invocations the summary replaced."""
        if not self._require_started():
            return None
        if parent_id is None:
            parent_id = self.active_leaf_id
        payload: dict[str, Any] = {
            "summary": summary,
            "manaBefore": mana_before,
            "retainedTail": [_serialise_invocation(inv) for inv in retained_tail],
        }
        if first_kept_entry_id is not None:
            payload["firstKeptEntryId"] = first_kept_entry_id
        entry = TomeEntry(
            id=_generate_short_id(),
            parent_id=parent_id,
            type=TomeEntryType.COMPACTION,
            timestamp=_timestamp_now(),
            payload=payload,
        )
        self._write_handle.append(entry)
        return entry

    def record_custom(
        self,
        custom_type: str,
        data: dict[str, Any] | None = None,
        parent_id: str | None = None,
    ) -> TomeEntry | None:
        if not self._require_started():
            return None
        if parent_id is None:
            parent_id = self.active_leaf_id
        entry = TomeEntry(
            id=_generate_short_id(),
            parent_id=parent_id,
            type=TomeEntryType.CUSTOM,
            timestamp=_timestamp_now(),
            payload={"type": custom_type, "data": data or {}},
        )
        self._write_handle.append(entry)
        return entry

    def record_tome_info(self, payload: dict[str, Any]) -> TomeEntry | None:
        if not self._require_started():
            return None
        entry = TomeEntry(
            id=_generate_short_id(),
            parent_id=None,
            type=TomeEntryType.TOME_INFO,
            timestamp=_timestamp_now(),
            payload=payload,
        )
        self._write_handle.append(entry)
        return entry

    async def active_leaf_id_async(self) -> str | None:
        return self.active_leaf_id

    async def record_message_async(
        self,
        role: str,
        content: Any,
        parent_id: str | None = None,
        model: str | None = None,
        provider: str | None = None,
    ) -> TomeEntry | None:
        return await asyncio.to_thread(
            self.record_message, role, content, parent_id, model, provider
        )

    async def record_compaction_async(
        self,
        summary: str,
        mana_before: int,
        retained_tail: list[Any],
        first_kept_entry_id: str | None = None,
        parent_id: str | None = None,
    ) -> TomeEntry | None:
        return await asyncio.to_thread(
            self.record_compaction,
            summary,
            mana_before,
            retained_tail,
            first_kept_entry_id,
            parent_id,
        )

    async def record_custom_async(
        self,
        custom_type: str,
        data: dict[str, Any] | None = None,
        parent_id: str | None = None,
    ) -> TomeEntry | None:
        return await asyncio.to_thread(self.record_custom, custom_type, data, parent_id)

    async def record_tome_info_async(self, payload: dict[str, Any]) -> TomeEntry | None:
        return await asyncio.to_thread(self.record_tome_info, payload)

    async def _safe_emit(self, hook: SigilHook, data: Any) -> None:
        if self._rune_runner is None:
            return
        try:
            await self._rune_runner.emit_async(hook, data)
        except Exception:
            logger.exception("Failed to emit %s", hook)

    async def _safe_emit_first(self, hook: SigilHook, data: Any) -> Any | None:
        if self._rune_runner is None:
            return None
        try:
            return await self._rune_runner.emit_first(hook, data)
        except Exception:
            logger.exception("Failed to emit %s", hook)
            return None


__all__ = ["MvgeTome"]
