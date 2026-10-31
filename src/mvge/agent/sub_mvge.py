from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from mvge.core.abort import AbortController, AbortError, AbortSignal
from mvge.core.layers import sessions_dir

if TYPE_CHECKING:
    from mvge.agent.harness.harness import MvgeHarness
    from mvge.agent.mvge import Mvge
    from mvge.provider.registry import RealmRegistry
    from mvge.runes.rune_runner import RuneRunner

logger = logging.getLogger(__name__)


class SubMvgeState(StrEnum):
    RUNNING = "running"
    IDLE = "idle"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    CANCELING = "canceling"
    TERMINATED = "terminated"
    COMPLETED = "completed"
    ERRORED = "errored"


@dataclass(frozen=True)
class SubMvgeSpec:
    role: str
    prompt: str
    agent_name: str | None = None
    model: str = "inherit"
    workspace: Literal["inherit", "worktree"] = "inherit"
    allowed_spells: frozenset[str] | None = None
    wait: bool = True


@dataclass
class SubMvgeRecord:
    sub_mvge_id: str
    role: str
    agent_name: str
    model_id: str
    tome_path: Path
    child_tome_id: str
    workspace_path: Path
    state: SubMvgeState
    state_detail: str = ""
    summary: str | None = None
    error: str | None = None
    mana_used: dict[str, int] = field(default_factory=dict)


