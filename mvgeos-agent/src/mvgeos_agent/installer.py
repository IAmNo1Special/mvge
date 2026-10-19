from __future__ import annotations

import contextlib
import json
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mvgeos_core.layers import agents_dir, extensions_dir
from mvgeos_runes.catalog import (
    DEFAULT_MARKETPLACE_URL,
    MVGES_SECTION,
    PROFILES_SECTION,
    fetch_catalog,
    local_catalog_candidate,
    resolve_artifact,
    section_of,
)
from mvgeos_runes.deps import confirm_python_deps_install
from mvgeos_runes.installer import install_rune

logger = logging.getLogger(__name__)

DEFAULT_PROFILES: dict[str, dict[str, Any]] = {
    "minimal": {
        "name": "minimal",
        "description": (
            "Minimal coding environment with standard safety gate and steering"
        ),
        "mvges": ["coding_mvge"],
        "runes": ["opencode-realm", "approval-rune", "steering-bridge"],
    },
    "coding": {
        "name": "coding",
        "description": (
            "Default flagship coding profile with active discovery, skills, "
            "diagnostics, checkpoints, and sub-agents"
        ),
        "mvges": ["coding_mvge"],
        "runes": [
            "opencode-realm",
            "approval-rune",
            "steering-bridge",
            "seeker",
            "skills-bridge",
            "session-search",
            "session-title",
            "lsp-bridge",
            "checkpoint-bridge",
            "submvge-bridge",
        ],
    },
    "autonomous": {
        "name": "autonomous",
        "description": (
            "Full autonomous suite with knowledge graphs, ADR tracking, "
            "MCP integration, self-modification, CodeAct REPL, and evals"
        ),
        "mvges": ["coding_mvge"],
        "runes": [
            "opencode-realm",
            "approval-rune",
            "steering-bridge",
            "seeker",
            "skills-bridge",
            "session-search",
            "session-title",
            "lsp-bridge",
            "checkpoint-bridge",
            "submvge-bridge",
            "okf-bridge",
            "adr-bridge",
            "mcp-bridge",
            "selfmod-bridge",
            "skill-evolution",
            "opentelemetry-bridge",
            "codeact-rune",
            "eval-bridge",
        ],
    },
}


# Allowlist for install/uninstall directory names. Anything outside this set
# (path separators, "..", URL-encoded tricks) is rejected before it can reach
# the filesystem.
_INSTALL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_install_name(name: str, *, kind: str) -> str:
    """Validate an install/uninstall name against the allowlist.

    Args:
        name: The directory name to validate.
        kind: Human label used in the error ("mvge" or "rune").

    Raises:
        ValueError: If the name is not a plain ``^[A-Za-z0-9_-]+$`` name.
    """
    if not _INSTALL_NAME_RE.fullmatch(name):
        raise ValueError(f"Invalid {kind} name {name!r}: must match ^[A-Za-z0-9_-]+$.")
    return name


def _dest_within_target(target: Path, name: str) -> Path:
    """Return ``target / name`` after asserting it stays inside ``target``.

    Defense-in-depth alongside :func:`_validate_install_name`: catches
    symlink swaps and other resolution tricks even for well-formed names.

    Raises:
        ValueError: If the resolved destination escapes the target directory.
    """
    dest = target / name
    resolved_target = target.resolve()
    resolved_dest = dest.resolve()
    if resolved_dest != resolved_target and not resolved_dest.is_relative_to(
        resolved_target
    ):
        raise ValueError(
            f"Install destination {str(dest)!r} escapes target directory "
            f"{str(target)!r}."
        )
    return dest


def _manifest_install_name(source_dir: Path, *, kind: str) -> str:
    """Read the install directory name from a staged source's manifest.

    The manifest ``name`` is the single source of truth for install, list,
    and uninstall, so the three commands always agree on where a mvge lives.

    Raises:
        ValueError: If the manifest is missing, unreadable, nameless, or
            the name fails the install-name allowlist (e.g. traversal).
    """
    manifest_path = source_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"Invalid {kind}: manifest.json missing.")
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid {kind}: manifest.json unreadable: {exc}") from exc
    name = data.get("name") if isinstance(data, dict) else None
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"Invalid {kind}: manifest.json missing 'name'.")
    return _validate_install_name(name.strip(), kind=kind)


def _clear_dest(dest: Path) -> None:
    """Remove an existing install destination (dir or file)."""
    if dest.exists():
        if dest.is_dir() and not dest.is_symlink():
            shutil.rmtree(dest)
        else:
            dest.unlink()


