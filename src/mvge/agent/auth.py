from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path

from mvge.core.layers import auth_file

#: The Realm whose credential these module-level names refer to.
#:
#: Bound at import rather than resolved per call on purpose: SOM-15 established
#: that reading through ``auth_file()`` on every call would resolve the global
#: layer at call time, so a relocated ``$HOME`` picked up the wrong directory.
#: Import-time binding is what keeps a relocated layer reading the real home
#: credential. Realm-specific callers pass the realm explicitly.
DEFAULT_REALM = "openrouter"

AUTH_FILE_PATH = auth_file(DEFAULT_REALM)
AUTH_FILE_PERMS = 0o600
AUTH_DIR_PERMS = 0o700


def enforce_file_permissions(
    path: Path,
    mode: int = AUTH_FILE_PERMS,
    dir_mode: int = AUTH_DIR_PERMS,
) -> None:
    """Enforce restricted POSIX permissions on a file and its parent directory."""
    if os.name == "nt":
        return
    with contextlib.suppress(OSError):
        if path.parent.exists():
            path.parent.chmod(dir_mode)
        if path.exists():
            path.chmod(mode)


def _read_key(path: Path) -> str | None:
    if not path.exists():
        return None
    if os.name != "nt":
        enforce_file_permissions(path)
    with contextlib.suppress(json.JSONDecodeError, OSError):
        data = json.loads(path.read_text(encoding="utf-8"))
        api_key = data.get("api_key")
        return api_key if isinstance(api_key, str) and api_key.strip() else None
    return None


def load_api_key_from_auth() -> str | None:
    """Load API key from ~/.agents/auth/openrouter.json."""
    return _read_key(AUTH_FILE_PATH)


def load_api_key_for_realm(realm: str) -> str | None:
    """Load the API key for one realm from ``~/.agents/auth/<realm>.json``.

    Each realm keeps its own credential. Sharing one file would mean installing
    a second realm silently overwrites the first one's key, and a realm that
    receives another realm's key authenticates at the wrong host and then fails
    every call with a message naming the model.
    """
    if not realm or realm == DEFAULT_REALM:
        return load_api_key_from_auth()
    return _read_key(auth_file(realm))


def save_api_key_to_auth(api_key: str, realm: str = DEFAULT_REALM) -> Path:
    """Save an API key to ``~/.agents/auth/<realm>.json``.

    The default realm writes through the module-level ``AUTH_FILE_PATH`` rather
    than re-resolving it, so relocating the layer and patching that name keep
    working for it. That indirection is the whole reason the constant exists
    (SOM-15), and re-resolving here would have quietly undone it.
    """
    path = AUTH_FILE_PATH if not realm or realm == DEFAULT_REALM else auth_file(realm)
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        with contextlib.suppress(OSError):
            path.parent.chmod(AUTH_DIR_PERMS)

    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(path), flags, AUTH_FILE_PERMS)
    try:
        with open(fd, "w", encoding="utf-8", closefd=True) as f:
            f.write(json.dumps({"api_key": api_key}, indent=2))
    except Exception:
        with contextlib.suppress(OSError):
            os.close(fd)
        raise

    if os.name != "nt":
        with contextlib.suppress(OSError):
            path.chmod(AUTH_FILE_PERMS)

    return path


__all__ = [
    "AUTH_DIR_PERMS",
    "AUTH_FILE_PATH",
    "AUTH_FILE_PERMS",
    "DEFAULT_REALM",
    "enforce_file_permissions",
    "load_api_key_for_realm",
    "load_api_key_from_auth",
    "save_api_key_to_auth",
]
