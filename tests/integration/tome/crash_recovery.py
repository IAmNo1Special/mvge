from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from pathlib import Path

import portalocker
import pytest

from mvge.tome.handle import TomeHandleFactory
from mvge.tome.types import TomeEntry, TomeEntryType


def _kill_pid(pid: int) -> None:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        windll = getattr(ctypes, "windll", None)
        if windll:
            kernel32 = windll.kernel32
            process_terminate = 0x0001
            handle = kernel32.OpenProcess(process_terminate, False, wintypes.DWORD(pid))
            if handle:
                try:
                    kernel32.TerminateProcess(handle, 1)
                finally:
                    kernel32.CloseHandle(handle)
    else:
        with suppress(OSError):
            os.kill(pid, signal.SIGKILL)


def test_kernel_lease_released_on_crash() -> None:
    """A killed holder releases its kernel lease; a new writer proceeds."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tome_dir = Path(tmpdir)
        factory = TomeHandleFactory(tome_dir)
        write = factory.create_tome(str(tome_dir), tome_id="t1")
        lock_path = str(write.path) + ".lock"

        child_code = (
            "import portalocker, os, time, sys\n"
            f"lock = portalocker.Lock(r'{lock_path}', timeout=30)\n"
            "lock.acquire()\n"
            "print(f'LOCKED {os.getpid()}', flush=True)\n"
            "time.sleep(30)\n"
        )

        proc = subprocess.Popen(
            [sys.executable, "-c", child_code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        child_pid: int | None = None
        try:
            assert proc.stdout is not None
            line = proc.stdout.readline().strip()
            assert line.startswith("LOCKED")
            child_pid = int(line.split()[1])

            # The live holder blocks a second writer with a short timeout.
            with pytest.raises(portalocker.exceptions.AlreadyLocked):
                portalocker.Lock(lock_path, timeout=0.5).acquire()

            _kill_pid(child_pid)
            proc.kill()
            proc.wait()
            time.sleep(0.5)

            # After the crash the lease is free: appending works.
            recovered = factory.open_write("t1")
            recovered.append(
                TomeEntry(
                    id="entry-1",
                    parent_id=None,
                    type=TomeEntryType.MESSAGE,
                    timestamp=time.time(),
                    payload={"text": "recovered"},
                )
            )

            entries = factory.get_entries("t1")
            assert len(entries) == 1
            assert entries[0].payload == {"text": "recovered"}
        finally:
            if child_pid is not None:
                _kill_pid(child_pid)
            if proc.poll() is None:
                proc.kill()
                proc.wait()


def test_torn_tail_repaired_by_resumer() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tome_dir = Path(tmpdir)
        factory = TomeHandleFactory(tome_dir)
        write = factory.create_tome(str(tome_dir), tome_id="t1")
        write.append(
            TomeEntry(
                id="entry-1",
                parent_id=None,
                type=TomeEntryType.MESSAGE,
                timestamp=time.time(),
                payload={"text": "kept"},
            )
        )

        with write.path.open("a", encoding="utf-8") as f:
            f.write('{"id": "partial", "type": "messa')

        truncated = factory.open_write("t1").repair_torn_tail()
        assert truncated > 0
        assert [e.id for e in factory.get_entries("t1")] == ["entry-1"]
