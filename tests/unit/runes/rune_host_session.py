"""Unit tests for HostSession internals, without spawning a host.

The subprocess integration tests in rune_host.py cover the real
wire; these tests cover the engine-side branches those runs do
not reach: every host->engine request type, the spell-execution
loop's error branches, stop/kill paths, and the hello/ready
wait branches. Transports and processes here are fakes, so the
tests are fast and deterministic.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import dataclasses
import json
import subprocess
import threading
from pathlib import Path

import pytest

from mvge.core.abort import AbortController
from mvge.runes.host import HostSession, host_rune_session
from mvge.runes.host_protocol import (
    HostAbortedError,
    HostCrashError,
    HostError,
    HostInProcessOnlyError,
    HostProtocolError,
    HostTimeoutError,
    HostUnsupportedError,
    VersionMismatchError,
)
from mvge.runes.host_transport import HostRemoteError
from mvge.runes.rune_audit import AUDIT_FILENAME, RuneAuditLog
from mvge.runes.rune_runner import RuneRunner
from mvge.runes.types import RuneManifest, SigilHook


class _FakeProcess:
    def __init__(self, wait_results: list | None = None) -> None:
        self.pid = 4242
        self._poll: int | None = None
        self._wait_results = list(wait_results or [])
        self.terminated = False
        self.killed = False
        self.stdin = None
        self.stdout = None

    def poll(self) -> int | None:
        return self._poll

    def wait(self, timeout: float | None = None) -> int:
        if self._wait_results:
            outcome = self._wait_results.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return 0

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


class _FakeTransport:
    """Canned transport for a HostSession under test."""

    def __init__(self) -> None:
        self.fatal_error: HostError | None = None
        self.sent_events: list[tuple[str, dict]] = []
        self.requests: list[tuple[str, dict]] = []
        self.request_result: dict | None = None
        self.request_error: Exception | None = None
        self.begin_future: concurrent.futures.Future | None = None
        self.closed = False

    def send_event(self, msg_type: str, payload: dict) -> None:
        self.sent_events.append((msg_type, payload))

    def request(self, msg_type: str, payload: dict, timeout: float = 30.0) -> dict:
        self.requests.append((msg_type, payload))
        if self.request_error is not None:
            raise self.request_error
        return dict(self.request_result or {})

    def begin_request(
        self, msg_type: str, payload: dict
    ) -> tuple[int, concurrent.futures.Future]:
        future = self.begin_future
        if future is None:
            future = concurrent.futures.Future()
            future.set_result({"result": {}})
            self.begin_future = future
        return 1, future

    def close(self) -> None:
        self.closed = True


def _session(tmp_path: Path, name: str = "unit-rune") -> tuple[RuneRunner, HostSession]:
    runner = RuneRunner()
    manifest = RuneManifest(
        name=name,
        version="0.1.0",
        description="unit",
        entry_point="rune.py",
        path=str(tmp_path),
    )
    session = HostSession(
        manifest,
        tmp_path,
        runner,
        stderr_log_path=tmp_path / "host.log",
    )
    session._transport = _FakeTransport()  # type: ignore[assignment]
    session._process = _FakeProcess()  # type: ignore[assignment]
    return runner, session


def _transport(session: HostSession) -> _FakeTransport:
    transport = session._transport
    assert isinstance(transport, _FakeTransport)
    return transport


class TestHandleHostRequests:
    def test_every_request_type(self, tmp_path: Path) -> None:
        runner, session = _session(tmp_path)
        runner.set_global_spell_allowlist(["a-spell", "b-spell"])
        assert session._handle_host_request(
            "register_spell",
            {
                "name": "s1",
                "description": "d",
                "parameters": {},
                "read_only": False,
                "execution_mode": "parallel",
            },
        ) == {"registered": True}
        assert session._handle_host_request(
            "register_spell",
            {
                "name": "s2",
                "description": "d",
                "parameters": {},
                "read_only": False,
                "execution_mode": "bogus-mode",
            },
        ) == {"registered": True}
        assert runner.get_spell("s1") is not None
        assert runner.get_spell("s2") is not None
        assert (
            session._handle_host_request("get_spell", {"name": "s1"})["spell"]["name"]
            == "s1"
        )
        assert (
            session._handle_host_request("get_spell", {"name": "missing"})["spell"]
            is None
        )
        assert len(session._handle_host_request("get_all_spells", {})["spells"]) == 2
        assert (
            len(session._handle_host_request("get_discoverable_spells", {})["spells"])
            == 2
        )
        assert session._handle_host_request(
            "register_command", {"name": "c1", "description": "d"}
        ) == {"registered": True}
        assert session._handle_host_request(
            "register_shortcut", {"key": "k1", "description": "d"}
        ) == {"registered": True}
        assert session._handle_host_request(
            "register_hook", {"hook": "turn_start"}
        ) == {"registered": True}
        assert session._handle_host_request(
            "register_skill",
            {
                "name": "sk1",
                "description": "d",
                "path": "/tmp/sk1",
            },
        ) == {"registered": True}
        assert session._handle_host_request(
            "register_skill_path", {"path": "/tmp/skills"}
        ) == {"registered": True}
        assert session._handle_host_request("clear_skills_for_rune", {}) == {"ok": True}
        assert session._handle_host_request("get_shortcuts", {})["shortcuts"] == [
            {"key": "k1", "description": "d"}
        ]
        assert session._handle_host_request("get_skills", {})["skills"] == []
        assert session._handle_host_request("get_session_name", {}) == {
            "session_name": None
        }
        assert session._handle_host_request("set_session_name", {"name": "T"}) == {
            "ok": True
        }
        assert runner.session_name == "T"
        assert session._handle_host_request("send_message", {"content": "hi"}) == {
            "ok": True
        }
        assert session._handle_host_request("get_active_spells", {})["spell_names"] == [
            "s1",
            "s2",
        ]
        assert session._handle_host_request(
            "set_active_spells", {"spell_names": ["s1"]}
        ) == {"ok": True}
        assert session._handle_host_request("get_global_spell_allowlist", {}) == {
            "allowlist": ["a-spell", "b-spell"]
        }
        assert session._handle_host_request(
            "widen_global_allowlist", {"spell_names": ["c-spell"]}
        ) == {"ok": True}
        # widen builds from a set, so order varies by hash seed:
        # compare sorted, not in order.
        assert sorted(
            session._handle_host_request(
                "narrow_global_allowlist", {"spell_names": ["a-spell"]}
            )["allowlist"]
        ) == ["b-spell", "c-spell"]
        assert session._handle_host_request("has_spell_gateway", {}) == {
            "has_spell_gateway": False
        }
        assert session._handle_host_request("gateway_spell_names", {}) == {
            "spell_names": []
        }
        assert "context" in session._handle_host_request("get_context", {})
        assert session._handle_host_request("unregister_spell", {"name": "s2"}) == {
            "unregistered": True
        }
        assert runner.get_spell("s2") is None
        with pytest.raises(ValueError, match="Unknown host request"):
            session._handle_host_request("bogus", {})

    def test_proxy_handlers_delegate_to_session(self, tmp_path: Path) -> None:
        runner, session = _session(tmp_path)
        transport = _transport(session)
        transport.request_result = {"result": "cmd-out"}
        session._handle_host_request(
            "register_command", {"name": "c1", "description": "d"}
        )
        (command,) = runner.get_commands()
        assert asyncio.run(command.handler("x")) == "cmd-out"
        transport.request_result = {"result": "hook-out"}
        session._handle_host_request("register_hook", {"hook": "turn_start"})
        (handler,) = runner.get_sigil_handlers(SigilHook.TURN_START)
        assert asyncio.run(handler({"note": "hi"})) == "hook-out"
        transport.request_result = {"result": "short-out"}
        session._handle_host_request(
            "register_shortcut", {"key": "k1", "description": "d"}
        )
        (shortcut,) = runner.get_shortcuts()
        assert asyncio.run(shortcut.handler("y")) == "short-out"


class TestHandleHostEvents:
    def test_hello_ready_update_and_audit(self, tmp_path: Path) -> None:
        runner, session = _session(tmp_path)
        log = RuneAuditLog(tmp_path)
        session._audit_log = log
        session._handle_host_event(
            "hello", {"protocol_version": 1, "rune_name": "unit-rune"}
        )
        assert session._hello_event.is_set()
        assert session._hello_payload == {
            "protocol_version": 1,
            "rune_name": "unit-rune",
        }
        session._handle_host_event("ready", {"rune_name": "unit-rune"})
        assert session._ready_event.is_set()
        # An update for an unknown cast is dropped, not an error.
        session._handle_host_event(
            "spell_update", {"cast_id": "nobody", "update": {"step": 1}}
        )
        session._handle_host_event(
            "audit",
            {
                "op": "did_thing",
                "outcome": "ok",
                "code": "ok",
                "message": "m",
                "target": "t",
                "extra": {"k": "v"},
            },
        )
        records = [
            json.loads(line)
            for line in (tmp_path / AUDIT_FILENAME).read_text().splitlines()
        ]
        assert len(records) == 1
        assert records[0]["rune"] == "unit-rune"
        assert records[0]["op"] == "did_thing"
        assert records[0]["k"] == "v"

    def test_hook_data_conversion(self, tmp_path: Path) -> None:
        @dataclasses.dataclass
        class _Payload:
            note: str = "hi"

        runner, session = _session(tmp_path)
        transport = _transport(session)
        transport.request_result = {"result": "ok"}
        assert session.invoke_hook(SigilHook.TURN_START, _Payload()) == "ok"
        assert transport.requests[-1][1]["data"] == {"note": "hi"}
        with pytest.raises(HostInProcessOnlyError):
            session.invoke_hook(SigilHook.TURN_START, object())
        with pytest.raises(HostInProcessOnlyError):
            session.invoke_hook(SigilHook.TURN_START, {"bad": object()})


class TestExecuteSpellBranches:
    def _run_in_thread(self, session: HostSession, **kwargs) -> tuple:
        results: dict = {}

        def _target() -> None:
            try:
                results["value"] = session.execute_spell(**kwargs)
            except Exception as exc:  # noqa: BLE001 - captured for assert
                results["error"] = exc

        thread = threading.Thread(target=_target, daemon=True)
        thread.start()
        return results, thread

    def test_success_with_streaming_update(self, tmp_path: Path) -> None:
        runner, session = _session(tmp_path)
        transport = _transport(session)
        future: concurrent.futures.Future = concurrent.futures.Future()
        transport.begin_future = future
        seen: list[dict] = []
        results, thread = self._run_in_thread(
            session,
            spell_name="s",
            spell_cast_id="cast-1",
            params={},
            on_update=seen.append,
        )
        # The cast's update queue exists once the loop starts; poll
        # briefly, then deliver an update and the result.
        for _ in range(100):
            if "cast-1" in session._update_queues:
                break
            threading.Event().wait(0.01)
        session._handle_host_event(
            "spell_update", {"cast_id": "cast-1", "update": {"step": 0}}
        )
        future.set_result({"result": {"done": True}})
        thread.join(timeout=5)
        assert results["value"] == {"done": True}
        assert seen == [{"step": 0}]

    def test_abort_is_sent_when_signal_aborts(self, tmp_path: Path) -> None:
        runner, session = _session(tmp_path)
        transport = _transport(session)
        future: concurrent.futures.Future = concurrent.futures.Future()
        transport.begin_future = future
        controller = AbortController()
        controller.abort()
        results, thread = self._run_in_thread(
            session,
            spell_name="s",
            spell_cast_id="cast-2",
            params={},
            signal=controller.signal,
        )
        threading.Event().wait(0.2)
        future.set_result({"result": {"aborted": True}})
        thread.join(timeout=5)
        assert results["value"] == {"aborted": True}
        assert ("abort", {"cast_id": "cast-2"}) in transport.sent_events

    @pytest.mark.parametrize(
        ("kind", "expected"),
        [
            ("aborted", HostAbortedError),
            ("HostUnsupportedError", HostUnsupportedError),
            ("weird-kind", HostError),
        ],
    )
    def test_remote_error_mapping(
        self, tmp_path: Path, kind: str, expected: type
    ) -> None:
        runner, session = _session(tmp_path)
        transport = _transport(session)
        future: concurrent.futures.Future = concurrent.futures.Future()
        future.set_exception(HostRemoteError(kind, "remote said no"))
        transport.begin_future = future
        with pytest.raises(expected):
            session.execute_spell("s", "cast-3", {})

    def test_crash_and_non_dict_result(self, tmp_path: Path) -> None:
        runner, session = _session(tmp_path)
        transport = _transport(session)
        future: concurrent.futures.Future = concurrent.futures.Future()
        future.set_exception(HostCrashError("died"))
        transport.begin_future = future
        with pytest.raises(HostCrashError):
            session.execute_spell("s", "cast-4", {})
        future2: concurrent.futures.Future = concurrent.futures.Future()
        future2.set_result({"result": "not-a-dict"})
        transport.begin_future = future2
        with pytest.raises(HostError):
            session.execute_spell("s", "cast-5", {})

    def test_timeout_kills_host(self, tmp_path: Path) -> None:
        runner, session = _session(tmp_path)
        session.timeout_seconds = 0.2
        killed: list[bool] = []
        session._kill_host = lambda: killed.append(True)  # type: ignore[method-assign]
        transport = _transport(session)
        transport.begin_future = concurrent.futures.Future()
        with pytest.raises(HostTimeoutError):
            session.execute_spell("s", "cast-6", {})
        assert killed == [True]

    def test_not_running_and_bad_params(self, tmp_path: Path) -> None:
        runner, session = _session(tmp_path)
        session._process = None
        with pytest.raises(HostCrashError):
            session.execute_spell("s", "cast-7", {})
        session._process = _FakeProcess()  # type: ignore[assignment]
        with pytest.raises(HostInProcessOnlyError):
            session.execute_spell("s", "cast-8", {"bad": object()})

    def test_invoke_error_mapping(self, tmp_path: Path) -> None:
        runner, session = _session(tmp_path)
        transport = _transport(session)
        transport.request_error = HostRemoteError("aborted", "no")
        with pytest.raises(HostAbortedError):
            session.invoke_hook(SigilHook.TURN_START, {})
        with pytest.raises(HostAbortedError):
            session.invoke_command("c", "args")
        with pytest.raises(HostAbortedError):
            session.invoke_shortcut("k", "args")
        transport.request_error = TimeoutError("slow")
        session._kill_host = lambda: None  # type: ignore[method-assign]
        with pytest.raises(HostTimeoutError):
            session.invoke_hook(SigilHook.TURN_START, {})
        with pytest.raises(HostTimeoutError):
            session.invoke_command("c", "args")
        with pytest.raises(HostTimeoutError):
            session.invoke_shortcut("k", "args")
        session._process = None
        with pytest.raises(HostCrashError):
            session.invoke_hook(SigilHook.TURN_START, {})


class TestLifecycleBranches:
    def test_stop_terminate_then_kill(self, tmp_path: Path) -> None:
        runner, session = _session(tmp_path)
        process = _FakeProcess(
            wait_results=[
                subprocess.TimeoutExpired("cmd", 5),
                subprocess.TimeoutExpired("cmd", 2),
                0,
            ]
        )
        session._process = process  # type: ignore[assignment]
        transport = _transport(session)
        transport.request_error = HostError("shutdown refused")
        session.stop()
        assert process.terminated
        assert process.killed
        assert transport.closed
        assert session._process is None

    def test_stop_when_nothing_running(self, tmp_path: Path) -> None:
        runner, session = _session(tmp_path)
        session._process = None
        session.stop()  # No-op, no error.

    def test_kill_host_variants(self, tmp_path: Path) -> None:
        runner, session = _session(tmp_path)
        session._process = None
        session._kill_host()  # No-op.
        process = _FakeProcess()
        process.poll = lambda: 0  # type: ignore[method-assign]
        session._process = process  # type: ignore[assignment]
        session._kill_host()  # Already exited: no terminate/kill.
        assert not process.terminated
        process2 = _FakeProcess()
        process2.terminate = lambda: (_ for _ in ()).throw(OSError("gone"))  # type: ignore[method-assign]
        session._process = process2  # type: ignore[assignment]
        session._kill_host()  # OSError suppressed.
        assert session._process is None

    def test_read_stderr_missing_file(self, tmp_path: Path) -> None:
        runner, session = _session(tmp_path)
        assert session.read_stderr() == ""

    def test_is_alive_variants(self, tmp_path: Path) -> None:
        runner, session = _session(tmp_path)
        session._process = None
        assert not session.is_alive()
        session._process = _FakeProcess()  # type: ignore[assignment]
        assert session.is_alive()
        _transport(session).fatal_error = HostCrashError("fatal")
        assert not session.is_alive()

    def test_wait_branches(self, tmp_path: Path) -> None:
        runner, session = _session(tmp_path)
        session.start_timeout_seconds = 0.2
        # Hello timeout.
        with pytest.raises(HostTimeoutError):
            session._wait_for_hello()
        # Hello fatal.
        _transport(session).fatal_error = HostProtocolError("bad wire")
        session._hello_event.clear()
        with pytest.raises(HostProtocolError):
            session._wait_for_hello()
        # Hello crash.
        _transport(session).fatal_error = None
        session._process = None
        with pytest.raises(HostCrashError):
            session._wait_for_hello()
        # Version mismatch.
        session._process = _FakeProcess()  # type: ignore[assignment]
        session._hello_payload = {"protocol_version": 99}
        session._hello_event.set()
        with pytest.raises(VersionMismatchError):
            session._wait_for_hello()
        # Ready timeout / crash / fatal.
        session._ready_event.clear()
        session.start_timeout_seconds = 0.2
        with pytest.raises(HostTimeoutError):
            session._wait_for_ready()
        _transport(session).fatal_error = HostProtocolError("bad wire")
        with pytest.raises(HostProtocolError):
            session._wait_for_ready()
        _transport(session).fatal_error = None
        session._process = None
        with pytest.raises(HostCrashError):
            session._wait_for_ready()

    def test_init_payload_install_id_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner, session = _session(tmp_path)

        def _boom(name: str) -> str:
            raise RuntimeError("no id")

        monkeypatch.setattr(runner, "_install_id_for_rune", _boom)
        payload = session._init_payload()
        assert payload["install_id"] is None
        assert payload["rune_name"] == "unit-rune"

    def test_host_rune_session_reuse_and_refusal(self, tmp_path: Path) -> None:
        runner, session = _session(tmp_path)
        runner.hosted_sessions["unit-rune"] = session
        assert host_rune_session(runner, session.manifest, tmp_path) is session
        manifest = RuneManifest(
            name="approval-rune",
            version="0.1.0",
            description="x",
            entry_point="rune.py",
        )
        with pytest.raises(HostInProcessOnlyError):
            host_rune_session(runner, manifest, tmp_path)
