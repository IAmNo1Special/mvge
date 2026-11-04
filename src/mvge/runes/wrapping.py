"""Phase 5b whole-process wrapping: an opt-in deployment posture.

This module wraps a Phase 4 host process (``mvge.runes.host_server``)
in an OS/container boundary. It does NOT change the default:
unwrapped is the default, and wrapping requires an explicit
``WrappingPolicy(enabled=True, backend=...)`` from the operator.
There is no silent fallback: an enabled policy whose backend is
unavailable, unknown, or whose mounts fail validation refuses
with ``WRAPPING_UNAVAILABLE`` before any host process starts.

What a wrapped deployment confines, and what it does not:

- Mounts: the workspace (read-only, except declared write roots,
  which are mounted writable), the rune directory (read-only,
  bound last so it stays read-only even under a declared write
  root), plus read-only runtime binds the host needs to run
  (interpreter prefix, engine source tree, system directories).
  Nothing else from the host filesystem is visible: no home
  directory, no ``~/.agents`` tree, no credential directories.
  A mount of the home directory itself, of ``~/.agents``, or of
  a credential directory is refused at validation time.
- Credentials: the host environment is the Phase 4 scrubbed
  environment, and wrapping asserts after scrubbing that no
  credential-shaped name survived (``assert_no_credential_env``).
  Docker adds a second guarantee by construction: ``docker run``
  does not inherit host environment variables, and the Docker
  backend passes no ``-e``/``--env`` flags at all. Credentials
  cross to a wrapped host only over the Phase 4 wire, never as
  environment or files. Credential-named files inside a mount
  (``.env``, ``*.pem``, ``*.key``) are reported by
  ``credential_files_in_mounts`` and fail the deployment proof
  (``scripts/verify_wrapped_deployment.py``): the engine cannot
  know whether a workspace ``.env`` is meant for the rune, so
  the operator must remove it before claiming a wrapped
  deployment is credential-free.
- Network: all-or-none in v1. Denied means bwrap
  ``--unshare-net`` or Docker ``--network none``. Allowed means
  the host network (bwrap) or the default bridge network
  (Docker): there is NO per-destination egress allowlist in v1,
  and ``network_description`` says so. A policy that needs a
  destination allowlist is not satisfied by this module.
- Platforms: bwrap is Linux-only and full; Docker is available
  wherever a Docker daemon runs (Linux, macOS, Windows/WSL2
  with Linux containers) and full under that daemon. There is
  no native Windows wrapping backend in v1: on Windows without
  Docker, wrapping refuses, and the honest posture is the Phase
  5a ACL ladder alone -- write-confined, read/network
  documented gap. Windows containers are untested and not
  claimed. macOS without Docker likewise refuses: Seatbelt is
  a Phase 5a write policy, not whole-process wrapping, and is
  deliberately not a wrapping backend.

Composition, stated so it can be checked:

- Phase 4: wrapping wraps the host command only. The host still
  speaks the Phase 4 wire, the engine-side capability gate in
  ``HostSession`` still applies to every wire registration,
  and ``classify_manifest`` still refuses the in-process-only
  tier before any process starts. Validation here refuses an
  in-process-only manifest outright, so wrapping cannot be
  used to claim confinement for a rune the host will not host.
- Phase 5a: ``WrappingPolicy.from_subprocess_policy`` derives
  a wrapping policy from a ladder policy, so write roots and
  the network flag cannot diverge silently between the two
  layers. The outer boundary is stricter than the ladder
  (unbound paths are invisible, not merely unwritable), and
  a ladder run nested inside a wrapped host is best-effort:
  nested bwrap may fail inside a namespace, and that failure
  is fail-closed via ``SANDBOX_UNAVAILABLE`` -- it is not
  claimed as proven here.
- Phase 3: wrapping does not replace pinning. Pin verification
  happens at load, before a host is started, and a wrapped
  host still runs the pinned tree. Pinned means unmodified,
  not benign, wrapped or not.
- Phase 5a escalation (F3, approved one-time widening) is
  still deferred: it is not built here, and widening a wrapped
  deployment means constructing a new, wider policy --
  exactly one explicit operator act, never a runtime widening.

Proven per deployment, not by the unit suite alone: the suite
proves the policy, argv construction, and (where bwrap is
installed) live confinement probes, but a deployment is only
claimed confined after ``scripts/verify_wrapped_deployment.py``
passes against that deployment's own workspace, backend, and
mounts. See ``docs/agents/rune-whole-process-wrapping-5b.md``
for the per-platform matrix and the proof procedure.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

import mvge
from mvge.runes.host_classification import classify_manifest
from mvge.runes.host_protocol import is_credential_env_name
from mvge.runes.subprocess_policy import SubprocessPolicy
from mvge.runes.types import RuneManifest

WRAPPING_UNAVAILABLE = "WRAPPING_UNAVAILABLE"

_SYSTEM_RO_BINDS: tuple[str, ...] = ("/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc")

_FORBIDDEN_HOME_CHILDREN: frozenset[str] = frozenset(
    {".agents", ".ssh", ".aws", ".gnupg", ".docker"}
)

_CREDENTIAL_FILE_NAMES: frozenset[str] = frozenset(
    {".env", ".netrc", "credentials.json"}
)

_CREDENTIAL_FILE_SUFFIXES: tuple[str, ...] = (".pem", ".key")

_SCAN_EXCLUDED_DIRS: frozenset[str] = frozenset(
    {"__pycache__", ".git", ".venv", "venv", ".ruff_cache", ".pytest_cache"}
)


class WrappingUnavailableError(Exception):
    """Named fail-closed refusal: wrapping cannot be provided, never unwrapped."""

    code = WRAPPING_UNAVAILABLE


@dataclass(frozen=True)
class Mount:
    """One host path bound into the wrapped process."""

    host_path: Path
    container_path: Path
    mode: Literal["ro", "rw"]


def is_forbidden_mount(path: Path) -> bool:
    """True when a mount would expose a home or credential tree.

    Forbidden: the home directory itself, any ancestor of home
    (which contains home), and the credential-bearing children
    of home (``.agents``, ``.ssh``, ``.aws``, ``.gnupg``,
    ``.docker``). A workspace or rune directory *under* home is
    not forbidden by this rule: it is a specific subtree, not
    the home tree, and it is validated as a policy mount.
    """
    resolved = Path(path).resolve()
    home = Path.home().resolve()
    if resolved == home or resolved in home.parents:
        return True
    return resolved.parent == home and resolved.name in _FORBIDDEN_HOME_CHILDREN


def assert_no_credential_env(env: dict[str, str]) -> None:
    """Refuse when a credential-shaped name survives scrubbing.

    This runs after the Phase 4 scrub, so any hit here is a
    scrub failure, not a policy choice: refuse, never launch.
    """
    for name in env:
        if is_credential_env_name(name):
            raise WrappingUnavailableError(
                f"Credential-shaped environment variable {name!r} would "
                f"reach a wrapped host ({WRAPPING_UNAVAILABLE}): refusing "
                "to launch. Scrub the environment before wrapping."
            )


def credential_files_in_mounts(mounts: list[Path] | tuple[Path, ...]) -> list[Path]:
    """Existing credential-named files under the given mounts.

    Scans recursively, skipping caches, VCS metadata, and
    dependency venvs. A hit is not proof of a leak by itself --
    a workspace ``.env`` may be intended for the rune -- but a
    wrapped deployment that claims to be credential-free must
    have none, and the deployment proof fails on any hit.
    """
    found: list[Path] = []
    for mount in mounts:
        root = Path(mount)
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in root.walk():
            dirnames[:] = [d for d in dirnames if d not in _SCAN_EXCLUDED_DIRS]
            for filename in filenames:
                candidate = dirpath / filename
                if (
                    filename in _CREDENTIAL_FILE_NAMES
                    or filename.startswith("id_rsa")
                    or filename.endswith(_CREDENTIAL_FILE_SUFFIXES)
                ):
                    found.append(candidate)
    return sorted(found)


def _runtime_ro_binds() -> list[Path]:
    """Read-only binds the wrapped host needs in order to run.

    The interpreter prefix (a uv-managed interpreter lives
    outside the system directories) and the engine source tree
    (an editable install points at the repo ``src/``). Both the
    unresolved interpreter prefix (a ``.venv`` whose
    ``pyvenv.cfg`` gives the child its site-packages) and every
    prefix along the symlink chain to the real interpreter are
    bound: a uv-managed interpreter chains through an
    intermediate directory, and binding only the final prefix
    leaves the chain broken inside the namespace (the child
    then fails with a missing executable, not a clean error).
    Resolving the executable alone would break venv discovery,
    and binding only the venv would hide the real interpreter.
    A bind already covered by a system directory is dropped.
    """
    binds: list[Path] = []
    chain: list[Path] = []
    current = Path(sys.executable)
    for _ in range(10):
        chain.append(current)
        if not current.is_symlink():
            break
        target = Path(os.readlink(current))
        current = target if target.is_absolute() else current.parent / target
    chain.append(Path(sys.executable).resolve())
    for exe in chain:
        prefix = exe.parent.parent
        if (
            prefix.exists()
            and prefix not in binds
            and not any(
                Path(bound) in prefix.parents or prefix == Path(bound)
                for bound in _SYSTEM_RO_BINDS
            )
        ):
            binds.append(prefix)
    engine_root = Path(mvge.__file__).resolve().parent.parent
    if engine_root.exists() and not any(
        Path(bound) in engine_root.parents or engine_root == Path(bound)
        for bound in _SYSTEM_RO_BINDS
    ):
        binds.append(engine_root)
    return binds


@dataclass(frozen=True)
class WrappingPolicy:
    """An operator's explicit choice to wrap a host process.

    ``enabled=False`` is the default and means unwrapped, exactly
    as Phase 4 ships. ``enabled=True`` requires a backend name;
    validation (not construction) checks backend availability,
    so a policy can be described before its backend is probed.
    """

    workspace_root: Path
    enabled: bool = False
    backend: str | None = None
    rune_dir: Path | None = None
    write_roots: tuple[Path, ...] = ()
    allow_network: bool = False
    extra_read_only_mounts: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        workspace = Path(self.workspace_root).resolve()
        object.__setattr__(self, "workspace_root", workspace)
        home = Path.home().resolve()
        if workspace == home or workspace in home.parents:
            raise ValueError(
                "A wrapping workspace must be a subtree of home, not home "
                "itself or an ancestor of it: mounting it would expose "
                "the whole home tree to the wrapped process."
            )
        kept: list[Path] = []
        for root in self.write_roots:
            resolved = Path(root).resolve()
            if resolved != workspace and workspace not in resolved.parents:
                raise ValueError(
                    f"Wrapping write root {root} is outside the workspace "
                    f"{workspace}: wrapping never mounts a write root "
                    "outside the workspace."
                )
            if resolved not in kept:
                kept.append(resolved)
        object.__setattr__(self, "write_roots", tuple(kept))
        if self.rune_dir is not None:
            object.__setattr__(self, "rune_dir", Path(self.rune_dir).resolve())
        object.__setattr__(
            self,
            "extra_read_only_mounts",
            tuple(Path(p).resolve() for p in self.extra_read_only_mounts),
        )

    @classmethod
    def from_subprocess_policy(
        cls,
        ladder: SubprocessPolicy,
        *,
        backend: str,
        rune_dir: Path | None = None,
    ) -> WrappingPolicy:
        """Derive a wrapping policy from a Phase 5a ladder policy.

        Write roots and the network flag come from the ladder,
        so the two layers cannot diverge silently. The wrapping
        policy is enabled by construction: calling this is the
        operator's explicit opt-in.
        """
        return cls(
            workspace_root=ladder.workspace_root,
            enabled=True,
            backend=backend,
            rune_dir=rune_dir,
            write_roots=tuple(ladder.write_roots),
            allow_network=ladder.allow_network,
        )

    def allows_write(self, path: str | Path) -> bool:
        """True iff a write to ``path`` falls under a declared root."""
        resolved = Path(path).resolve()
        return any(
            resolved == root or root in resolved.parents for root in self.write_roots
        )

    @property
    def network_description(self) -> str:
        """Plain-language network posture, including the v1 gap."""
        if not self.allow_network:
            if self.backend == "docker":
                return "none (docker --network none)"
            if self.backend == "bwrap":
                return "none (unshared network namespace)"
            return "none"
        if self.backend == "docker":
            return "default bridge network (no allowlist)"
        if self.backend == "bwrap":
            return "host network (no allowlist)"
        return "unrestricted (no allowlist)"

    def mounts(self) -> list[Mount]:
        """Policy mounts, in application order.

        The workspace comes first (read-only unless it is itself
        a declared write root); declared write roots override it
        for their subtrees; extra read-only mounts follow; the
        rune directory is bound last and read-only, so rune code
        stays read-only even under a declared write root.
        Runtime binds (interpreter, engine source, system dirs)
        are backend-internal and are not policy mounts.
        """
        workspace = self.workspace_root
        result: list[Mount] = [
            Mount(
                host_path=workspace,
                container_path=workspace,
                mode="rw" if workspace in self.write_roots else "ro",
            )
        ]
        for root in self.write_roots:
            if root != workspace:
                result.append(Mount(host_path=root, container_path=root, mode="rw"))
        for extra in self.extra_read_only_mounts:
            result.append(Mount(host_path=extra, container_path=extra, mode="ro"))
        if self.rune_dir is not None:
            result.append(
                Mount(
                    host_path=self.rune_dir,
                    container_path=self.rune_dir,
                    mode="ro",
                )
            )
        return result

    def describe(self) -> dict[str, Any]:
        """Operator-readable summary for logs and the proof report."""
        return {
            "enabled": self.enabled,
            "backend": self.backend,
            "workspace_root": str(self.workspace_root),
            "write_roots": [str(p) for p in self.write_roots],
            "allow_network": self.allow_network,
            "network": self.network_description,
            "mounts": [
                {
                    "host": str(m.host_path),
                    "container": str(m.container_path),
                    "mode": m.mode,
                }
                for m in self.mounts()
            ],
        }


class WrappingBackend(Protocol):
    """One whole-process wrapping mechanism."""

    name: str
    enforcement: Literal["full", "partial"]

    def available(self) -> bool:
        """True when this platform and tool can actually wrap."""
        ...

    def build_argv(self, argv: list[str], policy: WrappingPolicy) -> list[str]:
        """Wrap ``argv`` in the backend's confinement invocation."""
        ...


