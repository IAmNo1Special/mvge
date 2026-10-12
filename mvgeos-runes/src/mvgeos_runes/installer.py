from __future__ import annotations

import contextlib
import json
import logging
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from mvgeos_core.layers import extensions_dir, global_agents_dir

from mvgeos_runes.catalog import (
    DEFAULT_MARKETPLACE_URL,
    RUNES_SECTION,
    fetch_catalog,
    resolve_artifact,
    section_of,
)
from mvgeos_runes.deps import install_python_deps

logger = logging.getLogger(__name__)

# Re-exported because every caller of this module has always spelled it this
# way, and the rune installer's public signature names it as its default.
__all__ = ["DEFAULT_MARKETPLACE_URL", "install_rune", "uninstall_rune"]

# Allowlist for install/uninstall directory names. Anything outside this set
# (path separators, "..", URL-encoded tricks) is rejected before it can reach
# the filesystem.
_INSTALL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


# Name of the installer-owned install-id file written into every rune dir.
INSTALL_ID_FILENAME = ".install-id"

# Manifest name of the approval rune: uninstalling it purges its policy file.
APPROVAL_RUNE_NAME = "approval-rune"


def _approval_dir() -> Path:
    """User-owned approval data directory (``<global>/approval``).

    Separate from the rune's installed code directory on purpose: updates
    replace the code dir but never touch grants or history here.
    """
    return global_agents_dir() / "approval"


def read_or_create_install_id(rune_dir: Path) -> str:
    """Return the installer-owned install id for a rune directory.

    Generates a random UUIDv4 and writes it to ``<rune-dir>/.install-id``
    when absent. The file is installer-owned: preserved across updates,
    destroyed on uninstall. The policy layer stamps this id into the policy
    file so a policy from a different installation is never applied.
    """
    id_file = Path(rune_dir) / INSTALL_ID_FILENAME
    try:
        existing = id_file.read_text(encoding="utf-8").strip()
    except OSError:
        existing = ""
    if existing:
        return existing
    new_id = uuid.uuid4().hex
    with contextlib.suppress(OSError):
        id_file.write_text(new_id + "\n", encoding="utf-8")
    return new_id


def _install_tree(source: Path, dest: Path) -> None:
    """Copy a staged rune tree into place, preserving the install id.

    An existing ``.install-id`` survives the update; a fresh install gets a
    new UUIDv4.
    """
    id_file = dest / INSTALL_ID_FILENAME
    existing = ""
    if id_file.is_file():
        with contextlib.suppress(OSError):
            existing = id_file.read_text(encoding="utf-8").strip()
    _clear_dest(dest)
    shutil.copytree(source, dest)
    if existing:
        with contextlib.suppress(OSError):
            (dest / INSTALL_ID_FILENAME).write_text(existing + "\n", encoding="utf-8")
    else:
        read_or_create_install_id(dest)


def _manifest_name_or_none(rune_dir: Path) -> str | None:
    manifest_path = rune_dir / "manifest.json"
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, dict):
        name = data.get("name")
        return str(name) if name else None
    return None


def _purge_approval_policy() -> None:
    """Delete the approval rune's policy file on uninstall.

    Only ``policy.toml`` is removed (allow/deny rules, project approvals).
    The audit log (``audit.jsonl``) survives uninstall with its normal
    rotation; reinstalling starts with empty policy.
    """
    policy = _approval_dir() / "policy.toml"
    with contextlib.suppress(OSError):
        policy.unlink()


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
    and uninstall, so the three commands always agree on where a rune lives.

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


