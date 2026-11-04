#!/usr/bin/env python3
"""Prove one wrapped deployment, against its own workspace and backend.

Phase 5b is a deployment posture, proven per deployment, not by
the unit suite alone. This script is the proof: it builds the
operator's wrapping policy, runs probes inside the wrapped argv,
and fails loudly on any confinement failure. A deployment whose
proof has not been run (or has failed) must not be described
as confined.

Usage::

    uv run python scripts/verify_wrapped_deployment.py \
      --workspace /path/to/workspace \
      --backend bwrap \
      --write-root data

Checks (see docs/agents/rune-whole-process-wrapping-5b.md for
what each PASS proves):

- secret-read-denied: a probe cannot read a secret file placed
  outside the mounts.
- outside-write-denied: a probe cannot write outside the mounts.
- workspace-root-write-denied: a probe cannot write to the
  workspace root, unless the root is a declared write root.
- declared-root-write: a probe CAN write to each declared
  write root, and the file is visible from the host afterwards.
- credential-env-absent: no credential-shaped environment
  variable is visible inside the wrap.
- network-denied: a probe cannot reach a TCP listener this
  script controls (loopback, plus the host's outward address
  for Docker's bridge). The listener matters: probing a dead
  port proves nothing, because a refused connection counts as
  "denied" even with full network. When --allow-network is
  passed, the same probe must succeed instead (bwrap: loopback
  on the host network; Docker: host address over the bridge --
  a failure there is a WARNING, not a FAIL, because host
  firewall rules can block bridge-to-host traffic without any
  confinement defect).
- host-proc-hidden: a probe cannot read this script's own
  /proc/<pid>/cmdline inside the wrap. With a fresh procfs
  (bwrap) or a container PID namespace (Docker), host PIDs
  are invisible, so the engine's environment cannot leak
  through /proc/<engine-pid>/environ.
- no-credential-files-in-mounts: no credential-named file
  exists under any mount. Pass
  --allow-workspace-credential-files only to record an explicit
  operator decision that a workspace credential file is
  intended for the rune; that check then prints WARNING
  instead of PASS, and the decision is visible in the output.

Exit code is 0 only when every check passes (or is explicitly
allowed as a WARNING). Any FAIL exits non-zero and prints the
wrapped argv used, so the operator can re-run any probe by hand.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from mvge.runes.host_protocol import is_credential_env_name
    from mvge.runes.wrapping import (
        WrappingPolicy,
        credential_files_in_mounts,
        wrap_host_command,
    )
except ImportError:  # Direct python3 run without the venv: use repo src/.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from mvge.runes.host_protocol import is_credential_env_name
    from mvge.runes.wrapping import (
        WrappingPolicy,
        credential_files_in_mounts,
        wrap_host_command,
    )


def _listener_and_host_ip() -> tuple[socket.socket, int, str | None]:
    """A listener the script controls, plus the host's outward address.

    The listener binds all interfaces so a Docker bridge probe
    can reach it via the host address; a bwrap host-network
    probe reaches it via loopback. The outward address comes
    from a UDP connect that sends no packets.
    """
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("0.0.0.0", 0))
    listener.listen(5)
    port = listener.getsockname()[1]
    host_ip: str | None = None
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            host_ip = probe.getsockname()[0]
        finally:
            probe.close()
    except OSError:
        host_ip = None
    return listener, port, host_ip


def _network_probe_code(port: int, host_ip: str | None) -> str:
    """Probe code: exit 0 iff any controlled listener address connects."""
    targets = [f"('127.0.0.1', {port})"]
    if host_ip:
        targets.append(f"({host_ip!r}, {port})")
    return (
        "import socket,sys\n"
        "for target in [" + ", ".join(targets) + "]:\n"
        "    try:\n"
        "        s = socket.socket()\n"
        "        s.settimeout(3)\n"
        "        s.connect(target)\n"
        "        print('CONNECTED')\n"
        "        sys.exit(0)\n"
        "    except OSError:\n"
        "        continue\n"
        "sys.exit(1)\n"
    )


def _run_probe(
    policy: WrappingPolicy, code: str, *, timeout: float
) -> subprocess.CompletedProcess[str]:
    argv = wrap_host_command([sys.executable, "-c", code], policy)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        cwd=policy.workspace_root,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--backend", default="bwrap", choices=["bwrap", "docker"])
    parser.add_argument(
        "--write-root",
        action="append",
        default=[],
        help="Workspace-relative writable root; repeatable.",
    )
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--rune-dir", type=Path, default=None)
    parser.add_argument("--allow-workspace-credential-files", action="store_true")
    parser.add_argument("--probe-timeout", type=float, default=60.0)
    args = parser.parse_args(argv)

    workspace = args.workspace.resolve()
    if not workspace.is_dir():
        print(f"FAIL workspace-missing: {workspace} is not a directory")
        return 2
    write_roots = tuple(workspace / root for root in args.write_root)
    for root in write_roots:
        if not root.is_dir():
            print(f"FAIL write-root-missing: declared root {root} does not exist")
            return 2
    try:
        policy = WrappingPolicy(
            enabled=True,
            backend=args.backend,
            workspace_root=workspace,
            rune_dir=args.rune_dir,
            write_roots=write_roots,
            allow_network=args.allow_network,
        )
        wrapped_argv = wrap_host_command([sys.executable, "-c", "pass"], policy)
    except Exception as exc:
        print(f"FAIL wrapping-unavailable: {exc}")
        return 2

    print("Wrapped argv:")
    print("  " + shlex.join(wrapped_argv))
    print(f"Policy: {json.dumps(policy.describe(), indent=2)}")

    failures = 0
    warnings = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        status = "PASS" if ok else "FAIL"
        if not ok:
            failures += 1
        print(f"{status} {name}" + (f": {detail}" if detail else ""))

    secret_dir = Path(tempfile.mkdtemp(prefix="mvge-wrapping-proof-"))
    secret = secret_dir / "secret.txt"
    secret.write_text("wrapping-proof-secret\n", encoding="utf-8")
    try:
        read_code = (
            "import pathlib,sys\n"
            f"sys.stdout.write(pathlib.Path({str(secret)!r}).read_text())\n"
        )
        result = _run_probe(policy, read_code, timeout=args.probe_timeout)
        check(
            "secret-read-denied",
            result.returncode != 0 and "wrapping-proof-secret" not in result.stdout,
            result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "",
        )

        outside_target = secret_dir / "outside.txt"
        write_code = (
            f"import pathlib\npathlib.Path({str(outside_target)!r}).write_text('x')\n"
        )
        result = _run_probe(policy, write_code, timeout=args.probe_timeout)
        check(
            "outside-write-denied",
            result.returncode != 0 and not outside_target.exists(),
        )

        root_target = workspace / "wrapping-proof-root.txt"
        root_code = (
            f"import pathlib\npathlib.Path({str(root_target)!r}).write_text('x')\n"
        )
        result = _run_probe(policy, root_code, timeout=args.probe_timeout)
        if workspace in policy.write_roots:
            check(
                "workspace-root-write",
                result.returncode == 0 and root_target.exists(),
                "workspace root is a declared write root, so writes must succeed",
            )
            root_target.unlink(missing_ok=True)
        else:
            check(
                "workspace-root-write-denied",
                result.returncode != 0 and not root_target.exists(),
            )

        for root in policy.write_roots:
            if root == workspace:
                continue
            target = root / "wrapping-proof.txt"
            code = f"import pathlib\npathlib.Path({str(target)!r}).write_text('ok')\n"
            result = _run_probe(policy, code, timeout=args.probe_timeout)
            ok = result.returncode == 0 and (
                target.exists() and target.read_text(encoding="utf-8") == "ok"
            )
            check(f"declared-root-write:{root}", ok, result.stderr.strip())
            target.unlink(missing_ok=True)

        env_code = "import json,os\nprint(json.dumps(sorted(os.environ)))\n"
        result = _run_probe(policy, env_code, timeout=args.probe_timeout)
        seen: list[str] = []
        if result.returncode == 0:
            try:
                seen = json.loads(result.stdout.strip().splitlines()[-1])
            except (ValueError, IndexError):
                seen = ["<unparsable probe output>"]
        leaked = [name for name in seen if is_credential_env_name(name)]
        check(
            "credential-env-absent",
            result.returncode == 0 and not leaked,
            ",".join(leaked),
        )

        listener, port, host_ip = _listener_and_host_ip()
        try:
            net_code = _network_probe_code(port, host_ip)
            result = _run_probe(policy, net_code, timeout=args.probe_timeout)
            if args.allow_network:
                if args.backend == "docker" and result.returncode != 0:
                    warnings += 1
                    print(
                        "WARNING network-allowed: probe could not reach "
                        "the controlled listener over the bridge; host "
                        "firewall rules can block bridge-to-host traffic, "
                        "so this is not counted as a confinement failure"
                    )
                else:
                    check("network-allowed", result.returncode == 0)
            else:
                check("network-denied", result.returncode != 0)
        finally:
            listener.close()

        proc_code = (
            "import pathlib,sys\n"
            f"path = pathlib.Path('/proc/{os.getpid()}/cmdline')\n"
            "try:\n"
            "    sys.stdout.write(path.read_text())\n"
            "except OSError:\n"
            "    sys.exit(1)\n"
        )
        result = _run_probe(policy, proc_code, timeout=args.probe_timeout)
        check(
            "host-proc-hidden",
            "verify_wrapped_deployment" not in result.stdout,
            "host process cmdline visible inside the wrap",
        )

        mounts = [m.host_path for m in policy.mounts()]
        found = credential_files_in_mounts(mounts)
        if found:
            detail = ", ".join(str(p) for p in found)
            if args.allow_workspace_credential_files:
                warnings += 1
                print(
                    "WARNING no-credential-files-in-mounts: operator allowed "
                    f"workspace credential files: {detail}"
                )
            else:
                check("no-credential-files-in-mounts", False, detail)
        else:
            check("no-credential-files-in-mounts", True)
    finally:
        for leftover in secret_dir.iterdir():
            leftover.unlink(missing_ok=True)
        secret_dir.rmdir()

    if failures:
        print(f"RESULT FAIL: {failures} check(s) failed, {warnings} warning(s)")
        return 1
    print(f"RESULT PASS: wrapped deployment proven ({warnings} warning(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
