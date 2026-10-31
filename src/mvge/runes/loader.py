from __future__ import annotations

import contextlib
import importlib.util
import inspect
import re
import site
import sys
from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import cast

from packaging.requirements import InvalidRequirement, Requirement

from mvge.core.layers import extensions_dir
from mvge.core.proxy import sanitize_no_proxy_env
from mvge.runes.installer import read_or_create_install_id
from mvge.runes.manifest import load_manifest
from mvge.runes.rune_api import RuneFactory
from mvge.runes.trust import (
    WorkspaceTrustStatus,
    WorkspaceTrustStore,
    canonicalize_workspace_path,
)
from mvge.runes.types import (
    Diagnostic,
    DiagnosticKind,
    RuneLoad,
    RuneManifest,
    RuneScope,
)


def check_python_dep_installed(dep: str) -> bool:
    """Return True if a declared python dependency is satisfied.

    ``dep`` is a PEP 508 requirement string: bare names (``httpx``), version
    pins (``httpx>=0.27``), extras (``uvicorn[standard]``), environment markers
    (``pywin32; sys_platform == 'win32'``), and direct references
    (``goapauto @ git+https://github.com/IAmNo1Special/goapauto@main``).
    Satisfaction is resolved against installed-distribution metadata, so
    distribution names that differ from their importable top-level module
    (``python-dotenv`` -> ``dotenv``, ``PyYAML`` -> ``yaml``) are handled.
    A version pin alone never fails the check: any installed distribution of
    that name counts, matching the installer's ``uv add`` behavior.
    """
    requirement = _parse_requirement(dep)
    if requirement is None:
        return _legacy_module_present(dep)
    if requirement.marker is not None and not requirement.marker.evaluate():
        return True  # Dependency does not apply to this environment.
    top_levels = _installed_top_levels(requirement.name)
    if top_levels is None:
        # No installed distribution under that name: fall back to the
        # import-name guess so stdlib modules (``json``) still resolve.
        return _module_importable(requirement.name.replace("-", "_"))
    return any(_module_importable(top) for top in top_levels)


def _parse_requirement(dep: str) -> Requirement | None:
    """Parse a PEP 508 requirement string, returning None when it is not one."""
    try:
        return Requirement(dep)
    except InvalidRequirement:
        return None


