"""Phase 4: out-of-process rune host, v1.

One host process per hosted rune, stdio JSON wire, scrubbed env.
Realm, spell-gateway, and approval runes stay in-process, protected
by Phase 3 pinning. A hostile host rune can still read files its OS
user can read and reach the network: Phase 4 is not a credential
or filesystem boundary, and no doc or diagnostic here claims it is.

The in-process path is untouched: these tests start hosted sessions
explicitly, and the runner's normal load path is covered elsewhere.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
from pathlib import Path

import pytest

from mvge.core.spells import SpellContext
from mvge.runes.host import HostSession
from mvge.runes.host_classification import (
    HOST_SUPPORTED_METHODS,
    HOST_UNSUPPORTED_METHODS,
    classify_manifest,
)
from mvge.runes.host_protocol import (
    HostCrashError,
    HostError,
    HostInProcessOnlyError,
    HostProtocolError,
    HostTimeoutError,
    HostUnsupportedError,
    OversizeMessageError,
    VersionMismatchError,
    scrub_env,
)
from mvge.runes.host_server import HostRuneAPI
from mvge.runes.manifest import load_manifest
from mvge.runes.pinning import (
    PIN_FILENAME,
    RunePin,
    compute_rune_tree_hash,
    write_pin,
)
from mvge.runes.rune_runner import RuneRunner
from mvge.runes.source_audit import audit_hosted_source
from mvge.runes.types import RuneManifest, SigilHook


def _write_rune(
    parent: Path,
    name: str,
    body: str,
    extra: dict | None = None,
) -> Path:
    rune_dir = parent / name
    rune_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": name,
        "version": "0.1.0",
        "description": "host test rune",
        "entry_point": "rune.py",
    }
    if extra:
        manifest.update(extra)
    (rune_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (rune_dir / "rune.py").write_text(body, encoding="utf-8")
    pin = RunePin(source_kind="local", tree_hash=compute_rune_tree_hash(rune_dir))
    write_pin(rune_dir, pin)
    assert (rune_dir / PIN_FILENAME).is_file()
    return rune_dir


def _load(rune_dir: Path) -> RuneManifest:
    manifest = load_manifest(rune_dir)
    assert manifest is not None
    manifest.path = str(rune_dir)
    return manifest


def _start(
    tmp_path: Path,
    rune_dir: Path,
    *,
    timeout_seconds: float = 30.0,
    log_name: str = "host.log",
) -> tuple[RuneRunner, HostSession]:
    runner = RuneRunner()
    manifest = _load(rune_dir)
    session = HostSession(
        manifest,
        rune_dir,
        runner,
        timeout_seconds=timeout_seconds,
        stderr_log_path=tmp_path / log_name,
    )
    session.start()
    return runner, session


_ECHO_BODY = (
    "from mvge.runes.types import SpellDefinition\n"
    "\n"
    "def _echo(params):\n"
    "    return {'echo': params.get('text', '')}\n"
    "\n"
    "def rune_factory(api):\n"
    "    api.register_spell(SpellDefinition(\n"
    "        name='echo-spell', description='echo',\n"
    "        parameters={'type': 'object',\n"
    "                    'properties': {'text': {'type': 'string'}}},\n"
    "        handler=_echo))\n"
)

_HOOK_BODY = (
    "from mvge.runes.types import SigilHook\n"
    "\n"
    "def _hook(data):\n"
    "    return {'seen': data.get('note', '')}\n"
    "\n"
    "def rune_factory(api):\n"
    "    api.on(SigilHook.TURN_START, _hook)\n"
)

_COMMAND_BODY = (
    "def _cmd(args):\n"
    "    return f'got:{args}'\n"
    "\n"
    "def rune_factory(api):\n"
    "    api.register_command('hello-cmd', 'says hello', _cmd)\n"
)

_SKILL_BODY = (
    "from mvge.runes.types import SkillManifest\n"
    "\n"
    "def rune_factory(api):\n"
    "    api.register_skill(SkillManifest(\n"
    "        name='hosted-skill', description='from host',\n"
    "        path='/tmp/hosted-skill'))\n"
)

_CRASH_BODY = (
    "import os\n"
    "from mvge.runes.types import SpellDefinition\n"
    "\n"
    "def _boom(params):\n"
    "    os._exit(42)\n"
    "\n"
    "def rune_factory(api):\n"
    "    api.register_spell(SpellDefinition(\n"
    "        name='boom-spell', description='boom', handler=_boom))\n"
)

_HANG_BODY = (
    "import time\n"
    "from mvge.runes.types import SpellDefinition\n"
    "\n"
    "def _hang(params):\n"
    "    time.sleep(60)\n"
    "    return {'done': True}\n"
    "\n"
    "def rune_factory(api):\n"
    "    api.register_spell(SpellDefinition(\n"
    "        name='hang-spell', description='hang', handler=_hang))\n"
)

_ABORT_BODY = (
    "import time\n"
    "from mvge.runes.types import SpellDefinition\n"
    "\n"
    "def _work(params, signal=None):\n"
    "    for _ in range(200):\n"
    "        if signal is not None and signal.aborted:\n"
    "            return {'aborted': True}\n"
    "        time.sleep(0.05)\n"
    "    return {'aborted': False}\n"
    "\n"
    "def rune_factory(api):\n"
    "    api.register_spell(SpellDefinition(\n"
    "        name='work-spell', description='work', handler=_work))\n"
)

_UPDATE_BODY = (
    "from mvge.runes.types import SpellDefinition\n"
    "\n"
    "def _stream(params, on_update=None):\n"
    "    for i in range(3):\n"
    "        if on_update is not None:\n"
    "            on_update({'step': i})\n"
    "    return {'done': True, 'steps': 3}\n"
    "\n"
    "def rune_factory(api):\n"
    "    api.register_spell(SpellDefinition(\n"
    "        name='stream-spell', description='stream', handler=_stream))\n"
)

_UNSUPPORTED_BODY = (
    "async def rune_factory(api):\n"
    "    await api.stream_model_text('some-model', 'hi')\n"
)

_ENV_BODY = (
    "import os\n"
    "from mvge.runes.types import SpellDefinition\n"
    "\n"
    "def _env(params):\n"
    "    return {\n"
    "        'key_present': os.environ.get('OPENAI_API_KEY') is not None,\n"
    "        'path_present': 'PATH' in os.environ,\n"
    "    }\n"
    "\n"
    "def rune_factory(api):\n"
    "    api.register_spell(SpellDefinition(\n"
    "        name='env-spell', description='env', handler=_env))\n"
)

_BAD_RESULT_BODY = (
    "from mvge.runes.types import SpellDefinition\n"
    "\n"
    "def _bad(params):\n"
    "    return {'bad': object()}\n"
    "\n"
    "def rune_factory(api):\n"
    "    api.register_spell(SpellDefinition(\n"
    "        name='bad-spell', description='bad', handler=_bad))\n"
)


def _cast(spell: object, params: dict, **kwargs: object) -> dict:
    return asyncio.run(spell.execute("cast-1", params, **kwargs))  # type: ignore[attr-defined]


class TestClassification:
    @pytest.mark.parametrize(
        ("extra", "reason_part"),
        [
            ({"capabilities": ["realm"]}, "realm"),
            ({"types": ["RealmProvider"]}, "realm"),
            ({"spell_gateway": True}, "gateway"),
            ({"types": ["Codec"]}, "codec"),
            ({"types": ["Spell Modifier"]}, "modifier"),
            ({"types": ["Spell Gate"]}, "gate"),
            ({"hosting": "in-process"}, "manifest"),
            ({"hosting": "bogus-value"}, "in-process"),
        ],
    )
    def test_in_process_only_tier(
        self, tmp_path: Path, extra: dict, reason_part: str
    ) -> None:
        rune_dir = _write_rune(
            tmp_path, "tier-rune", "def rune_factory(api):\n    return None\n", extra
        )
        verdict = classify_manifest(_load(rune_dir))
        assert not verdict.hostable
        assert reason_part in verdict.reason.lower()
        assert verdict.tier == "in-process-only"

    def test_approval_rune_is_in_process_only(self, tmp_path: Path) -> None:
        rune_dir = _write_rune(
            tmp_path, "approval-rune", "def rune_factory(api):\n    return None\n"
        )
        verdict = classify_manifest(_load(rune_dir))
        assert not verdict.hostable
        assert "approval" in verdict.reason.lower()

    def test_spell_hook_command_skill_manifest_is_hostable(
        self, tmp_path: Path
    ) -> None:
        rune_dir = _write_rune(
            tmp_path,
            "plain-rune",
            "def rune_factory(api):\n    return None\n",
            {"capabilities": ["spell", "hook", "command", "skill"]},
        )
        verdict = classify_manifest(_load(rune_dir))
        assert verdict.hostable
        assert verdict.tier == "hosted"

    def test_runner_host_rune_refuses_in_process_tier(self, tmp_path: Path) -> None:
        runner = RuneRunner()
        rune_dir = _write_rune(
            tmp_path,
            "approval-rune",
            "def rune_factory(api):\n    return None\n",
        )
        with pytest.raises(HostInProcessOnlyError):
            runner.host_rune(_load(rune_dir), rune_dir)
        assert runner.hosted_sessions == {}


class TestHostedSpellExecution:
    def test_echo_spell_runs_in_a_separate_process(self, tmp_path: Path) -> None:
        rune_dir = _write_rune(tmp_path, "echo-rune", _ECHO_BODY)
        runner, session = _start(tmp_path, rune_dir)
        try:
            assert session.is_alive()
            assert session.pid is not None
            assert session.pid != os.getpid()
            spell = runner.get_spell("echo-spell")
            assert spell is not None
            assert _cast(spell, {"text": "hello"}) == {"echo": "hello"}
            assert session.is_alive()
        finally:
            session.stop()

    def test_hosted_hook_command_and_skill_registrations(self, tmp_path: Path) -> None:
        runner = RuneRunner()
        sessions: list[HostSession] = []
        try:
            for body, name in (
                (_HOOK_BODY, "hook-rune"),
                (_COMMAND_BODY, "cmd-rune"),
                (_SKILL_BODY, "skill-rune"),
            ):
                rune_dir = _write_rune(tmp_path, name, body)
                session = HostSession(
                    _load(rune_dir),
                    rune_dir,
                    runner,
                    stderr_log_path=tmp_path / f"{name}.log",
                )
                session.start()
                sessions.append(session)
            assert runner.get_sigil_handlers(SigilHook.TURN_START)
            result = sessions[0].invoke_hook(SigilHook.TURN_START, {"note": "hi"})
            assert result == {"seen": "hi"}
            commands = {c.name: c for c in runner.get_commands()}
            assert "hello-cmd" in commands
            assert asyncio.run(commands["hello-cmd"].handler("world")) == "got:world"
            skills = runner.skill_catalog.get_skills()
            assert any(s.name == "hosted-skill" for s in skills)
        finally:
            for session in sessions:
                session.stop()

    def test_hosted_rune_crash_does_not_take_down_engine(self, tmp_path: Path) -> None:
        rune_dir = _write_rune(tmp_path, "crash-rune", _CRASH_BODY)
        runner, session = _start(tmp_path, rune_dir)
        try:
            spell = runner.get_spell("boom-spell")
            assert spell is not None
            with pytest.raises(HostCrashError):
                _cast(spell, {})
            assert not session.is_alive()
            # The engine survives: the runner is still usable.
            runner.send_message("engine still alive")
            assert runner.get_spell("boom-spell") is not None
        finally:
            session.stop()

    def test_hung_hosted_spell_times_out_and_host_is_killed(
        self, tmp_path: Path
    ) -> None:
        rune_dir = _write_rune(tmp_path, "hang-rune", _HANG_BODY)
        runner, session = _start(tmp_path, rune_dir, timeout_seconds=1.0)
        try:
            spell = runner.get_spell("hang-spell")
            assert spell is not None
            with pytest.raises(HostTimeoutError):
                _cast(spell, {})
            assert not session.is_alive()
        finally:
            session.stop()

    def test_abort_crosses_the_wire(self, tmp_path: Path) -> None:
        from mvge.core.abort import AbortController

        rune_dir = _write_rune(tmp_path, "abort-rune", _ABORT_BODY)
        runner, session = _start(tmp_path, rune_dir, timeout_seconds=15.0)
        try:
            spell = runner.get_spell("work-spell")
            assert spell is not None
            controller = AbortController()

            async def _run() -> dict:
                task = asyncio.create_task(
                    spell.execute("cast-1", {}, signal=controller.signal)
                )
                await asyncio.sleep(0.3)
                controller.abort()
                return await task

            result = asyncio.run(_run())
            assert result == {"aborted": True}
            assert session.is_alive()
        finally:
            session.stop()

    def test_streaming_updates_cross_the_wire(self, tmp_path: Path) -> None:
        rune_dir = _write_rune(tmp_path, "update-rune", _UPDATE_BODY)
        runner, session = _start(tmp_path, rune_dir)
        try:
            spell = runner.get_spell("stream-spell")
            assert spell is not None
            seen: list[dict] = []
            result = asyncio.run(spell.execute("cast-1", {}, on_update=seen.append))
            assert result == {"done": True, "steps": 3}
            assert seen == [{"step": 0}, {"step": 1}, {"step": 2}]
        finally:
            session.stop()

    def test_non_serializable_result_fails_closed_without_killing_host(
        self, tmp_path: Path
    ) -> None:
        rune_dir = _write_rune(tmp_path, "bad-rune", _BAD_RESULT_BODY)
        runner, session = _start(tmp_path, rune_dir)
        try:
            spell = runner.get_spell("bad-spell")
            assert spell is not None
            with pytest.raises(HostError):
                _cast(spell, {})
            assert session.is_alive()
        finally:
            session.stop()

    def test_spell_requiring_live_backend_is_in_process_only(
        self, tmp_path: Path
    ) -> None:
        rune_dir = _write_rune(tmp_path, "echo-rune", _ECHO_BODY)
        runner, session = _start(tmp_path, rune_dir)
        try:
            spell = runner.get_spell("echo-spell")
            assert spell is not None
            ctx = SpellContext(spell_cast_id="cast-1", backend=object())
            with pytest.raises(HostInProcessOnlyError):
                _cast(spell, {"text": "hi"}, ctx=ctx)
            ctx_state = SpellContext(spell_cast_id="cast-1", state={"f": object()})
            with pytest.raises(HostInProcessOnlyError):
                _cast(spell, {"text": "hi"}, ctx=ctx_state)
        finally:
            session.stop()

    def test_host_restart_restores_registrations(self, tmp_path: Path) -> None:
        rune_dir = _write_rune(tmp_path, "echo-rune", _ECHO_BODY)
        runner, session = _start(tmp_path, rune_dir)
        try:
            first_pid = session.pid
            session.restart()
            assert session.pid is not None
            assert session.pid != first_pid
            spell = runner.get_spell("echo-spell")
            assert spell is not None
            assert _cast(spell, {"text": "again"}) == {"echo": "again"}
            assert len(runner.get_all_registered_spells()) == 1
        finally:
            session.stop()

    def test_host_env_is_scrubbed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "test-secret-value-12345")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-secret-value-12345")
        rune_dir = _write_rune(tmp_path, "env-rune", _ENV_BODY)
        runner, session = _start(tmp_path, rune_dir, log_name="env-host.log")
        try:
            spell = runner.get_spell("env-spell")
            assert spell is not None
            assert _cast(spell, {}) == {
                "key_present": False,
                "path_present": True,
            }
            stderr_text = session.read_stderr()
            assert "test-secret-value-12345" not in stderr_text
        finally:
            session.stop()

    def test_unsupported_factory_call_fails_host_start_closed(
        self, tmp_path: Path
    ) -> None:
        rune_dir = _write_rune(tmp_path, "bad-api-rune", _UNSUPPORTED_BODY)
        runner = RuneRunner()
        session = HostSession(
            _load(rune_dir),
            rune_dir,
            runner,
            stderr_log_path=tmp_path / "unsupported.log",
        )
        with pytest.raises(HostUnsupportedError):
            session.start()
        assert not session.is_alive()
        session.stop()


class TestWireProtocol:
    def _stub_session(
        self, tmp_path: Path, script: str
    ) -> tuple[RuneRunner, HostSession]:
        script_path = tmp_path / "stub_host.py"
        script_path.write_text(script, encoding="utf-8")
        runner = RuneRunner()
        manifest = RuneManifest(
            name="stub-rune",
            version="0.1.0",
            description="stub",
            entry_point="rune.py",
        )
        session = HostSession(
            manifest,
            tmp_path,
            runner,
            command=[sys.executable, str(script_path)],
            stderr_log_path=tmp_path / "stub.log",
        )
        return runner, session

    def test_version_mismatch_is_rejected(self, tmp_path: Path) -> None:
        script = (
            "import json, sys, time\n"
            "sys.stdout.write(json.dumps({'protocol': 'mvge-rune-host/1',"
            " 'kind': 'event', 'type': 'hello',"
            " 'payload': {'protocol_version': 99, 'rune_name': 'stub'}}) + chr(10))\n"
            "sys.stdout.flush()\n"
            "time.sleep(30)\n"
        )
        _, session = self._stub_session(tmp_path, script)
        with pytest.raises(VersionMismatchError):
            session.start()
        assert not session.is_alive()
        session.stop()

    def test_malformed_message_is_rejected(self, tmp_path: Path) -> None:
        script = (
            "import json, sys, time\n"
            "sys.stdout.write(json.dumps({'protocol': 'mvge-rune-host/1',"
            " 'kind': 'event', 'type': 'hello',"
            " 'payload': {'protocol_version': 1, 'rune_name': 'stub'}}) + chr(10))\n"
            "sys.stdout.flush()\n"
            "sys.stdout.write('this is not json' + chr(10))\n"
            "sys.stdout.flush()\n"
            "time.sleep(30)\n"
        )
        _, session = self._stub_session(tmp_path, script)
        with pytest.raises(HostProtocolError):
            session.start()
        assert not session.is_alive()
        session.stop()

    def test_oversize_message_is_rejected(self, tmp_path: Path) -> None:
        script = (
            "import json, sys, time\n"
            "sys.stdout.write(json.dumps({'protocol': 'mvge-rune-host/1',"
            " 'kind': 'event', 'type': 'hello',"
            " 'payload': {'protocol_version': 1, 'rune_name': 'stub'}}) + chr(10))\n"
            "sys.stdout.flush()\n"
            "sys.stdout.write('x' * (2 * 1024 * 1024) + chr(10))\n"
            "sys.stdout.flush()\n"
            "time.sleep(30)\n"
        )
        _, session = self._stub_session(tmp_path, script)
        with pytest.raises(OversizeMessageError):
            session.start()
        assert not session.is_alive()
        session.stop()

    def test_scrub_env_removes_credential_names(self) -> None:
        env = {
            "PATH": "/usr/bin",
            "OPENAI_API_KEY": "x",
            "MY_TOKEN": "x",
            "DB_PASSWORD": "x",
            "SECRET_SAUCE": "x",
            "NORMAL_VAR": "keep",
        }
        scrubbed = scrub_env(env)
        assert scrubbed["PATH"] == "/usr/bin"
        assert scrubbed["NORMAL_VAR"] == "keep"
        for key in ("OPENAI_API_KEY", "MY_TOKEN", "DB_PASSWORD", "SECRET_SAUCE"):
            assert key not in scrubbed

    def test_default_host_command_is_cross_platform(self, tmp_path: Path) -> None:
        runner = RuneRunner()
        manifest = RuneManifest(
            name="x", version="0.1.0", description="x", entry_point="rune.py"
        )
        session = HostSession(manifest, tmp_path, runner)
        assert session.command[0] == sys.executable
        assert "mvge.runes.host_server" in session.command
        assert "shell" not in str(session.command).lower()


class TestHostParity:
    def test_audit_and_host_runtime_agree_on_unsupported_methods(self) -> None:
        """One table drives both: audit flags exactly the methods the
        hosted API refuses. Any drift fails here (Phase 4 release gate).
        """
        for method in sorted(HOST_UNSUPPORTED_METHODS):
            findings = audit_hosted_source(
                f"def rune_factory(api):\n    return api.{method}\n"
            )
            assert any(f.kind == "host_unsupported_method" for f in findings), (
                f"audit did not flag hosted use of {method}"
            )
            api = HostRuneAPI(_FakeTransport(), "parity-rune", {})
            with pytest.raises(HostUnsupportedError):
                _call_unsupported(api, method)

    def test_supported_methods_are_not_flagged_and_not_refused(self) -> None:
        for method in sorted(HOST_SUPPORTED_METHODS):
            findings = audit_hosted_source(
                f"def rune_factory(api):\n    return api.{method}\n"
            )
            assert not any(f.kind == "host_unsupported_method" for f in findings), (
                f"audit wrongly flagged hosted use of {method}"
            )
            api = HostRuneAPI(_FakeTransport(), "parity-rune", {})
            assert hasattr(api, method), f"host API lacks {method}"


def _call_unsupported(api: HostRuneAPI, method: str) -> None:
    """Get and invoke one hosted-unsupported method (property access
    alone raises for ``sandbox``)."""
    attr = getattr(api, method)
    if inspect.iscoroutinefunction(attr):
        asyncio.run(attr(*_required_args(attr)))
    elif callable(attr):
        attr(*_required_args(attr))
    else:  # pragma: no cover - property case handled by getattr
        raise AssertionError("unreachable")


def _required_args(func: object) -> list[None]:
    """One None per required positional parameter of a bound method."""
    args: list[None] = []
    for param in inspect.signature(func).parameters.values():  # type: ignore[arg-type]
        if param.default is inspect.Parameter.empty and param.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            args.append(None)
    return args


class _FakeTransport:
    """In-process stand-in for the wire: canned acks, no process."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.events: list[tuple[str, dict]] = []

    def send_request(self, msg_type: str, payload: dict, timeout: float = 30.0) -> dict:
        self.sent.append((msg_type, payload))
        if msg_type == "register_spell":
            return {"registered": True}
        if msg_type == "register_command":
            return {"registered": True}
        if msg_type == "register_shortcut":
            return {"registered": True}
        if msg_type == "register_hook":
            return {"registered": True}
        if msg_type == "register_skill":
            return {"registered": True}
        if msg_type == "register_skill_path":
            return {"registered": True}
        if msg_type == "get_session_name":
            return {"session_name": None}
        if msg_type == "get_spell":
            return {"spell": None}
        if msg_type == "get_all_spells":
            return {"spells": []}
        if msg_type == "get_discoverable_spells":
            return {"spells": []}
        if msg_type == "get_shortcuts":
            return {"shortcuts": []}
        if msg_type == "get_skills":
            return {"skills": []}
        if msg_type == "get_active_spells":
            return {"spell_names": []}
        if msg_type == "get_global_spell_allowlist":
            return {"allowlist": None}
        if msg_type == "gateway_spell_names":
            return {"spell_names": []}
        if msg_type == "has_spell_gateway":
            return {"has_spell_gateway": False}
        if msg_type == "get_context":
            return {"context": {}}
        return {"ok": True}

    def send_event(self, msg_type: str, payload: dict) -> None:
        self.events.append((msg_type, payload))
