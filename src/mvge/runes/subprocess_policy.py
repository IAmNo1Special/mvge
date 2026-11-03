"""Phase 5a fail-closed subprocess ladder.

A per-spell file-effect policy for subprocesses, seeded from the
existing ``SpellDefinition.read_only`` flag plus declared
filesystem-capability write roots. The filesystem capability was
deferred from Phase 2B precisely because it had no enforcing
gate; this module is that gate, so the capability is minted
only now (see ``mvge.runes.capabilities``).

ONE declaration table: the audit (``mvge.runes.source_audit``)
and the runtime both import ``filesystem_write_admitted`` and
``resolve_effective_roots`` from here. Neither invents its own
mapping, so an audit verdict and a runtime verdict for the same
declaration cannot drift. The audit side is a review aid and
admission gate only: it never claims to block in-process
escape (dynamically built calls, ``exec``, ``getattr`` bypass
any pattern list).

Backends, in selection order:

- ``bwrap`` (Linux, enforcement ``full``): bubblewrap mount
  namespace. The workspace is bound read-only and only declared
  write roots are bound writable, so reads outside the bound
  dirs are invisible to the child; network is unshared unless
  the policy allows it. ``/tmp`` is a tmpfs scratch dir, not a
  policy root: child temp writes vanish with the namespace.
  The tmpfs is mounted before the workspace bind, so a
  workspace under ``/tmp`` stays visible instead of being
  hidden by the scratch mount. ``/proc`` is bound read-only
  from the host: a fresh proc mount is not portable to
  restricted containers, and a read-only bind cannot be used
  to write (host process metadata stays visible read-only).
- ``seatbelt`` (macOS, enforcement ``partial``): ``sandbox-exec``
  with a generated profile denying file writes outside the
  declared roots. Reads and network are not denied, so this is
  partial by construction, reported as such.
- ``windows-acl`` (Windows, enforcement ``partial``): temporary
  deny-write ACEs (``icacls``) on workspace entries that are
  disjoint from every declared write root, restored after the
  child exits. Reads and sockets are not intersected, writes
  outside the workspace are not intersected, and a top-level
  entry that contains a declared root beneath it is left
  unprotected rather than breaking that root. Partial by
  construction, reported as such.

Fail-closed everywhere: when no backend is available, or a
named backend is unavailable, or the Windows deny ACEs cannot
be applied, the run is refused with ``SANDBOX_UNAVAILABLE``
and the child never executes. There is no unconfined fallback
path in this module. ``run_sandboxed`` is opt-in: in-process
spell execution is unchanged, and a spell with no declared
roots gets no writable binds under the ladder.

Honest claim for this stage: spell/subprocess effects are
confined per-spell with a fail-closed policy. Rune code
itself is not yet confined; that is Phase 5b, an opt-in
deployment posture, and nothing here claims otherwise.
"""

from __future__ import annotations

import getpass
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

SANDBOX_UNAVAILABLE = "SANDBOX_UNAVAILABLE"

FILESYSTEM_CAPABILITY_ID = "filesystem"

_SYSTEM_RO_BINDS: tuple[str, ...] = ("/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc")


class SandboxUnavailableError(Exception):
    """Named fail-closed refusal: no usable backend, never unconfined."""

    code = SANDBOX_UNAVAILABLE


class SandboxTimeoutError(Exception):
    """Raised when a sandboxed subprocess exceeds its timeout."""


@dataclass(frozen=True)
class SandboxResult:
    """Outcome of one sandboxed run, with enforcement reported."""

    returncode: int
    stdout: str
    stderr: str
    backend_name: str
    enforcement: Literal["full", "partial"]
    command: tuple[str, ...] = ()


def filesystem_write_admitted(
    *,
    declared_capabilities: list[str] | None,
    filesystem_roots: list[str] | None,
    read_only: bool,
) -> bool | None:
    """Admission verdict for a filesystem write, shared by audit and runtime.

    Returns None when the gate is inactive: a legacy manifest
    (no capabilities field) or a rune that never opted into the
    filesystem capability is outside the admission gate and
    keeps its pre-5a posture there. Returns a bool once the
    rune opts in by declaring the filesystem capability: True
    exactly when write roots are declared and the spell is not
    read-only. Declaring the capability without roots grants
    nothing; read-only overrides any declaration.
    """
    if declared_capabilities is None:
        return None
    if FILESYSTEM_CAPABILITY_ID not in declared_capabilities:
        return None
    if read_only:
        return False
    return bool(filesystem_roots)


