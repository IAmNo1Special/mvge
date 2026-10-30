"""Unit tests for the NO_PROXY sanitizer."""

from __future__ import annotations

import os

import httpx
import pytest

from mvge.core import sanitize_no_proxy_env as public_sanitize_no_proxy_env
from mvge.core.proxy import (
    sanitize_no_proxy_env,
    strip_bracketed_no_proxy_entries,
)


def test_strip_removes_only_bracketed_tokens() -> None:
    assert (
        strip_bracketed_no_proxy_entries("[::1],localhost,127.0.0.1")
        == "localhost,127.0.0.1"
    )
    assert strip_bracketed_no_proxy_entries("localhost") == "localhost"
    assert strip_bracketed_no_proxy_entries("") == ""


# NOTE: Windows environment variables are case-insensitive, so
# NO_PROXY and no_proxy are the SAME slot there. These tests must never
# assume two distinct slots: each case sets exactly one name, after
# clearing both (clearing is a no-op for the second name on Windows).
@pytest.mark.parametrize("name", ["NO_PROXY", "no_proxy"])
def test_sanitize_rewrites_bracketed_env(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setenv(name, "[::1],localhost")
    changed = sanitize_no_proxy_env()
    assert changed is True
    assert os.environ[name] == "localhost"


@pytest.mark.parametrize("name", ["NO_PROXY", "no_proxy"])
def test_sanitize_leaves_clean_env_alone(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setenv(name, "localhost")
    assert sanitize_no_proxy_env() is False
    assert os.environ[name] == "localhost"


def test_sanitize_is_public_from_mvge_core() -> None:
    """The helper is declared public API, not a hidden module path.

    A rune author must be able to find it as ``mvge.core`` API. An
    importable-but-undeclared symbol can be renamed with no signal
    here while a rune already relies on it.
    """
    assert public_sanitize_no_proxy_env is sanitize_no_proxy_env


# Rune-position tests: a rune building its own httpx client, in library
# use where CLI startup never ran. No request is made, so the tests stay
# hermetic: client construction alone parses NO_PROXY and raises.
@pytest.mark.parametrize("name", ["NO_PROXY", "no_proxy"])
def test_rune_position_sync_client_builds_after_helper(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setenv(name, "[::1],localhost")
    assert public_sanitize_no_proxy_env() is True
    with httpx.Client() as client:
        assert not client.is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["NO_PROXY", "no_proxy"])
async def test_rune_position_async_client_builds_after_helper(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setenv(name, "[::1],localhost")
    assert public_sanitize_no_proxy_env() is True
    async with httpx.AsyncClient() as client:
        assert not client.is_closed


@pytest.mark.parametrize("name", ["NO_PROXY", "no_proxy"])
def test_rune_position_client_fails_without_helper(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Control: the unprotected path must still fail, or the proof above
    proves nothing."""
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setenv(name, "[::1],localhost")
    with pytest.raises(httpx.InvalidURL):
        httpx.Client()
