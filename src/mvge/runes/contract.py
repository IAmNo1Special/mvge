"""Which engine imports a Rune is entitled to.

The marketplace clones this repository and imports from ``main``. When
``DEFAULT_TOME_DIR`` was replaced by the ``sessions_dir()`` resolver,
``pi-codec`` and ``session-search`` broke on the marketplace's ``main``
with ten import errors, and nothing in this repository failed -- because the
marketplace only runs when someone pushes to it.

This module is the rule, in one place, so both repositories can check against
it. It is deliberately **inclusive**, and it names two different kinds of
entry:

* entries a Rune imports today, which are load-bearing on the strength of it;
* entries no Rune currently imports but which describe an import path the
  engine permits, so that writing one is declared rather than surprising.

Its value is that it is now a *declaration*:

* removing a line here is a visible decision, and the consequence lands in the
  marketplace's own CI on the next push rather than as a surprise import error;
* an import outside this set is reported as ``unpublished``, which is a kind the
  previous check could not produce -- it only looked at package roots, so a
  Rune subclassing ``mvge.provider.sse.SSEStreamingRealm`` passed cleanly.

``mvge.provider.sse`` is the second kind. The Realm seam moved to
``mvge.provider.realm_seam`` and both marketplace Realms now import
``SSEChunk``, ``SSEStreamingRealm`` and ``retry_realm_request`` from the
package root, so no Rune imports this submodule today. It is declared because
such an import is a permitted path, not because anything takes it. An earlier
draft of this docstring described it as a leak held back by an unmigrated
consumer; that was true when it was written and stopped being true when the
seam moved.

The other leak the review named, ``mvge.core.telemetry``, is closed by
ADR 0021: the vocabulary now lives in the ``opentelemetry-bridge`` Rune that was
its only consumer, and the engine module is gone.

Declaration is only meaningful for imports from a package root, since
``__all__`` describes a namespace rather than a submodule.
"""

from __future__ import annotations

import ast
import importlib
from dataclasses import dataclass
from pathlib import Path

#: The packages this engine publishes, matched on the first dotted segment so
#: a rune's own package (``mvge_rune_pi_codec``) is not mistaken for the
#: engine's (``mvge.runes``).
ENGINE_PACKAGES = frozenset(
    {
        "mvge",
        "mvge.core",
        "mvge.agent",
        "mvge.provider",
        "mvge.runes",
        "mvge.tome",
        "mvge.cli",
        "mvgeos_core",
        "mvgeos_agent",
        "mvgeos_provider",
        "mvgeos_runes",
        "mvgeos_tome",
        "mvgeos_cli",
    }
)

RUNE_IMPORTABLE_MODULES = frozenset(
    {
        # The Rune seam itself: what a factory is handed and what it registers.
        "mvge.runes",
        "mvge.runes.types",
        "mvge.runes.rune_api",
        "mvge.runes.rune_runner",
        "mvge.runes.manifest",
        "mvge.runes.loader",
        "mvge.runes.codecs",
        "mvge.runes.installer",
        # What a Rune produces: Spells, and the vocabulary the loop hands them.
        "mvge.core",
        "mvge.core.spells",
        "mvge.core.abort",
        "mvge.core.approval",
        "mvge.core.channel",
        "mvge.core.constants",
        "mvge.core.events",
        "mvge.core.invocations",
        "mvge.core.layers",
        "mvge.core.spell_schema",
        "mvge.core.truncate",
        "mvge.core.sandbox",
        # A Realm is a Rune's product, so its seam is published.
        "mvge.provider",
        "mvge.provider.base",
        "mvge.provider.registry",
        # The implementation module. No Rune imports it -- the Realm seam above
        # is the published path -- but subclassing SSEStreamingRealm from its
        # defining module is permitted, and declaring it keeps that a choice.
        "mvge.provider.sse",
        "mvge.provider.retry",
        "mvge.provider.realm_seam",
        "mvge.provider.model_registry",
        # An Mvge is the engine itself; an artifact packaging one wraps it.
        "mvge.agent",
        "mvge.agent.mvge",
        "mvge.agent.environment",
        "mvge.agent.function_spell",
        "mvge.agent.harness",
        "mvge.agent.harness._state",
        "mvge.agent.sigil_payloads",
        "mvge.agent.config_manager",
        "mvge.agent.agent_name",
        "mvge.agent.sub_mvge",
        # A Tome codec is a Rune's product.
        "mvge.tome",
        "mvge.tome.types",
        "mvge.tome.codec",
        "mvge.tome.handle",
    }
)