def resolve_effective_roots(
    *,
    read_only: bool,
    spell_write_roots: tuple[str, ...] | list[str] | None,
    rune_write_roots: tuple[str, ...] | list[str] | None,
    declared_capabilities: list[str] | None,
    workspace_root: Path,
) -> tuple[tuple[Path, ...], tuple[str, ...]]:
    """Resolve declared write roots to absolute paths under the workspace.

    Spell roots win over rune roots when the spell declares any.
    A read-only spell resolves to no roots. A manifest that
    declares capabilities without the filesystem capability
    resolves to no roots: under the ladder, undeclared writes
    are refused rather than silently allowed. Invalid roots
    (absolute paths, ``..`` escapes, empty strings, symlink
    escapes out of the workspace) are dropped fail-closed and
    reported in the second element.
    """
    workspace = Path(workspace_root).resolve()
    if read_only:
        return (), ()
    if (
        declared_capabilities is not None
        and FILESYSTEM_CAPABILITY_ID not in declared_capabilities
    ):
        return (), ()
    candidates: list[str] = (
        list(spell_write_roots) if spell_write_roots else list(rune_write_roots or [])
    )
    roots: list[Path] = []
    dropped: list[str] = []
    for raw in candidates:
        if not isinstance(raw, str) or raw == "":
            dropped.append(str(raw))
            continue
        candidate = Path(raw)
        if candidate.is_absolute() or ".." in candidate.parts:
            dropped.append(raw)
            continue
        resolved = (workspace / raw).resolve()
        if resolved != workspace and workspace not in resolved.parents:
            dropped.append(raw)
            continue
        if resolved not in roots:
            roots.append(resolved)
    return tuple(roots), tuple(dropped)


@dataclass(frozen=True)
class SubprocessPolicy:
    """File-effect policy for one spell's subprocesses."""

    spell_name: str
    read_only: bool
    workspace_root: Path
    write_roots: tuple[Path, ...] = ()
    dropped_roots: tuple[str, ...] = ()
    allow_network: bool = False

    def __post_init__(self) -> None:
        workspace = Path(self.workspace_root).resolve()
        object.__setattr__(self, "workspace_root", workspace)
        kept: list[Path] = []
        dropped: list[str] = list(self.dropped_roots)
        for root in self.write_roots:
            resolved = Path(root).resolve()
            if resolved == workspace or workspace in resolved.parents:
                if resolved not in kept:
                    kept.append(resolved)
            else:
                dropped.append(str(root))
        object.__setattr__(self, "write_roots", tuple(kept))
        object.__setattr__(self, "dropped_roots", tuple(dropped))

    def allows_write(self, path: str | Path) -> bool:
        """True iff a write to ``path`` falls under a declared root."""
        if self.read_only:
            return False
        resolved = Path(path).resolve()
        return any(
            resolved == root or root in resolved.parents for root in self.write_roots
        )


def allows_write(policy: SubprocessPolicy, path: str | Path) -> bool:
    """Module-level form of ``SubprocessPolicy.allows_write``."""
    return policy.allows_write(path)


def policy_for_spell(
    spell: object,
    workspace_root: Path,
    *,
    rune_write_roots: tuple[str, ...] | list[str] = (),
    declared_capabilities: list[str] | None = None,
) -> SubprocessPolicy:
    """Build a spell's ladder policy from its declaration.

    ``spell`` is any object with ``name``, ``read_only``, and
    ``write_roots`` attributes (``SpellDefinition`` and its host
    proxy both qualify). Read-only wins over any declaration;
    spell roots win over rune roots.
    """
    read_only = bool(getattr(spell, "read_only", False))
    spell_roots = getattr(spell, "write_roots", ()) or ()
    roots, dropped = resolve_effective_roots(
        read_only=read_only,
        spell_write_roots=list(spell_roots),
        rune_write_roots=list(rune_write_roots),
        declared_capabilities=declared_capabilities,
        workspace_root=workspace_root,
    )
    return SubprocessPolicy(
        spell_name=str(getattr(spell, "name", "")),
        read_only=read_only,
        workspace_root=Path(workspace_root),
        write_roots=roots,
        dropped_roots=dropped,
    )


