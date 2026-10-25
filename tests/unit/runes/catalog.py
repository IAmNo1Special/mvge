"""The catalog module: where the index lives and how an artifact is resolved.

Two installers used to answer this for themselves, and they disagreed. These
tests pin the one module that owns it now: the URL tiers, the base64 decode,
the local-checkout probe, and resolving one entry out of a section.

The behaviour under test is the fallback *order*, not the HTTP layer. The CDN
caches a branch for long enough that a just-pushed catalog is invisible, so the
first tier has to be allowed to fail and the second tried before anything is
reported as missing -- otherwise a publish's first moments surface as "not found
in marketplace", which reads as a typo.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from mvge.runes.catalog import (
    DEFAULT_MARKETPLACE_URL,
    MVGES_SECTION,
    RUNES_SECTION,
    fetch_catalog,
    local_catalog_candidate,
    resolve_artifact,
    section_of,
)


def _response(payload: Any) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    return resp


# --- the URL tiers ---------------------------------------------------------


def test_the_branch_url_is_tried_first() -> None:
    """The published URL wins whenever it carries the section being looked for."""
    good = _response({"mvges": {"coding_mvge": {"name": "coding_mvge"}}})
    seen: list[str] = []

    def fake_get(url: str, **kwargs: object) -> MagicMock:
        seen.append(url)
        return good

    with patch("httpx.get", side_effect=fake_get):
        catalog = fetch_catalog()

    assert seen == [DEFAULT_MARKETPLACE_URL]
    assert "coding_mvge" in section_of(catalog, MVGES_SECTION)


def test_a_stale_branch_falls_back_to_head() -> None:
    """A CDN that pinned the branch must not be reported as a missing artifact.

    The main URL answers, but with a payload carrying no section -- which is
    what a cached-but-wrong response looks like. The ``/HEAD/`` retry is what
    turns that into a found catalog rather than an empty index.
    """
    stale = _response({"runes": {}})
    fresh = _response({"mvges": {"coding_mvge": {"name": "coding_mvge"}}})

    def fake_get(url: str, **kwargs: object) -> MagicMock:
        if "/HEAD/" in url:
            return fresh
        return stale

    with patch("httpx.get", side_effect=fake_get):
        catalog = fetch_catalog()

    assert "coding_mvge" in section_of(catalog, MVGES_SECTION)


def test_the_contents_api_is_the_last_tier_and_is_decoded() -> None:
    """GitHub's API wraps file content in base64, and it is the final resort."""
    stale = _response({"runes": {}})
    empty = _response({"runes": {}})
    content = {"mvges": {"coding_mvge": {"name": "coding_mvge"}}}
    api = _response(
        {"content": base64.b64encode(json.dumps(content).encode()).decode("utf-8")}
    )

    def fake_get(url: str, **kwargs: object) -> MagicMock:
        if "api.github.com" in url:
            return api
        if "/HEAD/" in url:
            return empty
        return stale

    with patch("httpx.get", side_effect=fake_get):
        catalog = fetch_catalog()

    assert "coding_mvge" in section_of(catalog, MVGES_SECTION)


def test_a_url_without_a_branch_gets_no_head_retry() -> None:
    """The /HEAD/ rewrite is only valid for a URL that names /main/.

    Rewriting a mirror's URL would fetch some other path and call the result
    stale, so the tiers depend on the shape of what was passed in.
    """
    good = _response({"mvges": {"custom": {"name": "custom"}}})
    seen: list[str] = []

    def fake_get(url: str, **kwargs: object) -> MagicMock:
        seen.append(url)
        return good

    with patch("httpx.get", side_effect=fake_get):
        fetch_catalog("https://custom.example.org/catalog.json")

    assert seen == ["https://custom.example.org/catalog.json"]


def test_a_payload_with_no_section_is_not_the_catalog() -> None:
    """An error wrapper from any tier must not be mistaken for an empty index."""
    wrapper = _response({"message": "Not Found", "documentation_url": "..."})

    with patch("httpx.get", return_value=wrapper):
        assert fetch_catalog() == {}


