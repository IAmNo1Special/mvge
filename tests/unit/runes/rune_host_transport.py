"""Unit tests for the NDJSON transport, over a socket pair.

Two transports (engine side, host side) talk through connected
sockets, so request/response, events, and fatal-error paths run
for real without spawning a process.
"""

from __future__ import annotations

import io
import json
import socket
import time

import pytest

from mvge.runes.host_protocol import (
    PROTOCOL_NAME,
    HostCrashError,
    HostProtocolError,
    OversizeMessageError,
)
from mvge.runes.host_transport import JsonLineTransport


def _pair() -> tuple[JsonLineTransport, JsonLineTransport, list, list]:
    left_sock, right_sock = socket.socketpair()
    left_events: list = []
    right_events: list = []
    left = JsonLineTransport(
        left_sock.makefile("rb"),
        left_sock.makefile("wb"),
        on_request=lambda t, p: {"echo": p},
        on_event=lambda t, p: left_events.append((t, p)),
    )
    right = JsonLineTransport(
        right_sock.makefile("rb"),
        right_sock.makefile("wb"),
        on_request=lambda t, p: {"echo": p},
        on_event=lambda t, p: right_events.append((t, p)),
    )
    left.start()
    left.start()  # Idempotent: the second start is a no-op.
    right.start()
    return left, right, left_events, right_events


def test_request_response_roundtrip() -> None:
    left, right, _, _ = _pair()
    try:
        assert left.request("ping", {"n": 1}, timeout=5.0) == {"echo": {"n": 1}}
        assert right.request("ping", {"n": 2}, timeout=5.0) == {"echo": {"n": 2}}
    finally:
        left.close()
        right.close()


def test_events_arrive() -> None:
    left, right, left_events, right_events = _pair()
    try:
        left.send_event("note", {"v": 1})
        right.send_event("note", {"v": 2})
        deadline = time.monotonic() + 5.0
        while len(left_events) < 1 or len(right_events) < 1:
            if time.monotonic() > deadline:
                break
            time.sleep(0.01)
        assert ("note", {"v": 2}) in left_events
        assert ("note", {"v": 1}) in right_events
    finally:
        left.close()
        right.close()


def test_handler_error_becomes_error_response() -> None:
    left_sock, right_sock = socket.socketpair()
    left = JsonLineTransport(
        left_sock.makefile("rb"),
        left_sock.makefile("wb"),
    )
    right = JsonLineTransport(
        right_sock.makefile("rb"),
        right_sock.makefile("wb"),
        on_request=lambda t, p: (_ for _ in ()).throw(ValueError("boom")),
    )
    left.start()
    right.start()
    try:
        from mvge.runes.host_transport import HostRemoteError

        with pytest.raises(HostRemoteError, match="boom"):
            left.request("x", {}, timeout=5.0)
    finally:
        left.close()
        right.close()


def test_no_handler_becomes_unsupported_response() -> None:
    left_sock, right_sock = socket.socketpair()
    left = JsonLineTransport(
        left_sock.makefile("rb"),
        left_sock.makefile("wb"),
    )
    right = JsonLineTransport(
        right_sock.makefile("rb"),
        right_sock.makefile("wb"),
    )
    left.start()
    right.start()
    try:
        from mvge.runes.host_transport import HostRemoteError

        with pytest.raises(HostRemoteError, match="No handler"):
            left.request("x", {}, timeout=5.0)
    finally:
        left.close()
        right.close()


def test_eof_is_a_fatal_crash() -> None:
    left_sock, right_sock = socket.socketpair()
    left = JsonLineTransport(
        left_sock.makefile("rb"),
        left_sock.makefile("wb"),
    )
    left.start()
    right_sock.close()
    deadline = time.monotonic() + 5.0
    while left.fatal_error is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert isinstance(left.fatal_error, HostCrashError)
    left.close()


