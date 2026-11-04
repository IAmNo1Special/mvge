"""Phase 5b: whole-process wrapping as an opt-in deployment posture.

Wrapping runs a Phase 4 host process inside an OS/container
boundary (bwrap on Linux, Docker where available) with
workspace-only mounts, a scrubbed environment, and an
all-or-none network policy. Unwrapped is the default: wrapping
requires an explicit ``WrappingPolicy(enabled=True, ...)``.

What this phase does NOT claim: wrapping is proven per
deployment (see ``scripts/verify_wrapped_deployment.py`` and
``docs/agents/rune-whole-process-wrapping-5b.md``), not by
this suite alone; Windows without Docker has no wrapping
backend (write-confined only via the Phase 5a ACL ladder, with
a read/network gap); network is all-or-none in v1, with no
per-destination egress allowlist; and the one-time escalation
deferred from Phase 5a (F3) is still deferred.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from mvge.runes.host import HostSession
from mvge.runes.host_classification import classify_manifest
from mvge.runes.manifest import load_manifest
from mvge.runes.pinning import (
    PIN_FILENAME,
    RunePin,
    compute_rune_tree_hash,
    write_pin,
)
from mvge.runes.rune_runner import RuneRunner
from mvge.runes.subprocess_policy import SubprocessPolicy, policy_for_spell
from mvge.runes.types import RuneManifest, SpellDefinition
from mvge.runes.wrapping import (
    WRAPPING_UNAVAILABLE,
    BwrapWrappingBackend,
    DockerWrappingBackend,
    WrappingPolicy,
    WrappingUnavailableError,
    assert_no_credential_env,
    credential_files_in_mounts,
    select_wrapping_backend,
    validate_wrapping_policy,
    wrap_host_command,
)


class _FakeBwrapBackend:
    """A stand-in bwrap backend for wiring tests.

    Building the real bwrap argv is covered by direct
    ``BwrapWrappingBackend`` tests (pure, no tool needed).
    Wiring tests (does the session command go through the
    wrapper at all) must not depend on bwrap being installed:
    the suite is hermetic, and a missing tool must skip live
    tests, not fail wiring tests.
    """

    name = "bwrap"
    enforcement = "full"

    def available(self) -> bool:
        return True

    def build_argv(self, argv: list[str], policy: WrappingPolicy) -> list[str]:
        return ["bwrap", "--", *argv]


def _policy(
    tmp_path: Path,
    *,
    enabled: bool = True,
    backend: str | None = "bwrap",
    allow_network: bool = False,
    write_roots: tuple[Path, ...] = (),
    rune_dir: Path | None = None,
) -> WrappingPolicy:
    return WrappingPolicy(
        enabled=enabled,
        backend=backend,
        workspace_root=tmp_path,
        rune_dir=rune_dir,
        write_roots=write_roots,
        allow_network=allow_network,
    )


def _write_rune(parent: Path, name: str, body: str) -> Path:
    rune_dir = parent / name
    rune_dir.mkdir(parents=True, exist_ok=True)
    (rune_dir / "manifest.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "0.1.0",
                "description": "wrapping test rune",
                "entry_point": "rune.py",
            }
        ),
        encoding="utf-8",
    )
    (rune_dir / "rune.py").write_text(body, encoding="utf-8")
    pin = RunePin(source_kind="local", tree_hash=compute_rune_tree_hash(rune_dir))
    write_pin(rune_dir, pin)
    assert (rune_dir / PIN_FILENAME).is_file()
    return rune_dir


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


# ---------------------------------------------------------------- opt-in


def test_unwrapped_is_the_default(tmp_path: Path) -> None:
    policy = WrappingPolicy(workspace_root=tmp_path)
    assert policy.enabled is False
    argv = ["python", "-m", "mvge.runes.host_server"]
    assert wrap_host_command(argv, policy) == argv
    assert wrap_host_command(argv, None) == argv


def test_wrapping_requires_an_explicit_backend(tmp_path: Path) -> None:
    policy = WrappingPolicy(enabled=True, backend=None, workspace_root=tmp_path)
    with pytest.raises(WrappingUnavailableError, match=WRAPPING_UNAVAILABLE):
        validate_wrapping_policy(policy)


def test_unknown_backend_refuses_fail_closed(tmp_path: Path) -> None:
    policy = _policy(tmp_path, backend="no-such-backend")
    with pytest.raises(WrappingUnavailableError, match=WRAPPING_UNAVAILABLE):
        validate_wrapping_policy(policy)
    with pytest.raises(WrappingUnavailableError):
        select_wrapping_backend("no-such-backend")


def test_unavailable_named_backend_never_falls_back_to_unwrapped(
    tmp_path: Path,
) -> None:
    # Docker is not installed in this sandbox; asking for it must
    # refuse, never silently run the host unwrapped.
    if shutil.which("docker") is not None:
        pytest.skip("docker is installed here; the unavailable case cannot run")
    policy = _policy(tmp_path, backend="docker")
    with pytest.raises(WrappingUnavailableError, match=WRAPPING_UNAVAILABLE):
        wrap_host_command(["python", "-m", "x"], policy)


# ---------------------------------------------------------------- mounts


def test_workspace_is_mounted_read_only_by_default(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    mounts = policy.mounts()
    workspace_mounts = [m for m in mounts if m.host_path == tmp_path.resolve()]
    assert len(workspace_mounts) == 1
    assert workspace_mounts[0].mode == "ro"


def test_declared_write_root_is_the_only_writable_mount(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    policy = _policy(tmp_path, write_roots=(data,))
    writable = [m for m in policy.mounts() if m.mode == "rw"]
    assert [m.host_path for m in writable] == [data.resolve()]
    assert policy.allows_write(data / "x") is True
    assert policy.allows_write(tmp_path / "other" / "x") is False


def test_write_root_outside_the_workspace_is_refused(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-wrapping-test"
    with pytest.raises((ValueError, WrappingUnavailableError)):
        _policy(tmp_path, write_roots=(outside,))


def test_home_and_agents_dirs_are_never_mounts(tmp_path: Path) -> None:
    home = Path.home()
    for forbidden in (home, home / ".agents", home / ".ssh", home / ".aws"):
        policy = WrappingPolicy(
            enabled=True,
            backend="bwrap",
            workspace_root=tmp_path,
            extra_read_only_mounts=(forbidden,),
        )
        with pytest.raises(WrappingUnavailableError):
            validate_wrapping_policy(policy)


def test_ancestor_of_home_is_a_forbidden_mount() -> None:
    from mvge.runes.wrapping import is_forbidden_mount

    # A genuine ancestor of home, not a drive root: on Windows
    # runners the checkout drive and the home drive differ, so
    # Path("/") is not an ancestor of home there.
    assert is_forbidden_mount(Path.home().parent) is True
    assert is_forbidden_mount(Path.home() / ".agents") is True


def test_network_description_names_the_v1_gap(tmp_path: Path) -> None:
    assert (
        _policy(tmp_path, backend="docker").network_description
        == "none (docker --network none)"
    )
    assert (
        _policy(tmp_path, backend="bwrap", allow_network=True).network_description
        == "host network (no allowlist)"
    )
    assert (
        WrappingPolicy(
            workspace_root=tmp_path, enabled=True, allow_network=True
        ).network_description
        == "unrestricted (no allowlist)"
    )


def test_workspace_as_its_own_write_root_mounts_writable_once(
    tmp_path: Path,
) -> None:
    policy = _policy(tmp_path, write_roots=(tmp_path,))
    mounts = policy.mounts()
    assert len(mounts) == 1
    assert mounts[0].mode == "rw"
    assert policy.allows_write(tmp_path / "x") is True


def test_credential_scan_skips_non_directories(tmp_path: Path) -> None:
    stray = tmp_path / ".env"
    stray.write_text("x\n", encoding="utf-8")
    assert credential_files_in_mounts([stray]) == []


def test_workspace_must_not_be_home_or_above_it(tmp_path: Path) -> None:
    home = Path.home()
    with pytest.raises(ValueError, match="home"):
        WrappingPolicy(enabled=True, backend="bwrap", workspace_root=home)
    with pytest.raises(ValueError, match="home"):
        WrappingPolicy(enabled=True, backend="bwrap", workspace_root=home.parent)


def test_rune_dir_is_mounted_read_only(tmp_path: Path) -> None:
    rune_dir = tmp_path / "some-rune"
    rune_dir.mkdir()
    policy = _policy(tmp_path, rune_dir=rune_dir)
    mounts = [m for m in policy.mounts() if m.host_path == rune_dir.resolve()]
    assert len(mounts) == 1
    assert mounts[0].mode == "ro"


# ---------------------------------------------------------------- credentials


def test_credential_env_names_are_refused_after_scrubbing() -> None:
    for name in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "MY_SERVICE_TOKEN",
        "DB_PASSWORD",
        "AWS_SECRET_ACCESS_KEY",
    ):
        with pytest.raises(WrappingUnavailableError):
            assert_no_credential_env({name: "x", "PATH": "/usr/bin"})
    # PATH and other non-credential names pass through.
    assert_no_credential_env({"PATH": "/usr/bin", "HOME": "/tmp/nope"})


def test_credential_files_in_a_mount_are_reported(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / ".env").write_text("OPENAI_API_KEY=x\n", encoding="utf-8")
    (workspace / "notes.txt").write_text("hello\n", encoding="utf-8")
    found = credential_files_in_mounts([workspace])
    assert workspace / ".env" in found
    assert workspace / "notes.txt" not in found


def test_bwrap_argv_carries_no_credential_env_flag(tmp_path: Path) -> None:
    backend = BwrapWrappingBackend()
    policy = _policy(tmp_path)
    argv = backend.build_argv(["python3", "-c", "pass"], policy)
    assert "--unshare-net" in argv
    assert not any("API_KEY" in part for part in argv)
    assert "--" in argv


def test_bwrap_argv_mounts_fresh_proc_not_host_proc(tmp_path: Path) -> None:
    backend = BwrapWrappingBackend()
    argv = backend.build_argv(["python3", "-c", "pass"], _policy(tmp_path))
    # --proc is added only when /proc exists on the build
    # machine (wrapping.py gates on it): on Windows/macOS it
    # is absent, and bwrap is Linux-only anyway, so a missing
    # --proc there can never reach a real wrap.
    if Path("/proc").exists():
        assert "--proc" in argv
        assert argv[argv.index("--proc") + 1] == "/proc"
    else:
        assert "--proc" not in argv
    # No host /proc ro-bind anywhere in the bwrap flags, on
    # every platform: this half is meaningful unconditionally.
    flags = argv[: argv.index("--")]
    assert not any(
        flags[i] == "--ro-bind" and flags[i + 1] == "/proc"
        for i in range(len(flags) - 1)
    )


def test_docker_argv_has_no_env_passthrough_and_identity_mounts(
    tmp_path: Path,
) -> None:
    backend = DockerWrappingBackend()
    policy = _policy(tmp_path, backend="docker")
    argv = backend.build_argv([sys.executable, "-m", "mvge.runes.host_server"], policy)
    assert argv[:3] == ["docker", "run", "--rm"]
    assert "--network" in argv
    assert "none" in argv
    assert "-e" not in argv
    assert "--env" not in argv
    assert "python3" in argv
    assert "-m" in argv
    assert "mvge.runes.host_server" in argv
    # Identity mount: the workspace path is the same inside the container,
    # so paths carried on the host wire still resolve.
    assert f"{tmp_path.resolve()}:{tmp_path.resolve()}:ro" in argv


def test_docker_argv_maps_a_windows_interpreter_path(tmp_path: Path) -> None:
    backend = DockerWrappingBackend()
    policy = _policy(tmp_path, backend="docker")
    argv = backend.build_argv(
        [r"C:\hostedtoolcache\python.exe", "-m", "mvge.runes.host_server"], policy
    )
    assert "python3" in argv
    assert not any("hostedtoolcache" in part for part in argv)


def test_docker_network_allowed_means_default_bridge_not_allowlist(
    tmp_path: Path,
) -> None:
    backend = DockerWrappingBackend()
    policy = _policy(tmp_path, backend="docker", allow_network=True)
    argv = backend.build_argv(["python3", "-c", "pass"], policy)
    assert "none" not in argv
    # All-or-none in v1: allowing network does not add any
    # per-destination restriction, and the policy says so.
    assert policy.network_description == "default bridge network (no allowlist)"


def test_bwrap_network_flag_follows_the_policy(tmp_path: Path) -> None:
    backend = BwrapWrappingBackend()
    denied = backend.build_argv(
        ["python3", "-c", "pass"], _policy(tmp_path, allow_network=False)
    )
    assert "--unshare-net" in denied
    allowed = backend.build_argv(
        ["python3", "-c", "pass"], _policy(tmp_path, allow_network=True)
    )
    assert "--unshare-net" not in allowed


# ---------------------------------------------------------------- composition


def test_wrapping_policy_derives_from_the_5a_policy(tmp_path: Path) -> None:
    spell = SpellDefinition(
        name="s", description="s", read_only=False, write_roots=("data",)
    )
    ladder = policy_for_spell(
        spell, tmp_path, declared_capabilities=["spell", "filesystem"]
    )
    assert isinstance(ladder, SubprocessPolicy)
    wrapped = WrappingPolicy.from_subprocess_policy(ladder, backend="bwrap")
    assert wrapped.workspace_root == tmp_path.resolve()
    assert wrapped.write_roots == ladder.write_roots
    assert wrapped.allow_network == ladder.allow_network
    assert wrapped.enabled is True


def test_read_only_5a_policy_wraps_with_no_writable_mount(tmp_path: Path) -> None:
    spell = SpellDefinition(
        name="s", description="s", read_only=True, write_roots=("data",)
    )
    ladder = policy_for_spell(
        spell, tmp_path, declared_capabilities=["spell", "filesystem"]
    )
    wrapped = WrappingPolicy.from_subprocess_policy(ladder, backend="bwrap")
    assert wrapped.write_roots == ()
    assert all(m.mode == "ro" for m in wrapped.mounts())


def test_wrapping_does_not_reclassify_the_in_process_tier(
    tmp_path: Path,
) -> None:
    manifest = RuneManifest(
        name="approval-rune",
        version="0.1.0",
        description="approval",
        entry_point="rune.py",
        hosting="in-process",
    )
    verdict = classify_manifest(manifest)
    assert verdict.hostable is False
    # Wrapping validation refuses an in-process-only rune outright:
    # wrapping cannot be used to claim confinement for a rune the
    # host itself will not host.
    policy = _policy(tmp_path)
    with pytest.raises(WrappingUnavailableError):
        validate_wrapping_policy(policy, manifest=manifest)


def test_wrapped_host_command_keeps_the_host_server_entry(
    tmp_path: Path,
) -> None:
    policy = _policy(tmp_path)
    argv = wrap_host_command(
        [sys.executable, "-m", "mvge.runes.host_server", "--rune-dir", "/r"],
        policy,
        backends=[_FakeBwrapBackend()],
    )
    assert argv[0] == "bwrap"
    assert "mvge.runes.host_server" in argv


def test_host_session_command_is_wrapped_only_when_opted_in(
    tmp_path: Path,
) -> None:
    runner = RuneRunner()
    manifest = RuneManifest(
        name="echo-rune",
        version="0.1.0",
        description="echo",
        entry_point="rune.py",
    )
    unwrapped = HostSession(manifest, tmp_path, runner)
    assert unwrapped.command[0] == sys.executable
    wrapped = HostSession(
        manifest,
        tmp_path,
        runner,
        wrapping_policy=_policy(tmp_path),
        wrapping_backends=[_FakeBwrapBackend()],
    )
    assert wrapped.command[0] == "bwrap"


def test_wrapped_session_still_enforces_the_capability_gate(
    tmp_path: Path,
) -> None:
    # The wire gate lives in HostSession, not in the wrapper: a
    # wrapped host speaking the wire directly gains nothing.
    runner = RuneRunner()
    manifest = RuneManifest(
        name="echo-rune",
        version="0.1.0",
        description="echo",
        entry_point="rune.py",
        capabilities=["spell"],
    )
    session = HostSession(
        manifest,
        tmp_path,
        runner,
        wrapping_policy=_policy(tmp_path),
        wrapping_backends=[_FakeBwrapBackend()],
    )
    assert session.command[0] == "bwrap"
    # The gate check itself is unchanged by wrapping: it is the
    # same method the unwrapped session uses.
    assert session._wire_registration_allowed("register_spell") in (True, False)


# ---------------------------------------------------------------- live bwrap


def _run_wrapped_probe(
    tmp_path: Path,
    code: str,
    *,
    write_roots: tuple[Path, ...] = (),
    allow_network: bool = False,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    policy = _policy(tmp_path, write_roots=write_roots, allow_network=allow_network)
    argv = wrap_host_command([sys.executable, "-c", code], policy)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=60, env=env, cwd=tmp_path
    )


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bwrap not installed")
def test_bwrap_probe_cannot_read_outside_the_mounts(tmp_path: Path) -> None:
    secret_dir = tmp_path.parent / "wrapping-secret-dir"
    secret_dir.mkdir(exist_ok=True)
    secret = secret_dir / "secret.txt"
    secret.write_text("do-not-read\n", encoding="utf-8")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    code = f"import pathlib,sys\nprint(pathlib.Path({str(secret)!r}).read_text())\n"
    result = _run_wrapped_probe(workspace, code)
    assert result.returncode != 0
    assert "do-not-read" not in result.stdout
    assert "do-not-read" not in result.stderr


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bwrap not installed")
def test_bwrap_probe_cannot_write_outside_declared_roots(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    code = (
        "import pathlib\n"
        f"pathlib.Path({str(workspace / 'root.txt')!r}).write_text('x')\n"
    )
    result = _run_wrapped_probe(workspace, code)
    assert result.returncode != 0
    assert not (workspace / "root.txt").exists()


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bwrap not installed")
def test_bwrap_probe_can_write_a_declared_root(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    data = workspace / "data"
    data.mkdir(parents=True)
    code = f"import pathlib\npathlib.Path({str(data / 'ok.txt')!r}).write_text('ok')\n"
    result = _run_wrapped_probe(workspace, code, write_roots=(data,))
    assert result.returncode == 0, result.stderr
    assert (data / "ok.txt").read_text(encoding="utf-8") == "ok"


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bwrap not installed")
def test_bwrap_probe_has_no_network_when_denied(tmp_path: Path) -> None:
    # The probe connects to a listener this test controls, not
    # to a dead port: connecting to 127.0.0.1:9 proves nothing,
    # because a refused connection counts as "denied" even with
    # full network. With a live listener, a network leak
    # connects successfully and the probe exits 0.
    workspace = tmp_path / "ws"
    workspace.mkdir()
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        code = (
            "import socket\n"
            "s = socket.socket()\n"
            "s.settimeout(3)\n"
            f"s.connect(('127.0.0.1', {listener.getsockname()[1]}))\n"
        )
        result = _run_wrapped_probe(workspace, code)
        assert result.returncode != 0
    finally:
        listener.close()


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bwrap not installed")
def test_bwrap_probe_can_reach_the_listener_when_allowed(tmp_path: Path) -> None:
    # Symmetric half of the denied test: the same listener
    # probe must succeed when network is allowed, proving the
    # probe is capable of succeeding at all.
    workspace = tmp_path / "ws"
    workspace.mkdir()
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        code = (
            "import socket\n"
            "s = socket.socket()\n"
            "s.settimeout(3)\n"
            f"s.connect(('127.0.0.1', {listener.getsockname()[1]}))\n"
        )
        result = _run_wrapped_probe(workspace, code, allow_network=True)
        assert result.returncode == 0, result.stderr
    finally:
        listener.close()


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bwrap not installed")
def test_bwrap_probe_cannot_see_host_proc(tmp_path: Path) -> None:
    # Fresh procfs: the host PID of this test process must not
    # be visible inside the wrap, so its environ/cmdline cannot
    # be read through /proc.
    workspace = tmp_path / "ws"
    workspace.mkdir()
    code = (
        "import pathlib\n"
        f"path = pathlib.Path('/proc/{os.getpid()}/cmdline')\n"
        "print(path.read_text())\n"
    )
    result = _run_wrapped_probe(workspace, code)
    assert result.returncode != 0
    assert str(os.getpid()) not in result.stdout


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bwrap not installed")
def test_bwrap_probe_sees_no_credential_env(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    code = (
        "import os\n"
        "names = [k for k in os.environ if 'API_KEY' in k or 'TOKEN' in k]\n"
        "print('CREDENTIALS:' + ','.join(sorted(names)))\n"
    )
    result = _run_wrapped_probe(workspace, code)
    assert result.returncode == 0, result.stderr
    assert "CREDENTIALS:" in result.stdout
    assert result.stdout.strip().endswith("CREDENTIALS:")


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bwrap not installed")
def test_wrapped_host_session_runs_a_spell(tmp_path: Path) -> None:
    # End-to-end through the real host: the wrapped host still
    # speaks the Phase 4 wire, registers its spell, and answers.
    rune_dir = _write_rune(tmp_path / "runes", "echo-rune", _ECHO_BODY)
    manifest = load_manifest(rune_dir)
    assert manifest is not None
    manifest.path = str(rune_dir)
    runner = RuneRunner()
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    session = HostSession(
        manifest,
        rune_dir,
        runner,
        timeout_seconds=30.0,
        stderr_log_path=tmp_path / "host.log",
        wrapping_policy=_policy(workspace, rune_dir=rune_dir),
    )
    try:
        session.start()
        assert session.is_alive()
        result = session.execute_spell("echo-spell", "cast-1", {"text": "hi"})
        assert result == {"echo": "hi"}
    finally:
        session.stop()
