"""Phase 5a: fail-closed subprocess ladder.

Policy is seeded from ``SpellDefinition.read_only`` plus declared
filesystem write roots (the Phase 2B filesystem capability, whose
enforcing gate is this module). An unavailable backend refuses
with SANDBOX_UNAVAILABLE and never runs unconfined. Enforcement
is reported explicitly as full or partial.

The ladder confines subprocess effects only. It does not confine
rune code itself: that is Phase 5b, an opt-in deployment posture.
The static audit is a review aid and admission gate, never a
claim that in-process escape is blocked.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mvge.runes.source_audit import audit_source, blocking_findings
from mvge.runes.subprocess_policy import (
    SANDBOX_UNAVAILABLE,
    BwrapBackend,
    SandboxResult,
    SandboxTimeoutError,
    SandboxUnavailableError,
    SeatbeltBackend,
    SubprocessPolicy,
    WindowsAclBackend,
    allows_write,
    filesystem_write_admitted,
    policy_for_spell,
    resolve_effective_roots,
    run_sandboxed,
    seatbelt_profile,
)
from mvge.runes.types import SpellDefinition


def _spell(
    name: str = "s",
    read_only: bool = False,
    write_roots: tuple[str, ...] = (),
) -> SpellDefinition:
    return SpellDefinition(
        name=name,
        description="test spell",
        read_only=read_only,
        write_roots=write_roots,
    )


# ---------------------------------------------------------------- policy


def test_sandbox_unavailable_code_is_named() -> None:
    assert SANDBOX_UNAVAILABLE == "SANDBOX_UNAVAILABLE"
    assert SandboxUnavailableError.code == "SANDBOX_UNAVAILABLE"


def test_read_only_spell_gets_no_write_roots(tmp_path: Path) -> None:
    policy = policy_for_spell(
        _spell(read_only=True, write_roots=("data",)),
        tmp_path,
        rune_write_roots=["data"],
        declared_capabilities=["spell", "filesystem"],
    )
    assert policy.read_only is True
    assert policy.write_roots == ()
    assert policy.allows_write(tmp_path / "data" / "x") is False
    assert allows_write(policy, tmp_path / "anything") is False


def test_spell_roots_win_over_rune_roots(tmp_path: Path) -> None:
    policy = policy_for_spell(
        _spell(write_roots=("a",)),
        tmp_path,
        rune_write_roots=["b"],
        declared_capabilities=["spell", "filesystem"],
    )
    assert policy.write_roots == (tmp_path / "a",)
    assert policy.allows_write(tmp_path / "a" / "x") is True
    assert policy.allows_write(tmp_path / "b" / "x") is False


def test_rune_roots_apply_when_spell_declares_none(tmp_path: Path) -> None:
    policy = policy_for_spell(
        _spell(),
        tmp_path,
        rune_write_roots=["data"],
        declared_capabilities=["spell", "filesystem"],
    )
    assert policy.write_roots == (tmp_path / "data",)
    assert policy.allows_write(tmp_path / "data" / "x") is True
    assert policy.allows_write(tmp_path / "elsewhere" / "x") is False


def test_dot_root_means_the_workspace_itself(tmp_path: Path) -> None:
    policy = policy_for_spell(
        _spell(write_roots=(".",)),
        tmp_path,
        declared_capabilities=["spell", "filesystem"],
    )
    assert policy.write_roots == (tmp_path,)
    assert policy.allows_write(tmp_path / "any" / "file") is True


def test_writing_spell_outside_declared_roots_is_refused(tmp_path: Path) -> None:
    policy = policy_for_spell(
        _spell(write_roots=("data",)),
        tmp_path,
        declared_capabilities=["spell", "filesystem"],
    )
    assert policy.allows_write(tmp_path / "data" / "x") is True
    assert policy.allows_write(tmp_path / "secret") is False
    assert policy.allows_write(tmp_path) is False


def test_declared_without_filesystem_gets_no_ladder_writes(tmp_path: Path) -> None:
    policy = policy_for_spell(
        _spell(write_roots=("data",)),
        tmp_path,
        rune_write_roots=["data"],
        declared_capabilities=["spell"],
    )
    assert policy.write_roots == ()
    assert policy.allows_write(tmp_path / "data" / "x") is False


def test_legacy_manifest_keeps_pre_5a_posture(tmp_path: Path) -> None:
    policy = policy_for_spell(
        _spell(write_roots=("data",)),
        tmp_path,
        declared_capabilities=None,
    )
    assert policy.write_roots == (tmp_path / "data",)


def test_invalid_roots_are_dropped_fail_closed(tmp_path: Path) -> None:
    roots, dropped = resolve_effective_roots(
        read_only=False,
        spell_write_roots=("/etc", "../escape", "", "data"),
        rune_write_roots=[],
        declared_capabilities=["filesystem"],
        workspace_root=tmp_path,
    )
    assert roots == (tmp_path / "data",)
    assert "/etc" in dropped
    assert "../escape" in dropped
    assert "" in dropped


def test_filesystem_write_admitted_truth_table() -> None:
    # Gate inactive: legacy manifests and runes that never opted
    # into the filesystem capability are outside the audit gate.
    assert (
        filesystem_write_admitted(
            declared_capabilities=None,
            filesystem_roots=["data"],
            read_only=False,
        )
        is None
    )
    assert (
        filesystem_write_admitted(
            declared_capabilities=["spell"],
            filesystem_roots=["data"],
            read_only=False,
        )
        is None
    )
    # Gate active: the filesystem capability is declared.
    assert (
        filesystem_write_admitted(
            declared_capabilities=["spell", "filesystem"],
            filesystem_roots=["data"],
            read_only=False,
        )
        is True
    )
    assert (
        filesystem_write_admitted(
            declared_capabilities=["filesystem"],
            filesystem_roots=[],
            read_only=False,
        )
        is False
    )
    assert (
        filesystem_write_admitted(
            declared_capabilities=["filesystem"],
            filesystem_roots=["data"],
            read_only=True,
        )
        is False
    )


# ------------------------------------------------------------- selection


class _FakeBackend:
    def __init__(self, name: str, enforcement: str, available: bool = True) -> None:
        self.name = name
        self.enforcement = enforcement
        self._available = available
        self.seen: list[list[str]] = []

    def available(self) -> bool:
        return self._available

    def build_argv(self, argv: list[str], policy: SubprocessPolicy) -> list[str]:
        return [self.name, *argv]

    def run(
        self,
        argv: list[str],
        policy: SubprocessPolicy,
        *,
        timeout_seconds: float,
        env: dict[str, str] | None,
        cwd: Path,
    ) -> SandboxResult:
        self.seen.append(argv)
        return SandboxResult(
            returncode=0,
            stdout="ok",
            stderr="",
            backend_name=self.name,
            enforcement=self.enforcement,  # type: ignore[arg-type]
            command=tuple(self.build_argv(argv, policy)),
        )


def test_no_backend_fails_closed_and_never_runs(tmp_path: Path) -> None:
    marker = tmp_path / "marker"
    policy = policy_for_spell(_spell(), tmp_path)
    with pytest.raises(SandboxUnavailableError) as exc_info:
        run_sandboxed(
            [sys.executable, "-c", f"open({str(marker)!r}, 'w')"],
            policy,
            backends=[],
        )
    assert exc_info.value.code == SANDBOX_UNAVAILABLE
    assert not marker.exists()


def test_named_unavailable_backend_fails_closed(tmp_path: Path) -> None:
    policy = policy_for_spell(_spell(), tmp_path)
    with pytest.raises(SandboxUnavailableError):
        run_sandboxed(
            ["true"],
            policy,
            backend="bwrap",
            backends=[_FakeBackend("bwrap", "full", available=False)],
        )


def test_first_available_backend_wins(tmp_path: Path) -> None:
    policy = policy_for_spell(_spell(), tmp_path)
    first = _FakeBackend("first", "partial")
    second = _FakeBackend("second", "full")
    result = run_sandboxed(["true"], policy, backends=[first, second])
    assert result.backend_name == "first"
    assert result.enforcement == "partial"
    assert first.seen == [["true"]]
    assert second.seen == []


def test_run_sandboxed_reports_enforcement(tmp_path: Path) -> None:
    policy = policy_for_spell(_spell(), tmp_path)
    result = run_sandboxed(
        ["true"], policy, backends=[_FakeBackend("seatbelt", "partial")]
    )
    assert result.backend_name == "seatbelt"
    assert result.enforcement == "partial"
    assert result.returncode == 0


def test_run_sandboxed_rejects_empty_argv_and_outside_cwd(tmp_path: Path) -> None:
    policy = policy_for_spell(_spell(), tmp_path)
    with pytest.raises(ValueError, match="non-empty argv"):
        run_sandboxed([], policy, backends=[_FakeBackend("x", "partial")])
    with pytest.raises(ValueError, match="inside the policy workspace"):
        run_sandboxed(
            ["true"],
            policy,
            backends=[_FakeBackend("x", "partial")],
            cwd=Path("/tmp"),
        )


# ----------------------------------------------------------------- bwrap


def test_bwrap_argv_confines_workspace_and_roots(tmp_path: Path) -> None:
    backend = BwrapBackend()
    policy = SubprocessPolicy(
        spell_name="s",
        read_only=False,
        workspace_root=tmp_path,
        write_roots=(tmp_path / "data",),
    )
    argv = backend.build_argv(["sh", "-c", "echo hi"], policy)
    assert argv[0] == "bwrap"
    # Workspace is read-only; only the declared root is writable.
    assert "--ro-bind" in argv
    ro_idx = argv.index("--ro-bind")
    assert argv[ro_idx + 1] == str(tmp_path)
    bind_idx = argv.index("--bind")
    assert argv[bind_idx + 1] == str(tmp_path / "data")
    # Mount order is load-bearing: the scratch tmpfs must come
    # before the workspace bind, or a workspace under /tmp
    # would be hidden by the tmpfs instead of bound.
    assert argv.index("--tmpfs") < ro_idx
    assert argv[-3:] == ["sh", "-c", "echo hi"]


def test_bwrap_argv_read_only_has_no_writable_bind(tmp_path: Path) -> None:
    backend = BwrapBackend()
    policy = SubprocessPolicy(
        spell_name="s",
        read_only=True,
        workspace_root=tmp_path,
        write_roots=(),
    )
    argv = backend.build_argv(["true"], policy)
    assert "--bind" not in argv
    assert "--ro-bind" in argv


def test_bwrap_argv_workspace_root_is_writable_when_declared(tmp_path: Path) -> None:
    backend = BwrapBackend()
    policy = SubprocessPolicy(
        spell_name="s",
        read_only=False,
        workspace_root=tmp_path,
        write_roots=(tmp_path,),
    )
    argv = backend.build_argv(["true"], policy)
    bind_idx = argv.index("--bind")
    assert argv[bind_idx + 1] == str(tmp_path)


def test_bwrap_argv_network_is_unshared_unless_allowed(tmp_path: Path) -> None:
    backend = BwrapBackend()
    closed = SubprocessPolicy(
        spell_name="s",
        read_only=False,
        workspace_root=tmp_path,
        write_roots=(),
    )
    assert "--unshare-net" in backend.build_argv(["true"], closed)
    open_policy = SubprocessPolicy(
        spell_name="s",
        read_only=False,
        workspace_root=tmp_path,
        write_roots=(),
        allow_network=True,
    )
    assert "--unshare-net" not in backend.build_argv(["true"], open_policy)


def test_bwrap_reports_full_enforcement() -> None:
    assert BwrapBackend().enforcement == "full"


def test_bwrap_run_timeout_is_a_sandbox_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*args: object, **kwargs: object) -> object:
        raise subprocess.TimeoutExpired(cmd="bwrap", timeout=1.0)

    monkeypatch.setattr(subprocess, "run", _boom)
    backend = BwrapBackend()
    policy = SubprocessPolicy(spell_name="s", read_only=False, workspace_root=tmp_path)
    with pytest.raises(SandboxTimeoutError):
        backend.run(
            ["sleep", "30"],
            policy,
            timeout_seconds=1.0,
            env=None,
            cwd=tmp_path,
        )


# -------------------------------------------------------------- seatbelt


def test_seatbelt_profile_denies_writes_outside_roots(tmp_path: Path) -> None:
    policy = SubprocessPolicy(
        spell_name="s",
        read_only=False,
        workspace_root=tmp_path,
        write_roots=(tmp_path / "data",),
    )
    profile = seatbelt_profile(policy)
    assert "(deny file-write* (subpath " in profile
    assert f'(allow file-write* (subpath "{tmp_path / "data"}"))' in profile
    assert profile.count("(allow file-write*") == 2  # root + scratch


def test_seatbelt_profile_read_only_allows_no_writes(tmp_path: Path) -> None:
    policy = SubprocessPolicy(
        spell_name="s",
        read_only=True,
        workspace_root=tmp_path,
        write_roots=(),
    )
    profile = seatbelt_profile(policy)
    assert "(deny file-write*" in profile
    assert "(allow file-write*" not in profile


def test_seatbelt_reports_partial_enforcement() -> None:
    assert SeatbeltBackend().enforcement == "partial"


def test_seatbelt_argv_uses_sandbox_exec(tmp_path: Path) -> None:
    backend = SeatbeltBackend()
    policy = SubprocessPolicy(spell_name="s", read_only=False, workspace_root=tmp_path)
    argv = backend.build_argv(["true"], policy)
    assert argv[0] == "sandbox-exec"
    assert argv[1] == "-p"
    assert argv[3] == "true"


# ---------------------------------------------------------------- windows


def test_windows_acl_reports_partial_enforcement() -> None:
    assert WindowsAclBackend().enforcement == "partial"


def test_windows_acl_apply_run_restore_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def _fake_run(argv: object, **kwargs: object) -> MagicMock:
        calls.append(list(argv))  # type: ignore[arg-type]
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setenv("USERNAME", "testuser")
    (tmp_path / "data").mkdir()
    (tmp_path / "other").mkdir()
    backend = WindowsAclBackend()
    policy = SubprocessPolicy(
        spell_name="s",
        read_only=False,
        workspace_root=tmp_path,
        write_roots=(tmp_path / "data",),
    )
    result = backend.run(
        ["cmd.exe", "/c", "echo hi"],
        policy,
        timeout_seconds=10.0,
        env=None,
        cwd=tmp_path,
    )
    assert result.backend_name == "windows-acl"
    assert result.enforcement == "partial"
    kinds = [c[0] for c in calls]
    assert kinds[0] == "icacls"  # deny
    assert kinds[1] == "cmd.exe"  # child
    assert kinds[2] == "icacls"  # restore
    # The protected entry is "other"; the declared root is untouched.
    assert str(tmp_path / "other") in calls[0]
    assert str(tmp_path / "data") not in calls[0]
    assert str(tmp_path / "other") in calls[2]


def test_windows_acl_apply_failure_never_runs_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def _fake_run(argv: object, **kwargs: object) -> MagicMock:
        calls.append(list(argv))  # type: ignore[arg-type]
        if list(argv)[0] == "icacls":  # type: ignore[arg-type]
            raise subprocess.CalledProcessError(1, argv)
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setenv("USERNAME", "testuser")
    (tmp_path / "other").mkdir()
    backend = WindowsAclBackend()
    policy = SubprocessPolicy(spell_name="s", read_only=False, workspace_root=tmp_path)
    with pytest.raises(SandboxUnavailableError):
        backend.run(
            ["child.exe"],
            policy,
            timeout_seconds=10.0,
            env=None,
            cwd=tmp_path,
        )
    assert all(c[0] == "icacls" for c in calls)


# -------------------------------------------------- audit/runtime parity


_WRITE_SOURCE = "from pathlib import Path\nPath('x').write_text('hi')\n"


def _audit_filesystem_blocked(
    *,
    declared: list[str] | None,
    roots: list[str] | None,
    read_only: bool,
) -> bool:
    findings = audit_source(
        _WRITE_SOURCE,
        declared_capabilities=declared,
        filesystem_write_roots=roots,
        read_only=read_only,
    )
    return any(
        f.kind == "undeclared_filesystem_write" for f in blocking_findings(findings)
    )


def test_audit_runtime_parity_for_filesystem_gate(tmp_path: Path) -> None:
    """One declaration table, two verdicts, no drift (release gate).

    For every fixture in the gate's active domain (the rune opted
    into the filesystem capability), the audit's verdict and the
    runtime policy's verdict are computed from the same
    ``filesystem_write_admitted`` predicate and must agree:
    admitted exactly when roots are declared and the spell is
    not read-only.
    """
    fixtures = [
        # (declared, roots, read_only, admitted)
        (["spell", "filesystem"], ["data"], False, True),
        (["filesystem"], [], False, False),
        (["filesystem"], ["data"], True, False),
        (["filesystem"], ["data", "out"], False, True),
    ]
    for declared, roots, read_only, admitted in fixtures:
        assert _audit_filesystem_blocked(
            declared=declared, roots=roots, read_only=read_only
        ) == (not admitted), (declared, roots, read_only)
        policy = policy_for_spell(
            _spell(read_only=read_only),
            tmp_path,
            rune_write_roots=roots,
            declared_capabilities=declared,
        )
        assert (len(policy.write_roots) > 0) == admitted, (
            declared,
            roots,
            read_only,
        )
        if admitted:
            assert policy.allows_write(tmp_path / roots[0] / "x") is True
        assert policy.allows_write(tmp_path / "outside" / "x") is False


def test_audit_gate_is_inactive_outside_the_opt_in(tmp_path: Path) -> None:
    """Documented asymmetry, not drift: outside the opt-in domain
    the audit stays silent (pre-5a posture) while the ladder stays
    fail-closed for declared-but-not-filesystem runes.
    """
    # Legacy: audit silent, ladder honors declared roots.
    assert (
        _audit_filesystem_blocked(declared=None, roots=["data"], read_only=False)
        is False
    )
    legacy = policy_for_spell(
        _spell(), tmp_path, rune_write_roots=["data"], declared_capabilities=None
    )
    assert legacy.write_roots == (tmp_path / "data",)
    # Declared without filesystem: audit silent, ladder denies.
    assert (
        _audit_filesystem_blocked(declared=["spell"], roots=["data"], read_only=False)
        is False
    )
    declared = policy_for_spell(
        _spell(),
        tmp_path,
        rune_write_roots=["data"],
        declared_capabilities=["spell"],
    )
    assert declared.write_roots == ()


def test_audit_flags_write_calls_but_not_reads() -> None:
    findings = audit_source(
        "from pathlib import Path\nPath('x').read_text()\n",
        declared_capabilities=["filesystem"],
        filesystem_write_roots=[],
    )
    assert not any(
        f.kind == "undeclared_filesystem_write" for f in blocking_findings(findings)
    )
    findings = audit_source(
        "open('x', 'w')\n",
        declared_capabilities=["filesystem"],
        filesystem_write_roots=[],
    )
    assert any(
        f.kind == "undeclared_filesystem_write" for f in blocking_findings(findings)
    )
    # The audit is a review aid: dynamically built calls are not
    # detected, and no diagnostic here claims otherwise.
    findings = audit_source(
        "getattr(open, 'x')\n",
        declared_capabilities=["filesystem"],
        filesystem_write_roots=[],
    )
    assert not any(
        f.kind == "undeclared_filesystem_write" for f in blocking_findings(findings)
    )


def test_bwrap_extra_ro_bind_for_non_system_interpreter(tmp_path: Path) -> None:
    # A uv-managed interpreter outside /usr needs its prefix
    # bound read-only or the child cannot execute it at all.
    fake_prefix = tmp_path / "uv" / "python"
    fake_bin = fake_prefix / "bin"
    fake_bin.mkdir(parents=True)
    fake_exe = fake_bin / "python"
    fake_exe.write_text("", encoding="utf-8")
    backend = BwrapBackend()
    assert backend._extra_ro_binds(str(fake_exe)) == [str(fake_prefix)]
    assert backend._extra_ro_binds("/usr/bin/python") == []


def test_bwrap_argv_resolves_symlinked_interpreter(tmp_path: Path) -> None:
    # A venv interpreter path is a symlink whose target is
    # bound, while the link path itself is not: argv[0] must
    # resolve, or the child fails to exec inside the namespace.
    target = tmp_path / "real" / "python"
    target.parent.mkdir(parents=True)
    target.write_text("", encoding="utf-8")
    link = tmp_path / "venv" / "python"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    backend = BwrapBackend()
    policy = SubprocessPolicy(spell_name="s", read_only=False, workspace_root=tmp_path)
    argv = backend.build_argv([str(link), "-c", "pass"], policy)
    assert argv[-3] == str(target)
    # Bare command names are left alone for PATH resolution.
    argv = backend.build_argv(["python3", "-c", "pass"], policy)
    assert argv[-3] == "python3"


# ------------------------------------------------- backend run paths


def test_bwrap_run_success_reports_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _ok(argv: object, **kwargs: object) -> MagicMock:
        result = MagicMock(returncode=0, stdout="out", stderr="")
        return result

    monkeypatch.setattr(subprocess, "run", _ok)
    backend = BwrapBackend()
    policy = SubprocessPolicy(spell_name="s", read_only=False, workspace_root=tmp_path)
    result = backend.run(["true"], policy, timeout_seconds=10.0, env=None, cwd=tmp_path)
    assert result.returncode == 0
    assert result.stdout == "out"
    assert result.backend_name == "bwrap"
    assert result.enforcement == "full"
    assert result.command[0] == "bwrap"


def test_seatbelt_run_success_and_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def _ok(argv: object, **kwargs: object) -> MagicMock:
        calls.append(list(argv))  # type: ignore[arg-type]
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _ok)
    backend = SeatbeltBackend()
    policy = SubprocessPolicy(spell_name="s", read_only=False, workspace_root=tmp_path)
    result = backend.run(["true"], policy, timeout_seconds=10.0, env=None, cwd=tmp_path)
    assert result.backend_name == "seatbelt"
    assert result.enforcement == "partial"
    assert calls[0][0] == "sandbox-exec"

    def _boom(*args: object, **kwargs: object) -> object:
        raise subprocess.TimeoutExpired(cmd="sandbox-exec", timeout=1.0)

    monkeypatch.setattr(subprocess, "run", _boom)
    with pytest.raises(SandboxTimeoutError):
        backend.run(
            ["sleep", "30"],
            policy,
            timeout_seconds=1.0,
            env=None,
            cwd=tmp_path,
        )


def test_backend_availability_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mvge.runes.subprocess_policy.sys.platform", "linux")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/bwrap")
    assert BwrapBackend().available() is True
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert BwrapBackend().available() is False
    monkeypatch.setattr("mvge.runes.subprocess_policy.sys.platform", "darwin")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/sandbox-exec")
    assert SeatbeltBackend().available() is True
    monkeypatch.setattr("mvge.runes.subprocess_policy.sys.platform", "win32")
    monkeypatch.setattr(shutil, "which", lambda name: "C:\\icacls.exe")
    assert WindowsAclBackend().available() is True
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert WindowsAclBackend().available() is False


def test_select_backend_named_and_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = policy_for_spell(_spell(), tmp_path)
    wanted = _FakeBackend("wanted", "full")
    chosen = run_sandboxed(
        ["true"],
        policy,
        backend="wanted",
        backends=[_FakeBackend("other", "partial"), wanted],
    )
    assert chosen.backend_name == "wanted"
    monkeypatch.setattr(
        "mvge.runes.subprocess_policy.default_backends",
        lambda: [_FakeBackend("dflt", "partial")],
    )
    result = run_sandboxed(["true"], policy)
    assert result.backend_name == "dflt"


def test_windows_acl_restore_failure_raises_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def _fake_run(argv: object, **kwargs: object) -> MagicMock:
        args = list(argv)  # type: ignore[arg-type]
        calls.append(args)
        if "/remove:d" in args:
            raise subprocess.CalledProcessError(1, argv)
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setenv("USERNAME", "testuser")
    (tmp_path / "other").mkdir()
    backend = WindowsAclBackend()
    policy = SubprocessPolicy(spell_name="s", read_only=False, workspace_root=tmp_path)
    with pytest.raises(RuntimeError, match="failed to restore"):
        backend.run(
            ["child.exe"],
            policy,
            timeout_seconds=10.0,
            env=None,
            cwd=tmp_path,
        )
    assert calls[0][0] == "icacls"
    assert calls[1][0] == "child.exe"


def test_windows_acl_timeout_and_username_and_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("USERNAME", "testuser")

    def _timeout(argv: object, **kwargs: object) -> MagicMock:
        args = list(argv)  # type: ignore[arg-type]
        if args[0] == "icacls":
            return MagicMock(returncode=0, stdout="", stderr="")
        raise subprocess.TimeoutExpired(cmd=args, timeout=1.0)

    monkeypatch.setattr(subprocess, "run", _timeout)
    (tmp_path / "other").mkdir()
    backend = WindowsAclBackend()
    policy = SubprocessPolicy(spell_name="s", read_only=False, workspace_root=tmp_path)
    with pytest.raises(SandboxTimeoutError):
        backend.run(
            ["child.exe"],
            policy,
            timeout_seconds=1.0,
            env=None,
            cwd=tmp_path,
        )

    # No username resolvable: fail closed, child never runs.
    monkeypatch.delenv("USERNAME", raising=False)
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.setattr(
        "mvge.runes.subprocess_policy.getpass.getuser",
        lambda: (_ for _ in ()).throw(KeyError("no user")),
    )
    with pytest.raises(SandboxUnavailableError):
        backend.run(
            ["child.exe"],
            policy,
            timeout_seconds=10.0,
            env=None,
            cwd=tmp_path,
        )

    # Unlistable workspace: fail closed.
    monkeypatch.setenv("USERNAME", "testuser")
    missing = tmp_path / "missing"
    policy_missing = SubprocessPolicy(
        spell_name="s", read_only=False, workspace_root=missing
    )
    with pytest.raises(SandboxUnavailableError):
        backend.run(
            ["child.exe"],
            policy_missing,
            timeout_seconds=10.0,
            env=None,
            cwd=missing,
        )


def test_windows_acl_entry_containing_a_root_stays_unprotected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def _fake_run(argv: object, **kwargs: object) -> MagicMock:
        calls.append(list(argv))  # type: ignore[arg-type]
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setenv("USERNAME", "testuser")
    (tmp_path / "data" / "sub").mkdir(parents=True)
    (tmp_path / "other").mkdir()
    backend = WindowsAclBackend()
    policy = SubprocessPolicy(
        spell_name="s",
        read_only=False,
        workspace_root=tmp_path,
        write_roots=(tmp_path / "data" / "sub",),
    )
    backend.run(["child.exe"], policy, timeout_seconds=10.0, env=None, cwd=tmp_path)
    deny_targets = [c[1] for c in calls if c[0] == "icacls" and "/deny" in c]
    # "data" contains a declared root beneath it, so denying it
    # would deny the root itself: it stays unprotected.
    assert deny_targets == [str(tmp_path / "other")]


def test_policy_post_init_drops_outside_roots(tmp_path: Path) -> None:
    policy = SubprocessPolicy(
        spell_name="s",
        read_only=False,
        workspace_root=tmp_path,
        write_roots=(tmp_path / "data", Path("/etc")),
    )
    assert policy.write_roots == (tmp_path / "data",)
    assert "/etc" in policy.dropped_roots


def test_resolve_drops_symlink_escape(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = ws / "link"
    link.symlink_to(outside, target_is_directory=True)
    roots, dropped = resolve_effective_roots(
        read_only=False,
        spell_write_roots=["link"],
        rune_write_roots=[],
        declared_capabilities=["filesystem"],
        workspace_root=ws,
    )
    assert roots == ()
    assert dropped == ("link",)


def test_extra_ro_bind_missing_prefix_returns_empty(tmp_path: Path) -> None:
    backend = BwrapBackend()
    assert backend._extra_ro_binds("/nonexistent/prefix/bin/python") == []


def test_run_sandboxed_cwd_inside_workspace(tmp_path: Path) -> None:
    policy = policy_for_spell(_spell(), tmp_path)
    backend = _FakeBackend("x", "partial")
    sub = tmp_path / "sub"
    sub.mkdir()
    result = run_sandboxed(["true"], policy, backends=[backend], cwd=sub)
    assert result.returncode == 0


def test_resolve_dedupes_repeated_roots(tmp_path: Path) -> None:
    roots, dropped = resolve_effective_roots(
        read_only=False,
        spell_write_roots=["data", "data"],
        rune_write_roots=[],
        declared_capabilities=["filesystem"],
        workspace_root=tmp_path,
    )
    assert roots == (tmp_path / "data",)
    assert dropped == ()


def test_bwrap_argv_without_proc_or_missing_system_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orig_exists = Path.exists

    def _exists(self: Path) -> bool:
        if str(self) in ("/proc", "/lib64"):
            return False
        return orig_exists(self)

    monkeypatch.setattr(Path, "exists", _exists)
    backend = BwrapBackend()
    policy = SubprocessPolicy(spell_name="s", read_only=False, workspace_root=tmp_path)
    argv = backend.build_argv(["true"], policy)
    assert "/proc" not in argv
    assert "/lib64" not in argv


def test_default_backends_order() -> None:
    from mvge.runes.subprocess_policy import default_backends

    assert [b.name for b in default_backends()] == [
        "bwrap",
        "seatbelt",
        "windows-acl",
    ]


def test_unnamed_selection_all_unavailable_fails_closed(tmp_path: Path) -> None:
    policy = policy_for_spell(_spell(), tmp_path)
    with pytest.raises(SandboxUnavailableError) as exc_info:
        run_sandboxed(
            ["true"],
            policy,
            backends=[_FakeBackend("a", "partial", available=False)],
        )
    assert exc_info.value.code == SANDBOX_UNAVAILABLE