def _installed_top_levels(dist_name: str) -> list[str] | None:
    """Return the importable top-level modules of an installed distribution.

    Returns None when no distribution of that name is installed.
    """
    try:
        dist = distribution(dist_name)
    except PackageNotFoundError:
        return None
    top_level = dist.read_text("top_level.txt")
    if top_level:
        names = [
            line.strip()
            for line in top_level.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        if names:
            return names
    return [dist_name.replace("-", "_")]


def _module_importable(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ModuleNotFoundError, ValueError):
        return False


def _legacy_module_present(dep: str) -> bool:
    """Pre-PEP 508 fallback: split off version/marker/extras, probe the head."""
    base_name = re.split(r"[><=!~;\[]", dep)[0].strip().replace("-", "_")
    return bool(base_name) and _module_importable(base_name)


def _missing_dep_diagnostic(manifest: RuneManifest, dep: str) -> Diagnostic:
    """Build the MISSING_DEP diagnostic for a single uninstalled dependency."""
    return Diagnostic(
        kind=DiagnosticKind.MISSING_DEP,
        rune_name=manifest.name,
        message=(
            f"Rune '{manifest.name}' requires python dependency '{dep}' "
            "which is not installed. Run 'mvge setup install' to install it."
        ),
        scope=manifest.scope,
        path=manifest.path,
    )


def _venv_site_packages(base: Path) -> list[Path]:
    """Return the site-packages directories of ``base``'s venvs, if any."""
    site_pkg_paths: list[Path] = []
    for venv_name in (".venv", "venv"):
        venv_dir = base / venv_name
        if not venv_dir.is_dir():
            continue
        win_sp = venv_dir / "Lib" / "site-packages"
        if win_sp.is_dir() and win_sp not in site_pkg_paths:
            site_pkg_paths.append(win_sp)
        lib_dir = venv_dir / "lib"
        if lib_dir.is_dir():
            posix_sp = lib_dir / "site-packages"
            if posix_sp.is_dir() and posix_sp not in site_pkg_paths:
                site_pkg_paths.append(posix_sp)
            for sp in sorted(lib_dir.glob("python*/site-packages")):
                if sp.is_dir() and sp not in site_pkg_paths:
                    site_pkg_paths.append(sp)
    return site_pkg_paths


def discover_rune_site_packages(rune_dir: Path) -> list[Path]:
    """Return rune-local import roots (venv site-packages, entry dir, rune dir).

    A rune may bundle python_deps in its own ``.venv``/``venv`` site-packages;
    these must be on ``sys.path`` *before* dependency inspection so bundled
    deps are not falsely reported as missing.

    This covers only the rune's own directory and its parents. It does
    NOT cover the shared extensions environment: ``setup install`` always
    targets that one project whatever layer the rune lives in, so a rune
    outside the user layer never has the shared venv among its parents.
    See :func:`shared_extensions_site_packages` for that environment.
    """
    rune_dir_resolved = rune_dir.resolve()
    site_pkg_paths: list[Path] = []
    search_dirs = [rune_dir_resolved] + list(rune_dir_resolved.parents)[:2]
    for candidate_dir in search_dirs:
        for sp in _venv_site_packages(candidate_dir):
            if sp not in site_pkg_paths:
                site_pkg_paths.append(sp)
    site_pkg_paths.append(rune_dir_resolved)
    return site_pkg_paths


def shared_extensions_site_packages(
    env_project: Path | None = None,
) -> list[Path]:
    """Return the shared extensions environment's site-packages directories.

    The shared environment is the one project ``setup install`` targets
    (:func:`mvge.core.layers.extensions_dir` by default): its venv is where
    installed rune dependencies actually land. Probing the host interpreter
    alone therefore disagrees with a successful install for every dep that
    is not also a host dependency. Returns ``[]`` when the environment
    has no venv yet (nothing installed).
    """
    project = env_project if env_project is not None else extensions_dir()
    return _venv_site_packages(project)


def inject_dependency_site_packages(
    rune_dirs: Sequence[Path], env_project: Path | None = None
) -> None:
    """Put dependency import roots on ``sys.path`` for later probing.

    Injects each rune's discovered site-packages first (rune-local roots
    keep their established precedence), then the shared extensions
    environment last, so a dep installed by ``setup install`` resolves
    here exactly as it does at rune load time. ``setup check`` and
    ``setup install``'s missing computation call this before probing, so
    they inspect the same environment the install targets and the loader
    uses -- never the host interpreter alone.
    """
    for rune_dir in rune_dirs:
        for p in discover_rune_site_packages(rune_dir):
            p_str = str(p)
            if p.name == "site-packages":
                site.addsitedir(p_str)
            elif p_str not in sys.path:
                sys.path.insert(0, p_str)
    for sp in shared_extensions_site_packages(env_project):
        site.addsitedir(str(sp))


def _inject_rune_paths(rune_dir: Path, entry: Path) -> None:
    """Put rune-local import roots on ``sys.path`` so bundled deps resolve."""
    inject_dependency_site_packages([rune_dir])
    entry_parent = entry.parent.resolve()
    entry_parent_str = str(entry_parent)
    if entry_parent_str not in sys.path:
        sys.path.insert(0, entry_parent_str)


def preflight_rune_deps(
    manifest: RuneManifest,
) -> list[str]:
    """Return the list of declared python_deps that are not installed."""
    return [dep for dep in manifest.python_deps if not check_python_dep_installed(dep)]


def load_factory_from_manifest(
    manifest: RuneManifest,
    rune_dir: Path,
    diagnostics: list[Diagnostic] | None = None,
) -> RuneFactory | None:
    if not manifest.entry_point:
        return None

    # The install id is installer-owned: if the rune was not installed
    # through the installer (or the file was lost), the host generates one
    # at load so policy binding always has an id to stamp.
    read_or_create_install_id(rune_dir)

    # Check for non-Python runtime (e.g., TypeScript/JavaScript extensions for Pi)
    if getattr(manifest, "runtime", "").lower() in (
        "typescript",
        "ts",
        "javascript",
        "js",
        "node",
        "bun",
    ) or manifest.entry_point.endswith((".ts", ".js", ".mjs", ".cjs")):
        if diagnostics is not None:
            diagnostics.append(
                Diagnostic(
                    kind=DiagnosticKind.INCOMPATIBLE_RUNTIME,
                    rune_name=manifest.name,
                    message=(
                        f"Skipping extension '{manifest.name}': targets "
                        f"'{getattr(manifest, 'runtime', 'typescript')}' runtime"
                        " (Python host)"
                    ),
                    scope=manifest.scope,
                    path=manifest.path,
                )
            )
        return None

    entry = (rune_dir / manifest.entry_point).resolve()

    # Inject rune-local import roots (including any bundled venv) BEFORE
    # dependency inspection, so deps shipped inside the rune are not
    # falsely reported as missing.
    _inject_rune_paths(rune_dir, entry)

    missing = preflight_rune_deps(manifest)
    if missing:
        if diagnostics is not None:
            for dep in missing:
                diagnostics.append(_missing_dep_diagnostic(manifest, dep))
        return None

    if not entry.exists():
        if diagnostics is not None:
            diagnostics.append(
                Diagnostic(
                    kind=DiagnosticKind.LOAD_FAILURE,
                    rune_name=manifest.name,
                    message=f"Entry point file does not exist: {entry}",
                    scope=manifest.scope,
                    path=manifest.path,
                )
            )
        return None

    spec = importlib.util.spec_from_file_location(
        f"mvge_rune_{manifest.name}", str(entry)
    )

    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    # Register before executing. This is a documented importlib requirement,
    # not bookkeeping: a module that defines a @dataclass, a NamedTuple, or
    # anything calling typing.get_type_hints() looks itself up in
    # sys.modules while its own body is still running. Without this line the
    # lookup returns None and the entry point dies with
    # "'NoneType' object has no attribute '__dict__'".
    sys.modules[spec.name] = mod
    # Force a fresh read of the entry point: a quick same-second rewrite
    # can leave a stale __pycache__ entry the loader would otherwise trust,
    # silently re-executing old code on refresh. Best-effort: on Windows,
    # a concurrent load may hold the bytecode file open, so a locked file
    # is left alone rather than failing the load.
    stale_bytecode = Path(importlib.util.cache_from_source(str(entry)))
    with contextlib.suppress(OSError):
        stale_bytecode.unlink(missing_ok=True)
    # Before executing any rune module code: a module top-level may
    # build an httpx client, and client construction parses NO_PROXY
    # and raises on bracketed IPv6 tokens. Library embeddings never
    # ran CLI startup, so without this the rune would fail to load
    # for purely environmental reasons. The mutation is process-wide
    # and persists, so the factory and every later spell, sigil, and
    # realm-factory call in this process inherit the clean value --
    # unless rune code itself re-dirties it afterwards (see the
    # factory-call sites in RuneRunner, which sanitize again before
    # each factory). Only bracketed tokens are removed; this rewrites
    # no rune client and patches nothing.
    sanitize_no_proxy_env()
    try:
        spec.loader.exec_module(mod)
    except Exception as err:
        # Do not leave a half-initialised module behind for the next import
        # to trust.
        sys.modules.pop(spec.name, None)
        if diagnostics is not None:
            msg = f"Failed to execute rune module {manifest.name}: {err}"
            if manifest.python_deps:
                msg += f" (declared python_deps: {', '.join(manifest.python_deps)})"
            diagnostics.append(
                Diagnostic(
                    kind=DiagnosticKind.LOAD_FAILURE,
                    rune_name=manifest.name,
                    message=msg,
                    scope=manifest.scope,
                    path=manifest.path,
                )
            )
        return None
    factory = getattr(mod, "rune_factory", None)
    if factory is None or not callable(factory):
        if diagnostics is not None:
            diagnostics.append(
                Diagnostic(
                    kind=DiagnosticKind.LOAD_FAILURE,
                    rune_name=manifest.name,
                    message=(
                        f"Rune '{manifest.name}' does not export a callable "
                        "'rune_factory' function"
                    ),
                    scope=manifest.scope,
                    path=manifest.path,
                )
            )
        return None

    try:
        sig = inspect.signature(factory)
        params = list(sig.parameters.values())
        if not params and not any(
            p.kind == inspect.Parameter.VAR_POSITIONAL for p in params
        ):
            if diagnostics is not None:
                diagnostics.append(
                    Diagnostic(
                        kind=DiagnosticKind.LOAD_FAILURE,
                        rune_name=manifest.name,
                        message=(
                            f"Rune factory '{manifest.name}' signature expects "
                            "at least 1 argument (RuneAPI)"
                        ),
                        scope=manifest.scope,
                        path=manifest.path,
                    )
                )
            return None
    except (ValueError, TypeError):
        pass

    return cast(RuneFactory, factory)


def find_project_root_from_path(path: Path) -> Path:
    """Find the project root containing .agents from a sub-path."""
    p = canonicalize_workspace_path(path)
    for parent in [p, *p.parents]:
        if parent.name == ".agents":
            return parent.parent
        if (parent / ".agents").is_dir():
            return parent
    return p


def load_manifests(
    extensions_dir: Path,
    scope: RuneScope = RuneScope.PROJECT,
    diagnostics: list[Diagnostic] | None = None,
) -> list[RuneManifest]:
    if not extensions_dir.exists():
        return []
    manifests: list[RuneManifest] = []
    for entry in sorted(extensions_dir.iterdir(), key=lambda e: e.name):
        if not entry.is_dir():
            continue
        if entry.name.startswith("."):
            # Hidden directories are tooling, not runes: the shared
            # python-deps environment lives here as ``.venv``, and
            # scanning it produced a bogus "could not parse manifest"
            # warning on every load after a --confirm-python-deps
            # install.
            continue
        manifest = load_manifest(entry)
        if manifest is None:
            if diagnostics is not None:
                diagnostics.append(
                    Diagnostic(
                        kind=DiagnosticKind.PARSE_WARNING,
                        rune_name=entry.name,
                        message=f"Could not parse manifest in {entry.name}",
                        scope=scope,
                        path=str(entry),
                    )
                )
            continue
        if not manifest.enabled:
            continue
        manifest.scope = scope
        manifest.path = str(entry)
        manifests.append(manifest)
    return manifests


def _resolve_search_path(path_str: str | Path, agent_name: str | None = None) -> Path:
    """Expand ~ and replace {agent_name} placeholder in a search path."""
    expanded = str(path_str).replace("{agent_name}", agent_name or "")
    return Path(expanded).expanduser()


def load_runes_from_paths(
    paths: Sequence[tuple[str | Path, RuneScope]],
    agent_name: str | None = None,
    *,
    trust_store: WorkspaceTrustStore | None = None,
) -> tuple[list[RuneLoad], list[Diagnostic]]:
    """Load runes from multiple paths in precedence order (first wins).

    Args:
        paths: List of (path, scope) tuples, paths can include ~ and
            {agent_name} placeholder
        agent_name: Agent name to substitute {agent_name} placeholder
        trust_store: Optional WorkspaceTrustStore for verifying project trust

    Returns:
        Tuple of (rune_loads, diagnostics). RuneLoads are deduped
        by manifest name (first-wins), with the winner's scope recorded.
    """
    loads: list[RuneLoad] = []
    diagnostics: list[Diagnostic] = []
    seen_names: dict[str, tuple[RuneScope, str]] = {}

    for path_str, scope in paths:
        path = _resolve_search_path(path_str, agent_name)
        if not path.exists():
            continue
        if scope == RuneScope.PROJECT and (
            ".agents" in path.parts
            or (path / ".agents").is_dir()
            or trust_store is not None
        ):
            store = trust_store if trust_store is not None else WorkspaceTrustStore()
            project_root = find_project_root_from_path(path)
            trust_status = store.check_trust(project_root)
            if trust_status != WorkspaceTrustStatus.TRUSTED:
                diagnostics.append(
                    Diagnostic(
                        kind=DiagnosticKind.LOAD_WARNING,
                        rune_name=path.name,
                        message=(
                            f"Skipping project-level extensions at {path}: "
                            f"workspace {project_root} is untrusted "
                            f"({trust_status.value})"
                        ),
                        scope=scope,
                        path=str(path),
                    )
                )
                continue
        manifests = load_manifests(path, scope=scope, diagnostics=diagnostics)
        for manifest in manifests:
            factory = None
            if manifest.path:
                factory = load_factory_from_manifest(
                    manifest, Path(manifest.path), diagnostics=diagnostics
                )
            if manifest.name in seen_names:
                winner_scope, winner_path = seen_names[manifest.name]
                diagnostics.append(
                    Diagnostic(
                        kind=DiagnosticKind.SHADOWED_RUNE,
                        rune_name=manifest.name,
                        message=(
                            f"Rune '{manifest.name}' from {scope.value} "
                            f"({path}) shadowed by {winner_scope.value} "
                            f"({winner_path})"
                        ),
                        scope=scope,
                        path=str(path),
                    )
                )
                continue
            seen_names[manifest.name] = (scope, str(path))
            loads.append(RuneLoad(manifest=manifest, factory=factory))

    partitioned_loads = [r for r in loads if not r.manifest.spell_gateway] + [
        r for r in loads if r.manifest.spell_gateway
    ]
    return partitioned_loads, diagnostics
