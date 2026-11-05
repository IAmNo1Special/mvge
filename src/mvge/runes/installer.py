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

from mvge.core.layers import extensions_dir, global_agents_dir
from mvge.runes.capabilities import (
    CapabilityStore,
    default_capability_store_dir,
)
from mvge.runes.catalog import (
    DEFAULT_MARKETPLACE_URL,
    RUNES_SECTION,
    fetch_catalog,
    local_catalog_candidate,
    resolve_artifact,
    section_of,
)
from mvge.runes.deps import install_python_deps
from mvge.runes.manifest import load_manifest
from mvge.runes.pinning import (
    RunePin,
    adopt_rune_pin,
    compute_rune_tree_hash,
    write_pin,
)
from mvge.runes.types import RuneManifest

logger = logging.getLogger(__name__)

# Re-exported because every caller of this module has always spelled it this
# way, and the rune installer's public signature names it as its default.
__all__ = [
    "DEFAULT_MARKETPLACE_URL",
    "adopt_installed_rune",
    "consent_rune",
    "install_rune",
    "installed_rune_needs_consent",
    "uninstall_rune",
]

# Allowlist for install/uninstall directory names. Anything outside this set
# (path separators, "..", URL-encoded tricks) is rejected before it can reach
# the filesystem.
_INSTALL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


# Name of the installer-owned install-id file written into every rune dir.
INSTALL_ID_FILENAME = ".install-id"

# A recorded HEAD SHA is a full hex object id (SHA-1 today, SHA-256 where
# a repo uses it). Anything else -- including a mocked subprocess result
# in tests -- is not a commit and records as None, never as garbage.
_HEX_SHA_RE = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")


