"""Preflight gate for a release to PyPI.

Two jobs, one source of truth:

1. Derive the publish order from the workspace dependency graph, so a release
   uploads every package before the packages that import it. No hardcoded list
   to fall out of date when a package is added.
2. Refuse to ship wheels whose metadata cannot resolve on another machine.

The second job is the point. ``[tool.uv.sources]`` is a uv-only table that never
reaches a wheel; ``Requires-Dist`` is emitted verbatim from
``[project.dependencies]``. A workspace dependency written as a bare name
therefore ships as a bare name, which resolves against whatever version of the
sibling happens to be newest on PyPI. That is how a release becomes
uninstallable. This gate fails the release instead.

Usage:
    python scripts/check_publish.py --list          # print publish order
    python scripts/check_publish.py --dist-dir dist  # validate built wheels
    python scripts/check_publish.py --version 0.6.5  # also assert lockstep
"""

from __future__ import annotations

import argparse
import email.parser
import pathlib
import sys
import tomllib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Characters that end the distribution name in a PEP 508 requirement string.
_NAME_TERMINATORS = "<>=!~[; "


def _requirement_name(requirement: str) -> str:
    """Return the bare distribution name from a PEP 508 requirement string."""
    body = requirement.strip()
    for index, character in enumerate(body):
        if character in _NAME_TERMINATORS:
            return body[:index].strip()
    return body


def _has_version_specifier(requirement: str) -> bool:
    """True when the requirement pins a range, not just a bare name.

    ``mvgeos-core`` and ``mvgeos-core ; python_version < '3.13'`` both fail.
    ``mvgeos-core>=0.6.5`` and ``mvgeos-core==0.6.5`` both pass.
    """
    body = requirement
    for separator in (";", "["):
        body = body.partition(separator)[0]
    return any(marker in body for marker in ("==", ">=", "<=", "~=", "!=", ">", "<"))


def workspace_packages() -> dict[str, pathlib.Path]:
    """Map every publishable distribution name to its pyproject.toml path."""
    root = tomllib.loads((ROOT / "pyproject.toml").read_text())
    packages = {root["project"]["name"]: ROOT / "pyproject.toml"}
    workspace = root.get("tool", {}).get("uv", {}).get("workspace", {})
    for member in workspace.get("members", []):
        member_path = ROOT / member / "pyproject.toml"
        if member_path.exists():
            declared = tomllib.loads(member_path.read_text())["project"]["name"]
            packages[declared] = member_path
    return packages


def internal_dependencies(packages: dict[str, pathlib.Path]) -> dict[str, list[str]]:
    """Map each package to the internal requirements it declares."""
    if len(packages) == 1:
        return {next(iter(packages)): []}
    graph = {}
    for name, path in packages.items():
        declared = tomllib.loads(path.read_text())["project"]["dependencies"]
        graph[name] = [
            requirement
            for requirement in declared
            if _requirement_name(requirement) in packages
        ]
    if not any(graph.values()):
        raise SystemExit(
            "no internal dependency edges found; refusing to pass a release "
            "gate that is checking nothing"
        )
    return graph


def publish_order(packages: dict[str, pathlib.Path]) -> list[str]:
    """Order packages so every dependency precedes its dependents.

    A dependency cycle is a hard error: no ordering satisfies it, and uploading
    anyway would ship a broken release.
    """
    graph = internal_dependencies(packages)
    ordered: list[str] = []
    placed: set[str] = set()

    def place(name: str, trail: tuple[str, ...]) -> None:
        if name in placed:
            return
        if name in trail:
            cycle = " -> ".join((*trail, name))
            raise SystemExit(f"dependency cycle in the workspace: {cycle}")
        placed.add(name)
        for requirement in graph[name]:
            place(_requirement_name(requirement), (*trail, name))
        ordered.append(name)

    for name in sorted(packages):
        place(name, ())
    return ordered


def check_lockstep(packages: dict[str, pathlib.Path], version: str) -> list[str]:
    """Every package must carry the release version the bump job produced."""
    problems = []
    for name, path in sorted(packages.items()):
        found = tomllib.loads(path.read_text())["project"]["version"]
        if found != version:
            problems.append(f"{name}: version {found}, expected {version}")
    return problems


def check_wheels(
    packages: dict[str, pathlib.Path], dist_dir: pathlib.Path
) -> list[str]:
    """Every internal Requires-Dist in every built wheel must carry a constraint."""
    problems = []
    for name in sorted(packages):
        # uv build -o dist/<package>/ writes each distribution to its own
        # subdirectory, so a package never has to be told apart by filename.
        package_dir = dist_dir / name
        wheels = sorted(package_dir.glob("*.whl"))
        if not wheels:
            wheels = sorted(dist_dir.glob(f"{name.replace('-', '_')}*.whl"))
        if not wheels:
            problems.append(f"{name}: no wheel in {package_dir} or {dist_dir}")
            continue
        wheel = wheels[-1]
        with zipfile.ZipFile(wheel) as archive:
            metadata_name = next(
                entry
                for entry in archive.namelist()
                if entry.endswith(".dist-info/METADATA")
            )
            metadata = email.parser.Parser().parsestr(
                archive.read(metadata_name).decode()
            )
        for requirement in metadata.get_all("Requires-Dist", []):
            if _requirement_name(requirement) not in packages:
                continue
            if not _has_version_specifier(requirement):
                problems.append(
                    f"{name}: requires {requirement!r} with no version "
                    f"constraint; it must resolve against PyPI"
                )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--list",
        action="store_true",
        help="print the dependency-ordered package list, one per line",
    )
    parser.add_argument(
        "--dist-dir",
        type=pathlib.Path,
        help="directory holding per-package build output, to validate wheel metadata",
    )
    parser.add_argument(
        "--version",
        help="release version every package must match, e.g. 0.6.5",
    )
    arguments = parser.parse_args()

    packages = workspace_packages()

    if arguments.list:
        print("\n".join(publish_order(packages)))
        return 0

    problems: list[str] = []
    if arguments.version:
        problems += check_lockstep(packages, arguments.version.lstrip("v"))
    if arguments.dist_dir:
        problems += check_wheels(packages, arguments.dist_dir)

    if not problems:
        print(f"preflight ok: {len(packages)} distributions")
        return 0

    for problem in problems:
        print(f"preflight failed: {problem}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
