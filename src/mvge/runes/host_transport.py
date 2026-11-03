"""Thread-based NDJSON transport for the Phase 4 host wire.

One transport wraps one stdio pair (engine side: a subprocess
pipes; host side: ``sys.stdin.buffer`` / ``sys.stdout.buffer``).
It is deliberately thread-based, not asyncio-subprocess-based:
the same code runs on POSIX and Windows with no platform branch,
and a blocking spell execution in a worker thread can never stall
the reader that carries abort messages and streaming updates.

Reader thread: reads one line at a time, enforces
``MAX_MESSAGE_BYTES``, decodes and validates the envelope, and
dispatches:

- responses complete the pending future for their ``id``;
- requests are handled on a worker thread and the worker sends
  the response, so a slow handler never blocks the reader;
- events go to the ``on_event`` callback on the reader thread,
  so event handlers must be fast (queue, never block).

A fatal wire error (oversize line, malformed JSON, bad envelope,
EOF) fails every pending future with that error and is recorded
on ``fatal_error`` so callers waiting on events can surface it.
EOF is a host crash on the engine side: the process died.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import threading
import time
from collections.abc import Callable
from typing import Any

from mvge.runes.host_protocol import (
    MAX_MESSAGE_BYTES,
    HostCrashError,
    HostError,
    HostProtocolError,
    HostRemoteError,
    OversizeMessageError,
    decode_message,
    encode_message,
)

RequestHandler = Callable[[str, dict[str, Any]], dict[str, Any]]
EventHandler = Callable[[str, dict[str, Any]], None]


class JsonLineTransport:
    """Bidirectional NDJSON transport over binary stdio streams."""

    def __init__(
        self,
        read_stream: Any,
        write_stream: Any,
        *,
        on_request: RequestHandler | None = None,
        on_event: EventHandler | None = None,
        max_message_bytes: int = MAX_MESSAGE_BYTES,
    ) -> None:
        self._read_stream = read_stream
        self._write_stream = write_stream
        self._on_request = on_request
        self._on_event = on_event
        self._max_message_bytes = max_message_bytes
        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: dict[int, concurrent.futures.Future[dict[str, Any]]] = {}
        self._next_id = 1
        self._fatal_error: HostError | None = None
        self._fatal_lock = threading.Lock()
        self._reader: threading.Thread | None = None
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=16, thread_name_prefix="host-wire"
        )
        self._closed = False

    def bind_handlers(
        self,
        on_request: RequestHandler | None,
        on_event: EventHandler | None,
    ) -> None:
        """Set handlers before start() (host process wires itself up)."""
        self._on_request = on_request
        self._on_event = on_event

    @property
    def fatal_error(self) -> HostError | None:
        with self._fatal_lock:
            return self._fatal_error

    def start(self) -> None:
        """Start the reader thread. Idempotent."""
        if self._reader is not None:
            return
        self._reader = threading.Thread(
            target=self._read_loop, name="host-wire-reader", daemon=True
        )
        self._reader.start()

    def close(self) -> None:
        """Stop accepting work and fail pending futures."""
        self._closed = True
        self._fail_all_pending(
            HostCrashError("Host transport closed")
            if self.fatal_error is None
            else self.fatal_error
        )
        self._executor.shutdown(wait=False, cancel_futures=True)

    def send_message(self, message: dict[str, Any]) -> None:
        raw = encode_message(message)
        with self._write_lock:
            self._write_stream.write(raw)
            self._write_stream.flush()

    def send_event(self, msg_type: str, payload: dict[str, Any]) -> None:
        """Send a no-response event. Raises on oversize, never blocks."""
        from mvge.runes.host_protocol import PROTOCOL_NAME

        self.send_message(
            {
                "protocol": PROTOCOL_NAME,
                "kind": "event",
                "type": msg_type,
                "payload": payload,
            }
        )

    def begin_request(
        self, msg_type: str, payload: dict[str, Any]
    ) -> tuple[int, concurrent.futures.Future[dict[str, Any]]]:
        """Send a request and return its id and response future."""
        from mvge.runes.host_protocol import PROTOCOL_NAME

        with self._pending_lock:
            request_id = self._next_id
            self._next_id += 1
            future: concurrent.futures.Future[dict[str, Any]] = (
                concurrent.futures.Future()
            )
            self._pending[request_id] = future
        try:
            self.send_message(
                {
                    "protocol": PROTOCOL_NAME,
                    "kind": "request",
                    "id": request_id,
                    "type": msg_type,
                    "payload": payload,
                }
            )
        except Exception:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise
        return request_id, future

    def send_request(
        self, msg_type: str, payload: dict[str, Any], timeout: float = 30.0
    ) -> dict[str, Any]:
        """Alias for ``request`` (the name HostRuneAPI uses)."""
        return self.request(msg_type, payload, timeout)

    def request(
        self, msg_type: str, payload: dict[str, Any], timeout: float
    ) -> dict[str, Any]:
        """Send a request and wait for its response payload.

        Polls in short slices so a fatal wire error that arrived
        before this request was registered (e.g. a malformed line
        sent right after hello) surfaces instead of hanging until
        the timeout.
        """
        _, future = self.begin_request(msg_type, payload)
        deadline = time.monotonic() + timeout
        while True:
            try:
                return future.result(timeout=0.1)
            except concurrent.futures.TimeoutError:
                fatal = self.fatal_error
                if fatal is not None:
                    raise fatal from None
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Host request '{msg_type}' timed out after {timeout}s"
                    ) from None

    def _set_fatal(self, error: HostError) -> None:
        with self._fatal_lock:
            if self._fatal_error is None:
                self._fatal_error = error
        self._fail_all_pending(error)

    def _fail_all_pending(self, error: HostError) -> None:
        with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for future in pending:
            if not future.done():
                future.set_exception(error)

    def _send_response(
        self,
        request_id: int,
        ok: bool,
        payload: dict[str, Any] | None,
        error: dict[str, Any] | None,
    ) -> None:
        from mvge.runes.host_protocol import PROTOCOL_NAME

        # The peer is gone or the message is oversize; the caller
        # already holds the fatal error if one exists.
        with contextlib.suppress(Exception):
            self.send_message(
                {
                    "protocol": PROTOCOL_NAME,
                    "kind": "response",
                    "id": request_id,
                    "ok": ok,
                    "payload": payload,
                    "error": error,
                }
            )

    def _handle_request(
        self, request_id: int, msg_type: str, payload: dict[str, Any]
    ) -> None:
        if self._on_request is None:
            self._send_response(
                request_id,
                False,
                None,
                {"kind": "unsupported", "message": f"No handler for '{msg_type}'"},
            )
            return
        try:
            result = self._on_request(msg_type, payload)
            self._send_response(request_id, True, result, None)
        except Exception as exc:  # noqa: BLE001 - remote error payload
            kind = getattr(exc, "kind", None) or type(exc).__name__
            if isinstance(exc, HostRemoteError):
                kind = exc.kind
            self._send_response(
                request_id, False, None, {"kind": kind, "message": str(exc)}
            )

    def _read_loop(self) -> None:
        try:
            while True:
                raw = self._read_stream.readline()
                if not raw:
                    self._set_fatal(
                        HostCrashError("Host stdio closed (host process died)")
                    )
                    return
                if isinstance(raw, str):
                    raw = raw.encode("utf-8")
                # Length check before decode: a hostile or broken
                # peer must not make us buffer an unbounded line.
                # readline itself bounds by newline, so a line with
                # no newline still grows; the check below rejects
                # it as soon as it arrives whole.
                if len(raw) > self._max_message_bytes + 1:
                    self._set_fatal(
                        OversizeMessageError(
                            "Wire message exceeds the "
                            f"{self._max_message_bytes}-byte limit"
                        )
                    )
                    return
                try:
                    message = decode_message(raw.rstrip(b"\n"))
                except HostProtocolError as exc:
                    self._set_fatal(exc)
                    return
                kind = message.get("kind")
                if kind == "response":
                    request_id = message.get("id")
                    if not isinstance(request_id, int):
                        continue
                    with self._pending_lock:
                        future = self._pending.pop(request_id, None)
                    if future is not None and not future.done():
                        if message.get("ok"):
                            future.set_result(message.get("payload") or {})
                        else:
                            error = message.get("error") or {}
                            future.set_exception(
                                HostRemoteError(
                                    str(error.get("kind", "error")),
                                    str(error.get("message", "host error")),
                                )
                            )
                elif kind == "request":
                    request_id = message.get("id")
                    msg_type = str(message.get("type", ""))
                    payload = message.get("payload") or {}
                    if request_id is None:
                        continue
                    self._executor.submit(
                        self._handle_request, request_id, msg_type, payload
                    )
                elif kind == "event":
                    if self._on_event is not None:
                        # Event handlers must not kill the reader.
                        with contextlib.suppress(Exception):
                            self._on_event(
                                str(message.get("type", "")),
                                message.get("payload") or {},
                            )
        except (OSError, ValueError) as exc:
            self._set_fatal(HostCrashError(f"Host stdio failed: {exc}"))
