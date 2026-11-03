"""Host-process side of the Phase 4 v1 rune host.

This module runs inside the host process (``python -m
mvge.runes.host_server --rune-dir <dir>``), never in the engine
process. It loads one rune's factory with the engine loader
(pin verification and source audit run again inside the host,
so a host started by hand gets the same admission checks),
hands the factory a ``HostRuneAPI`` instead of a ``RuneAPI``,
and serves spell, hook, command, and shortcut invocations over
the stdio wire.

Debugging story (the reason stderr exists): the engine captures
this process's stderr to a log file (see ``HostSession``). A
developer debugging a hosted rune:

1. Reads the host stderr log: factory errors, unhandled spell
   exceptions, and host diagnostics land there, never on stdout
   (stdout is the wire — printing to it corrupts the protocol).
2. Runs the host by hand for a single rune:
   ``python -m mvge.runes.host_server --rune-dir <rune-dir>``
   and speaks NDJSON on stdin (hello, init, spell_execute) to
   reproduce a failure without the engine.
3. Attaches a debugger to the host process by pid
   (``HostSession.pid``): the host is a plain Python process,
   one per rune, so a breakpoint in rune code stops only that
   rune, never the engine session.

Unsupported facade methods raise ``HostUnsupportedError`` from
the same table the audit reads (``host_classification``). A
factory that calls one fails at init; a later call fails that
invocation only. Live objects never cross: no runner, no realm,
no approval channel, no sandbox, no model key. The host's own
environment is scrubbed of credential-shaped names at startup,
in addition to the engine scrubbing the spawn environment.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import inspect
import json
import os
import sys
import threading
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, cast

from mvge.core.abort import AbortController, AbortError
from mvge.core.proxy import sanitize_no_proxy_env
from mvge.core.spells import SpellContext
from mvge.runes.host_classification import HOST_UNSUPPORTED_METHODS
from mvge.runes.host_protocol import (
    PROTOCOL_VERSION,
    HostUnsupportedError,
    is_credential_env_name,
)
from mvge.runes.host_transport import JsonLineTransport
from mvge.runes.types import (
    RuneContext,
    RuneDisposableScope,
    SigilHook,
    SkillManifest,
    SpellDefinition,
)


def _hook_name(hook: Any) -> str:
    return str(getattr(hook, "value", hook))


def _run_awaitable(value: Any) -> Any:
    """Run an awaitable to completion, or return a plain value as-is."""
    if inspect.isawaitable(value):
        return asyncio.run(cast(Coroutine[Any, Any, Any], value))
    return value


def _serializable_or_repr(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return {"repr": str(value)}


def _spell_metadata(spell: SpellDefinition) -> dict[str, Any]:
    execution_mode = getattr(spell, "execution_mode", None)
    return {
        "name": spell.name,
        "description": spell.description,
        "parameters": spell.parameters or {},
        "read_only": bool(spell.read_only),
        "execution_mode": str(getattr(execution_mode, "value", execution_mode)),
        "timeout_ms": spell.timeout_ms,
        "prompt_snippet": spell.prompt_snippet,
        "prompt_guidelines": list(spell.prompt_guidelines or []),
    }


def _skill_payload(manifest: SkillManifest) -> dict[str, Any]:
    return {
        "name": manifest.name,
        "description": manifest.description,
        "path": manifest.path,
        "location": manifest.location,
        "version": manifest.version,
        "license": manifest.license,
        "compatibility": manifest.compatibility,
        "metadata": dict(manifest.metadata or {}),
        "allowed_tools": manifest.allowed_tools,
        "disable_model_invocation": bool(manifest.disable_model_invocation),
        "body": manifest.body,
    }


def _unsupported(method: str) -> HostUnsupportedError:
    return HostUnsupportedError(
        f"Hosted rune method '{method}' is unsupported in v1: "
        f"{HOST_UNSUPPORTED_METHODS[method]}"
    )


class HostRuneAPI:
    """The RuneAPI-shaped object a hosted rune factory receives.

    Supported methods cross the wire as requests (registrations,
    queries) or events (audit). Unsupported methods raise
    ``HostUnsupportedError`` before touching the transport, from
    the shared table — which is also why a transport of ``None``
    is enough to test the refusal path.
    """

    def __init__(
        self,
        transport: Any,
        rune_name: str,
        context: dict[str, Any] | None = None,
        install_id: str | None = None,
        state: Any = None,
    ) -> None:
        self._transport = transport
        self._rune_name = rune_name
        self._context_dict = dict(context or {})
        self._install_id = install_id
        self._state = state
        self._scope = RuneDisposableScope(rune_name=rune_name)
        self._local_spells: dict[str, SpellDefinition] = {}
        self._local_hooks: dict[str, list[Any]] = {}
        self._local_commands: dict[str, Any] = {}
        self._local_shortcuts: dict[str, Any] = {}

    # -- local registries (shared with HostState when present) --------
    @property
    def _spells(self) -> dict[str, SpellDefinition]:
        return self._state.spells if self._state is not None else self._local_spells

    @property
    def _hooks(self) -> dict[str, list[Any]]:
        return self._state.hooks if self._state is not None else self._local_hooks

    @property
    def _commands(self) -> dict[str, Any]:
        return self._state.commands if self._state is not None else self._local_commands

    @property
    def _shortcuts(self) -> dict[str, Any]:
        return (
            self._state.shortcuts if self._state is not None else self._local_shortcuts
        )

    def _request(self, msg_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            self._transport.send_request(msg_type, payload, timeout=30.0),
        )

    # -- identity / scope ----------------------------------------------
    @property
    def scope(self) -> RuneDisposableScope:
        return self._scope

    def on_dispose(self, callback: Any) -> None:
        self._scope.cleanup_callbacks.append(callback)

    @property
    def install_id(self) -> str | None:
        return self._install_id

    @property
    def context(self) -> RuneContext:
        known = {
            f.name
            for f in dataclasses.fields(RuneContext)
            if f.name in self._context_dict
        }
        return RuneContext(**{k: self._context_dict[k] for k in known})

    def sanitize_no_proxy_env(self) -> bool:
        return sanitize_no_proxy_env()

    # -- spells ----------------------------------------------------------
    def register_spell(self, spell: SpellDefinition, override: bool = False) -> bool:
        self._spells[spell.name] = spell
        payload = _spell_metadata(spell)
        payload["override"] = bool(override)
        result = self._request("register_spell", payload)
        registered = bool(result.get("registered", False))
        if registered:
            self._scope.spell_names.add(spell.name)
        return registered

    def unregister_spell(self, name: str) -> bool:
        self._spells.pop(name, None)
        result = self._request("unregister_spell", {"name": name})
        self._scope.spell_names.discard(name)
        return bool(result.get("unregistered", False))

    def get_spell(self, name: str) -> dict[str, Any] | None:
        result = self._request("get_spell", {"name": name})
        return result.get("spell")

    def get_all_spells(self) -> list[dict[str, Any]]:
        return list(self._request("get_all_spells", {}).get("spells", []))

    def get_discoverable_spells(self) -> list[dict[str, Any]]:
        return list(self._request("get_discoverable_spells", {}).get("spells", []))

    # -- commands / shortcuts ---------------------------------------------
    def register_command(
        self,
        name: str,
        description: str = "",
        handler: Any = None,
        override: bool = False,
    ) -> bool:
        self._commands[name] = handler
        result = self._request(
            "register_command",
            {
                "name": name,
                "description": description,
                "override": bool(override),
            },
        )
        registered = bool(result.get("registered", False))
        if registered:
            self._scope.commands.add(name)
        return registered

    def register_shortcut(
        self,
        key: str,
        description: str = "",
        handler: Any = None,
        override: bool = False,
    ) -> bool:
        self._shortcuts[key] = handler
        result = self._request(
            "register_shortcut",
            {
                "key": key,
                "description": description,
                "override": bool(override),
            },
        )
        registered = bool(result.get("registered", False))
        if registered:
            self._scope.shortcuts.add(key)
        return registered

    def get_shortcuts(self) -> list[dict[str, Any]]:
        return list(self._request("get_shortcuts", {}).get("shortcuts", []))

    # -- hooks -------------------------------------------------------------
    def on(self, hook: Any, handler: Any) -> None:
        name = _hook_name(hook)
        self._hooks.setdefault(name, []).append(handler)
        self._request("register_hook", {"hook": name})
        try:
            hook_value = SigilHook(name)
        except ValueError:
            hook_value = name  # type: ignore[assignment]
        self._scope.sigil_handlers.append((hook_value, handler))

    # -- spell visibility ----------------------------------------------------
    def get_active_spells(self) -> list[str]:
        return list(self._request("get_active_spells", {}).get("spell_names", []))

    def set_active_spells(self, spell_names: list[str]) -> None:
        self._request("set_active_spells", {"spell_names": list(spell_names)})

    def get_global_spell_allowlist(self) -> list[str] | None:
        return self._request("get_global_spell_allowlist", {}).get("allowlist")

    def widen_global_allowlist(self, spell_names: list[str]) -> None:
        self._request("widen_global_allowlist", {"spell_names": list(spell_names)})

    def narrow_global_allowlist(self, spell_names: list[str]) -> list[str]:
        return list(
            self._request(
                "narrow_global_allowlist", {"spell_names": list(spell_names)}
            ).get("allowlist", [])
        )

    @property
    def has_spell_gateway(self) -> bool:
        return bool(
            self._request("has_spell_gateway", {}).get("has_spell_gateway", False)
        )

    def gateway_spell_names(self) -> list[str]:
        return list(self._request("gateway_spell_names", {}).get("spell_names", []))

    # -- skills ---------------------------------------------------------------
    def get_skills(self) -> list[dict[str, Any]]:
        return list(self._request("get_skills", {}).get("skills", []))

    def register_skill(self, manifest: SkillManifest) -> None:
        self._request("register_skill", _skill_payload(manifest))

    def register_skill_path(self, path: Any) -> None:
        self._request("register_skill_path", {"path": str(path)})

    def clear_skills_for_rune(self) -> None:
        self._request("clear_skills_for_rune", {})

    # -- session / messages -----------------------------------------------------
    def send_message(self, content: str) -> None:
        self._request("send_message", {"content": content})

    def set_session_name(self, name: str) -> None:
        self._request("set_session_name", {"name": name})

    def get_session_name(self) -> str | None:
        return self._request("get_session_name", {}).get("session_name")

    # -- audit ---------------------------------------------------------------------
    def audit(
        self,
        op: str,
        *,
        outcome: str,
        code: str,
        message: str,
        target: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if not op:
            raise ValueError("audit op name must be non-empty")
        self._transport.send_event(
            "audit",
            {
                "op": op,
                "outcome": outcome,
                "code": code,
                "message": message,
                "target": target,
                "extra": dict(extra) if extra else {},
            },
        )

    # -- unsupported in v1 (fail closed, shared table) ------------------------------
    def register_realm_factory(
        self, prefix: str, factory: Any, override: bool = False
    ) -> bool:
        raise _unsupported("register_realm_factory")

    def register_provider(
        self, name: str, config: dict[str, Any], override: bool = False
    ) -> bool:
        raise _unsupported("register_provider")

    def get_registered_providers(self) -> dict[str, Any]:
        raise _unsupported("get_registered_providers")

    def get_registered_realm_factories(self) -> dict[str, Any]:
        raise _unsupported("get_registered_realm_factories")

    async def request_approval(self, request: Any) -> Any:
        raise _unsupported("request_approval")

    def register_spell_gate(self, handler: Any) -> None:
        raise _unsupported("register_spell_gate")

    async def stream_model_text(self, model_id: str, prompt: str, **kwargs: Any) -> str:
        raise _unsupported("stream_model_text")

    def approval_presenter_source(self) -> Any:
        raise _unsupported("approval_presenter_source")

    @property
    def sandbox(self) -> Any:
        raise _unsupported("sandbox")

    def on_event(self, channel: str, handler: Any) -> None:
        raise _unsupported("on_event")

    def emit_event(self, channel: str, data: Any) -> None:
        raise _unsupported("emit_event")


class HostState:
    """Host-process state: local registries plus the wire handlers."""

    def __init__(self, transport: Any, rune_dir: Path) -> None:
        self.transport = transport
        self.rune_dir = Path(rune_dir)
        self.spells: dict[str, SpellDefinition] = {}
        self.hooks: dict[str, list[Any]] = {}
        self.commands: dict[str, Any] = {}
        self.shortcuts: dict[str, Any] = {}
        self.controllers: dict[str, AbortController] = {}
        self.api: HostRuneAPI | None = None
        self.stop_event = threading.Event()
        self.rune_name = "unknown"

    # -- wire dispatch ---------------------------------------------------------
    def handle_request(self, msg_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        if msg_type == "init":
            return self._init(payload)
        if msg_type == "spell_execute":
            return self._spell_execute(payload)
        if msg_type == "hook_invoke":
            return self._hook_invoke(payload)
        if msg_type == "command_invoke":
            return self._command_invoke(payload)
        if msg_type == "shortcut_invoke":
            return self._shortcut_invoke(payload)
        if msg_type == "ping":
            return {"pong": True}
        if msg_type == "shutdown":
            self.stop_event.set()
            return {"stopped": True}
        raise ValueError(f"Unknown host request type: {msg_type}")

    def handle_event(self, msg_type: str, payload: dict[str, Any]) -> None:
        if msg_type == "abort":
            controller = self.controllers.get(str(payload.get("cast_id", "")))
            if controller is not None:
                controller.abort()

    # -- init --------------------------------------------------------------------
    def _init(self, payload: dict[str, Any]) -> dict[str, Any]:
        from mvge.runes.loader import load_factory_from_manifest
        from mvge.runes.manifest import load_manifest

        manifest = load_manifest(self.rune_dir)
        if manifest is None:
            raise ValueError(f"No manifest found in rune directory {self.rune_dir}")
        self.rune_name = manifest.name
        diagnostics: list[Any] = []
        factory = load_factory_from_manifest(
            manifest, self.rune_dir, diagnostics=diagnostics
        )
        if factory is None:
            detail = "; ".join(str(d.message) for d in diagnostics)
            raise ValueError(
                f"Host refused rune '{manifest.name}': {detail}"
                if detail
                else f"Host refused rune '{manifest.name}'"
            )
        context = payload.get("context") or {}
        api = HostRuneAPI(
            self.transport,
            manifest.name,
            context=context,
            install_id=payload.get("install_id"),
            state=self,
        )
        self.api = api
        _run_awaitable(factory(cast(Any, api)))
        self.transport.send_event("ready", {"rune_name": manifest.name})
        return {"ready": True}

    # -- invocation ------------------------------------------------------------------
    def _spell_execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("spell_name", ""))
        spell = self.spells.get(name)
        if spell is None:
            raise ValueError(f"Host has no spell named '{name}'")
        cast_id = str(payload.get("cast_id", ""))
        params = payload.get("params") or {}
        controller = AbortController()
        self.controllers[cast_id] = controller
        try:
            ctx = self._spell_context(payload, cast_id, controller)
            result = asyncio.run(
                spell.execute(
                    cast_id,
                    params,
                    signal=controller.signal,
                    on_update=lambda update: self._send_update(cast_id, update),
                    ctx=ctx,
                )
            )
        except AbortError as exc:
            raise HostAbortError(str(exc)) from exc
        finally:
            self.controllers.pop(cast_id, None)
        if not isinstance(result, dict):
            raise ValueError(f"Hosted spell '{name}' returned a non-dict result")
        try:
            json.dumps(result)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Hosted spell '{name}' returned a result that is not "
                f"JSON-serializable: {exc}"
            ) from exc
        return {"result": result}

    def _send_update(self, cast_id: str, update: Any) -> None:
        self.transport.send_event(
            "spell_update",
            {"cast_id": cast_id, "update": _serializable_or_repr(update)},
        )

    def _spell_context(
        self, payload: dict[str, Any], cast_id: str, controller: AbortController
    ) -> SpellContext:
        workspace_root = payload.get("workspace_root")
        project_root = payload.get("project_root")
        return SpellContext(
            spell_cast_id=cast_id,
            signal=controller.signal,
            on_update=lambda update: self._send_update(cast_id, update),
            backend=None,
            workspace_root=Path(workspace_root) if workspace_root else None,
            project_root=Path(project_root) if project_root else None,
            tome_id=payload.get("tome_id"),
            agent_name=payload.get("agent_name"),
            spell_action=str(payload.get("spell_action", "")),
            spell_summary=str(payload.get("spell_summary", "")),
            state=dict(payload.get("state") or {}),
        )

    def _hook_invoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        handlers = self.hooks.get(str(payload.get("hook", "")), [])
        data = payload.get("data") or {}
        result: Any = None
        for handler in handlers:
            value = _run_awaitable(handler(data))
            if value is not None:
                result = value
        return {"result": _serializable_or_repr(result)}

    def _invoke_callable(self, handler: Any, args: str) -> Any:
        try:
            accepts = len(inspect.signature(handler).parameters) > 0
        except (ValueError, TypeError):
            accepts = True
        return _run_awaitable(handler(args) if accepts else handler())

    def _command_invoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        handler = self.commands.get(str(payload.get("name", "")))
        if handler is None:
            raise ValueError(f"Host has no command named '{payload.get('name', '')}'")
        result = self._invoke_callable(handler, str(payload.get("args", "")))
        return {"result": _serializable_or_repr(result)}

    def _shortcut_invoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        handler = self.shortcuts.get(str(payload.get("key", "")))
        if handler is None:
            raise ValueError(f"Host has no shortcut named '{payload.get('key', '')}'")
        result = self._invoke_callable(handler, str(payload.get("args", "")))
        return {"result": _serializable_or_repr(result)}


class HostAbortError(Exception):
    """Marker for an aborted hosted spell, mapped to an error kind."""

    kind = "aborted"


def _scrub_own_env() -> None:
    """Remove credential-shaped names from this host process's env."""
    for key in list(os.environ):
        if is_credential_env_name(key):
            del os.environ[key]