def test_a_transport_failure_raises_rather_than_returning_empty() -> None:
    """``fetch_catalog`` fails loudly; only the list callers swallow it.

    The list-command callers want an empty index they can render. A caller
    resolving one artifact needs to know the index was never read, or a network
    failure reads as "not found in marketplace".
    """
    with (
        patch("httpx.get", side_effect=OSError("no route")),
        pytest.raises(OSError, match="no route"),
    ):
        fetch_catalog()


# --- resolving one artifact ------------------------------------------------


def test_resolve_artifact_returns_the_entry() -> None:
    catalog = {"mvges": {"coding_mvge": {"path": "mvges/coding_mvge"}}}

    with patch("mvge.runes.catalog.fetch_catalog", return_value=catalog):
        entry = resolve_artifact("coding_mvge", section=MVGES_SECTION)

    assert entry["path"] == "mvges/coding_mvge"


@pytest.mark.parametrize(
    ("asked", "available"),
    [
        ("approval-rune", "approval-rune"),
        ("approval-rune", "approval_rune"),
        ("approval_rune", "approval-rune"),
    ],
)
def test_resolve_artifact_accepts_either_spelling(asked: str, available: str) -> None:
    """A published key may be spelled with either separator."""
    catalog = {"runes": {available: {"path": f"runes/{available}"}}}

    with patch("mvge.runes.catalog.fetch_catalog", return_value=catalog):
        entry = resolve_artifact(asked, section=RUNES_SECTION)

    assert entry["path"] == f"runes/{available}"


def test_resolve_artifact_names_what_is_available_when_it_is_absent() -> None:
    """ "Not found" next to a populated index must say what was found instead."""
    catalog = {"runes": {"seeker": {}, "mcp-bridge": {}}}

    with (
        patch("mvge.runes.catalog.fetch_catalog", return_value=catalog),
        pytest.raises(ValueError, match="nope") as exc,
    ):
        resolve_artifact("nope", section=RUNES_SECTION)

    # The message names what the catalog does carry, because "not found" next
    # to a populated index reads as a typo rather than a missing artifact.
    assert "mcp-bridge, seeker" in str(exc.value)


def test_resolve_artifact_distinguishes_a_failed_fetch_from_a_missing_name() -> None:
    with (
        patch("mvge.runes.catalog.fetch_catalog", side_effect=OSError("no route")),
        pytest.raises(ValueError, match="Failed to fetch marketplace index"),
    ):
        resolve_artifact("coding_mvge", section=MVGES_SECTION)


def test_a_deprecated_section_alias_still_resolves() -> None:
    """A published index once spelled the section ``agents``."""
    catalog = {"agents": {"old": {"path": "mvges/old"}}}

    with patch("mvge.runes.catalog.fetch_catalog", return_value=catalog):
        entry = resolve_artifact("old", section=MVGES_SECTION)

    assert entry["path"] == "mvges/old"


# --- the local checkout probe ---------------------------------------------


def test_the_local_probe_finds_a_checkout_under_the_environment_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "marketplace"
    artifact = checkout / "mvges" / "coding_mvge"
    artifact.mkdir(parents=True)
    monkeypatch.setenv("MVGE_MARKETPLACE_DIR", str(checkout))

    assert local_catalog_candidate("mvges/coding_mvge") == artifact


def test_the_local_probe_returns_none_when_there_is_no_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MVGE_MARKETPLACE_DIR", str(tmp_path / "absent"))

    assert local_catalog_candidate("mvges/coding_mvge") is None


def test_the_local_probe_stays_inside_the_checkout_for_a_traversal_subpath(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "marketplace"
    checkout.mkdir()
    monkeypatch.setenv("MVGE_MARKETPLACE_DIR", str(checkout))

    # Neither of these resolves to a directory, so both are a miss -- the
    # probe is a last resort for a developer, not an install path.
    assert local_catalog_candidate("../../etc") is None
    assert local_catalog_candidate("/etc/passwd") is None
