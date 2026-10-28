"""The suite must not inherit the developer's machine.

These tests assert the conftest isolation fixtures themselves, so a future
change that drops a fixture fails here instead of surfacing as a
mysterious order-dependent failure in some other suite.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx


def test_global_agents_dir_is_isolated_from_real_home() -> None:
    """``MVGE_GLOBAL_DIR`` points at a scratch dir, never the real home."""
    value = os.environ.get("MVGE_GLOBAL_DIR")
    assert value is not None
    assert Path(value) != Path("~/.agents").expanduser()
    assert Path(value).is_dir()


def test_no_proxy_has_no_bracketed_entries() -> None:
    """Bracketed IPv6 literals in ``NO_PROXY`` break httpx client setup."""
    for name in ("NO_PROXY", "no_proxy"):
        value = os.environ.get(name)
        if value is not None:
            assert "[" not in value
            assert "]" not in value


def test_httpx_client_constructs_under_sanitized_env() -> None:
    """The observable failure the sanitizer exists to prevent."""
    client = httpx.AsyncClient()
    assert client is not None
