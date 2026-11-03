"""Phase 2B static source audit for runes.

An AST scan of rune source, never an import and never an
execution: a module whose top level would write a file, open a
socket, or import the engine still leaves no side effect behind
when audited. This module is deliberately separate from
``mvge.runes.rune_audit``: that file is the operations audit
log (append-only records of rune ops), this one is a source
audit. The two concerns must not be conflated.

Findings are blocking or advisory:

- Blocking (refused at admission by the loader):
  - ``private_runner_access`` — ``._runner``, ``._facade``, or
    ``._wrapped_runner`` attribute access, or ``getattr`` for
    any of those names. The Phase 2A facade closed ``._runner``
    as a documented path, but ``api._facade`` reaches the same
    live runner and bypasses the capability gate, so the
    facade chain is refused on the same terms. Deliberate
    boundary: dynamically constructed attribute names
    (concatenated ``getattr``, ``exec``, ``vars``) are not
    detected — the audit is a review aid, not a proof.
  - ``private_install_id_access`` — ``api._install_id`` or
    ``context._install_id`` (a rune's own gate object carrying
    its own ``_install_id`` is not this pattern and is not
    flagged: only ``api``/``context`` receivers are).
  - ``internal_engine_import`` — imports of engine-internal
    rune modules (``mvge.runes.rune_runner``,
    ``mvge.runes.runner_facade``, ``mvge.runes.loader``,
    ``mvge.runes.installer``, ``mvge.runes.watcher``,
    ``mvge.runes.skill_installer``, ``mvge.runes.catalog``,
    ``mvge.runes.deps``), in dotted, bare-submodule
    (``from mvge.runes import rune_runner``), and bare-object
    (``from mvge.runes import RuneRunner``) spellings. Public
    API modules (``rune_api``, ``types``, ``rune_audit``,
    ``capabilities``, ``source_audit``) and other bare
    ``mvge.runes`` re-exports are allowed.
  - ``registry_mutation`` — assignment into a runner's internal
    registries (``._spells``, ``._realm_factories``,
    ``._sigil_handlers``, ``._commands``, ``._handlers``,
    ``._rune_instances``) when the receiver is a runner, facade,
    or api object. An agent instance or a rune's own object
    carrying a ``_spells`` attribute is its own state, not the
    runner registry, and is not flagged.
  - ``sys_path_manipulation`` — ``sys.path`` append/insert/
    extend/remove/pop or assignment. Path injection is
    engine-owned (the loader injects rune roots); rune code
    must not do it.
  - ``undeclared_capability_use`` — a call to a RuneAPI method
    governed by the ONE declaration table in
    ``mvge.runes.capabilities`` (``CAPABILITY_GATES``) whose
    capability the rune did not declare. Checked only when the
    manifest declares capabilities; legacy manifests skip
    this check.
  - ``syntax_error`` — the file does not parse; it could not
    execute anyway.

- Advisory (flagged for review, admission still allows):
  - ``sys_modules_manipulation`` — ``sys.modules`` assignment
    or deletion. The sanctioned importlib pattern registers a
    module in ``sys.modules`` before ``exec_module`` (the
    engine loader itself does this), so this cannot be a
    refusal on its own; it is surfaced for a human to review.

The audit is a review aid and admission gate only. It never
claims to block in-process escape: a determined rune can still
reach objects through introspection, and no diagnostic or doc
string here says otherwise. The parity test (same declaration
table for audit and runtime) prepares Phase 4, where
unsupported calls fail closed from the same table.

Phase 4 hosted mode (``audit_hosted_source`` /
``audit_source(..., hosted=True)``): in addition to the checks
above, any ``api.<method>`` use of a method in the ONE hosted
table (``mvge.runes.host_classification.HOST_UNSUPPORTED_METHODS``)
is a blocking ``host_unsupported_method`` finding. The hosted
runtime (``HostRuneAPI``) refuses the same methods from the
same table, and the parity test asserts the two verdicts agree
for every method in it.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from mvge.runes.capabilities import METHOD_TO_CAPABILITY
from mvge.runes.host_classification import HOST_UNSUPPORTED_METHODS

INTERNAL_ENGINE_MODULES: frozenset[str] = frozenset(
    {
        "rune_runner",
        "runner_facade",
        "loader",
        "installer",
        "watcher",
        "skill_installer",
        "catalog",
        "deps",
    }
)

# Bare ``from mvge.runes import <name>`` of an engine object is
# also internal; public re-exports (types, API, audit) pass.
INTERNAL_BARE_IMPORTS: frozenset[str] = frozenset(
    {"RuneRunner", "RunnerFacade", "RuneWatcher"}
)

REGISTRY_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "_spells",
        "_realm_factories",
        "_sigil_handlers",
        "_commands",
        "_handlers",
        "_rune_instances",
    }
)

_SKIP_DIRS: frozenset[str] = frozenset(
    {".venv", "venv", "__pycache__", ".git", "tests", "test"}
)


@dataclass(frozen=True)
class AuditFinding:
    kind: str
    file: str
    line: int
    message: str
    blocking: bool


def blocking_findings(findings: list[AuditFinding]) -> list[AuditFinding]:
    return [f for f in findings if f.blocking]


class _Visitor(ast.NodeVisitor):
    def __init__(
        self,
        filename: str,
        declared: list[str] | None,
        hosted: bool = False,
    ) -> None:
        self._filename = filename
        self._declared = declared
        self._hosted = hosted
        self.findings: list[AuditFinding] = []

    def _add(self, kind: str, node: ast.AST, message: str, blocking: bool) -> None:
        self.findings.append(
            AuditFinding(
                kind=kind,
                file=self._filename,
                line=getattr(node, "lineno", 0),
                message=message,
                blocking=blocking,
            )
        )

    @staticmethod
    def _base_name(node: ast.AST) -> str | None:
        while isinstance(node, ast.Attribute):
            node = node.value
        if isinstance(node, ast.Name):
            return node.id
        return None

    @staticmethod
    def _chain_names(node: ast.AST) -> list[str]:
        names: list[str] = []
        while isinstance(node, ast.Attribute):
            names.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            names.append(node.id)
        return names

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if (
            self._hosted
            and node.attr in HOST_UNSUPPORTED_METHODS
            and "api" in self._chain_names(node)
        ):
            self._add(
                "host_unsupported_method",
                node,
                f"Hosted use of api.{node.attr} is refused in v1: "
                f"{HOST_UNSUPPORTED_METHODS[node.attr]}",
                True,
            )
        if node.attr in ("_runner", "_facade", "_wrapped_runner"):
            self._add(
                "private_runner_access",
                node,
                f"Access to a private {node.attr} attribute is refused at admission",
                True,
            )
        elif node.attr == "_install_id" and self._base_name(node.value) in (
            "api",
            "context",
        ):
            self._add(
                "private_install_id_access",
                node,
                "Access to api/context _install_id is refused at admission",
                True,
            )
        elif node.attr == "path" and self._base_name(node.value) == "sys":
            # Only a mutation context counts: sys.path used as a
            # value (e.g. passed to a function) is not flagged.
            # The parent check happens in visit_Call/Assign; this
            # branch intentionally does nothing on its own.
            pass
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        # getattr(obj, "_runner" | "_facade" | "_wrapped_runner"
        # | "_install_id")
        if (
            isinstance(func, ast.Name)
            and func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value
            in ("_runner", "_facade", "_wrapped_runner", "_install_id")
        ):
            kind = (
                "private_install_id_access"
                if node.args[1].value == "_install_id"
                else "private_runner_access"
            )
            self._add(kind, node, "getattr escape to a private attribute", True)
        if (
            self._hosted
            and isinstance(func, ast.Name)
            and func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in HOST_UNSUPPORTED_METHODS
        ):
            self._add(
                "host_unsupported_method",
                node,
                f"Hosted getattr use of api.{node.args[1].value} is "
                "refused in v1: "
                f"{HOST_UNSUPPORTED_METHODS[str(node.args[1].value)]}",
                True,
            )
        if isinstance(func, ast.Attribute):
            chain = self._chain_names(func)
            # sys.path.append/insert/extend/remove/pop
            if (
                len(chain) >= 3
                and chain[-1] == "sys"
                and chain[-2] == "path"
                and chain[0] in ("append", "insert", "extend", "remove", "pop")
            ):
                self._add(
                    "sys_path_manipulation",
                    node,
                    "sys.path manipulation is engine-owned",
                    True,
                )
            # sys.modules.pop(...)
            if (
                len(chain) >= 3
                and chain[-1] == "sys"
                and chain[-2] == "modules"
                and chain[0] == "pop"
            ):
                self._add(
                    "sys_modules_manipulation",
                    node,
                    "sys.modules manipulation flagged for review",
                    False,
                )
            # RuneAPI capability method calls: api.<method>(...) or
            # self.api.<method>(...) — an "api" link in the chain.
            method = func.attr
            capability = METHOD_TO_CAPABILITY.get(method)
            if (
                capability is not None
                and "api" in chain
                and self._declared is not None
                and capability not in self._declared
            ):
                self._add(
                    "undeclared_capability_use",
                    node,
                    f"Call to {method} requires capability '{capability}', "
                    "which this rune did not declare",
                    True,
                )
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._check_mutation_target(target, node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._check_mutation_target(node.target, node)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._check_mutation_target(node.target, node)
        self.generic_visit(node)

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            self._check_mutation_target(target, node)
        self.generic_visit(node)

    def _check_mutation_target(self, target: ast.AST, node: ast.AST) -> None:
        # Registry mutation counts only when the receiver is a
        # runner/facade/api object: a rune's own object (or an
        # agent instance, as in skill-evolution's proposer binding
        # spells onto its own agent) carrying a ``_spells``
        # attribute is its own state, not the runner registry.
        if isinstance(target, ast.Attribute):
            if target.attr in REGISTRY_ATTRIBUTES and (
                self._base_name(target.value) in ("runner", "facade", "api")
                or "runner" in self._chain_names(target)
                or "facade" in self._chain_names(target)
            ):
                self._add(
                    "registry_mutation",
                    node,
                    f"Direct mutation of internal registry '{target.attr}'",
                    True,
                )
            if target.attr == "path" and self._base_name(target.value) == "sys":
                self._add(
                    "sys_path_manipulation",
                    node,
                    "sys.path assignment is engine-owned; rune code must not do it",
                    True,
                )
        if isinstance(target, ast.Subscript):
            value = target.value
            if isinstance(value, ast.Attribute):
                if value.attr in REGISTRY_ATTRIBUTES and (
                    self._base_name(value.value) in ("runner", "facade", "api")
                    or "runner" in self._chain_names(value)
                    or "facade" in self._chain_names(value)
                ):
                    self._add(
                        "registry_mutation",
                        node,
                        f"Direct mutation of internal registry '{value.attr}'",
                        True,
                    )
                if value.attr == "modules" and self._base_name(value.value) == "sys":
                    self._add(
                        "sys_modules_manipulation",
                        node,
                        "sys.modules manipulation flagged for review",
                        False,
                    )
                if value.attr == "path" and self._base_name(value.value) == "sys":
                    self._add(
                        "sys_path_manipulation",
                        node,
                        "sys.path manipulation is engine-owned",
                        True,
                    )

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            parts = alias.name.split(".")
            if (
                len(parts) >= 3
                and parts[0] == "mvge"
                and parts[1] == "runes"
                and parts[2] in INTERNAL_ENGINE_MODULES
            ):
                self._add(
                    "internal_engine_import",
                    node,
                    f"Direct import of internal engine module '{alias.name}'",
                    True,
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        parts = module.split(".")
        if (
            len(parts) >= 3
            and parts[0] == "mvge"
            and parts[1] == "runes"
            and parts[2] in INTERNAL_ENGINE_MODULES
        ):
            self._add(
                "internal_engine_import",
                node,
                f"Direct import from internal engine module '{module}'",
                True,
            )
        elif module == "mvge.runes":
            for alias in node.names:
                if alias.name in INTERNAL_BARE_IMPORTS | INTERNAL_ENGINE_MODULES:
                    self._add(
                        "internal_engine_import",
                        node,
                        f"Direct import of internal engine object '{alias.name}'",
                        True,
                    )
        self.generic_visit(node)


def audit_source(
    source: str,
    *,
    declared_capabilities: list[str] | None = None,
    filename: str = "<source>",
    hosted: bool = False,
) -> list[AuditFinding]:
    """Audit one source string. Parses only; never imports or executes.

    With ``hosted=True`` the Phase 4 hosted table also applies:
    use of a hosted-unsupported facade method is a blocking
    ``host_unsupported_method`` finding.
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as err:
        return [
            AuditFinding(
                kind="syntax_error",
                file=filename,
                line=err.lineno or 0,
                message=f"Source does not parse: {err}",
                blocking=True,
            )
        ]
    visitor = _Visitor(filename, declared_capabilities, hosted=hosted)
    visitor.visit(tree)
    return visitor.findings