def test_oversize_line_is_fatal() -> None:
    read = io.BytesIO(b"x" * (1024 * 1024 + 10) + b"\n")
    transport = JsonLineTransport(read, io.BytesIO())
    transport.start()
    deadline = time.monotonic() + 5.0
    while transport.fatal_error is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert isinstance(transport.fatal_error, OversizeMessageError)
    transport.close()


def test_malformed_line_is_fatal_and_str_stream_works() -> None:
    message = json.dumps(
        {
            "protocol": PROTOCOL_NAME,
            "kind": "event",
            "type": "hello",
            "payload": {},
        }
    )
    read = io.StringIO(message + "\n" + "not json\n")
    events: list = []
    transport = JsonLineTransport(
        read, io.BytesIO(), on_event=lambda t, p: events.append((t, p))
    )
    transport.start()
    deadline = time.monotonic() + 5.0
    while transport.fatal_error is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert events == [("hello", {})]
    assert isinstance(transport.fatal_error, HostProtocolError)
    transport.close()


def test_bad_response_ids_are_ignored() -> None:
    # The peer socket stays open, so no EOF fatal can mask the check.
    left_sock, right_sock = socket.socketpair()
    transport = JsonLineTransport(
        left_sock.makefile("rb"),
        left_sock.makefile("wb"),
    )
    transport.start()
    writer = right_sock.makefile("wb")
    for message in (
        {
            "protocol": PROTOCOL_NAME,
            "kind": "response",
            "id": "not-an-int",
            "ok": True,
            "payload": {},
        },
        {
            "protocol": PROTOCOL_NAME,
            "kind": "request",
            "id": None,
            "type": "x",
            "payload": {},
        },
    ):
        writer.write(json.dumps(message).encode() + b"\n")
    writer.flush()
    time.sleep(0.3)
    assert transport.fatal_error is None
    transport.close()
    right_sock.close()


def test_event_handler_error_does_not_kill_reader() -> None:
    left_sock, right_sock = socket.socketpair()
    called: list = []
    transport = JsonLineTransport(
        left_sock.makefile("rb"),
        left_sock.makefile("wb"),
        on_event=lambda t, p: (
            called.append(t) or (_ for _ in ()).throw(RuntimeError("handler"))
        ),
    )
    transport.start()
    writer = right_sock.makefile("wb")
    writer.write(
        json.dumps(
            {
                "protocol": PROTOCOL_NAME,
                "kind": "event",
                "type": "boom",
                "payload": {},
            }
        ).encode()
        + b"\n"
    )
    writer.flush()
    time.sleep(0.3)
    assert called == ["boom"]
    assert transport.fatal_error is None
    transport.close()
    right_sock.close()


def test_read_error_is_fatal() -> None:
    class _BadStream(io.RawIOBase):
        def readline(self, *args: object) -> bytes:
            raise OSError("read failed")

    transport = JsonLineTransport(_BadStream(), io.BytesIO())
    transport.start()
    deadline = time.monotonic() + 5.0
    while transport.fatal_error is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert isinstance(transport.fatal_error, HostCrashError)
    transport.close()


def test_bind_handlers_and_send_failure_pops_pending() -> None:
    read = io.BytesIO()

    class _FailWrite(io.BytesIO):
        def write(self, data: bytes) -> int:
            raise OSError("write failed")

    transport = JsonLineTransport(read, _FailWrite())
    transport.bind_handlers(lambda t, p: {}, None)
    with pytest.raises(OSError, match="write failed"):
        transport.begin_request("x", {})
    transport.close()


def test_request_timeout_surfaces_fatal_first() -> None:
    # The peer socket stays open but never answers, so the request
    # times out on its own instead of failing on a wire error.
    left_sock, right_sock = socket.socketpair()
    transport = JsonLineTransport(
        left_sock.makefile("rb"),
        left_sock.makefile("wb"),
    )
    transport.start()
    with pytest.raises(TimeoutError):
        transport.request("x", {}, timeout=0.2)
    transport.close()
    right_sock.close()