def main(argv: list[str] | None = None) -> int:
    """Host process entry point. Returns a process exit code."""
    parser = argparse.ArgumentParser(
        prog="mvge.runes.host_server",
        description="Out-of-process host for one rune (Phase 4 v1).",
    )
    parser.add_argument("--rune-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    _scrub_own_env()
    from mvge.runes.manifest import load_manifest

    manifest = load_manifest(args.rune_dir)
    rune_name = manifest.name if manifest is not None else "unknown"
    transport = JsonLineTransport(
        sys.stdin.buffer,
        sys.stdout.buffer,
    )
    state = HostState(transport, args.rune_dir)
    state.rune_name = rune_name
    transport.bind_handlers(state.handle_request, state.handle_event)
    transport.start()
    try:
        transport.send_event(
            "hello",
            {"protocol_version": PROTOCOL_VERSION, "rune_name": rune_name},
        )
    except Exception as exc:  # noqa: BLE001 - stderr only, then exit
        print(f"host: failed to send hello: {exc}", file=sys.stderr)  # noqa: T201
        return 1
    while not state.stop_event.is_set():
        if transport.fatal_error is not None:
            print(  # noqa: T201 - host stderr is the debug log
                f"host: wire failed: {transport.fatal_error}",
                file=sys.stderr,
            )
            return 1
        state.stop_event.wait(0.2)
    # Run dispose callbacks on clean shutdown, then exit.
    if state.api is not None:
        for callback in list(state.api.scope.cleanup_callbacks):
            try:
                _run_awaitable(callback())
            except Exception as exc:  # noqa: BLE001 - stderr only
                print(  # noqa: T201 - host stderr is the debug log
                    f"host: dispose callback failed: {exc}", file=sys.stderr
                )
    transport.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