@dataclass(frozen=True)
class Violation:
    """One bad engine import, located in the artifact that makes it."""

    kind: str
    location: str
    module: str
    name: str

    def __str__(self) -> str:
        if self.kind == "unpublished":
            return (
                f"{self.location}: '{self.module}' is not published for Runes "
                f"({self.name}). Add it to "
                "mvge.runes.contract.RUNE_IMPORTABLE_MODULES if it should be, "
                "or reach it another way."
            )
        if self.kind == "unimportable":
            return (
                f"{self.location}: {self.module} could not be imported "
                f"({self.name}) -- the import will fail at runtime"
            )
        if self.kind == "missing":
            return (
                f"{self.location}: {self.module} has no '{self.name}' "
                "-- the import will fail at runtime"
            )
        return (
            f"{self.location}: '{self.name}' is not declared in "
            f"{self.module}.__all__ -- undeclared dependency, can be removed "
            "with no signal here"
        )


def _summary(names: list[str]) -> str:
    """How an import is named in a message: one name, or a counted list."""
    if len(names) == 1:
        return names[0]
    return f"{len(names)} symbols including {names[0]}"


def _imported_symbols(tree: ast.AST) -> list[tuple[str, list[str], int]]:
    """Return ``(module, names, lineno)`` for every engine import in a tree.

    Walks the whole tree rather than only module-level statements, so imports
    inside ``try``/``TYPE_CHECKING`` blocks are covered too: a type-only import
    that no longer resolves still breaks the artifact's typecheck.
    """
    found: list[tuple[str, list[str], int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level:
            continue
        module = node.module or ""
        if module.split(".", 1)[0] not in ENGINE_PACKAGES:
            continue
        names = [alias.name for alias in node.names if alias.name != "*"]
        if names:
            found.append((module, names, node.lineno))
    return found


def check_source(source: str, *, location: str = "<source>") -> list[Violation]:
    """Every contract violation in one module of artifact source.

    ``location`` is how the violation is attributed -- a path relative to the
    artifact, so the message names the file its reader will open.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [Violation("unparseable", location, "<parse>", str(exc))]

    violations: list[Violation] = []
    for module, names, lineno in _imported_symbols(tree):
        at = f"{location}:{lineno}"

        if module not in RUNE_IMPORTABLE_MODULES:
            # One violation per import, not per symbol: the fix is the same
            # either way, and sixteen near-identical lines read as a fault
            # count rather than an instruction.
            violations.append(Violation("unpublished", at, module, _summary(names)))
            continue

        try:
            imported = importlib.import_module(module)
        except ImportError as exc:
            # Report the module once, rather than blaming every symbol for a
            # failure they did not cause.
            violations.append(Violation("unimportable", at, module, str(exc)))
            continue

        declared = getattr(imported, "__all__", None)
        is_package_root = module in ENGINE_PACKAGES
        for name in names:
            if not hasattr(imported, name):
                violations.append(Violation("missing", at, module, name))
            elif is_package_root and declared is not None and name not in declared:
                violations.append(Violation("undeclared", at, module, name))
    return violations


def check_tree(root: object, *, relative_to: object = None) -> list[Violation]:
    """Every violation under an artifact directory.

    Accepts ``pathlib.Path`` objects; typed loosely so the marketplace's own
    test can hand it roots it derived from the catalog.
    """
    base = Path(root)  # type: ignore[arg-type]
    origin = Path(relative_to) if relative_to is not None else base  # type: ignore[arg-type]
    violations: list[Violation] = []
    for path in sorted(base.rglob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
        except OSError as exc:
            violations.append(
                Violation(
                    "unreadable", str(path.relative_to(origin)), "<read>", str(exc)
                )
            )
            continue
        violations.extend(check_source(source, location=str(path.relative_to(origin))))
    return violations