class SubprocessBackend(Protocol):
    """One rung of the ladder: an OS confinement mechanism."""

    name: str
    enforcement: Literal["full", "partial"]

    def available(self) -> bool:
        """True when this platform and tool can actually confine."""
        ...

    def build_argv(self, argv: list[str], policy: SubprocessPolicy) -> list[str]:
        """Wrap ``argv`` in the backend's confinement invocation."""
        ...

    def run(
        self,
        argv: list[str],
        policy: SubprocessPolicy,
        *,
        timeout_seconds: float,
        env: dict[str, str] | None,
        cwd: Path,
    ) -> SandboxResult:
        """Run ``argv`` confined, reporting enforcement explicitly."""
        ...


def _run_captured(
    command: list[str],
    *,
    timeout_seconds: float,
    env: dict[str, str] | None,
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        env=env,
        cwd=str(cwd),
    )


def _result(
    completed: subprocess.CompletedProcess[str],
    backend_name: str,
    enforcement: Literal["full", "partial"],
    command: list[str],
) -> SandboxResult:
    return SandboxResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        backend_name=backend_name,
        enforcement=enforcement,
        command=tuple(command),
    )


class BwrapBackend:
    """Linux bubblewrap backend: full enforcement."""

    name = "bwrap"
    enforcement: Literal["full", "partial"] = "full"

    def available(self) -> bool:
        return sys.platform.startswith("linux") and shutil.which("bwrap") is not None

    def _extra_ro_binds(self, executable: str | None = None) -> list[str]:
        """Read-only binds for an interpreter outside the system dirs.

        A uv-managed interpreter lives outside ``/usr``; without
        its prefix bound read-only, the child cannot execute it
        at all inside the namespace. Returns [] when the
        interpreter already sits under a bound system dir.
        """
        exe = Path(executable or sys.executable).resolve()
        for bound in _SYSTEM_RO_BINDS:
            bound_path = Path(bound)
            if exe == bound_path or bound_path in exe.parents:
                return []
        prefix = exe.parent.parent
        return [str(prefix)] if prefix.exists() else []

    def build_argv(self, argv: list[str], policy: SubprocessPolicy) -> list[str]:
        # A path-style argv[0] must be resolved before wrapping:
        # a venv interpreter is typically a symlink, and the
        # symlink path (e.g. a project .venv) is not bound into
        # the namespace, while the resolved interpreter under
        # its prefix is. Bare command names are left alone:
        # they resolve via PATH inside the namespace.
        resolved_argv = list(argv)
        if (
            resolved_argv
            and "/" in resolved_argv[0]
            and Path(resolved_argv[0]).exists()
        ):
            resolved_argv[0] = str(Path(resolved_argv[0]).resolve())
        workspace = str(policy.workspace_root)
        command: list[str] = [
            "bwrap",
            "--unshare-user-try",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--die-with-parent",
        ]
        if not policy.allow_network:
            command.append("--unshare-net")
        # Mount order is load-bearing: the scratch tmpfs must
        # come before the workspace bind, or a workspace under
        # /tmp would be hidden by the tmpfs instead of bound.
        command += ["--dev", "/dev", "--tmpfs", "/tmp"]
        if policy.workspace_root in policy.write_roots:
            command += ["--bind", workspace, workspace]
        else:
            command += ["--ro-bind", workspace, workspace]
        if Path("/proc").exists():
            command += ["--ro-bind", "/proc", "/proc"]
        for bound in _SYSTEM_RO_BINDS:
            if Path(bound).exists():
                command += ["--ro-bind", bound, bound]
        for extra in self._extra_ro_binds():
            command += ["--ro-bind", extra, extra]
        for root in policy.write_roots:
            if root != policy.workspace_root:
                command += ["--bind", str(root), str(root)]
        command += ["--", *resolved_argv]
        return command

    def run(
        self,
        argv: list[str],
        policy: SubprocessPolicy,
        *,
        timeout_seconds: float,
        env: dict[str, str] | None,
        cwd: Path,
    ) -> SandboxResult:
        command = self.build_argv(argv, policy)
        try:
            completed = _run_captured(
                command, timeout_seconds=timeout_seconds, env=env, cwd=cwd
            )
        except subprocess.TimeoutExpired as exc:
            raise SandboxTimeoutError(
                f"Sandboxed command timed out after {timeout_seconds} seconds."
            ) from exc
        return _result(completed, self.name, self.enforcement, command)