def fetch_marketplace_runes(
    marketplace_url: str = DEFAULT_MARKETPLACE_URL,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Fetch available runes from the marketplace index.

    Where the catalog lives and how it is fetched is ``mvgeos_runes.catalog``'s
    decision, not this one. This reads its ``runes`` section.
    """
    try:
        catalog = fetch_catalog(marketplace_url, timeout=timeout)
    except Exception as exc:
        logger.warning(
            "Failed to fetch marketplace runes from '%s': %s",
            marketplace_url,
            exc,
        )
        return {}
    return dict(section_of(catalog, RUNES_SECTION))


def list_installed_runes(
    target_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """List installed extension runes from the target directory."""
    target = target_dir.expanduser() if target_dir is not None else extensions_dir()
    if not target.is_dir():
        return []

    installed: list[dict[str, Any]] = []
    for subdir in target.iterdir():
        if not subdir.is_dir():
            continue
        manifest_path = subdir / "manifest.json"
        if manifest_path.is_file():
            try:
                with manifest_path.open("r", encoding="utf-8-sig") as f:
                    manifest = json.load(f)
            except Exception:
                continue

            if not isinstance(manifest, dict):
                continue

            raw_types = manifest.get("types")
            types: list[str] = []
            if isinstance(raw_types, list):
                types = [str(t) for t in raw_types if t]
            elif "type" in manifest and manifest["type"]:
                types = [str(manifest["type"])]
            raw_commands = manifest.get("commands", [])
            commands: list[str] = (
                [c for c in raw_commands if isinstance(c, str)]
                if isinstance(raw_commands, list)
                else []
            )
            installed.append(
                {
                    "name": manifest.get("name", subdir.name),
                    "version": manifest.get("version", "unknown"),
                    "description": manifest.get("description", ""),
                    "runtime": manifest.get("runtime", "python"),
                    "enabled": manifest.get("enabled", True),
                    "path": str(subdir),
                    "hooks": manifest.get("hooks", []),
                    "python_deps": manifest.get("python_deps", []),
                    "commands": commands,
                    "type": types[0] if types else manifest.get("type", ""),
                    "types": types,
                    "created_at": manifest.get("created_at", ""),
                    "updated_at": manifest.get(
                        "updated_at", manifest.get("last_updated", "")
                    ),
                }
            )

    installed.sort(key=lambda r: str(r.get("name", "")))
    return installed


def uninstall_rune(name: str, target_dir: Path | None = None) -> bool:
    """Uninstall an installed rune by name.

    Uninstalling the approval rune additionally purges its policy file
    (``<global>/approval/policy.toml``); the audit log is never touched.
    """
    target = target_dir.expanduser() if target_dir is not None else extensions_dir()
    path = _dest_within_target(target, _validate_install_name(name, kind="rune"))
    is_approval = (
        name == APPROVAL_RUNE_NAME or _manifest_name_or_none(path) == APPROVAL_RUNE_NAME
    )
    if path.exists():
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        if is_approval:
            _purge_approval_policy()
        return True
    return False


def set_rune_enabled(name: str, enabled: bool, target_dir: Path | None = None) -> bool:
    """Set the enabled flag in an installed rune's manifest.json.

    Returns True when the manifest was updated, False when the rune
    is not installed or its manifest cannot be read/written.
    """
    target = target_dir.expanduser() if target_dir is not None else extensions_dir()
    rune_dir = _dest_within_target(target, _validate_install_name(name, kind="rune"))
    manifest_path = rune_dir / "manifest.json"
    if not manifest_path.is_file():
        return False
    try:
        with manifest_path.open("r", encoding="utf-8-sig") as f:
            manifest = json.load(f)
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(manifest, dict):
        return False
    manifest["enabled"] = bool(enabled)
    try:
        with manifest_path.open("w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
            f.write("\n")
    except OSError:
        return False
    return True


def install_rune(
    source: str,
    target_dir: Path | None = None,
    marketplace_url: str = DEFAULT_MARKETPLACE_URL,
    confirm_python_deps: bool | None = None,
) -> Path:
    """Install an extension rune from local path, Git repository, or marketplace."""
    stripped_source = source.strip()
    if not stripped_source:
        raise ValueError("Rune source cannot be empty.")

    target = target_dir.expanduser() if target_dir is not None else extensions_dir()
    target.mkdir(parents=True, exist_ok=True)

    source_path = Path(stripped_source).expanduser()
    if source_path.exists():
        if not source_path.is_dir():
            raise ValueError(f"Invalid rune: not a directory: {source_path}")
        # The manifest name is the single source of truth for the install
        # directory, so install/list/uninstall always agree. Read it before
        # touching the target: a bad manifest fails without copying.
        name = _manifest_install_name(source_path, kind="rune")
        dest = _dest_within_target(target, name)
        if source_path.resolve() != dest.resolve():
            _install_tree(source_path, dest)
        else:
            # Installing in place: just ensure the install id exists.
            read_or_create_install_id(dest)
    elif stripped_source.startswith(
        ("git@", "git://", "http://", "https://", "ssh://")
    ) or stripped_source.endswith(".git"):
        # Preflight: reject obviously-malicious URLs before cloning. The
        # URL basename is only a sanity check here -- the manifest name
        # decides the actual install directory below.
        url_basename = stripped_source.rstrip("/").split("/")[-1].removesuffix(".git")
        _validate_install_name(url_basename, kind="rune")
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
            name = _manifest_install_name(staged, kind="rune")
            dest = _dest_within_target(target, name)
            _install_tree(staged, dest)
    else:
        rune_entry = resolve_artifact(
            stripped_source, section=RUNES_SECTION, marketplace_url=marketplace_url
        )
        git_url = rune_entry.get("git")
        if not git_url or not isinstance(git_url, str):
            raise ValueError(f"Rune '{stripped_source}' not found in marketplace.")

        _validate_install_name(stripped_source, kind="rune")
        subpath = rune_entry.get("path") if isinstance(rune_entry, dict) else None
        # Stage in a temp dir first so the manifest name (not the marketplace
        # key) decides the install directory.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            if subpath:
                subprocess.run(
                    ["git", "clone", "--depth", "1", "--", git_url, str(tmp_dir)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                staged = Path(tmp_dir) / str(subpath).strip().strip("/\\")
            else:
                subprocess.run(
                    ["git", "clone", "--", git_url, str(tmp_dir)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                staged = Path(tmp_dir)
            name = _manifest_install_name(staged, kind="rune")
            dest = _dest_within_target(target, name)
            _install_tree(staged, dest)

    # The manifest was already validated from the staged source; dest carries
    # an identical copy.
    install_python_deps(
        [],
        env_project=extensions_dir(),
        manifest_path=dest / "manifest.json",
        confirm=confirm_python_deps,
    )

    return dest
