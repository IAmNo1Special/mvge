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