class BwrapWrappingBackend:
    """Linux bubblewrap backend: full enforcement.

    Mount order is load-bearing and mirrors the Phase 5a
    ladder: the scratch tmpfs comes before the workspace bind,
    or a workspace under ``/tmp`` would be hidden by it.
    Unbound paths are invisible to the child, so reads outside
    the mounts fail instead of being denied after the fact.
    """

    name = "bwrap"
    enforcement: Literal["full", "partial"] = "full"

    def available(self) -> bool:
        return sys.platform.startswith("linux") and shutil.which("bwrap") is not None

    def build_argv(self, argv: list[str], policy: WrappingPolicy) -> list[str]:
        # argv[0] is deliberately NOT resolved: a venv interpreter
        # is a symlink, and resolving it would make the child run
        # the bare interpreter (losing pyvenv.cfg, hence the
        # venv's site-packages and the editable engine install).
        # _runtime_ro_binds binds both the venv prefix and the
        # resolved prefix, so the symlink and its target are
        # both reachable inside the namespace.
        resolved_argv = list(argv)
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
        command += ["--dev", "/dev", "--tmpfs", "/tmp"]
        for mount in policy.mounts():
            flag = "--bind" if mount.mode == "rw" else "--ro-bind"
            command += [flag, str(mount.host_path), str(mount.container_path)]
        if Path("/proc").exists():
            command += ["--ro-bind", "/proc", "/proc"]
        for bound in _SYSTEM_RO_BINDS:
            if Path(bound).exists():
                command += ["--ro-bind", bound, bound]
        for extra in _runtime_ro_binds():
            command += ["--ro-bind", str(extra), str(extra)]
        command += ["--", *resolved_argv]
        return command


