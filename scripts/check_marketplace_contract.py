#!/usr/bin/env python3
"""Verify a marketplace checkout only imports published engine API.

The marketplace clones this repository and imports engine internals from
``main``. When ``DEFAULT_TOME_DIR`` was replaced by the ``sessions_dir()``
resolver, ``pi-codec`` and ``session-search`` broke on the marketplace's
``main`` with ten import errors, and nothing here failed, because the
marketplace only runs when someone pushes to it.

The rule lives in ``mvge.runes.contract`` so the marketplace's own suite can
check against the same one. This script is an adapter for the engine side: it
runs in CI, where a checkout of the marketplace is placed beside this
repository, so an engine change that breaks a published artifact fails *here*
rather than three pushes later.

Four kinds of problem are reported, and each fails the check:

``unpublished``
    The module is not declared for Runes. This is the kind the previous
    revision could not produce -- it only inspected package roots, so a Rune
    reaching into an engine submodule passed cleanly.

``unimportable`` / ``missing``
    The module or the symbol does not exist. Collection will break.

``undeclared``
    The symbol exists but is absent from the package's ``__all__``. The import
    works today and can be renamed or removed with no signal here, which is how
    ``selfmod-bridge`` came to depend on ``AuditError``.

Usage::

    uv run python scripts/check_marketplace_contract.py <path>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mvge.runes.contract import Violation, check_tree

_SCAN_DIRS = ("runes", "mvges")


def check(marketplace: Path) -> list[Violation]:
    """Return every contract violation found in a marketplace checkout."""
    sources = [
        source
        for directory in _SCAN_DIRS
        for source in sorted((marketplace / directory).rglob("*.py"))
    ]
    if not sources:
        return [
            Violation(
                "layout",
                str(marketplace),
                "<layout>",
                f"no {' or '.join(_SCAN_DIRS)} directory found; is this a "
                "marketplace checkout?",
            )
        ]
    return [
        violation
        for directory in _SCAN_DIRS
        for violation in check_tree(marketplace / directory, relative_to=marketplace)
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("marketplace", type=Path, help="path to a marketplace checkout")
    args = parser.parse_args(argv)

    root = args.marketplace.resolve()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)  # noqa: T201
        return 2

    violations = check(root)
    if not violations:
        print("marketplace contract OK: every engine import is published and declared")  # noqa: T201
        return 0

    by_kind: dict[str, list[Violation]] = {}
    for violation in violations:
        by_kind.setdefault(violation.kind, []).append(violation)

    for kind in (
        "unparseable",
        "unreadable",
        "unimportable",
        "missing",
        "unpublished",
        "undeclared",
        "layout",
    ):
        for violation in by_kind.get(kind, []):
            print(f"{kind}: {violation}", file=sys.stderr)  # noqa: T201

    counts = ", ".join(
        f"{len(by_kind.get(kind, []))} {kind}"
        for kind in (
            "unparseable",
            "unreadable",
            "unimportable",
            "missing",
            "unpublished",
            "undeclared",
            "layout",
        )
    )
    print(f"\n{counts}", file=sys.stderr)  # noqa: T201
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