def fetch_marketplace_mvges(
    marketplace_url: str = DEFAULT_MARKETPLACE_URL,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Fetch available mvges (agents) from the marketplace index.

    Where the catalog lives and how it is fetched is ``mvgeos_runes.catalog``'s
    decision. This reads its ``mvges`` section.
    """
    try:
        catalog = fetch_catalog(marketplace_url, timeout=timeout)
    except Exception as exc:
        logger.warning(
            "Failed to fetch marketplace mvges from '%s': %s",
            marketplace_url,
            exc,
        )
        return {}
    return dict(section_of(catalog, MVGES_SECTION))


def list_installed_mvges(
    target_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """List installed mvges from the target directory."""
    target = target_dir.expanduser() if target_dir is not None else agents_dir()
    if not target.is_dir():
        return []

    installed: list[dict[str, Any]] = []
    for subdir in target.iterdir():
        if not subdir.is_dir():
            continue
        manifest_path = subdir / "manifest.json"
        agent_md_path = subdir / "agent.md"
        spells_dir = subdir / "spells"

        manifest: dict[str, Any] = {}
        if manifest_path.is_file():
            try:
                with manifest_path.open("r", encoding="utf-8-sig") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        manifest = data
            except Exception:
                pass

        spells: list[str] = manifest.get("spells", [])
        if not spells and spells_dir.is_dir():
            spells = [
                f.stem
                for f in sorted(spells_dir.iterdir())
                if f.is_file() and f.suffix == ".py" and not f.name.startswith("_")
            ]

        installed.append(
            {
                "name": manifest.get("name", subdir.name),
                "version": manifest.get("version", "unknown"),
                "description": manifest.get("description", ""),
                "runtime": manifest.get("runtime", "python"),
                "enabled": manifest.get("enabled", True),
                "path": str(subdir),
                "spells": spells,
                "has_manifest": manifest_path.is_file(),
                "has_agent_md": agent_md_path.is_file(),
            }
        )

    installed.sort(key=lambda r: str(r.get("name", "")))
    return installed


def uninstall_mvge(name: str, target_dir: Path | None = None) -> bool:
    """Uninstall an installed mvge by name."""
    target = target_dir.expanduser() if target_dir is not None else agents_dir()
    path = _dest_within_target(target, _validate_install_name(name, kind="mvge"))
    if path.exists():
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        return True
    return False


def install_mvge(
    source: str,
    target_dir: Path | None = None,
    marketplace_url: str = DEFAULT_MARKETPLACE_URL,
    confirm_python_deps: bool | None = None,
) -> Path:
    """Install an agent mvge from local path, Git repository, or marketplace.

    Args:
        source: Local path, git URL, or marketplace mvge name.
        target_dir: Install target directory (default ``~/.agents/agents``).
        marketplace_url: Marketplace index URL.
        confirm_python_deps: Gate for installing ``python_deps`` declared in
            the installed manifest. ``True`` installs without prompting,
            ``False`` skips, ``None`` (default) prompts interactively and
            fails closed (skips) in non-interactive sessions.
    """
    stripped_source = source.strip()
    if not stripped_source:
        raise ValueError("Mvge source cannot be empty.")

    target = target_dir.expanduser() if target_dir is not None else agents_dir()
    target.mkdir(parents=True, exist_ok=True)

    source_path = Path(stripped_source).expanduser()
    if source_path.exists():
        if not source_path.is_dir():
            raise ValueError(f"Invalid mvge: not a directory: {source_path}")
        # The manifest name is the single source of truth for the install
        # directory, so install/list/uninstall always agree (BUG-4). Read it
        # before touching the target: a bad manifest fails without copying.
        name = _manifest_install_name(source_path, kind="mvge")
        dest = _dest_within_target(target, name)
        if source_path.resolve() != dest.resolve():
            _clear_dest(dest)
            shutil.copytree(source_path, dest)
    elif stripped_source.startswith(
        ("git@", "git://", "http://", "https://", "ssh://")
    ) or stripped_source.endswith(".git"):
        # Preflight: reject obviously-malicious URLs before cloning. The
        # URL basename is only a sanity check here -- the manifest name
        # decides the actual install directory below.
        url_basename = stripped_source.rstrip("/").split("/")[-1].removesuffix(".git")
        _validate_install_name(url_basename, kind="mvge")
        # Clone to a staging dir first so the manifest name (not the URL
        # basename) decides the install directory.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            staged = Path(tmp_dir) / "repo"
            subprocess.run(
                ["git", "clone", "--", stripped_source, str(staged)],
                check=True,
                capture_output=True,
                text=True,
            )
            name = _manifest_install_name(staged, kind="mvge")
            dest = _dest_within_target(target, name)
            _clear_dest(dest)
            shutil.copytree(staged, dest)
    else:
        mvge_entry = resolve_artifact(
            stripped_source, section=MVGES_SECTION, marketplace_url=marketplace_url
        )
        git_url = mvge_entry.get("git")
        if not git_url or not isinstance(git_url, str):
            raise ValueError(f"Mvge '{stripped_source}' not found in marketplace.")

        _validate_install_name(stripped_source, kind="mvge")
        subpath = mvge_entry.get("path") if isinstance(mvge_entry, dict) else None
        # Stage in a temp dir first so the manifest name (not the marketplace
        # key) decides the install directory.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            if subpath:
                staged_path: Path
                try:
                    subprocess.run(
                        ["git", "clone", "--depth", "1", "--", git_url, str(tmp_dir)],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    staged_path = Path(tmp_dir) / str(subpath).strip().strip("/\\")
                except (subprocess.CalledProcessError, OSError):
                    candidate = local_catalog_candidate(subpath)
                    if candidate is None:
                        raise
                    staged_path = candidate
            else:
                subprocess.run(
                    ["git", "clone", "--", git_url, str(tmp_dir)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                staged_path = Path(tmp_dir)
            name = _manifest_install_name(staged_path, kind="mvge")
            dest = _dest_within_target(target, name)
            _clear_dest(dest)
            shutil.copytree(staged_path, dest)

    # The manifest was already validated from the staged source; dest carries
    # an identical copy.
    manifest_path = dest / "manifest.json"
    try:
        with manifest_path.open("r", encoding="utf-8-sig") as f:
            manifest_data = json.load(f)
        python_deps = manifest_data.get("python_deps")
        if python_deps and isinstance(python_deps, list):
            deps = [str(d) for d in python_deps if str(d).strip()]
            if deps:
                if confirm_python_deps_install(deps, confirm=confirm_python_deps):
                    subprocess.run(
                        ["uv", "add", *deps],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                else:
                    logger.warning(
                        "Skipping unreviewed python dependencies for %s: %s",
                        dest,
                        deps,
                    )
        if (dest / "pyproject.toml").is_file():
            with contextlib.suppress(Exception):
                subprocess.run(
                    ["uv", "add", "--editable", str(dest)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        pass

    return dest


@dataclass(frozen=True)
class ProfileInstallResult:
    profile: str
    installed_mvges: list[str]
    installed_runes: list[str]
    skipped_mvges: list[str]
    skipped_runes: list[str]


def fetch_marketplace_profiles(
    marketplace_url: str = DEFAULT_MARKETPLACE_URL,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Fetch available profiles from the marketplace index or fallback defaults."""
    try:
        catalog = fetch_catalog(marketplace_url, timeout=timeout)
        profiles = section_of(catalog, PROFILES_SECTION)
        if profiles:
            return dict(profiles)
    except Exception as exc:
        logger.warning(
            "Failed to fetch marketplace profiles from '%s': %s",
            marketplace_url,
            exc,
        )
    return dict(DEFAULT_PROFILES)


def install_profile(
    profile_name: str,
    target_agents_dir: Path | None = None,
    target_extensions_dir: Path | None = None,
    marketplace_url: str = DEFAULT_MARKETPLACE_URL,
    confirm_python_deps: bool | None = None,
    force: bool = False,
) -> ProfileInstallResult:
    """Install all mvges and runes declared by a named profile.

    Args:
        profile_name: Named profile ("minimal", "coding", "autonomous", etc.).
        target_agents_dir: Directory to install mvges into (default agents_dir()).
        target_extensions_dir: Directory to install runes into (default
            extensions_dir()).
        marketplace_url: Marketplace index URL.
        confirm_python_deps: Gate for installing python dependencies.
        force: If True, reinstall already installed mvges and runes.
    """
    profiles = fetch_marketplace_profiles(marketplace_url=marketplace_url)
    profile_data = profiles.get(profile_name)
    if profile_data is None:
        profile_data = DEFAULT_PROFILES.get(profile_name)
    if not isinstance(profile_data, dict):
        available = ", ".join(sorted(profiles or DEFAULT_PROFILES))
        raise ValueError(f"Unknown profile '{profile_name}'. Available: {available}.")

    agents_target = (
        target_agents_dir.expanduser()
        if target_agents_dir is not None
        else agents_dir()
    )
    extensions_target = (
        target_extensions_dir.expanduser()
        if target_extensions_dir is not None
        else extensions_dir()
    )

    installed_mvges: list[str] = []
    skipped_mvges: list[str] = []
    for mvge_name in profile_data.get("mvges", []):
        dest = agents_target / mvge_name
        if dest.is_dir() and not force:
            skipped_mvges.append(mvge_name)
            continue
        install_mvge(
            mvge_name,
            target_dir=agents_target,
            marketplace_url=marketplace_url,
            confirm_python_deps=confirm_python_deps,
        )
        installed_mvges.append(mvge_name)

    installed_runes: list[str] = []
    skipped_runes: list[str] = []
    for rune_name in profile_data.get("runes", []):
        dest = extensions_target / rune_name
        if dest.is_dir() and not force:
            skipped_runes.append(rune_name)
            continue
        install_rune(
            rune_name,
            target_dir=extensions_target,
            marketplace_url=marketplace_url,
            confirm_python_deps=confirm_python_deps,
        )
        installed_runes.append(rune_name)

    return ProfileInstallResult(
        profile=profile_name,
        installed_mvges=installed_mvges,
        installed_runes=installed_runes,
        skipped_mvges=skipped_mvges,
        skipped_runes=skipped_runes,
    )
