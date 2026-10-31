"""Python-dependency installation for installed extensions.

Runes are loaded in-process and mutate registries the host owns -- the
realm registry, the rune runner. Both Pi and DeepSeek's harness settled
on the same constraint for their extensions: one shared, host-singleton
dependency tree, because an extension that resolves its own private copy
of a host package registers into a module-level registry the agent loop
never reads. That failure is silent, and it is why this project does not
give each rune its own virtualenv.

So there is exactly one environment, owned by the extensions directory.
That environment is found at load and check time by
``mvge.runes.loader.inject_dependency_site_packages``, which injects the
shared extensions venv for every rune whatever layer it lives in, plus
each rune's own parent venvs. Rune-local discovery alone is not enough:
it only walks the rune's own parents, which never reaches the shared
extensions venv for an agent- or project-layer rune.

The environment has its own project manifest, mirroring the single
generated manifest per install root that Pi maintains for its
extensions. Dependencies therefore never land in the pyproject.toml or
uv.lock of whatever project the user happened to invoke the CLI from.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

#: Name of the generated project that owns every rune's dependencies.
EXTENSIONS_PROJECT_NAME = "mvge-extensions"

_EXTENSIONS_MANIFEST = f"""\
[project]
name = "{EXTENSIONS_PROJECT_NAME}"
version = "0.0.0"
description = "Shared Python environment for mvge extensions"
requires-python = ">=3.13"
dependencies = []
"""


def ensure_extensions_project(env_project: Path) -> Path:
    """Create the extensions project's manifest if it is not already there.

    Never rewrites an existing manifest: an earlier install may already
    have added dependencies that must survive.
    """
    env_project.mkdir(parents=True, exist_ok=True)
    manifest = env_project / "pyproject.toml"
    if not manifest.is_file():
        manifest.write_text(_EXTENSIONS_MANIFEST, encoding="utf-8")
    return manifest


def confirm_python_deps_install(deps: list[str], *, confirm: bool | None) -> bool:
    """Decide whether to install manifest-declared dependencies.

    A manifest is authored by whoever published the rune, so its
    ``python_deps`` is an instruction to fetch arbitrary packages from
    PyPI. Gate it.

    Args:
        deps: The dependency specifiers from the manifest.
        confirm: ``True`` installs without prompting, ``False`` skips,
            ``None`` (default) prompts interactively and fails closed
            (skips) when stdin is not a TTY.

    Returns:
        True when the dependencies should be installed.
    """
    if confirm is True:
        return True
    if confirm is False:
        logger.info("Skipping python dependency install (confirm=False).")
        return False
    if not sys.stdin.isatty():
        logger.warning(
            "Non-interactive session: skipping install of unreviewed python "
            "dependencies %s. Re-run with confirm=True to install.",
            deps,
        )
        return False
    print("The extension manifest declares the following python dependencies:")  # noqa: T201
    for dep in deps:
        print(f"  - {dep}")  # noqa: T201
    try:
        answer = input(f"Install them into {EXTENSIONS_PROJECT_NAME}? [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def read_manifest_python_deps(manifest_path: Path) -> list[str]:
    """Return the non-blank ``python_deps`` declared by a manifest.

    Returns an empty list when the manifest is absent or unreadable: a
    manifest that cannot be parsed is not grounds for guessing at a
    dependency list.
    """
    try:
        with Path(manifest_path).open("r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, dict):
        return []
    raw = data.get("python_deps")
    if not isinstance(raw, list):
        return []
    return [str(d).strip() for d in raw if str(d).strip()]


def install_python_deps(
    deps: list[str],
    *,
    env_project: Path,
    manifest_path: Path | None = None,
    confirm: bool | None = None,
) -> bool:
    """Install ``deps`` into the shared extensions environment.

    Args:
        deps: Dependency specifiers. Read from ``manifest_path`` instead
            when given, so the caller never has to parse the manifest.
        env_project: Directory owning the shared environment.
        manifest_path: Manifest to read ``python_deps`` from.
        confirm: Forwarded to :func:`confirm_python_deps_install`.

    Returns:
        True when the dependencies were installed, False when they were
        skipped or the install failed. A failure is logged rather than
        swallowed: the previous bare ``uv add`` discarded the error, so a
        rune could install cleanly and then fail at load with a bare
        ImportError pointing nowhere near the cause.
    """
    if manifest_path is not None:
        deps = read_manifest_python_deps(manifest_path)
    specifiers = [d.strip() for d in deps if d.strip()]
    if not specifiers:
        return True
    if not confirm_python_deps_install(specifiers, confirm=confirm):
        return False

    ensure_extensions_project(env_project)
    argv = ["uv", "add", "--project", str(env_project), *specifiers]
    try:
        subprocess.run(argv, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        logger.warning(
            "Failed to install python dependencies %s into %s: %s",
            specifiers,
            env_project,
            detail or f"uv exited {exc.returncode}",
        )
        return False
    except (FileNotFoundError, OSError) as exc:
        logger.warning(
            "Could not run uv to install python dependencies %s: %s",
            specifiers,
            exc,
        )
        return False
    logger.info("Installed python dependencies %s into %s", specifiers, env_project)
    return True