def _git_head(repo_dir: Path) -> str | None:
    """HEAD SHA of a checkout, or None when it cannot be proven.

    Best-effort on purpose: a rune installed from a source without a
    usable HEAD still gets a tree-hash pin, which is what load verifies.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, OSError):
        return None
    head = result.stdout.strip() if isinstance(result.stdout, str) else ""
    return head if _HEX_SHA_RE.fullmatch(head) else None


def _clone_pinned(git_url: str, dest: Path, commit: str) -> None:
    """Clone a catalog repo and detach it at the pinned commit.

    A depth-1 clone cannot check out an older commit, so pinned installs
    take a full clone. After checkout the HEAD must equal the pin: a
    branch that moved since the index was cut still yields the pinned
    tree, never the new HEAD.
    """
    subprocess.run(
        ["git", "clone", "--", git_url, str(dest)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(dest), "checkout", "--detach", commit],
        check=True,
        capture_output=True,
        text=True,
    )
    if _git_head(dest) != commit:
        raise ValueError(
            f"Catalog checkout for pinned commit {commit} did not yield that commit."
        )


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

    Where the catalog lives and how it is fetched is ``mvge.runes.catalog``'s
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
                    "ui": manifest.get("ui", {}),
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


def adopt_installed_rune(name: str, target_dir: Path | None = None) -> Path:
    """Record an operator review of an installed rune's current tree.

    The ``mvge rune trust --adopt`` remedy for sidecar-less legacy runes
    and deliberate local edits: writes a ``manual`` sidecar for the tree
    as it stands. Refuses unknown runes and revoked runes.

    Raises:
        ValueError: If the rune is not installed, or is revoked.
    """
    target = target_dir.expanduser() if target_dir is not None else extensions_dir()
    rune_dir = _dest_within_target(target, _validate_install_name(name, kind="rune"))
    if not rune_dir.is_dir() or not (rune_dir / "manifest.json").is_file():
        raise ValueError(f"Rune '{name}' is not installed.")
    adopt_rune_pin(rune_dir, name)
    return rune_dir


def _installed_rune_dir(name: str, target_dir: Path | None = None) -> Path:
    """Installed directory for a rune name, validated to stay in target."""
    target = target_dir.expanduser() if target_dir is not None else extensions_dir()
    return _dest_within_target(target, _validate_install_name(name, kind="rune"))


def _installed_manifest(
    name: str, target_dir: Path | None = None
) -> RuneManifest | None:
    """Load the installed manifest for a rune, or None when it is absent.

    The manifest name is the single source of truth for install, list,
    and uninstall, so consent reads it from the installed directory
    rather than trusting the name the caller passed in.
    """
    rune_dir = _installed_rune_dir(name, target_dir)
    if not rune_dir.is_dir():
        return None
    manifest = load_manifest(rune_dir)
    return manifest if isinstance(manifest, RuneManifest) else None


def _record_install_consent(rune_dir: Path) -> None:
    """Record install-time consent for a freshly installed rune.

    Installing a rune is the operator's explicit act of consenting to
    the capabilities its manifest declares at that moment — that is
    the consent the Phase 2B design left without a host. It runs only
    when no consent record exists yet: a reinstall or update that
    widens the declaration must not silently grant the new capability
    (or the widened write roots); that needs explicit re-consent via
    :func:`consent_rune`. A legacy manifest declares nothing, so
    installing one records nothing.
    """
    manifest = load_manifest(rune_dir)
    if manifest is None or manifest.capabilities is None:
        return
    store = CapabilityStore(default_capability_store_dir())
    if store.has_record(manifest.name):
        return
    store.consent_to_declaration(
        manifest.name, manifest.capabilities, manifest.filesystem_write_roots
    )


def consent_rune(name: str, target_dir: Path | None = None) -> list[str]:
    """Record host consent for an installed rune's current declaration.

    The explicit re-consent path: for a rune installed before
    install-time consent existed, and for a rune whose declaration
    widened after install. Consents to exactly what the installed
    manifest declares now, and records that declaration as the
    consented one, so the widening stops being outstanding.

    Returns the granted capabilities.

    Raises:
        ValueError: If the rune is not installed, its manifest is
            invalid, or it declares no capabilities (legacy).
    """
    if not _installed_rune_dir(name, target_dir).is_dir():
        raise ValueError(f"Rune '{name}' is not installed.")
    manifest = _installed_manifest(name, target_dir)
    if manifest is None:
        raise ValueError(f"Rune '{name}' has an invalid manifest.")
    if manifest.capabilities is None:
        raise ValueError(
            f"Rune '{name}' declares no capabilities (legacy manifest); "
            "nothing to consent to."
        )
    store = CapabilityStore(default_capability_store_dir())
    store.consent_to_declaration(
        manifest.name, manifest.capabilities, manifest.filesystem_write_roots
    )
    return list(manifest.capabilities)


def installed_rune_needs_consent(name: str, target_dir: Path | None = None) -> bool:
    """True when an installed rune declares capabilities (or write roots)
    that are not all granted.

    False for a rune that is not installed, a legacy manifest (ungated,
    nothing to consent to), and a rune whose current declaration is
    fully granted. The CLI uses this to tell an installed-but-unconsented
    rune apart from a genuinely missing one, so the NoRealm remedy names
    consent instead of a reinstall that changes nothing.
    """
    manifest = _installed_manifest(name, target_dir)
    if manifest is None or manifest.capabilities is None:
        return False
    store = CapabilityStore(default_capability_store_dir())
    if store.granted(manifest.name, manifest.capabilities) != set(
        manifest.capabilities
    ):
        return True
    if "filesystem" in manifest.capabilities:
        return store.granted_write_roots(
            manifest.name, manifest.filesystem_write_roots
        ) != set(manifest.filesystem_write_roots)
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
            # Hash the staged source before copying and before any
            # python_deps install: the pin records the source tree,
            # never generated state.
            staged_hash = compute_rune_tree_hash(source_path)
            _install_tree(source_path, dest)
            write_pin(
                dest,
                RunePin(
                    source_kind="local",
                    tree_hash=staged_hash,
                    local_path=str(source_path.resolve()),
                ),
            )
        else:
            # Installing in place: just ensure the install id exists,
            # and record the current tree as a local pin.
            read_or_create_install_id(dest)
            write_pin(
                dest,
                RunePin(
                    source_kind="local",
                    tree_hash=compute_rune_tree_hash(dest),
                    local_path=str(dest.resolve()),
                ),
            )
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
            staged_hash = compute_rune_tree_hash(staged)
            _install_tree(staged, dest)
            write_pin(
                dest,
                RunePin(
                    source_kind="git",
                    tree_hash=staged_hash,
                    git_url=stripped_source,
                    commit=_git_head(staged),
                ),
            )
    else:
        rune_entry = resolve_artifact(
            stripped_source, section=RUNES_SECTION, marketplace_url=marketplace_url
        )
        git_url = rune_entry.get("git")
        if not git_url or not isinstance(git_url, str):
            raise ValueError(f"Rune '{stripped_source}' not found in marketplace.")
        # Revocation is enforced here, before any clone: a version later
        # found malicious must not install even though its hash would match.
        if rune_entry.get("revoked") is True:
            raise ValueError(
                f"Rune '{stripped_source}' is revoked in the marketplace "
                "index and cannot be installed."
            )
        raw_commit = rune_entry.get("commit")
        pinned_commit = raw_commit if isinstance(raw_commit, str) else None
        raw_tree = rune_entry.get("tree_hash")
        pinned_tree = raw_tree if isinstance(raw_tree, str) else None

        _validate_install_name(stripped_source, kind="rune")
        subpath = rune_entry.get("path") if isinstance(rune_entry, dict) else None
        # Stage in a temp dir first so the manifest name (not the marketplace
        # key) decides the install directory.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            if subpath:
                if pinned_commit is not None:
                    # A failed pinned checkout is a hard error, never a
                    # fallback: falling back to a local checkout would
                    # install unpinned content under a pinned commit.
                    _clone_pinned(git_url, Path(tmp_dir), pinned_commit)
                    staged = Path(tmp_dir) / str(subpath).strip().strip("/\\")
                else:
                    try:
                        subprocess.run(
                            [
                                "git",
                                "clone",
                                "--depth",
                                "1",
                                "--",
                                git_url,
                                str(tmp_dir),
                            ],
                            check=True,
                            capture_output=True,
                            text=True,
                        )
                        staged = Path(tmp_dir) / str(subpath).strip().strip("/\\")
                    except (subprocess.CalledProcessError, OSError):
                        candidate = local_catalog_candidate(subpath)
                        if candidate is None:
                            raise
                        staged = candidate
            else:
                if pinned_commit is not None:
                    _clone_pinned(git_url, Path(tmp_dir), pinned_commit)
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
            # Hash the staged artifact before copying and before any
            # python_deps install. A pinned tree_hash that disagrees
            # refuses the install before anything is copied into place.
            staged_hash = compute_rune_tree_hash(staged)
            if pinned_tree is not None and staged_hash != pinned_tree:
                raise ValueError(
                    f"Rune '{stripped_source}' refused: staged tree_hash "
                    f"{staged_hash} does not match the marketplace index "
                    f"tree_hash {pinned_tree}."
                )
            _install_tree(staged, dest)
            write_pin(
                dest,
                RunePin(
                    source_kind="catalog",
                    tree_hash=staged_hash,
                    git_url=git_url,
                    commit=pinned_commit or _git_head(staged),
                ),
            )

    # The manifest was already validated from the staged source; dest carries
    # an identical copy.
    install_python_deps(
        [],
        env_project=extensions_dir(),
        manifest_path=dest / "manifest.json",
        confirm=confirm_python_deps,
    )

    # Installing is the consent act: a fresh install grants exactly what
    # the installed manifest declares, so the rune registers on first
    # load. A reinstall never re-consents (see _record_install_consent).
    _record_install_consent(dest)

    return dest