def _seatbelt_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def seatbelt_profile(policy: SubprocessPolicy) -> str:
    """Generate a Seatbelt profile denying writes outside the roots.

    The profile allows by default (reads, execution, and network
    are not denied: partial enforcement), denies file writes
    under ``/``, then re-allows writes under each declared root
    — the more specific root rule governs its subtree. A
    read-only policy allows no writes at all, not even scratch:
    read-only means no writes, full stop.
    """
    lines = [
        "(version 1)",
        "(allow default)",
        '(deny file-write* (subpath "/"))',
    ]
    for root in policy.write_roots:
        lines.append(f'(allow file-write* (subpath "{_seatbelt_escape(str(root))}"))')
    if policy.write_roots:
        lines.append('(allow file-write* (subpath "/private/tmp"))')
    return "\n".join(lines) + "\n"


class SeatbeltBackend:
    """macOS sandbox-exec backend: partial enforcement."""

    name = "seatbelt"
    enforcement: Literal["full", "partial"] = "partial"

    def available(self) -> bool:
        return sys.platform == "darwin" and shutil.which("sandbox-exec") is not None

    def build_argv(self, argv: list[str], policy: SubprocessPolicy) -> list[str]:
        return ["sandbox-exec", "-p", seatbelt_profile(policy), *argv]

    def run(
        self,
        argv: list[str],
        policy: SubprocessPolicy,
        *,
        timeout_seconds: float,
        env: dict[str, str] | None,
        cwd: Path,
    ) -> SandboxResult:
        command = self.build_argv(argv, policy)
        try:
            completed = _run_captured(
                command, timeout_seconds=timeout_seconds, env=env, cwd=cwd
            )
        except subprocess.TimeoutExpired as exc:
            raise SandboxTimeoutError(
                f"Sandboxed command timed out after {timeout_seconds} seconds."
            ) from exc
        return _result(completed, self.name, self.enforcement, command)