class DockerWrappingBackend:
    """Docker backend: full enforcement under a Linux-container daemon.

    The container filesystem is read-only with a tmpfs ``/tmp``;
    only policy mounts are visible, at identity paths so paths
    carried on the host wire still resolve. No host environment
    is passed: ``docker run`` does not inherit host variables,
    and no ``-e``/``--env`` flag is ever added, so no credential
    can reach the container through the environment. The image
    must provide ``python3`` and the ``mvge`` package; a host
    interpreter path in ``argv[0]`` is therefore mapped to
    ``python3`` inside the container (documented, not hidden).
    Windows containers are untested and not claimed: on
    Windows this backend is full only under WSL2 with Linux
    containers, which is a deployment prerequisite, stated as
    such in the docs.
    """

    name = "docker"
    enforcement: Literal["full", "partial"] = "full"

    def available(self) -> bool:
        return shutil.which("docker") is not None

    def build_argv(self, argv: list[str], policy: WrappingPolicy) -> list[str]:
        mapped_argv = list(argv)
        if mapped_argv and "/" in mapped_argv[0]:
            mapped_argv[0] = "python3"
        workspace = str(policy.workspace_root)
        command: list[str] = ["docker", "run", "--rm", "-i", "--read-only"]
        command += ["--tmpfs", "/tmp"]
        if not policy.allow_network:
            command += ["--network", "none"]
        command += ["--workdir", workspace]
        for mount in policy.mounts():
            command += [
                "-v",
                f"{mount.host_path}:{mount.container_path}:{mount.mode}",
            ]
        command += mapped_argv
        return command