def audit_hosted_source(
    source: str, *, filename: str = "<source>"
) -> list[AuditFinding]:
    """Audit one source string as a hosted rune (Phase 4 v1)."""
    return audit_source(source, filename=filename, hosted=True)


def audit_rune_dir(
    rune_dir: Path,
    declared_capabilities: list[str] | None = None,
    *,
    hosted: bool = False,
) -> list[AuditFinding]:
    """Audit every Python file under a rune dir, except vendored/test dirs.

    Never imports or executes anything found. Files are read as
    text and parsed with ``ast`` only.
    """
    findings: list[AuditFinding] = []
    for path in sorted(rune_dir.rglob("*.py")):
        if any(part in _SKIP_DIRS for part in path.relative_to(rune_dir).parts):
            continue
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        relative = str(path.relative_to(rune_dir))
        findings.extend(
            audit_source(
                source,
                declared_capabilities=declared_capabilities,
                filename=relative,
                hosted=hosted,
            )
        )
    return findings


def audit_admission(
    rune_dir: Path,
    declared_capabilities: list[str] | None = None,
) -> tuple[bool, list[AuditFinding]]:
    """Admission verdict: allowed iff no blocking finding exists."""
    findings = audit_rune_dir(rune_dir, declared_capabilities)
    return (not blocking_findings(findings)), findings
