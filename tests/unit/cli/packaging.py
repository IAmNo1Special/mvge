"""Packaging contract: every third-party import is a declared dependency, and
every internal dependency is version-bounded.

The first test is a regression test for clean-install breakage: ``mvge`` failed
on a fresh install with ``ModuleNotFoundError: No module named 'dotenv'``
because ``mvge.agent`` imported it without declaring ``python-dotenv`` (and
the same for ``click`` in ``mvge.cli``). If the code imports it, ``pyproject``
must declare it.

The second test guards the published-wheel contract. ``[tool.uv.sources]
workspace = true`` never reaches wheel metadata -- ``uv_build`` copies
``[project.dependencies]`` verbatim -- so a bare internal name ships as an
unbounded ``Requires-Dist`` and floats to whatever sibling is newest on the
index. Nothing local fails when that happens; it only surfaces when a stranger
installs, which is exactly how these packages reached their first release.
"""

import ast
import sys
import tomllib
from importlib.metadata import packages_distributions
from importlib.util import find_spec
from pathlib import Path


def _package_root() -> Path:
    # tests/unit/cli/packaging.py -> root/
    return Path(__file__).resolve().parents[3]


def _declared_distributions() -> set[str]:
    pyproject = _package_root() / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    names: set[str] = set()
    for dep in data["project"]["dependencies"]:
        name = dep.strip()
        for sep in ("[", ";", "=", ">", "<", "!", "~"):
            name = name.split(sep)[0]
        names.add(name.strip().lower().replace("-", "_"))
    return names


def _third_party_imports() -> set[str]:
    src = _package_root() / "src"
    stdlib = sys.stdlib_module_names
    own = {p.name for p in src.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    found: set[str] = set()
    for py in sorted(src.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    return {m for m in found if m not in stdlib and m not in own}


def test_all_third_party_imports_are_declared_dependencies() -> None:
    declared = _declared_distributions()
    provided: dict[str, set[str]] = {}
    for module, dists in packages_distributions().items():
        provided[module] = {d.lower().replace("-", "_") for d in dists}
    missing = sorted(
        mod
        for mod in _third_party_imports()
        if not _is_covered(mod, declared, provided)
    )
    assert not missing, (
        "third-party imports without a declared dependency in pyproject.toml: "
        + ", ".join(missing)
    )


def _is_covered(module: str, declared: set[str], provided: dict[str, set[str]]) -> bool:
    dists = provided.get(module)
    if dists is not None:
        return bool(dists & declared)
    normalized = module.lower().replace("-", "_")
    return normalized in declared and find_spec(module) is not None


def test_dependencies_are_version_bounded() -> None:
    root = _package_root()
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    unbounded: list[str] = []
    for requirement in data["project"]["dependencies"]:
        if not any(op in requirement for op in ("=", ">", "<", "~", "!")):
            unbounded.append(requirement.strip())

    assert not unbounded, (
        "dependencies without a version floor ship as unbounded: "
        + ", ".join(sorted(unbounded))
    )
