"""Where the marketplace catalog lives, and how to get an artifact from it.

Both installers used to answer this for themselves. ``mvge.agent.installer``
carried three fetch tiers and a local-checkout probe; ``mvgeos_runs.installer``
carried one ``httpx.get`` that swallowed its errors and raised on a bad URL. The
same catalog, reached two different ways, with one copy of the URL in each. A
change to the transport — a CDN that starts caching a branch, a repository that
moves — landed in one and not the other, and nothing said so.

One module owns the whole question. Its interface is two calls: give me the
catalog, give me one artifact. Everything behind it — the URL, the fallback
tiers, the base64 decode, the local checkout probe, the top-level section
names — is implementation, and no caller needs to know any of it exists.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

#: The published catalog. A copy of this lives nowhere: it was previously
#: identical in two installers, and identically wrong when it changed.
DEFAULT_MARKETPLACE_URL = (
    "https://raw.githubusercontent.com/IAmNo1Special/mvgeos-marketplace/main/index.json"
)

#: The catalog's top-level sections. ``agents`` is accepted as a deprecated
#: alias for ``mvges`` because a published index once shipped it and the engine
#: read it; the marketplace no longer emits it.
RUNES_SECTION = "runes"
MVGES_SECTION = "mvges"
PROFILES_SECTION = "profiles"
_MVGE_SECTION_ALIASES = (MVGES_SECTION, "agents")


def local_catalog_candidate(subpath: str) -> Path | None:
    """A checkout of a marketplace subpath on this machine, if there is one.

    A developer's desktop clone is the last resort, after every network tier.
    It is not a supported install path: it is how someone working on both
    repositories at once keeps testing without waiting on a CDN.
    """
    for base in (
        os.environ.get("MVGEOS_MARKETPLACE_DIR"),
        str(Path.home() / "Desktop" / "mvgeos-marketplace"),
    ):
        if not base:
            continue
        candidate = Path(base).expanduser() / subpath.strip().strip("/\\")
        if candidate.is_dir():
            return candidate
    return None


def fetch_catalog(
    marketplace_url: str = DEFAULT_MARKETPLACE_URL,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Fetch the marketplace catalog, returning ``{}`` when it cannot be read.

    Three tiers, tried in order, and the first one whose payload carries the
    section being looked for wins. This exists because the CDN caches a branch
    for long enough that a just-pushed catalog is invisible, and because a
    publish's first moments otherwise fail with a 404 rather than an empty
    index:

    1. the published URL, on ``main``;
    2. the same URL on ``HEAD``, which the CDN cannot pin to a stale branch;
    3. the GitHub Contents API, base64-decoded.

    A caller that wants the failure rather than an empty index should use
    :func:`resolve_artifact`, which raises.
    """
    first_error: Exception | None = None
    for url, is_contents_api in _catalog_urls(marketplace_url):
        try:
            response = httpx.get(url, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001 - every tier is best-effort
            if first_error is None:
                first_error = exc
            continue

        catalog = _decode_contents_api(payload) if is_contents_api else payload
        if isinstance(catalog, dict) and _has_a_section(catalog, marketplace_url):
            return catalog

    if first_error is not None:
        raise first_error
    return {}


def resolve_artifact(
    name: str,
    *,
    section: str,
    marketplace_url: str = DEFAULT_MARKETPLACE_URL,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """One catalog entry, or a ``ValueError`` naming what is available.

    Raises:
        ValueError: If the catalog cannot be fetched at all, or does not carry
            an entry for ``name``. The message lists what the catalog does
            carry, because "not found in marketplace" next to an empty index
            reads as a typo rather than a network failure.
    """
    try:
        catalog = fetch_catalog(marketplace_url, timeout=timeout)
    except Exception as exc:
        raise ValueError(
            f"Failed to fetch marketplace index from '{marketplace_url}': {exc}"
        ) from exc

    entries = section_of(catalog, section)
    entry = entries.get(name)
    if entry is None:
        # A published key may be spelled either way; the catalog has shipped
        # both conventions.
        for alias in (name.replace("-", "_"), name.replace("_", "-")):
            entry = entries.get(alias)
            if entry is not None:
                break

    if not isinstance(entry, dict):
        available = ", ".join(sorted(entries)) or "(the index is empty)"
        raise ValueError(f"'{name}' not found in marketplace. Available: {available}.")
    return entry


def section_of(catalog: dict[str, Any], section: str) -> dict[str, Any]:
    """One top-level section of the catalog, as a dict.

    A section may be missing, wrongly typed, or a deprecated alias. All three
    come back as an empty mapping so a caller does not need to know the
    catalog's shape.
    """
    names = _MVGE_SECTION_ALIASES if section == MVGES_SECTION else (section,)
    for name in names:
        value = catalog.get(name)
        if isinstance(value, dict):
            return value
    return {}


def _has_a_section(payload: dict[str, Any], marketplace_url: str) -> bool:
    """Whether a fetched payload is the catalog rather than an error wrapper.

    Judged by section presence rather than by any key, because a CDN error page
    and a GitHub API error are both JSON objects that look like a catalog until
    something reads the sections.
    """
    if section_of(payload, RUNES_SECTION) or section_of(payload, MVGES_SECTION):
        return True
    logger.debug(
        "Fetched '%s' but found neither '%s' nor '%s' in it",
        marketplace_url,
        RUNES_SECTION,
        MVGES_SECTION,
    )
    return False


def _catalog_urls(marketplace_url: str) -> list[tuple[str, bool]]:
    """The URLs to try, most authoritative first, and which one wraps content.

    A branch name the CDN can pin is why tier one can be stale: ``/HEAD/``
    cannot be pinned to one, so it is the natural second tier. The Contents API
    is the third, and only for the published marketplace — firing it at a mirror
    someone passed in would ask GitHub about a tree that is not that one.
    """
    urls = [(marketplace_url, False)]

    if "/main/" in marketplace_url:
        urls.append((marketplace_url.replace("/main/", "/HEAD/"), False))

    if (
        "githubusercontent.com" in marketplace_url
        and "mvgeos-marketplace" in marketplace_url
    ):
        urls.append(
            (
                "https://api.github.com/repos/IAmNo1Special/mvgeos-marketplace"
                "/contents/index.json",
                True,
            )
        )

    return urls


def _decode_contents_api(payload: Any) -> dict[str, Any] | None:
    """A GitHub Contents API payload, decoded, or ``None``.

    The API wraps file content in base64 by default. A response that is not
    that shape — a directory listing, an error — yields ``None`` rather than
    raising, because this is a fallback and a shaped failure is not worth more
    than an empty index.
    """
    if not isinstance(payload, dict) or "content" not in payload:
        return None
    try:
        decoded = json.loads(base64.b64decode(payload["content"]).decode("utf-8"))
    except (ValueError, TypeError):
        return None
    return decoded if isinstance(decoded, dict) else None