class WindowsAclBackend:
    """Windows icacls backend: partial enforcement.

    Deny-write ACEs are applied to workspace entries disjoint
    from every declared root, then removed after the child
    exits. Reads, sockets, and writes outside the workspace are
    not intersected. A top-level entry containing a declared
    root beneath it is left unprotected: denying the parent
    would deny the root itself. A failed restore raises
    loudly (the deny ACEs would otherwise linger) instead of
    being swallowed.
    """

    name = "windows-acl"
    enforcement: Literal["full", "partial"] = "partial"

    def available(self) -> bool:
        return sys.platform == "win32" and shutil.which("icacls") is not None

    def _username(self) -> str | None:
        username = os.environ.get("USERNAME") or os.environ.get("USER")
        if username:
            return username
        try:
            return getpass.getuser()
        except (OSError, KeyError):
            return None

    def _protected_entries(self, policy: SubprocessPolicy) -> list[Path]:
        try:
            entries = sorted(policy.workspace_root.iterdir())
        except OSError as exc:
            raise SandboxUnavailableError(
                f"Windows ACL backend cannot list the workspace "
                f"({SANDBOX_UNAVAILABLE}): refusing to run unconfined."
            ) from exc
        protected: list[Path] = []
        for entry in entries:
            disjoint = True
            for root in policy.write_roots:
                if root == entry or entry in root.parents or root in entry.parents:
                    disjoint = False
                    break
            if disjoint:
                protected.append(entry)
        return protected

    def _deny_command(self, entry: Path, username: str) -> list[str]:
        return [
            "icacls",
            str(entry),
            "/deny",
            f"{username}:(OI)(CI)W",
            "/t",
        ]

    def _restore_command(self, entry: Path, username: str) -> list[str]:
        return ["icacls", str(entry), "/remove:d", username, "/t"]

    def build_argv(self, argv: list[str], policy: SubprocessPolicy) -> list[str]:
        """The child argv itself: ACLs are applied around the run, not in it."""
        return list(argv)

    def run(
        self,
        argv: list[str],
        policy: SubprocessPolicy,
        *,
        timeout_seconds: float,
        env: dict[str, str] | None,
        cwd: Path,
    ) -> SandboxResult:
        username = self._username()
        if username is None:
            raise SandboxUnavailableError(
                f"Windows ACL backend cannot determine the current user "
                f"({SANDBOX_UNAVAILABLE}): refusing to run unconfined."
            )
        protected = self._protected_entries(policy)
        applied: list[Path] = []
        completed: subprocess.CompletedProcess[str] | None = None
        try:
            for entry in protected:
                try:
                    subprocess.run(
                        self._deny_command(entry, username),
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                except (subprocess.CalledProcessError, OSError) as exc:
                    raise SandboxUnavailableError(
                        f"Windows ACL backend could not apply deny-write "
                        f"ACEs ({SANDBOX_UNAVAILABLE}): refusing to run "
                        f"unconfined."
                    ) from exc
                applied.append(entry)
            try:
                completed = _run_captured(
                    list(argv),
                    timeout_seconds=timeout_seconds,
                    env=env,
                    cwd=cwd,
                )
            except subprocess.TimeoutExpired as exc:
                raise SandboxTimeoutError(
                    f"Sandboxed command timed out after {timeout_seconds} seconds."
                ) from exc
        finally:
            restore_failed: list[Path] = []
            for entry in applied:
                try:
                    subprocess.run(
                        self._restore_command(entry, username),
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                except (subprocess.CalledProcessError, OSError):
                    restore_failed.append(entry)
            if restore_failed:
                names = ", ".join(str(p) for p in restore_failed)
                raise RuntimeError(
                    "Windows ACL backend failed to restore deny-write "
                    f"ACEs for: {names}. The deny ACEs may still be in "
                    "place; remove them with "
                    f"icacls <path> /remove:d {username} /t."
                )
        assert completed is not None
        return _result(
            completed, self.name, self.enforcement, self.build_argv(argv, policy)
        )


def default_backends() -> list[SubprocessBackend]:
    """All rungs, in selection order; availability filters at runtime."""
    return [BwrapBackend(), SeatbeltBackend(), WindowsAclBackend()]


def select_backend(
    *,
    backend: str | None = None,
    backends: list[SubprocessBackend] | None = None,
) -> SubprocessBackend:
    """First available backend, or the named one, else fail closed.

    Never returns a "run unconfined" stand-in: no available
    backend is a refusal, not a fallback.
    """
    candidates = backends if backends is not None else default_backends()
    if backend is not None:
        for candidate in candidates:
            if candidate.name == backend and candidate.available():
                return candidate
        raise SandboxUnavailableError(
            f"Sandbox backend '{backend}' is unavailable "
            f"({SANDBOX_UNAVAILABLE}): refusing to run unconfined."
        )
    for candidate in candidates:
        if candidate.available():
            return candidate
    raise SandboxUnavailableError(
        f"No sandbox backend is available ({SANDBOX_UNAVAILABLE}): "
        "refusing to run unconfined."
    )


def run_sandboxed(
    argv: list[str] | tuple[str, ...],
    policy: SubprocessPolicy,
    *,
    backend: str | None = None,
    backends: list[SubprocessBackend] | None = None,
    timeout_seconds: float = 60.0,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> SandboxResult:
    """Run ``argv`` under the ladder for ``policy``.

    The child runs with the policy's workspace as its working
    directory unless ``cwd`` names a path inside the workspace.
    A missing backend, an unavailable named backend, an empty
    argv, or a cwd outside the workspace all refuse before the
    child executes. Enforcement (full/partial) is reported on
    the result; consult it, do not assume full confinement.
    """
    argv_list = list(argv)
    if not argv_list:
        raise ValueError("run_sandboxed requires a non-empty argv.")
    workspace = policy.workspace_root
    run_cwd = workspace if cwd is None else Path(cwd).resolve()
    if run_cwd != workspace and workspace not in run_cwd.parents:
        raise ValueError("Sandboxed cwd must be inside the policy workspace root.")
    chosen = select_backend(backend=backend, backends=backends)
    return chosen.run(
        argv_list,
        policy,
        timeout_seconds=timeout_seconds,
        env=env,
        cwd=run_cwd,
    )
