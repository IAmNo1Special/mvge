"""Engine side of the Phase 4 v1 out-of-process rune host.

``HostSession`` owns one host process for one rune: it spawns the
process with a scrubbed environment, performs the hello/init
handshake, applies the host's registrations to the engine's
``RuneRunner`` as proxy spells/hooks/commands/skills, and serves
spell executions, hook invocations, and command invocations over
the wire.

What crosses, and what does not:

- Cross: spell registration metadata, spell params and results
  (plain JSON), hook data (plain JSON), command args and results,
  skill metadata, session-name/message calls, abort as a message,
  streaming updates as messages.
- Never cross: provider keys and engine-held credentials (the
  spawn env is scrubbed and the host scrubs again), realm
  factories, approval channels, spell-gate decisions, live
  backends, live sandbox objects, live spell objects.

Fail-closed rules enforced here:

- A spell cast that carries a live ``SpellContext.backend`` is
  refused with ``HostInProcessOnlyError`` before anything is
  sent: that spell is in-process-only in v1.
- A spell cast whose context ``state`` (or whose params) is not
  JSON-serializable is refused the same way: a non-serializable
  context object cannot cross.
- A host that crashes mid-cast fails the cast with
  ``HostCrashError`` and the engine session survives.
- A host that does not answer within the call timeout is killed
  and the cast fails with ``HostTimeoutError``.
- A host that answers with an abort fails with
  ``HostAbortedError``, never with a success result.
- A hosted registration is refused engine-side when the
  Phase 2B capability gate denies it: ``HostSession``
  checks ``runner.capability_gate(self.manifest)`` for
  every gated wire registration, so a host speaking the
  wire directly gains nothing over ``HostRuneAPI``. A
  denied registration returns ``{"registered": False}``
  and changes no engine state; a legacy manifest with no
  ``capabilities`` field is ungated, exactly as in-process.

Lifecycle is cross-platform by construction: the host is started
with ``sys.executable -m mvge.runes.host_server`` (no shell, no
POSIX-only call), stopped with ``terminate()`` then ``kill()``
(the ``subprocess`` methods that exist on both POSIX and
Windows), and its stderr is captured to a log file so a
developer can debug a hosted rune after the fact. Windows
coverage gap, stated plainly: this suite runs on the CI
platforms, but no Windows-only leg (job objects, console
control) is exercised here, and Windows process teardown timing
may differ from POSIX; see the docs.

Hosts are not stopped automatically when a runner is discarded:
call ``HostSession.stop()`` (or use the session as a context
manager) or ``RuneRunner.shutdown_hosted_runes()``. That is a
known v1 gap, not an oversight to paper over: the runner has no
general teardown hook that hosted sessions can safely ride.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import dataclasses
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import mvge
from mvge.core.spells import ExecutionMode, MvgeSpell
from mvge.runes.capabilities import METHOD_TO_CAPABILITY
from mvge.runes.host_classification import classify_manifest
from mvge.runes.host_protocol import (
    DEFAULT_CALL_TIMEOUT_SECONDS,
    DEFAULT_START_TIMEOUT_SECONDS,
    HostAbortedError,
    HostCrashError,
    HostError,
    HostInProcessOnlyError,
    HostRemoteError,
    HostTimeoutError,
    HostUnsupportedError,
    VersionMismatchError,
    scrub_env,
)
from mvge.runes.host_transport import JsonLineTransport
from mvge.runes.rune_audit import RuneAuditLog, utcnow
from mvge.runes.types import (
    RegisteredCommand,
    RuneManifest,
    RuneShortcut,
    SigilHook,
    SkillManifest,
    SpellDefinition,
)
from mvge.runes.wrapping import (
    WrappingBackend,
    WrappingPolicy,
    assert_no_credential_env,
    wrap_host_command,
)

if TYPE_CHECKING:
    from mvge.runes.rune_runner import RuneRunner


def _spell_metadata(spell: SpellDefinition | MvgeSpell) -> dict[str, Any]:
    return {
        "name": spell.name,
        "description": spell.description,
        "parameters": spell.parameters or {},
        "read_only": bool(spell.read_only),
        "write_roots": list(getattr(spell, "write_roots", ()) or ()),
        "source_rune": getattr(spell, "source_rune", None),
    }


def _hook_data_to_json(data: Any) -> Any:
    """Convert a hook payload to plain JSON data, or refuse.

    Typed dataclass payloads become dicts via ``asdict``; plain
    dicts pass through; anything else that is not JSON-serializable
    is in-process-only in v1 (fail-closed, with a stated reason).
    """
    if dataclasses.is_dataclass(data) and not isinstance(data, type):
        value = dataclasses.asdict(data)
    elif isinstance(data, dict):
        value = data
    else:
        value = data
    try:
        json.dumps(value)
    except (TypeError, ValueError) as exc:
        raise HostInProcessOnlyError(
            "Hook payload is not JSON-serializable, so it cannot cross "
            f"to a hosted rune in v1: {exc}"
        ) from exc
    return value


def _json_or_refuse(value: Any, what: str) -> None:
    try:
        json.dumps(value)
    except (TypeError, ValueError) as exc:
        raise HostInProcessOnlyError(
            f"{what} is not JSON-serializable, so it cannot cross to a "
            f"hosted rune in v1: {exc}"
        ) from exc


# Wire request type -> RuneAPI method name, for the Phase 2B
# capability gate. The wire's ``register_hook`` is RuneAPI's
# ``on``; every other gated wire type shares its RuneAPI name.
# The capability itself comes from the single Phase 2B table
# ``METHOD_TO_CAPABILITY`` — never from a second mapping here.
_HOST_REQUEST_TO_API_METHOD: dict[str, str] = {
    "register_spell": "register_spell",
    "register_hook": "on",
    "register_command": "register_command",
    "register_shortcut": "register_shortcut",
    "register_skill": "register_skill",
    "register_skill_path": "register_skill_path",
}


class HostSession:
    """One host process for one rune, owned by the engine."""

    def __init__(
        self,
        manifest: RuneManifest,
        rune_dir: Path,
        runner: RuneRunner,
        *,
        command: list[str] | None = None,
        timeout_seconds: float = DEFAULT_CALL_TIMEOUT_SECONDS,
        start_timeout_seconds: float = DEFAULT_START_TIMEOUT_SECONDS,
        stderr_log_path: Path | None = None,
        audit_log: RuneAuditLog | None = None,
        wrapping_policy: WrappingPolicy | None = None,
        wrapping_backends: list[WrappingBackend] | None = None,
    ) -> None:
        self.manifest = manifest
        self.rune_name = manifest.name
        self.rune_dir = Path(rune_dir)
        self.runner = runner
        self.timeout_seconds = timeout_seconds
        self.start_timeout_seconds = start_timeout_seconds
        self._audit_log = audit_log
        self._command_override = list(command) if command is not None else None
        # Phase 5b: wrapping is opt-in and defaults to absent.
        # A present-but-disabled policy is the same as absent:
        # the host runs unwrapped, exactly as Phase 4 ships.
        # wrapping_backends is a backend-injection seam (a
        # deployment may supply its own wrapping backend, and
        # the suite injects one instead of depending on an
        # installed tool); None means the default backends.
        self._wrapping_policy = wrapping_policy
        self._wrapping_backends = wrapping_backends
        if stderr_log_path is not None:
            self.stderr_log_path = Path(stderr_log_path)
        else:
            self.stderr_log_path = (
                Path(tempfile.gettempdir()) / f"mvge-host-{self.rune_name}.log"
            )
        self._process: subprocess.Popen[bytes] | None = None
        self._transport: JsonLineTransport | None = None
        self._stderr_handle: Any = None
        self._hello_event = threading.Event()
        self._hello_payload: dict[str, Any] | None = None
        self._ready_event = threading.Event()
        self._update_queues: dict[str, queue.Queue[dict[str, Any]]] = {}
        self._update_lock = threading.Lock()
        self._stopped = False

    def _effective_wrapping_policy(self) -> WrappingPolicy | None:
        """The wrapping policy with the session's rune dir filled in."""
        policy = self._wrapping_policy
        if policy is None or not policy.enabled:
            return None
        if policy.rune_dir is not None:
            return policy
        return dataclasses.replace(policy, rune_dir=self.rune_dir)

    @property
    def command(self) -> list[str]:
        """The host command: cross-platform, no shell.

        Phase 5b: when a wrapping policy is enabled, the host
        command is the wrapped command. Wrapping validation
        raises before any process starts when the backend is
        unavailable, so a wrapped command is never silently
        replaced by the unwrapped one.
        """
        if self._command_override is not None:
            base = list(self._command_override)
        else:
            base = [
                sys.executable,
                "-m",
                "mvge.runes.host_server",
                "--rune-dir",
                str(self.rune_dir),
            ]
        return wrap_host_command(
            base,
            self._effective_wrapping_policy(),
            backends=self._wrapping_backends,
        )

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    def is_alive(self) -> bool:
        if self._process is None:
            return False
        if self._transport is not None and self._transport.fatal_error is not None:
            return False
        return self._process.poll() is None

    def read_stderr(self) -> str:
        """Host stderr captured for debugging (never contains credentials)."""
        try:
            return self.stderr_log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def __enter__(self) -> HostSession:
        self.start()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.stop()

    # -- lifecycle ------------------------------------------------------------
    def start(self) -> None:
        """Spawn the host and complete the hello/init handshake."""
        if self.is_alive():
            return
        self._stopped = False
        self._hello_event.clear()
        self._ready_event.clear()
        self._hello_payload = None
        env = scrub_env(dict(os.environ))
        env["PYTHONUNBUFFERED"] = "1"
        wrapping = self._effective_wrapping_policy()
        run_cwd: Path | None = None
        if wrapping is not None:
            # Phase 5b, fail-closed twice over: the scrub must
            # have removed every credential-shaped name, and
            # the wrapped process runs with the workspace as
            # its working directory (the inherited cwd is not
            # bound into the namespace). PYTHONPATH entries
            # are bound read-only so an editable engine install
            # stays importable inside the wrap.
            assert_no_credential_env(env)
            # The wrapped child must find the engine even when
            # its interpreter is resolved away from a venv: put
            # the engine source tree on PYTHONPATH for the child
            # (it is bound read-only below, like any PYTHONPATH
            # entry), in addition to the venv discovery above.
            engine_src = str(Path(mvge.__file__).resolve().parent.parent)
            existing_pythonpath = env.get("PYTHONPATH", "")
            entries = (
                [engine_src] + [e for e in existing_pythonpath.split(os.pathsep) if e]
                if existing_pythonpath
                else [engine_src]
            )
            env["PYTHONPATH"] = os.pathsep.join(entries)
            pythonpath_mounts = tuple(
                Path(entry).resolve()
                for entry in env.get("PYTHONPATH", "").split(os.pathsep)
                if entry and Path(entry).is_dir()
            )
            if pythonpath_mounts:
                wrapping = dataclasses.replace(
                    wrapping,
                    extra_read_only_mounts=(
                        *wrapping.extra_read_only_mounts,
                        *pythonpath_mounts,
                    ),
                )
                self._wrapping_policy = wrapping
            run_cwd = wrapping.workspace_root
        self.stderr_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._stderr_handle = self.stderr_log_path.open("wb")
        try:
            self._process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._stderr_handle,
                env=env,
                cwd=str(run_cwd) if run_cwd is not None else None,
            )
        except OSError as exc:
            self._stderr_handle.close()
            self._stderr_handle = None
            raise HostError(
                f"Failed to start host for '{self.rune_name}': {exc}"
            ) from exc
        assert self._process.stdin is not None
        assert self._process.stdout is not None
        transport = JsonLineTransport(
            self._process.stdout,
            self._process.stdin,
            on_request=self._handle_host_request,
            on_event=self._handle_host_event,
        )
        self._transport = transport
        transport.start()
        try:
            self._wait_for_hello()
            init_payload = self._init_payload()
            response = transport.request(
                "init", init_payload, timeout=self.start_timeout_seconds
            )
            if not response.get("ready"):
                raise HostError(f"Host for '{self.rune_name}' did not become ready")
            self._wait_for_ready()
        except HostRemoteError as exc:
            # Before HostError: HostRemoteError subclasses it, and
            # the remote kind must map to the engine-side error.
            self._kill_host()
            raise self._map_remote_error(exc) from exc
        except HostError:
            self._kill_host()
            raise
        except TimeoutError as exc:
            self._kill_host()
            raise HostTimeoutError(
                f"Host for '{self.rune_name}' did not start within "
                f"{self.start_timeout_seconds}s"
            ) from exc

    def _wait_for_hello(self) -> None:
        deadline = time.monotonic() + self.start_timeout_seconds
        while not self._hello_event.wait(timeout=0.1):
            transport = self._transport
            if transport is not None and transport.fatal_error is not None:
                raise transport.fatal_error
            if not self.is_alive():
                raise HostCrashError(f"Host for '{self.rune_name}' died before hello")
            if time.monotonic() > deadline:
                raise HostTimeoutError(
                    f"Host for '{self.rune_name}' sent no hello within "
                    f"{self.start_timeout_seconds}s"
                )
        payload = self._hello_payload or {}
        version = payload.get("protocol_version")
        if version != 1:
            raise VersionMismatchError(
                f"Host for '{self.rune_name}' speaks protocol version "
                f"{version!r}, this engine speaks version 1"
            )

    def _wait_for_ready(self) -> None:
        deadline = time.monotonic() + self.start_timeout_seconds
        while not self._ready_event.wait(timeout=0.1):
            transport = self._transport
            if transport is not None and transport.fatal_error is not None:
                raise transport.fatal_error
            if not self.is_alive():
                raise HostCrashError(f"Host for '{self.rune_name}' died before ready")
            if time.monotonic() > deadline:
                raise HostTimeoutError(
                    f"Host for '{self.rune_name}' did not become ready "
                    f"within {self.start_timeout_seconds}s"
                )

    def _init_payload(self) -> dict[str, Any]:
        context = dataclasses.asdict(self.runner.context)
        install_id = None
        try:
            install_id = self.runner._install_id_for_rune(self.rune_name)  # noqa: SLF001 - engine-owned runner
        except Exception:
            install_id = None
        return {
            "context": context,
            "install_id": install_id,
            "rune_name": self.rune_name,
        }

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the host cleanly, then terminate, then kill."""
        if self._process is None:
            return
        process = self._process
        transport = self._transport
        self._stopped = True
        if transport is not None and process.poll() is None:
            with contextlib.suppress(Exception):
                transport.request("shutdown", {}, timeout=2.0)
        if process.poll() is None:
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5.0)
        if transport is not None:
            transport.close()
        if self._stderr_handle is not None:
            with contextlib.suppress(OSError):
                self._stderr_handle.close()
            self._stderr_handle = None
        self._process = None
        self._transport = None

    def _kill_host(self) -> None:
        process = self._process
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5.0)
        except OSError:
            pass
        transport = self._transport
        if transport is not None:
            transport.close()
        if self._stderr_handle is not None:
            with contextlib.suppress(OSError):
                self._stderr_handle.close()
            self._stderr_handle = None
        self._process = None
        self._transport = None

    def restart(self) -> None:
        """Restart the host process and re-apply its registrations."""
        self.stop()
        self.runner.clear_rune(self.rune_name)
        self.start()

    # -- host -> engine messages --------------------------------------------------
    def _handle_host_event(self, msg_type: str, payload: dict[str, Any]) -> None:
        if msg_type == "hello":
            self._hello_payload = payload
            self._hello_event.set()
        elif msg_type == "ready":
            self._ready_event.set()
        elif msg_type == "spell_update":
            cast_id = str(payload.get("cast_id", ""))
            with self._update_lock:
                target = self._update_queues.get(cast_id)
            if target is not None:
                target.put(payload.get("update") or {})
        elif msg_type == "audit":
            self._append_audit(payload)

    def _append_audit(self, payload: dict[str, Any]) -> None:
        if self._audit_log is None:
            from mvge.runes.rune_audit import default_rune_ops_dir

            self._audit_log = RuneAuditLog(default_rune_ops_dir())
        record: dict[str, Any] = dict(payload.get("extra") or {})
        record.update(
            {
                "timestamp": utcnow(),
                "rune": self.rune_name,
                "op": payload.get("op", ""),
                "outcome": payload.get("outcome", ""),
                "code": payload.get("code", ""),
                "target": payload.get("target"),
                "message": payload.get("message", ""),
            }
        )
        # Audit failure must not kill the wire; the engine log
        # carries the failure for the operator.
        with contextlib.suppress(Exception):
            self._audit_log.append_event(record)

    def _wire_registration_allowed(self, msg_type: str) -> bool:
        """Engine-side Phase 2B gate for one wire registration.

        Mirrors in-process ``RuneAPI`` exactly: the gate is
        built from this session's manifest (so a manifest with
        no ``capabilities`` field is ungated, and a manifest
        that declares the field is granted only its consented
        capabilities), and ungated wire types are not in the
        mapping, so they pass through as in-process. This check
        lives here, on the engine side, because a host that
        speaks the wire directly bypasses ``HostRuneAPI``.
        """
        api_method = _HOST_REQUEST_TO_API_METHOD.get(msg_type)
        if api_method is None:
            return True
        capability = METHOD_TO_CAPABILITY[api_method]
        gate = self.runner.capability_gate(self.manifest)
        return gate.allows(self.rune_name, capability)

    def _handle_host_request(
        self, msg_type: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        runner = self.runner
        name = self.rune_name
        if msg_type in _HOST_REQUEST_TO_API_METHOD and not (
            self._wire_registration_allowed(msg_type)
        ):
            return {"registered": False}
        if msg_type == "register_spell":
            spell = self._proxy_spell(payload)
            registered = runner.register_spell(
                spell, rune_name=name, override=bool(payload.get("override", False))
            )
            if registered:
                runner.get_or_create_scope(name).spell_names.add(spell.name)
            return {"registered": registered}
        if msg_type == "unregister_spell":
            spell_name = str(payload.get("name", ""))
            ok = runner.unregister_spell(spell_name, rune_name=name)
            return {"unregistered": ok}
        if msg_type == "register_command":
            command = RegisteredCommand(
                name=str(payload.get("name", "")),
                description=str(payload.get("description", "")),
                handler=self._proxy_command(str(payload.get("name", ""))),
            )
            registered = runner.register_command(
                command, override=bool(payload.get("override", False))
            )
            if registered:
                runner.get_or_create_scope(name).commands.add(command.name)
            return {"registered": registered}
        if msg_type == "register_shortcut":
            shortcut = RuneShortcut(
                key=str(payload.get("key", "")),
                description=str(payload.get("description", "")),
                handler=self._proxy_shortcut(str(payload.get("key", ""))),
            )
            registered = runner.register_shortcut(
                shortcut, override=bool(payload.get("override", False))
            )
            if registered:
                runner.get_or_create_scope(name).shortcuts.add(shortcut.key)
            return {"registered": registered}
        if msg_type == "register_hook":
            hook = SigilHook(str(payload.get("hook", "")))
            handler = self._proxy_hook(hook)
            runner.register_handler(hook, handler, rune_name=name)
            runner.get_or_create_scope(name).sigil_handlers.append((hook, handler))
            return {"registered": True}
        if msg_type == "register_skill":
            manifest = SkillManifest(
                name=str(payload.get("name", "")),
                description=str(payload.get("description", "")),
                path=str(payload.get("path", "")),
                location=str(payload.get("location", "")),
                version=str(payload.get("version", "")),
                license=str(payload.get("license", "")),
                compatibility=str(payload.get("compatibility", "")),
                metadata=dict(payload.get("metadata") or {}),
                allowed_tools=str(payload.get("allowed_tools", "")),
                disable_model_invocation=bool(
                    payload.get("disable_model_invocation", False)
                ),
                body=payload.get("body"),
            )
            runner.skill_catalog.register_skill(manifest, source_rune=name)
            return {"registered": True}
        if msg_type == "register_skill_path":
            runner.skill_catalog.register_skill_path(str(payload.get("path", "")))
            return {"registered": True}
        if msg_type == "clear_skills_for_rune":
            runner.skill_catalog.clear_rune(name)
            return {"ok": True}
        if msg_type == "get_spell":
            found = runner.get_spell(str(payload.get("name", "")))
            return {"spell": _spell_metadata(found) if found is not None else None}
        if msg_type == "get_all_spells":
            return {
                "spells": [
                    _spell_metadata(s) for s in runner.get_all_registered_spells()
                ]
            }
        if msg_type == "get_discoverable_spells":
            spells = []
            for spell in runner.get_discoverable_spells():
                if hasattr(spell, "name"):
                    spells.append(_spell_metadata(spell))
            return {"spells": spells}
        if msg_type == "get_shortcuts":
            return {
                "shortcuts": [
                    {"key": s.key, "description": s.description}
                    for s in runner.get_shortcuts()
                ]
            }
        if msg_type == "get_skills":
            return {
                "skills": [
                    {
                        "name": s.name,
                        "description": s.description,
                        "path": s.path,
                    }
                    for s in runner.skill_catalog.get_skills()
                ]
            }
        if msg_type == "get_session_name":
            return {"session_name": runner.session_name}
        if msg_type == "set_session_name":
            runner.set_session_name(str(payload.get("name", "")))
            return {"ok": True}
        if msg_type == "send_message":
            runner.send_message(str(payload.get("content", "")))
            return {"ok": True}
        if msg_type == "get_active_spells":
            return {"spell_names": runner.get_active_spells()}
        if msg_type == "set_active_spells":
            runner.set_active_spells(list(payload.get("spell_names", [])), name)
            return {"ok": True}
        if msg_type == "get_global_spell_allowlist":
            return {"allowlist": runner.get_global_spell_allowlist()}
        if msg_type == "widen_global_allowlist":
            runner.widen_global_allowlist(list(payload.get("spell_names", [])))
            return {"ok": True}
        if msg_type == "narrow_global_allowlist":
            return {
                "allowlist": runner.narrow_global_allowlist(
                    list(payload.get("spell_names", []))
                )
            }
        if msg_type == "has_spell_gateway":
            return {"has_spell_gateway": runner.has_spell_gateway}
        if msg_type == "gateway_spell_names":
            return {"spell_names": runner.gateway_spell_names()}
        if msg_type == "get_context":
            return {"context": dataclasses.asdict(runner.context)}
        raise ValueError(f"Unknown host request type: {msg_type}")

    # -- proxies registered on the runner ---------------------------------------------
    def _proxy_spell(self, payload: dict[str, Any]) -> SpellDefinition:
        session = self

        async def _hosted_handler(
            spell_cast_id: str,
            params: dict[str, Any],
            signal: Any = None,
            on_update: Any = None,
            ctx: Any = None,
        ) -> dict[str, Any]:
            return await asyncio.to_thread(
                session.execute_spell,
                str(payload.get("name", "")),
                spell_cast_id,
                params,
                signal,
                on_update,
                ctx,
            )

        mode_raw = str(payload.get("execution_mode", "parallel"))
        try:
            mode = ExecutionMode(mode_raw)
        except ValueError:
            mode = ExecutionMode.PARALLEL
        return SpellDefinition(
            name=str(payload.get("name", "")),
            description=str(payload.get("description", "")),
            parameters=dict(payload.get("parameters") or {}),
            execution_mode=mode,
            prompt_snippet=payload.get("prompt_snippet"),
            prompt_guidelines=list(payload.get("prompt_guidelines") or []),
            source_rune=self.rune_name,
            handler=_hosted_handler,
            read_only=bool(payload.get("read_only", False)),
            timeout_ms=payload.get("timeout_ms"),
            write_roots=list(payload.get("write_roots") or []),
        )

    def _proxy_hook(self, hook: SigilHook) -> Any:
        session = self

        async def _hosted_hook(data: Any) -> Any:
            return await asyncio.to_thread(session.invoke_hook, hook, data)

        return _hosted_hook

    def _proxy_command(self, command_name: str) -> Any:
        session = self

        async def _hosted_command(args: str) -> Any:
            return await asyncio.to_thread(session.invoke_command, command_name, args)

        return _hosted_command

    def _proxy_shortcut(self, shortcut_key: str) -> Any:
        session = self

        async def _hosted_shortcut(args: str) -> Any:
            return await asyncio.to_thread(session.invoke_shortcut, shortcut_key, args)

        return _hosted_shortcut

    # -- engine -> host calls ---------------------------------------------------
    @staticmethod
    def _map_remote_error(exc: HostRemoteError) -> HostError:
        if exc.kind in ("HostUnsupportedError", "unsupported"):
            return HostUnsupportedError(exc.remote_message)
        if exc.kind in ("HostAbortedError", "aborted"):
            return HostAbortedError(exc.remote_message)
        if exc.kind in ("HostInProcessOnlyError",):
            return HostInProcessOnlyError(exc.remote_message)
        return HostError(exc.remote_message)

    def execute_spell(
        self,
        spell_name: str,
        spell_cast_id: str,
        params: dict[str, Any],
        signal: Any = None,
        on_update: Any = None,
        ctx: Any = None,
    ) -> dict[str, Any]:
        """Execute a hosted spell synchronously (called off the event loop)."""
        if ctx is not None and getattr(ctx, "backend", None) is not None:
            raise HostInProcessOnlyError(
                f"Spell '{spell_name}' requires a live backend, so it is "
                "in-process-only in v1"
            )
        _json_or_refuse(params, "Spell params")
        payload: dict[str, Any] = {
            "spell_name": spell_name,
            "cast_id": spell_cast_id,
            "params": params,
        }
        if ctx is not None:
            _json_or_refuse(getattr(ctx, "state", {}), "Spell context state")
            payload.update(
                {
                    "workspace_root": (
                        str(ctx.workspace_root)
                        if getattr(ctx, "workspace_root", None) is not None
                        else None
                    ),
                    "project_root": (
                        str(ctx.project_root)
                        if getattr(ctx, "project_root", None) is not None
                        else None
                    ),
                    "tome_id": getattr(ctx, "tome_id", None),
                    "agent_name": getattr(ctx, "agent_name", None),
                    "spell_action": getattr(ctx, "spell_action", ""),
                    "spell_summary": getattr(ctx, "spell_summary", ""),
                    "state": dict(getattr(ctx, "state", {}) or {}),
                }
            )
        transport = self._transport
        if transport is None or not self.is_alive():
            raise HostCrashError(f"Host for '{self.rune_name}' is not running")
        with self._update_lock:
            update_queue: queue.Queue[dict[str, Any]] = queue.Queue()
            self._update_queues[spell_cast_id] = update_queue
        try:
            _, future = transport.begin_request("spell_execute", payload)
            abort_sent = False
            deadline = time.monotonic() + self.timeout_seconds
            while True:
                try:
                    result = future.result(timeout=0.05)
                except concurrent.futures.TimeoutError:
                    result = None
                while not update_queue.empty():
                    update = update_queue.get_nowait()
                    if on_update is not None:
                        on_update(update)
                if result is not None:
                    if not isinstance(result.get("result"), dict):
                        raise HostError(
                            f"Hosted spell '{spell_name}' returned a non-dict result"
                        )
                    return dict(result["result"])
                if (
                    signal is not None
                    and getattr(signal, "aborted", False)
                    and not abort_sent
                ):
                    abort_sent = True
                    with contextlib.suppress(Exception):
                        transport.send_event("abort", {"cast_id": spell_cast_id})
                if future.done():
                    # Done without a result means an exception was set.
                    raise future.exception()  # type: ignore[misc]
                if time.monotonic() > deadline:
                    self._kill_host()
                    raise HostTimeoutError(
                        f"Hosted spell '{spell_name}' timed out after "
                        f"{self.timeout_seconds}s; the host was killed"
                    )
                if transport.fatal_error is not None:
                    raise transport.fatal_error
                if not self.is_alive():
                    raise HostCrashError(
                        f"Host for '{self.rune_name}' died during spell '{spell_name}'"
                    )
        except HostRemoteError as exc:
            raise self._map_remote_error(exc) from exc
        except (HostCrashError, HostTimeoutError, HostInProcessOnlyError):
            raise
        except concurrent.futures.CancelledError as exc:
            raise HostCrashError(
                f"Host for '{self.rune_name}' stopped during spell '{spell_name}'"
            ) from exc
        finally:
            with self._update_lock:
                self._update_queues.pop(spell_cast_id, None)

    def invoke_hook(self, hook: SigilHook, data: Any) -> Any:
        """Invoke hosted hook handlers synchronously, returning the result."""
        transport = self._transport
        if transport is None or not self.is_alive():
            raise HostCrashError(f"Host for '{self.rune_name}' is not running")
        payload = {
            "hook": hook.value if isinstance(hook, SigilHook) else str(hook),
            "data": _hook_data_to_json(data),
        }
        try:
            result = transport.request(
                "hook_invoke", payload, timeout=self.timeout_seconds
            )
        except HostRemoteError as exc:
            raise self._map_remote_error(exc) from exc
        except TimeoutError as exc:
            self._kill_host()
            raise HostTimeoutError(
                f"Hosted hook '{payload['hook']}' timed out after "
                f"{self.timeout_seconds}s; the host was killed"
            ) from exc
        return result.get("result")

    def invoke_command(self, command_name: str, args: str) -> Any:
        """Invoke a hosted command synchronously, returning its result."""
        transport = self._transport
        if transport is None or not self.is_alive():
            raise HostCrashError(f"Host for '{self.rune_name}' is not running")
        try:
            result = transport.request(
                "command_invoke",
                {"name": command_name, "args": args},
                timeout=self.timeout_seconds,
            )
        except HostRemoteError as exc:
            raise self._map_remote_error(exc) from exc
        except TimeoutError as exc:
            self._kill_host()
            raise HostTimeoutError(
                f"Hosted command '{command_name}' timed out after "
                f"{self.timeout_seconds}s; the host was killed"
            ) from exc
        return result.get("result")

    def invoke_shortcut(self, shortcut_key: str, args: str) -> Any:
        """Invoke a hosted shortcut synchronously, returning its result."""
        transport = self._transport
        if transport is None or not self.is_alive():
            raise HostCrashError(f"Host for '{self.rune_name}' is not running")
        try:
            result = transport.request(
                "shortcut_invoke",
                {"key": shortcut_key, "args": args},
                timeout=self.timeout_seconds,
            )
        except HostRemoteError as exc:
            raise self._map_remote_error(exc) from exc
        except TimeoutError as exc:
            self._kill_host()
            raise HostTimeoutError(
                f"Hosted shortcut '{shortcut_key}' timed out after "
                f"{self.timeout_seconds}s; the host was killed"
            ) from exc
        return result.get("result")


def host_rune_session(
    runner: RuneRunner,
    manifest: RuneManifest,
    rune_dir: Path,
    **kwargs: Any,
) -> HostSession:
    """Classify, then start, a hosted session for one rune.

    Raises ``HostInProcessOnlyError`` for the in-process-only tier
    before any process is started. The session is recorded on the
    runner so ``shutdown_hosted_runes`` can stop it.
    """
    verdict = classify_manifest(manifest)
    if not verdict.hostable:
        raise HostInProcessOnlyError(verdict.reason)
    existing = runner.hosted_sessions.get(manifest.name)
    if existing is not None and existing.is_alive():
        return existing
    session = HostSession(manifest, rune_dir, runner, **kwargs)
    session.start()
    runner.hosted_sessions[manifest.name] = session
    return session
