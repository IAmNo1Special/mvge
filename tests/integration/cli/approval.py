"""PTY integration tests for the interactive CLI approval presenter.

These prove the real terminal behavior end to end: a child Python process
with its stdio on a pty slave runs ``CliApprovalPresenter`` in prompt mode,
the parent drives it from the master side, and the child's decision is read
back. Covers the spec's CLI acceptance paths: prompt, EOF, and Ctrl-C
(Ctrl-C must resolve as denied, never a crash/traceback).
"""

from __future__ import annotations

import os
import select
import subprocess
import sys
import textwrap
import time

import pytest

try:
    import pty
except ImportError:
    # Windows has no pty module (pty -> tty -> termios is POSIX-only), and
    # every test in this module drives real terminal semantics through
    # pty.openpty() -- termios line discipline, ISIG, VEOF -- which cannot
    # be satisfied without it. Skip the whole module instead of breaking
    # collection. This skip goes away if a Windows pty ever ships in the
    # standard library.
    pytest.skip(
        "PTY integration tests require a POSIX pty (pty/tty/termios); "
        "unavailable on Windows",
        allow_module_level=True,
    )

_CTTY_PREAMBLE = textwrap.dedent(
    """\
    import asyncio
    import fcntl
    import os
    import sys
    import termios

    # Take the pty slave as our controlling terminal so ISIG (Ctrl-C)
    # delivers SIGINT to this process.
    os.setsid()
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    os.tcsetpgrp(0, os.getpgrp())
    """
)

# Same prompt, but the engine's contract is ``await presenter(request)``:
# the presenter must be directly callable.
_MAIN_CALLABLE = """\
    decision = asyncio.run(presenter(request))
    print(
        f"DECISION outcome={decision.outcome} scope={decision.scope} "
        f"reason={decision.reason_code}",
        flush=True,
    )
    """

_REQUEST_SETUP = textwrap.dedent(
    """\
    from mvge.cli.approval_presenter import CliApprovalPresenter
    from mvge.core.approval import ApprovalRequest

    request = ApprovalRequest(
        cast_id="call_8b17",
        spell_name="write",
        spell_identity={
            "name": "write",
            "source_kind": "builtin",
            "source_id": "",
            "runner_origin": "false",
            "read_only": "false",
        },
        arguments={"path": "/tmp/x.txt"},
        argument_digest="sha256:deadbeef",
        project_root="/workspace/mvge",
        tome_id="7f24c1",
        agent_name="coding_mvge",
    )
    presenter = CliApprovalPresenter(mode="prompt")
    """
)

_MAIN_SIMPLE = """\
    decision = asyncio.run(presenter.request_approval(request))
    print(
        f"DECISION outcome={decision.outcome} scope={decision.scope} "
        f"reason={decision.reason_code}",
        flush=True,
    )
    """

# Same prompt, but with an ambient SIGINT handler that returns normally
# instead of raising -- mirroring the REPL, whose SIGINT handler cancels the
# agent task and therefore cannot interrupt the prompt. Ctrl-C must still
# resolve as denied.
_MAIN_SWALLOWING_SIGINT = (
    """\
    import signal

    def _ambient(signum, frame):
        pass

    signal.signal(signal.SIGINT, _ambient)
"""
    + _MAIN_SIMPLE
)

# close() while a prompt is in flight must resolve it as denied: the prompt
# task is cancelled, which the gate turns into a deny.
_MAIN_CLOSE_RACE = """\
    async def _main():
        loop = asyncio.get_running_loop()
        loop.call_later(1.0, presenter.close)
        try:
            decision = await presenter.request_approval(request)
        except asyncio.CancelledError:
            print("DECISION outcome=deny scope=once reason=cancelled", flush=True)
            return
        print(
            f"DECISION outcome={decision.outcome} scope={decision.scope} "
            f"reason={decision.reason_code}",
            flush=True,
        )

    asyncio.run(_main())
    """

# The gate's abort race: cancelling the presenter future mid-prompt must deny
# the cast, which requires the event loop to stay responsive while the prompt
# waits (a blocking read would freeze the loop and the abort could never win).
_MAIN_ABORT_RACE = """\
    async def _main():
        task = asyncio.create_task(presenter.request_approval(request))
        await asyncio.sleep(1.0)
        task.cancel()
        try:
            decision = await task
        except asyncio.CancelledError:
            print("DECISION outcome=deny scope=once reason=aborted", flush=True)
            return
        print(
            f"DECISION outcome={decision.outcome} scope={decision.scope} "
            f"reason={decision.reason_code}",
            flush=True,
        )

    asyncio.run(_main())
    """


def _make_driver(main: str) -> str:
    return _CTTY_PREAMBLE + _REQUEST_SETUP + textwrap.dedent(main)


DRIVER = _make_driver(_MAIN_SIMPLE)
DRIVER_CALLABLE = _make_driver(_MAIN_CALLABLE)
DRIVER_SWALLOWING_SIGINT = _make_driver(_MAIN_SWALLOWING_SIGINT)
DRIVER_CLOSE_RACE = _make_driver(_MAIN_CLOSE_RACE)
DRIVER_ABORT_RACE = _make_driver(_MAIN_ABORT_RACE)

