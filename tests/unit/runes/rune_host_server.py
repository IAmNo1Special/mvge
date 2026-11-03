"""Unit tests for the host-process side (no subprocess).

HostState handlers run in-process against a fake transport, so
the spell/hook/command dispatch and abort handling are covered
without spawning a host. The subprocess integration tests in
rune_host.py cover the same paths over the real wire.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mvge.core.abort import AbortController
from mvge.runes.host_protocol import (
    MAX_MESSAGE_BYTES,
    HostProtocolError,
    OversizeMessageError,
    decode_message,
    encode_message,
)
from mvge.runes.host_server import HostState
from mvge.runes.pinning import RunePin, compute_rune_tree_hash, write_pin


class _FakeWire:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []
        self.requests: list[tuple[str, dict]] = []

    def send_event(self, msg_type: str, payload: dict) -> None:
        self.events.append((msg_type, payload))

    def send_request(self, msg_type: str, payload: dict, timeout: float = 30.0) -> dict:
        self.requests.append((msg_type, payload))
        return {"registered": True}


def _write_rune(parent: Path, name: str, body: str) -> Path:
    rune_dir = parent / name
    rune_dir.mkdir(parents=True, exist_ok=True)
    (rune_dir / "manifest.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "0.1.0",
                "description": "host server test rune",
                "entry_point": "rune.py",
            }
        ),
        encoding="utf-8",
    )
    (rune_dir / "rune.py").write_text(body, encoding="utf-8")
    write_pin(
        rune_dir,
        RunePin(source_kind="local", tree_hash=compute_rune_tree_hash(rune_dir)),
    )
    return rune_dir


_ECHO_BODY = (
    "from mvge.runes.types import SpellDefinition, SigilHook\n"
    "\n"
    "def _echo(params):\n"
    "    return {'echo': params.get('text', '')}\n"
    "\n"
    "def _hook(data):\n"
    "    return {'seen': data.get('note', '')}\n"
    "\n"
    "def _cmd(args):\n"
    "    return f'got:{args}'\n"
    "\n"
    "def rune_factory(api):\n"
    "    api.register_spell(SpellDefinition(\n"
    "        name='echo-spell', description='echo', handler=_echo))\n"
    "    api.on(SigilHook.TURN_START, _hook)\n"
    "    api.register_command('hello-cmd', 'says hello', _cmd)\n"
)


def _state(tmp_path: Path) -> HostState:
    rune_dir = _write_rune(tmp_path, "echo-rune", _ECHO_BODY)
    state = HostState(_FakeWire(), rune_dir)
    assert state.handle_request("init", {"context": {}, "install_id": "x"}) == {
        "ready": True
    }
    return state


def test_host_state_init_registers_spell_hook_command(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    assert "echo-spell" in state.spells
    assert "turn_start" in state.hooks
    assert "hello-cmd" in state.commands
    assert state.api is not None
    assert state.api.install_id == "x"
    wire = state.transport
    assert ("ready", {"rune_name": "echo-rune"}) in wire.events


def test_host_state_spell_execute(tmp_path: Path) -> None:
    state = _state(tmp_path)
    result = state.handle_request(
        "spell_execute",
        {"spell_name": "echo-spell", "cast_id": "c1", "params": {"text": "hi"}},
    )
    assert result == {"result": {"echo": "hi"}}


def test_host_state_spell_execute_unknown_spell(tmp_path: Path) -> None:
    state = _state(tmp_path)
    with pytest.raises(ValueError, match="no spell"):
        state.handle_request(
            "spell_execute",
            {"spell_name": "nope", "cast_id": "c1", "params": {}},
        )


def test_host_state_hook_and_command_invoke(tmp_path: Path) -> None:
    state = _state(tmp_path)
    assert state.handle_request(
        "hook_invoke", {"hook": "turn_start", "data": {"note": "hi"}}
    ) == {"result": {"seen": "hi"}}
    assert state.handle_request(
        "command_invoke", {"name": "hello-cmd", "args": "world"}
    ) == {"result": "got:world"}
    assert state.handle_request("ping", {}) == {"pong": True}
    with pytest.raises(ValueError, match="Unknown host request"):
        state.handle_request("bogus", {})


def test_host_state_abort_event_aborts_controller(tmp_path: Path) -> None:
    state = _state(tmp_path)
    controller = AbortController()
    state.controllers["c9"] = controller
    state.handle_event("abort", {"cast_id": "c9"})
    assert controller.signal.aborted
    # An abort for an unknown cast is a no-op, not an error.
    state.handle_event("abort", {"cast_id": "nobody"})


def test_wire_encode_decode_roundtrip() -> None:
    message = {
        "protocol": "mvge-rune-host/1",
        "kind": "event",
        "type": "hello",
        "payload": {"protocol_version": 1},
    }
    assert decode_message(encode_message(message)) == message


def test_wire_oversize_encode_and_decode_rejected() -> None:
    big = {
        "protocol": "mvge-rune-host/1",
        "kind": "event",
        "type": "x",
        "payload": {"pad": "y" * MAX_MESSAGE_BYTES},
    }
    with pytest.raises(OversizeMessageError):
        encode_message(big)
    with pytest.raises(OversizeMessageError):
        decode_message(b"x" * (MAX_MESSAGE_BYTES + 2))


def test_wire_bad_envelope_rejected() -> None:
    with pytest.raises(HostProtocolError):
        decode_message(b'{"protocol": "other/9", "kind": "event"}')
    with pytest.raises(HostProtocolError):
        decode_message(b"not json")
    with pytest.raises(HostProtocolError):
        decode_message(b"[1, 2]")
    with pytest.raises(HostProtocolError):
        decode_message(
            b'{"protocol": "mvge-rune-host/1", "kind": "bogus", "type": "x"}'
        )


def test_host_api_supported_methods() -> None:
    """Every supported HostRuneAPI method works against a canned wire."""
    from mvge.runes.host_server import HostRuneAPI
    from mvge.runes.types import SkillManifest, SpellDefinition

    wire = _FakeWire()
    api = HostRuneAPI(wire, "api-rune", {"agent_name": "a", "model_id": "m"})
    assert api.context.agent_name == "a"
    assert api.context.model_id == "m"
    assert api.install_id is None
    assert api.sanitize_no_proxy_env() is False
    disposed: list[bool] = []
    api.on_dispose(lambda: disposed.append(True))
    assert len(api.scope.cleanup_callbacks) == 1
    spell = SpellDefinition(name="s", description="d", handler=lambda p: {})
    assert api.register_spell(spell) is True
    assert api.unregister_spell("s") is False
    assert api.get_spell("s") is None
    assert api.get_all_spells() == []
    assert api.get_discoverable_spells() == []
    assert api.register_command("c", "d", lambda a: a) is True
    assert api.register_shortcut("k", "d", lambda a: a) is True
    assert api.get_shortcuts() == []
    assert api.get_active_spells() == []
    api.set_active_spells(["s"])
    assert api.get_global_spell_allowlist() is None
    api.widen_global_allowlist(["s"])
    assert api.narrow_global_allowlist(["s"]) == []
    assert api.has_spell_gateway is False
    assert api.gateway_spell_names() == []
    assert api.get_skills() == []
    api.register_skill(SkillManifest(name="sk", description="d", path="/tmp/sk"))
    api.register_skill_path("/tmp/skills")
    api.clear_skills_for_rune()
    api.send_message("hi")
    api.set_session_name("T")
    assert api.get_session_name() is None
    api.audit("op", outcome="ok", code="ok", message="m", target="t")
    assert wire.events[-1][0] == "audit"
    assert wire.events[-1][1]["op"] == "op"
    assert wire.requests[0][0] == "register_spell"
    assert api.scope.spell_names == set()
    assert api.scope.commands == {"c"}
    assert api.scope.shortcuts == {"k"}


def test_host_state_extra_branches(tmp_path: Path) -> None:
    """Spell-context fields, non-dict/non-serializable results,
    async hooks, shortcut dispatch, and init refusals."""
    from mvge.runes.host_server import HostState

    rune_dir = _write_rune(tmp_path, "ctx-rune", _ECHO_BODY)
    state = HostState(_FakeWire(), rune_dir)
    assert state.handle_request("init", {"context": {}, "install_id": "x"}) == {
        "ready": True
    }
    ctx = state._spell_context(
        {
            "workspace_root": "/tmp/ws",
            "project_root": "/tmp/pr",
            "tome_id": "t1",
            "agent_name": "a",
            "spell_action": "doing",
            "spell_summary": "done",
            "state": {"k": "v"},
        },
        "c1",
        AbortController(),
    )
    assert str(ctx.workspace_root) == "/tmp/ws"
    assert ctx.tome_id == "t1"
    assert ctx.state == {"k": "v"}
    with pytest.raises(ValueError, match="no shortcut"):
        state.handle_request("shortcut_invoke", {"key": "nope"})
    with pytest.raises(ValueError, match="no command"):
        state.handle_request("command_invoke", {"name": "nope", "args": ""})
    assert state.handle_request("shutdown", {}) == {"stopped": True}
    assert state.stop_event.is_set()


def test_host_state_init_refusals(tmp_path: Path) -> None:
    from mvge.runes.host_server import HostState

    empty_dir = tmp_path / "empty-rune"
    empty_dir.mkdir()
    state = HostState(_FakeWire(), empty_dir)
    with pytest.raises(ValueError, match="No manifest"):
        state.handle_request("init", {"context": {}})
    no_entry = tmp_path / "no-entry"
    no_entry.mkdir()
    (no_entry / "manifest.json").write_text(
        json.dumps(
            {
                "name": "no-entry",
                "version": "0.1.0",
                "description": "x",
                "entry_point": "missing.py",
            }
        ),
        encoding="utf-8",
    )
    state2 = HostState(_FakeWire(), no_entry)
    with pytest.raises(ValueError, match="refused"):
        state2.handle_request("init", {"context": {}})


def test_host_state_spell_result_shapes(tmp_path: Path) -> None:
    """Non-dict and non-serializable spell results fail closed."""
    from mvge.runes.host_server import HostState
    from mvge.runes.types import SpellDefinition

    state = HostState(_FakeWire(), tmp_path)
    state.spells["str-spell"] = SpellDefinition(
        name="str-spell", description="d", handler=lambda p: "not-a-dict"
    )
    with pytest.raises(ValueError, match="non-dict"):
        state.handle_request(
            "spell_execute",
            {"spell_name": "str-spell", "cast_id": "c1", "params": {}},
        )
    state.spells["bad-spell"] = SpellDefinition(
        name="bad-spell", description="d", handler=lambda p: {"bad": object()}
    )
    with pytest.raises(ValueError, match="not JSON-serializable"):
        state.handle_request(
            "spell_execute",
            {"spell_name": "bad-spell", "cast_id": "c2", "params": {}},
        )


def test_host_state_async_hook_and_update_repr(tmp_path: Path) -> None:
    """Async hook handlers run, and non-serializable updates cross
    as a repr instead of breaking the wire."""
    from mvge.runes.host_server import HostState
    from mvge.runes.types import SpellDefinition

    async def _async_hook(data: dict) -> dict:
        return {"seen": data.get("note", "")}

    state = HostState(_FakeWire(), tmp_path)
    state.hooks["turn_start"] = [_async_hook]
    assert state.handle_request(
        "hook_invoke", {"hook": "turn_start", "data": {"note": "hi"}}
    ) == {"result": {"seen": "hi"}}

    def _stream(params: dict, on_update: object = None) -> dict:
        if on_update is not None:
            on_update(object())  # type: ignore[operator]
        return {"done": True}

    state.spells["stream-spell"] = SpellDefinition(
        name="stream-spell", description="d", handler=_stream
    )
    wire = state.transport
    assert isinstance(wire, _FakeWire)
    result = state.handle_request(
        "spell_execute",
        {"spell_name": "stream-spell", "cast_id": "c9", "params": {}},
    )
    assert result == {"result": {"done": True}}
    updates = [p for t, p in wire.events if t == "spell_update"]
    assert len(updates) == 1
    assert "repr" in updates[0]["update"]