def default_wrapping_backends() -> list[WrappingBackend]:
    """All wrapping backends, in selection order; availability filters at runtime."""
    return [BwrapWrappingBackend(), DockerWrappingBackend()]


def select_wrapping_backend(
    name: str,
    *,
    backends: list[WrappingBackend] | None = None,
) -> WrappingBackend:
    """The named backend, or fail closed. There is no fallback."""
    candidates = backends if backends is not None else default_wrapping_backends()
    for candidate in candidates:
        if candidate.name == name:
            if candidate.available():
                return candidate
            raise WrappingUnavailableError(
                f"Wrapping backend '{name}' is unavailable "
                f"({WRAPPING_UNAVAILABLE}): refusing to run unwrapped."
            )
    raise WrappingUnavailableError(
        f"Unknown wrapping backend '{name}' "
        f"({WRAPPING_UNAVAILABLE}): refusing to run unwrapped."
    )


def validate_wrapping_policy(
    policy: WrappingPolicy,
    *,
    manifest: RuneManifest | None = None,
    backends: list[WrappingBackend] | None = None,
) -> WrappingPolicy:
    """Validate an enabled policy, fail-closed. Disabled policies pass through.

    Checks, in order: a backend is named; the backend is known
    and available; no mount exposes a home or credential tree;
    the rune directory (when given) is not a forbidden mount;
    and, when a manifest is given, the rune is hostable -- the
    in-process-only tier cannot be wrapped into a confinement
    claim, because the host itself will not host it.
    """
    if not policy.enabled:
        return policy
    if policy.backend is None:
        raise WrappingUnavailableError(
            "Wrapping is enabled but no backend is named "
            f"({WRAPPING_UNAVAILABLE}): wrapping requires an explicit "
            "operator choice of backend; refusing to run unwrapped."
        )
    select_wrapping_backend(policy.backend, backends=backends)
    for mount in policy.mounts():
        if mount.host_path != policy.workspace_root and is_forbidden_mount(
            mount.host_path
        ):
            raise WrappingUnavailableError(
                f"Wrapping mount {mount.host_path} would expose a home "
                f"or credential tree ({WRAPPING_UNAVAILABLE}): refusing "
                "to wrap."
            )
    if manifest is not None:
        verdict = classify_manifest(manifest)
        if not verdict.hostable:
            raise WrappingUnavailableError(
                f"Rune '{manifest.name}' is in-process-only in v1 "
                f"({verdict.reason}); it cannot be wrapped "
                f"({WRAPPING_UNAVAILABLE}). Its protection is Phase 3 "
                "pinning, stated as such."
            )
    return policy


def wrap_host_command(
    argv: list[str] | tuple[str, ...],
    policy: WrappingPolicy | None,
    *,
    backends: list[WrappingBackend] | None = None,
) -> list[str]:
    """Wrap a host argv under an enabled policy; unwrapped otherwise.

    Disabled or absent policies return ``argv`` unchanged: that
    is the default, and it is explicit at the call site, not a
    fallback inside the wrapper. An enabled policy that fails
    validation raises before any process starts.
    """
    argv_list = list(argv)
    if policy is None or not policy.enabled:
        return argv_list
    validate_wrapping_policy(policy, backends=backends)
    backend = select_wrapping_backend(str(policy.backend), backends=backends)
    return backend.build_argv(argv_list, policy)
