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


def test_sanitize_rewrites_bracketed_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_PROXY", "[::1],localhost")
    monkeypatch.setenv("no_proxy", "[fd8b:4f84:7d32:99::1]")
    changed = sanitize_no_proxy_env()
    assert changed is True
    assert os.environ["NO_PROXY"] == "localhost"
    assert os.environ["no_proxy"] == ""


def test_sanitize_leaves_clean_env_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_PROXY", "localhost")
    monkeypatch.delenv("no_proxy", raising=False)
    assert sanitize_no_proxy_env() is False
    assert os.environ["NO_PROXY"] == "localhost"
