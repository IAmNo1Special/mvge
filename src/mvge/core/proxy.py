"""NO_PROXY sanitization for runtime startup and for runes.

httpx parses ``NO_PROXY``/``no_proxy`` when constructing a client and
raises ``InvalidURL`` on bracketed IPv6 literals such as ``[::1]``
(it reads the ``:1]`` as a port). Sandboxes commonly export exactly
that form, so any realm that builds an httpx client fails for purely
environmental reasons before the run starts. The test suites sanitize
the same way per test; runtime sanitizes once at CLI startup.

Public rune contract: this helper is public API (exported from
``mvge.core`` and reachable from a rune factory as
``RuneAPI.sanitize_no_proxy_env``). A rune that builds its own httpx
client -- bypassing every engine construction site -- must call it
BEFORE building that client::

    from mvge.core import sanitize_no_proxy_env

    sanitize_no_proxy_env()
    client = httpx.AsyncClient()

The engine cannot interpose on a client it never sees, and sanitizing
after construction cannot repair a client that already failed to
build, so the call must come first. CLI runs are incidentally covered
because CLI startup sanitizes before runes load; library embeddings
that never run CLI startup are the exposed path, and that is where a
rune must call this itself. The call is idempotent, and a second call
after a clean environment is a no-op.

Only bracketed tokens are removed; plain hosts, CIDRs, and
unbracketed IPv6 literals pass through untouched.
"""

from __future__ import annotations

import os

_NO_PROXY_NAMES = ("NO_PROXY", "no_proxy")


def strip_bracketed_no_proxy_entries(value: str) -> str:
    """Drop bracketed IPv6 literals from a no-proxy list."""
    kept = [
        token.strip()
        for token in value.split(",")
        if token.strip() and "[" not in token and "]" not in token
    ]
    return ",".join(kept)


def sanitize_no_proxy_env() -> bool:
    """Strip bracketed tokens from the process NO_PROXY variables.

    Returns True when anything changed. Idempotent: a second call is
    a no-op.
    """
    changed = False
    for name in _NO_PROXY_NAMES:
        value = os.environ.get(name)
        if value is None:
            continue
        stripped = strip_bracketed_no_proxy_entries(value)
        if stripped != value:
            os.environ[name] = stripped
            changed = True
    return changed
