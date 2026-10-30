"""NO_PROXY sanitization for runtime startup and for runes.

httpx parses ``NO_PROXY``/``no_proxy`` when constructing a client and
raises ``InvalidURL`` on bracketed IPv6 literals such as ``[::1]``
(it reads the ``:1]`` as a port). Sandboxes commonly export exactly
that form, so any realm that builds an httpx client fails for purely
environmental reasons before the run starts. The test suites sanitize
the same way per test; runtime sanitizes once at CLI startup, and
every httpx construction site in the engine sanitizes again just
before building its client, so direct/library use that never runs
CLI startup is safe too. The CLI-startup call stays: it is the first
line of defense, and this per-site call is the second.

Public rune contract: this helper is public API (exported from
``mvge.core`` and reachable from a rune factory as
``RuneAPI.sanitize_no_proxy_env``). A rune loaded through the engine
rune loader needs no call: the loader sanitizes before executing the
rune module, and ``RuneRunner`` sanitizes again before every factory
invocation, so module-top-level, factory, and later spell/sigil
client builds in that process all start from a clean environment.
The helper remains for the two paths the engine never sees: a rune
module imported directly by a host (bypassing the loader entirely),
and rune code that re-dirties the environment after its factory
returns and then builds a client outside any engine factory call.
In both, call it BEFORE building the client::

    from mvge.core import sanitize_no_proxy_env

    sanitize_no_proxy_env()
    client = httpx.AsyncClient()

The engine cannot interpose on a client it never sees, and sanitizing
after construction cannot repair a client that already failed to
build, so the call must come first. The call is idempotent, and a
second call after a clean environment is a no-op.

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