_TIMEOUT = 20.0


def _interact(
    steps: list[tuple[bytes, bytes]], driver: str = DRIVER
) -> tuple[str, int]:
    """Run the driver on a pty; for each (wait_for, send) step, wait until
    ``wait_for`` appears in the child output then write ``send``. Returns
    (decoded output, child return code)."""
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        [sys.executable, "-c", driver],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        close_fds=True,
    )
    os.close(slave)
    output = b""
    deadline = time.time() + _TIMEOUT

    def _drain_until(marker: bytes) -> None:
        nonlocal output
        while marker not in output and time.time() < deadline:
            ready, _, _ = select.select([master], [], [], 0.5)
            if not ready:
                if proc.poll() is not None:
                    break
                continue
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            output += chunk

    try:
        for wait_for, send in steps:
            _drain_until(wait_for)
            os.write(master, send)
        _drain_until(b"DECISION")
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        # Final drain after exit.
        while True:
            ready, _, _ = select.select([master], [], [], 0.2)
            if not ready:
                break
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            output += chunk
    finally:
        os.close(master)
    return output.decode(errors="replace"), proc.returncode or 0


def test_pty_prompt_allows_once() -> None:
    output, returncode = _interact([(b"Choice [1/2/3/4/5/m]:", b"1\n")])
    assert returncode == 0
    assert "Approve spell cast?" in output
    assert "Spell:     write (builtin)" in output
    assert "DECISION outcome=allow scope=once reason=user" in output


def test_pty_prompt_denies() -> None:
    output, returncode = _interact([(b"Choice [1/2/3/4/5/m]:", b"2\n")])
    assert returncode == 0
    assert "DECISION outcome=deny scope=once reason=user" in output


def test_pty_engine_callable_contract() -> None:
    # The engine invokes the presenter as ``await presenter(request)``;
    # prove that path works on a real terminal too.
    output, returncode = _interact(
        [(b"Choice [1/2/3/4/5/m]:", b"1\n")], driver=DRIVER_CALLABLE
    )
    assert returncode == 0
    assert "Traceback" not in output
    assert "DECISION outcome=allow scope=once reason=user" in output


def test_pty_always_allow_needs_second_confirmation() -> None:
    output, returncode = _interact(
        [
            (b"Choice [1/2/3/4/5/m]:", b"3\n"),
            (b"Type ALLOW to confirm", b"allow\n"),
        ]
    )
    assert returncode == 0
    assert "Confirm persistent grant" in output
    assert "DECISION outcome=allow scope=spell reason=user" in output


def test_pty_eof_denies() -> None:
    # Ctrl-D (VEOF) on an empty canonical-mode buffer reads as EOF.
    output, returncode = _interact([(b"Choice [1/2/3/4/5/m]:", b"\x04")])
    assert returncode == 0
    assert "Traceback" not in output
    assert "DECISION outcome=deny scope=once reason=user" in output


def test_pty_ctrl_c_denies_without_traceback() -> None:
    # Ctrl-C (VINTR) raises SIGINT in the child; the prompt must resolve as
    # denied with a clean return, never a crash.
    output, returncode = _interact([(b"Choice [1/2/3/4/5/m]:", b"\x03")])
    assert returncode == 0
    assert "Traceback" not in output
    assert "DECISION outcome=deny scope=once reason=user" in output


def test_pty_ctrl_c_denies_despite_ambient_handler() -> None:
    # With a swallowing ambient SIGINT handler installed (the REPL case),
    # Ctrl-C must still deny the prompt: the presenter handles SIGINT on the
    # loop for the prompt duration and restores the handler afterwards.
    output, returncode = _interact(
        [(b"Choice [1/2/3/4/5/m]:", b"\x03")],
        driver=DRIVER_SWALLOWING_SIGINT,
    )
    assert returncode == 0
    assert "Traceback" not in output
    assert "DECISION outcome=deny scope=once reason=user" in output


def test_pty_close_during_prompt_cancels_cleanly() -> None:
    # close() (unbind/shutdown) while a prompt is in flight resolves it as
    # denied via task cancellation: no hang, no traceback.
    output, returncode = _interact(
        [],
        driver=DRIVER_CLOSE_RACE,
    )
    assert returncode == 0
    assert "Traceback" not in output
    assert "DECISION outcome=deny scope=once reason=cancelled" in output


def test_pty_abort_race_denies_during_prompt() -> None:
    # The gate's abort race: cancelling the presenter future mid-prompt must
    # deny the cast. Proves the event loop stays responsive while the prompt
    # waits -- with a blocking read the sleep/cancel below could never run
    # until the user answered.
    output, returncode = _interact(
        [],
        driver=DRIVER_ABORT_RACE,
    )
    assert returncode == 0
    assert "Traceback" not in output
    assert "DECISION outcome=deny scope=once reason=aborted" in output