def _extract_invocation_text(inv: object) -> str:
    content = getattr(inv, "content", None)
    if isinstance(content, list):
        parts = [
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        if parts:
            return "".join(parts)
    text = getattr(inv, "text", None)
    if isinstance(text, str):
        return text
    return str(inv)


class SubMvgeManager:
    """Kernel manager for spawning, coordinating, and persisting child sub-Mvges.

    Persists all child sessions under ~/.agents/sessions/subsessions/,
    enforces post-Rune allowed_spells boundaries, propagates AbortSignals,
    shares approval presentation locks, and wakes parents reactively.
    """

    def __init__(
        self,
        *,
        parent_mvge: Mvge | None = None,
        parent_harness: MvgeHarness | None = None,
        parent_workspace: Path | None = None,
        parent_model_id: str | None = None,
        parent_agent_name: str | None = None,
        shared_realm_registry: RealmRegistry | None = None,
        shared_runner: RuneRunner | None = None,
        subsessions_dir: Path | None = None,
    ) -> None:
        self._parent_mvge = parent_mvge
        self._parent_harness = parent_harness or (
            getattr(parent_mvge, "harness", None)
            or getattr(parent_mvge, "_harness", None)
            if parent_mvge is not None
            else None
        )
        self._parent_workspace = parent_workspace or (
            getattr(parent_mvge, "cwd", None) if parent_mvge is not None else Path.cwd()
        )
        self._parent_model_id = parent_model_id or (
            getattr(parent_mvge, "model_id", None) if parent_mvge is not None else ""
        )
        self._parent_agent_name = parent_agent_name or (
            getattr(parent_mvge, "name", None)
            if parent_mvge is not None
            else "coding_mvge"
        )
        self._shared_realm_registry = shared_realm_registry or (
            getattr(parent_mvge, "provider_registry", None)
            or getattr(parent_mvge, "_provider_registry", None)
            if parent_mvge is not None
            else None
        )
        self._shared_runner = shared_runner or (
            getattr(parent_mvge, "runner", None) if parent_mvge is not None else None
        )
        self._subsessions_dir = subsessions_dir or (sessions_dir() / "subsessions")
        self._subsessions_dir.mkdir(parents=True, exist_ok=True)

        self._records: dict[str, SubMvgeRecord] = {}
        self._instances: dict[str, Mvge] = {}
        self._controllers: dict[str, AbortController] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._worktrees: dict[str, Path] = {}

    def list_active(self) -> list[SubMvgeRecord]:
        """Return records for all tracked sub-Mvges, including terminal
        records (completed, errored, terminated)."""
        return list(self._records.values())

    async def summon(
        self,
        specs: Sequence[SubMvgeSpec],
        *,
        parent_signal: AbortSignal | None = None,
        parent_spell_cast_id: str | None = None,
    ) -> list[SubMvgeRecord]:
        """Spawn one or more child sub-Mvges based on the provided specifications."""
        from mvge.agent.mvge import Mvge

        records_to_return: list[SubMvgeRecord] = []
        waiting_coroutines: list[tuple[str, str]] = []

        for spec in specs:
            sub_mvge_id = f"sub-{uuid.uuid4().hex[:8]}"
            workspace_path = await self._resolve_workspace(sub_mvge_id, spec.workspace)

            child_model = (
                self._parent_model_id if spec.model == "inherit" else spec.model
            )
            child_name = str(
                spec.agent_name or self._parent_agent_name or "coding_mvge"
            )
            child_mvge = Mvge(
                name=child_name,
                caller_dir=workspace_path,
                tome_dir=self._subsessions_dir,
                model_id=child_model,
                allowed_spells=spec.allowed_spells,
                sub_mvge_id=sub_mvge_id,
                parent_spell_cast_id=parent_spell_cast_id,
                shared_realm_registry=self._shared_realm_registry,
                skip_closing_shared_resources=True,
            )

            await child_mvge.initialize()

            # Share approval presenter mutex from parent runner if available
            if self._shared_runner is not None and child_mvge.runner is not None:
                child_mvge.runner.share_approval_presenter_from(self._shared_runner)

            child_tome = child_mvge.agent_tome
            child_tome_id = (
                child_tome.tome_id if child_tome is not None else f"tome-{sub_mvge_id}"
            )
            tome_path = (
                child_tome.path
                if child_tome is not None
                else (self._subsessions_dir / f"{child_tome_id}.jsonl")
            )

            record = SubMvgeRecord(
                sub_mvge_id=sub_mvge_id,
                role=spec.role,
                agent_name=child_mvge.name,
                model_id=child_mvge.model_id or (self._parent_model_id or ""),
                tome_path=tome_path,
                child_tome_id=child_tome_id,
                workspace_path=workspace_path,
                state=SubMvgeState.RUNNING,
                state_detail="Initializing",
            )

            self._records[sub_mvge_id] = record
            self._instances[sub_mvge_id] = child_mvge

            # Setup cancellation cascade
            child_controller = AbortController()
            self._controllers[sub_mvge_id] = child_controller
            if parent_signal is not None:
                parent_signal.on_abort(child_controller.abort)

            records_to_return.append(record)

            if spec.wait:
                waiting_coroutines.append((sub_mvge_id, spec.prompt))
            else:
                task = asyncio.create_task(
                    self._run_child(sub_mvge_id, spec.prompt, notify=True)
                )
                self._tasks[sub_mvge_id] = task

        if waiting_coroutines:
            await asyncio.gather(
                *[
                    self._run_child(sub_id, prompt, notify=False)
                    for sub_id, prompt in waiting_coroutines
                ]
            )

        return records_to_return

    async def _resolve_workspace(
        self, sub_mvge_id: str, workspace_mode: Literal["inherit", "worktree"]
    ) -> Path:
        parent_ws = self._parent_workspace or Path.cwd()
        if workspace_mode != "worktree":
            return parent_ws

        git_dir = parent_ws / ".git"
        if not git_dir.exists():
            return parent_ws

        worktree_dir = self._subsessions_dir / "worktrees" / sub_mvge_id
        worktree_dir.parent.mkdir(parents=True, exist_ok=True)

        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "worktree",
                "add",
                "--detach",
                str(worktree_dir),
                "HEAD",
                cwd=str(parent_ws),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            if proc.returncode == 0:
                self._worktrees[sub_mvge_id] = worktree_dir
                return worktree_dir
        except Exception as ex:
            logger.warning("Failed to create worktree for %s: %s", sub_mvge_id, ex)

        return parent_ws

    async def _run_child(self, sub_mvge_id: str, prompt: str, notify: bool) -> None:
        rec = self._records[sub_mvge_id]
        mvge = self._instances[sub_mvge_id]
        rec.state = SubMvgeState.RUNNING
        rec.state_detail = "Executing turn"
        controller = self._controllers.get(sub_mvge_id)
        signal = controller.signal if controller is not None else None
        try:
            inv = await mvge.run(prompt, signal=signal)
            # An aborted turn returns normally (the loop ends with an
            # ABORTED stop reason), so the signal is the only reliable
            # witness: without this check an aborted child would be
            # reported COMPLETED with the prompt itself as its summary.
            # Previously the controller was never passed to run at all,
            # so a parent abort left the child turn running to completion.
            if signal is not None and signal.aborted:
                rec.state = SubMvgeState.CANCELING
                rec.state_detail = "Cancelled"
                rec.error = "Cancelled"
            else:
                rec.summary = _extract_invocation_text(inv)
                rec.state = SubMvgeState.COMPLETED
                rec.state_detail = "Completed"
                rec.error = None
                harness = getattr(mvge, "harness", None) or getattr(
                    mvge, "_harness", None
                )
                if harness is not None and getattr(harness, "state", None) is not None:
                    rec.mana_used = {
                        "mana_total": getattr(harness.state, "mana_used", 0)
                    }
        except (asyncio.CancelledError, AbortError):
            rec.state = SubMvgeState.CANCELING
            rec.state_detail = "Cancelled"
            rec.error = "Cancelled"
        except Exception as ex:
            rec.state = SubMvgeState.ERRORED
            rec.state_detail = f"Error: {ex}"
            rec.error = str(ex)
        finally:
            if notify:
                self._notify_parent(rec)

    def _notify_parent(self, rec: SubMvgeRecord) -> None:
        harness = self._parent_harness
        if harness is None and self._parent_mvge is not None:
            harness = getattr(self._parent_mvge, "harness", None) or getattr(
                self._parent_mvge, "_harness", None
            )
        if harness is None:
            return

        detail = rec.summary or rec.error or rec.state_detail or ""
        notification = (
            f'<sub_mvge_notification id="{rec.sub_mvge_id}" '
            f'role="{rec.role}" '
            f'status="{rec.state.value}" '
            f'child_tome_id="{rec.child_tome_id}">\n'
            f"{detail}\n"
            f"</sub_mvge_notification>"
        )
        if getattr(harness, "turn_in_flight", False):
            harness.steer(notification)
        else:
            harness.follow_up(notification)

    async def send_message(self, sub_mvge_id: str, message: str) -> SubMvgeRecord:
        """Send a steering or follow-up instruction to a specific sub-Mvge."""
        rec = self._records.get(sub_mvge_id)
        if rec is None:
            raise KeyError(f"No sub-Mvge with id '{sub_mvge_id}' found.")
        if rec.state == SubMvgeState.TERMINATED:
            raise RuntimeError(
                f"Sub-Mvge '{sub_mvge_id}' was terminated and cannot "
                "receive messages; summon a new sub-Mvge to continue."
            )
        mvge = self._instances.get(sub_mvge_id)
        if mvge is None:
            raise RuntimeError(f"Sub-Mvge instance '{sub_mvge_id}' is not available.")

        harness = getattr(mvge, "harness", None) or getattr(mvge, "_harness", None)

        if rec.state == SubMvgeState.RUNNING:
            if harness is not None:
                harness.steer(message)
                rec.state_detail = f"Steering message enqueued: {message[:60]}"
            return rec

        rec.state = SubMvgeState.RUNNING
        rec.state_detail = f"Running follow-up turn: {message[:60]}"
        # A controller aborted by an earlier parent abort or kill stays
        # aborted forever; a follow-up is a new turn, so it needs a fresh
        # one, otherwise every revival would abort before it starts.
        controller = self._controllers.get(sub_mvge_id)
        if controller is None or controller.signal.aborted:
            controller = AbortController()
            self._controllers[sub_mvge_id] = controller
        try:
            inv = await mvge.run(message, signal=controller.signal)
            if controller.signal.aborted:
                rec.state = SubMvgeState.CANCELING
                rec.state_detail = "Cancelled"
                rec.error = "Cancelled"
            else:
                rec.summary = _extract_invocation_text(inv)
                rec.state = SubMvgeState.COMPLETED
                rec.state_detail = "Follow-up turn completed"
                rec.error = None
                if harness is not None and getattr(harness, "state", None) is not None:
                    rec.mana_used = {
                        "mana_total": getattr(harness.state, "mana_used", 0)
                    }
        except (asyncio.CancelledError, AbortError):
            rec.state = SubMvgeState.CANCELING
            rec.state_detail = "Cancelled"
            rec.error = "Cancelled"
        except Exception as ex:
            rec.state = SubMvgeState.ERRORED
            rec.state_detail = f"Error: {ex}"
            rec.error = str(ex)

        return rec

    async def kill(self, sub_mvge_ids: Sequence[str]) -> list[SubMvgeRecord]:
        """Terminate active sub-Mvges and clean up resources."""
        killed: list[SubMvgeRecord] = []
        for sub_id in sub_mvge_ids:
            rec = self._records.get(sub_id)
            if rec is None:
                continue

            ctrl = self._controllers.get(sub_id)
            if ctrl is not None:
                ctrl.abort()

            task = self._tasks.get(sub_id)
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

            rec.state = SubMvgeState.TERMINATED
            rec.state_detail = "Terminated"
            rec.error = "Terminated"

            mvge = self._instances.get(sub_id)
            if mvge is not None:
                with contextlib.suppress(Exception):
                    await mvge.close()

            worktree_dir = self._worktrees.pop(sub_id, None)
            if worktree_dir is not None and worktree_dir.exists():
                with contextlib.suppress(Exception):
                    proc = await asyncio.create_subprocess_exec(
                        "git",
                        "worktree",
                        "remove",
                        "--force",
                        str(worktree_dir),
                        cwd=str(self._parent_workspace or Path.cwd()),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await proc.communicate()

            killed.append(rec)

        return killed

    async def kill_all(self) -> list[SubMvgeRecord]:
        """Terminate all active sub-Mvges."""
        return await self.kill(list(self._records.keys()))


__all__ = [
    "SubMvgeManager",
    "SubMvgeRecord",
    "SubMvgeSpec",
    "SubMvgeState",
]
