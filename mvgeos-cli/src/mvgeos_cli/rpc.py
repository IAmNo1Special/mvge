"""Headless bidirectional stdio JSONL RPC server for MvgeOS.

Compatible with earendil-works/pi --mode rpc.
Provides strict newline-delimited JSON messaging over stdio, supporting
streaming turn events, approval requests (extension UI), and asynchronous commands.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import inspect
import json
import logging
import sys
import uuid
from collections.abc import Callable, Mapping
from enum import Enum
from pathlib import Path
from typing import Any, TextIO

from mvgeos_agent.commands import SLASH_COMMANDS
from mvgeos_agent.protocol import MvgeAgent
from mvgeos_core.events import MvgeEvent, MvgeEventType

from mvgeos_cli.approval_binding import approval_presenter_bound
from mvgeos_cli.approval_types import (
    ApprovalDecision,
    ApprovalReasonCode,
    ApprovalRequest,
    ApprovalScope,
    allow,
    deny,
)

logger = logging.getLogger(__name__)


def _json_safe(val: Any) -> Any:
    if isinstance(val, (dict, Mapping)):
        return {str(k): _json_safe(v) for k, v in val.items()}
    if isinstance(val, (list, tuple)):
        return [_json_safe(x) for x in val]
    if isinstance(val, Enum):
        return val.value
    if isinstance(val, Path):
        return str(val)
    if dataclasses.is_dataclass(val) and not isinstance(val, type):
        return dataclasses.asdict(val)
    if isinstance(val, (str, int, float, bool, type(None))):
        return val
    return str(val)


def _serialize_event_data(
    event_type: MvgeEventType, data: dict[str, Any]
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in data.items():
        # O(1) bandwidth rule (ADR 0025): Strip cumulative transcript
        # from MESSAGE_UPDATE
        if event_type == MvgeEventType.MESSAGE_UPDATE and k in (
            "invocation",
            "content",
            "transcript",
        ):
            if hasattr(v, "id"):
                out["message_id"] = v.id
            if hasattr(v, "role"):
                out["role"] = v.role
            continue
        out[k] = _json_safe(v)
    return out


class RpcApprovalPresenter:
    """Approval presenter dispatching approval requests across stdio RPC."""

    def __init__(self, send_fn: Callable[[dict[str, Any]], None]) -> None:
        self._send_fn = send_fn
        self._pending: dict[
            str, tuple[asyncio.Future[ApprovalDecision], ApprovalRequest]
        ] = {}
        self._closed = False

    def close(self) -> None:
        self._closed = True
        for fut, req in list(self._pending.values()):
            if not fut.done():
                with contextlib.suppress(Exception):
                    fut.set_result(deny(req, reason_code=ApprovalReasonCode.FAILURE))
        self._pending.clear()

    @property
    def closed(self) -> bool:
        return self._closed

    def resolve(self, request_id: str, decision: str, reason: str = "") -> bool:
        entry = self._pending.pop(request_id, None)
        if entry is None:
            return False
        fut, req = entry
        if fut.done():
            return False
        dec_lower = decision.lower()
        if dec_lower in ("allow", "allow_once", "once"):
            fut.set_result(
                allow(
                    req,
                    reason_code=ApprovalReasonCode.USER,
                    scope=ApprovalScope.ONCE,
                )
            )
        elif dec_lower in ("allow_session", "session"):
            fut.set_result(
                allow(
                    req,
                    reason_code=ApprovalReasonCode.USER,
                    scope=ApprovalScope.SESSION,
                )
            )
        elif dec_lower in ("always_allow", "always", "always_allow_spell", "spell"):
            fut.set_result(
                allow(
                    req,
                    reason_code=ApprovalReasonCode.USER,
                    scope=ApprovalScope.SPELL,
                )
            )
        else:
            fut.set_result(
                deny(
                    req,
                    reason_code=ApprovalReasonCode.USER,
                    scope=ApprovalScope.ONCE,
                )
            )
        return True

    async def __call__(self, request: ApprovalRequest) -> ApprovalDecision:
        return await self.request_approval(request)

    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        if self._closed:
            return deny(request, reason_code=ApprovalReasonCode.FAILURE)
        req_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[ApprovalDecision] = loop.create_future()
        self._pending[req_id] = (fut, request)

        cast_id = getattr(request, "cast_id", None) or getattr(
            request, "spell_cast_id", ""
        )
        grant_scope_obj = getattr(request, "grant_scope", None)
        grant_scope_val = (
            getattr(grant_scope_obj, "value", None)
            if grant_scope_obj is not None
            else None
        )
        frame = {
            "type": "extension_ui_request",
            "request_id": req_id,
            "spell_name": request.spell_name,
            "spell_cast_id": cast_id,
            "arguments": _json_safe(request.arguments),
            "reason": getattr(request, "reason", ""),
            "grant_scope": grant_scope_val,
        }
        self._send_fn(frame)

        try:
            return await fut
        except asyncio.CancelledError:
            self._pending.pop(req_id, None)
            return deny(request, reason_code=ApprovalReasonCode.FAILURE)


class RpcServer:
    """Headless stdio JSONL RPC server driving an MvgeAgent session."""

    def __init__(
        self,
        agent: MvgeAgent,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
    ) -> None:
        self.agent = agent
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self.presenter = RpcApprovalPresenter(self._send_frame)
        self._running = False
        self._active_turn_task: asyncio.Task[Any] | None = None
        self._unsubscribers: list[Callable[[], None]] = []

    def _send_frame(self, frame: dict[str, Any]) -> None:
        line = json.dumps(frame) + "\n"
        self.stdout.write(line)
        self.stdout.flush()

    def _send_response(
        self,
        cmd_id: str | None,
        command: str,
        success: bool,
        data: Any = None,
        error: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "type": "response",
            "id": cmd_id,
            "command": command,
            "success": success,
        }
        if data is not None:
            payload["data"] = data
        if error is not None:
            payload["error"] = error
        self._send_frame(payload)

    def _on_agent_event(self, event: MvgeEvent) -> None:
        serialized = _serialize_event_data(event.type, event.data)
        self._send_frame(
            {
                "type": "event",
                "event": event.type.value,
                "data": serialized,
            }
        )
        if event.type == MvgeEventType.MESSAGE_UPDATE:
            text = event.data.get("text") or event.data.get("delta") or ""
            kind = event.data.get("kind")
            if text:
                if kind == "contemplation":
                    self._send_frame({"type": "thinking_delta", "delta": text})
                else:
                    self._send_frame({"type": "text_delta", "delta": text})

    async def _handle_command(self, req: dict[str, Any]) -> None:
        cmd = req.get("command")
        cmd_id = req.get("id")
        params = req.get("params")
        if not isinstance(params, dict):
            params = req

        if not cmd:
            self._send_frame({"type": "error", "error": "Missing 'command' field"})
            return

        try:
            if cmd == "prompt":
                content = (
                    params.get("content")
                    or params.get("prompt")
                    or params.get("message")
                    or ""
                )
                images = params.get("images") or []
                self._send_response(
                    cmd_id, "prompt", success=True, data={"disposition": "started"}
                )

                prompt_arg: str | list[dict[str, Any]] = content
                if images and isinstance(images, list):
                    prompt_arg = [{"type": "text", "text": content}]
                    for img in images:
                        prompt_arg.append({"type": "image", "image": img})

                self._active_turn_task = asyncio.create_task(self.agent.run(prompt_arg))
                try:
                    await self._active_turn_task
                except asyncio.CancelledError:
                    pass
                finally:
                    self._active_turn_task = None

            elif cmd == "steer":
                content = params.get("content") or params.get("text") or ""
                if hasattr(self.agent, "steer"):
                    self.agent.steer(content)
                self._send_response(cmd_id, "steer", success=True)

            elif cmd == "follow_up":
                content = params.get("content") or params.get("text") or ""
                if hasattr(self.agent, "follow_up"):
                    self.agent.follow_up(content)
                self._send_response(cmd_id, "follow_up", success=True)

            elif cmd == "abort":
                if hasattr(self.agent, "abort"):
                    self.agent.abort()
                if self._active_turn_task and not self._active_turn_task.done():
                    self._active_turn_task.cancel()
                self._send_response(cmd_id, "abort", success=True)

            elif cmd == "get_state":
                state_data = {
                    "model": getattr(self.agent, "model_id", ""),
                    "contemplation": str(
                        getattr(self.agent, "contemplation_level", "")
                    ),
                    "tome_id": getattr(self.agent, "tome_id", None),
                    "session_id": getattr(self.agent, "session_id", None),
                    "mana_used": getattr(self.agent, "mana_used", 0),
                    "enabled_spells": getattr(self.agent, "enabled_spells", []),
                    "available_spells": getattr(self.agent, "available_spells", []),
                    "queue_mode": getattr(
                        getattr(self.agent, "queue_mode", None),
                        "value",
                        "one_at_a_time",
                    ),
                }
                self._send_response(cmd_id, "get_state", success=True, data=state_data)

            elif cmd in ("set_model", "switch_model"):
                model = params.get("model") or params.get("model_id")
                if model:
                    if hasattr(self.agent, "switch_model"):
                        res_m = self.agent.switch_model(model)
                        if inspect.isawaitable(res_m):
                            await res_m
                    elif hasattr(self.agent, "set_model"):
                        res_m = self.agent.set_model(model)
                        if inspect.isawaitable(res_m):
                            await res_m
                self._send_response(
                    cmd_id, "set_model", success=True, data={"model": model}
                )

            elif cmd in ("set_thinking_level", "set_contemplation_level"):
                level = params.get("level") or params.get("contemplation")
                if level and hasattr(self.agent, "set_contemplation_level"):
                    res_c = self.agent.set_contemplation_level(level)
                    if inspect.isawaitable(res_c):
                        await res_c
                self._send_response(
                    cmd_id, "set_thinking_level", success=True, data={"level": level}
                )

            elif cmd == "compact":
                compact_res = None
                if hasattr(self.agent, "compact"):
                    compact_res = await self.agent.compact()
                res_str = str(compact_res) if compact_res else None
                self._send_response(
                    cmd_id,
                    "compact",
                    success=True,
                    data={"compacted": True, "result": res_str},
                )

            elif cmd == "fork":
                source_leaf = params.get("source_leaf_id") or params.get("leaf_id")
                new_leaf = None
                agent_tome = getattr(self.agent, "agent_tome", None)
                if agent_tome and hasattr(agent_tome, "fork"):
                    new_leaf = agent_tome.fork(source_leaf_id=source_leaf)
                self._send_response(
                    cmd_id, "fork", success=True, data={"leaf_id": new_leaf}
                )

            elif cmd == "switch_session":
                tome_id = params.get("session_id") or params.get("tome_id")
                if hasattr(self.agent, "switch_session"):
                    await self.agent.switch_session(tome_id)
                self._send_response(
                    cmd_id,
                    "switch_session",
                    success=True,
                    data={"session_id": tome_id},
                )

            elif cmd == "get_tree":
                tree: Any = {}
                agent_tome = getattr(self.agent, "agent_tome", None)
                if agent_tome and hasattr(agent_tome, "get_tree"):
                    tree = agent_tome.get_tree()
                elif (
                    agent_tome
                    and hasattr(agent_tome, "handle")
                    and hasattr(agent_tome.handle, "get_tree")
                ):
                    tree = agent_tome.handle.get_tree()
                self._send_response(cmd_id, "get_tree", success=True, data=tree)

            elif cmd == "get_commands":
                commands_list: list[dict[str, str]] = []
                for sc_name, sc_desc in SLASH_COMMANDS.items():
                    commands_list.append(
                        {
                            "name": sc_name,
                            "description": sc_desc,
                            "kind": "slash",
                        }
                    )
                runner = getattr(self.agent, "runner", None)
                if runner and hasattr(runner, "commands"):
                    for c_name, c_obj in runner.commands.items():
                        commands_list.append(
                            {
                                "name": c_name,
                                "description": getattr(c_obj, "description", ""),
                                "kind": "rune",
                            }
                        )
                self._send_response(
                    cmd_id, "get_commands", success=True, data=commands_list
                )

            elif cmd == "extension_ui_response":
                req_id = str(params.get("request_id") or "")
                decision_val = str(params.get("decision") or "deny")
                reason_val = str(params.get("reason") or "")
                ok = self.presenter.resolve(req_id, decision_val, reason_val)
                self._send_response(cmd_id, "extension_ui_response", success=ok)

            else:
                self._send_response(
                    cmd_id,
                    cmd,
                    success=False,
                    error=f"Unknown command '{cmd}'",
                )
        except Exception as exc:
            logger.exception("Error executing RPC command '%s'", cmd)
            self._send_response(cmd_id, cmd, success=False, error=str(exc))

    async def run(self) -> int:
        self._running = True

        if hasattr(self.agent, "on"):
            for event_type in MvgeEventType:
                try:
                    unsub = self.agent.on(event_type, self._on_agent_event)
                    self._unsubscribers.append(unsub)
                except Exception:
                    pass

        runner = getattr(self.agent, "runner", None)
        try:
            with approval_presenter_bound(runner, self.presenter):
                while self._running:
                    line = await asyncio.to_thread(self.stdin.readline)
                    if not line:
                        break
                    line_str = line.strip()
                    if not line_str:
                        continue
                    try:
                        req = json.loads(line_str)
                    except Exception as err:
                        self._send_frame(
                            {
                                "type": "error",
                                "error": f"Malformed JSON: {err}",
                            }
                        )
                        continue

                    if isinstance(req, dict):
                        await self._handle_command(req)
        finally:
            self._running = False
            self.presenter.close()
            for unsub in self._unsubscribers:
                with contextlib.suppress(Exception):
                    unsub()
            self._unsubscribers.clear()

        return 0


async def run_rpc(
    agent: MvgeAgent,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    """Run an MvgeAgent in headless stdio JSONL RPC mode."""
    server = RpcServer(agent, stdin=stdin, stdout=stdout)
    return await server.run()
