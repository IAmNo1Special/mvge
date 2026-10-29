"""Unit tests for the NO_PROXY sanitizer."""

from __future__ import annotations

import os

import pytest

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
