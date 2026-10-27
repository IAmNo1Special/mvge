#!/usr/bin/env python3
"""Verify the ADRs still describe this codebase.

The ADR index drifted silently for 227 commits. ADR-0007 named ``TomeLedger``,
an in-memory ``Index`` and ``filelock``; none of them existed in any ``src/``.
ADR-0003 committed to Pi's exact 12-entry schema; there are six. Nothing failed,
because nothing compared the records to the code.

This check closes that gap. It reports these classes of problem:

``no-status``
    The ADR declares no ``## Status`` section, so a reader cannot tell whether it
    is live. (ADR-0010 was in this state while the index listed it as superseded.)

``unlisted``
    The ADR exists on disk but no row in ``README.md`` claims it.

``stale-index``
    ``README.md`` and the ADR disagree about the status.

``missing-contract``
    A symbol in the ADR's ``## Contract`` section does not exist in first-party
    source. This is the failure that matters: an implementer following the ADR
    would write code against a name that is gone.

Dotted names are resolved as ``Owner.member`` against a real AST index, not by
substring. That distinction is load-bearing: ``MvgeHarness.steer`` and
``Mvge.steer`` are different promises, and ADR-0012 makes the first while only
the second exists.

Deliberately narrow scope. Only names inside a ``## Contract`` section are
checked, never prose. An ADR that retires a decision is *expected* to name the
symbols it retired -- that is the whole point of a supersede note -- so scanning
the body would report every honest amendment as a violation.

An ADR with no ``## Contract`` section is counted as ``unchecked`` and does not
fail the run. That is a conscious omission, not a silent one: the count is
printed on every run.

Usage::

    uv run python scripts/check_adr_contract.py [repo_root]
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: First-party source the contract is checked against.
SOURCE_GLOBS = ("src/**/*.py", "*/src/**/*.py")
EXTRA_SOURCE_PATHS = ("conftest.py",)

CONTRACT_RE = re.compile(
    r"^<!--\s*adr-contract:\s*(?P<names>[^>]*?)\s*-->\s*$",
    re.MULTILINE,
)

STATUS_RE = re.compile(
    r"^##[ \t]+Status[ \t]*$(?P<body>.*?)(?=^##[ \t]|\Z)",
    re.MULTILINE | re.DOTALL,
)

INDEX_ROW_RE = re.compile(
    r"^\|\s*(?P<num>\d{4})\s*\|.*?\|\s*(?P<status>[^|]+?)\s*\|\s*(?P<date>\S+)\s*\|",
    re.MULTILINE,
)


@dataclass
class SourceIndex:
    """What first-party source actually defines.

    ``qualified`` holds ``Owner.member`` pairs and module-level names.
    ``referenced`` holds every identifier and attribute the source mentions,
    which is what a bare contract name is matched against: a contract may name
    something the engine only calls (``realm.close``) rather than defines.
    """

    qualified: set[str] = field(default_factory=set)
    referenced: set[str] = field(default_factory=set)

    def has(self, symbol: str) -> bool:
        """Whether first-party source provides this symbol.

        A dotted name is matched strictly against ``Owner.member``. Falling back
        to the leaf would defeat the purpose: ``MvgeHarness.steer`` and
        ``Mvge.steer`` are different promises, and ADR-0012 makes the first while
        only the second exists. A substring match would report that promise kept.
        """
        if "." in symbol:
            return symbol in self.qualified
        return symbol in self.qualified or symbol in self.referenced


@dataclass(frozen=True)
class Violation:
    """One ADR that no longer matches the codebase or the index."""

    kind: str
    adr: str
    detail: str

    def __str__(self) -> str:
        return f"{self.kind}: {self.adr}: {self.detail}"


def _collect(path: Path, index: SourceIndex) -> None:
    """Add one module's definitions and references to the index."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            index.qualified.add(node.name)
            index.referenced.add(node.name)
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    index.qualified.add(f"{node.name}.{child.name}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            index.qualified.add(node.name)
            index.referenced.add(node.name)
        elif isinstance(node, ast.Name):
            index.referenced.add(node.id)
        elif isinstance(node, ast.Attribute):
            index.referenced.add(node.attr)
        elif isinstance(node, ast.alias):
            name = node.asname or node.name.split(".")[0]
            index.qualified.add(name)
            index.referenced.add(name)


def _build_index(root: Path) -> SourceIndex:
    """Walk first-party source once and record what it defines and names."""
    index = SourceIndex()
    for pattern in SOURCE_GLOBS:
        for path in sorted(root.glob(pattern)):
            _collect(path, index)
    for name in EXTRA_SOURCE_PATHS:
        path = root / name
        if path.is_file():
            _collect(path, index)
    return index


def _status_of(text: str) -> str | None:
    """Return the ADR's ``## Status`` section as one collapsed line.

    A status may wrap across lines ("Superseded by ADR 0002, 0013, 0014"), so
    consecutive non-blank lines are joined. Blockquote lines are skipped: the
    explanatory note that follows a status is prose, not part of the status.
    """
    match = STATUS_RE.search(text)
    if match is None:
        return None
    parts: list[str] = []
    for line in match.group("body").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(">"):
            if parts:
                break
            continue
        parts.append(stripped)
    return " ".join(parts) if parts else None


def _contract_names(text: str) -> list[str]:
    """Return every symbol named by a ``## Contract`` marker."""
    names: list[str] = []
    for match in CONTRACT_RE.finditer(text):
        names.extend(
            part.strip() for part in match.group("names").split(",") if part.strip()
        )
    return names


def _indexed_statuses(index_text: str) -> dict[str, str]:
    """Map ADR number to the status the index claims."""
    return {
        m.group("num"): m.group("status") for m in INDEX_ROW_RE.finditer(index_text)
    }


_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_ADR_NUM_RE = re.compile(r"\b\d{4}\b")


def _status_key(status: str) -> tuple[str, frozenset[str]]:
    """Reduce a status to a comparable key.

    Two things must agree: whether the ADR is live, and which ADRs it points at.
    Phrasing need not match -- "Superseded by [ADR 0014](0014-....md)" and
    "Superseded by ADR 0014" are the same claim, and a check that rejected them
    would be rejected itself. So the first word carries liveness and the cited
    ADR numbers carry the pointers; everything in between is prose.
    """
    text = _LINK_RE.sub(r"\1", status.strip().lstrip(">").strip())
    words = text.split()
    live = words[0].lower() if words else ""
    return live, frozenset(_ADR_NUM_RE.findall(text))


def check(root: Path) -> tuple[list[Violation], int]:
    """Return every ADR violation, plus the count of unchecked ADRs.

    ``unchecked`` counts ADRs carrying no ``## Contract`` section. They do not
    fail the run, but the count is always reported so the gap stays visible.
    """
    adr_dir = root / "docs" / "adr"
    index_path = adr_dir / "README.md"
    if not adr_dir.is_dir():
        return [Violation("no-adr-dir", "docs/adr", "directory does not exist")], 0
    if not index_path.is_file():
        return [Violation("no-index", "docs/adr/README.md", "index is missing")], 0

    indexed = _indexed_statuses(index_path.read_text(encoding="utf-8"))
    source = _build_index(root)

    violations: list[Violation] = []
    unchecked: list[str] = []

    for path in sorted(adr_dir.glob("[0-9][0-9][0-9][0-9]-*.md")):
        name = path.name
        number = name[:4]
        text = path.read_text(encoding="utf-8")

        status = _status_of(text)
        if status is None:
            violations.append(
                Violation("no-status", name, "declares no '## Status' section")
            )
        elif number not in indexed:
            violations.append(
                Violation("unlisted", name, "no row in docs/adr/README.md claims it")
            )
        elif _status_key(status) != _status_key(indexed[number]):
            violations.append(
                Violation(
                    "stale-index",
                    name,
                    f"ADR status {status!r} but index says {indexed[number]!r}",
                )
            )

        contract = _contract_names(text)
        if not contract:
            unchecked.append(name)
            continue
        for symbol in contract:
            if not source.has(symbol):
                violations.append(
                    Violation(
                        "missing-contract",
                        name,
                        f"{symbol!r} is committed to but absent from source",
                    )
                )

    return violations, len(unchecked)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "root",
        type=Path,
        nargs="?",
        default=Path(__file__).resolve().parents[1],
        help="repository root (defaults to the checkout containing this script)",
    )
    args = parser.parse_args(argv)

    violations, unchecked = check(args.root.resolve())

    for kind in (
        "no-adr-dir",
        "no-index",
        "no-status",
        "unlisted",
        "stale-index",
        "missing-contract",
    ):
        for violation in (v for v in violations if v.kind == kind):
            print(f"{kind}: {violation}", file=sys.stderr)  # noqa: T201

    if violations:
        print(  # noqa: T201
            f"\n{len(violations)} ADR violation(s); {unchecked} ADR(s) carry no "
            "## Contract section",
            file=sys.stderr,
        )
        return 1

    print(  # noqa: T201
        f"ADR contract OK: every declared status matches the index and every "
        f"contracted symbol exists ({unchecked} ADR(s) carry no ## Contract "
        "section)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
