"""Install agent skills from a local path or git URL.

A skill is a directory containing a ``SKILL.md`` file (agentskills.io format).
Installed skills land in ``~/.agents/skills/<name>/`` by default, where
``AppState.load_skills`` discovers them.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from mvge.core.layers import skills_dir


def _is_git_url(source: str) -> bool:
    return source.startswith(
        ("https://", "http://", "git@", "ssh://")
    ) or source.endswith(".git")


def _name_from_git_url(url: str) -> str:
    # Take the last path segment, strip .git, sanitize.
    segment = url.rstrip("/").split("/")[-1]
    if segment.endswith(".git"):
        segment = segment[:-4]
    # git@github.com:owner/repo -> repo
    if ":" in segment and "/" not in segment:
        segment = segment.split(":")[-1]
    name = re.sub(r"[^A-Za-z0-9_.-]", "-", segment).strip("-")
    return name or "skill"


def _validate_install_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned or cleaned in (".", ".."):
        raise ValueError(f"Invalid skill name: {name!r}")
    if "/" in cleaned or "\\" in cleaned or "\x00" in cleaned:
        raise ValueError(f"Invalid skill name: {name!r}")
    return cleaned


def _clone_git_repo(url: str, dest: Path) -> None:
    """Clone a git repo to dest. Separated for test mocking."""
    subprocess.run(
        ["git", "clone", "--depth", "1", url, str(dest)],
        check=True,
        capture_output=True,
    )


def install_skill(
    source: str,
    name: str | None = None,
    target_dir: Path | None = None,
) -> Path:
    """Install a skill from a local directory or git URL.

    Args:
        source: Local path to a skill directory, or a git URL.
        name: Override the installed directory name.
        target_dir: Where to install (default ``~/.agents/skills``).

    Returns:
        Path to the installed skill directory.

    Raises:
        ValueError: When the source is invalid, has no SKILL.md,
            or a skill with the resolved name is already installed.
    """
    target = target_dir.expanduser() if target_dir is not None else skills_dir()

    if _is_git_url(source):
        skill_name = _validate_install_name(name or _name_from_git_url(source))
        dest = target / skill_name
        if dest.exists():
            raise ValueError(f"Skill '{skill_name}' is already installed at {dest}")
        target.mkdir(parents=True, exist_ok=True)
        try:
            _clone_git_repo(source, dest)
        except subprocess.CalledProcessError as exc:
            raise ValueError(f"Failed to clone {source!r}: {exc.stderr}") from exc
    else:
        src = Path(source).expanduser()
        if not src.exists():
            raise ValueError(f"Skill source does not exist: {source!r}")
        if not src.is_dir():
            raise ValueError(f"Skill source is not a directory: {source!r}")
        skill_name = _validate_install_name(name or src.name)
        dest = target / skill_name
        if dest.exists():
            raise ValueError(f"Skill '{skill_name}' is already installed at {dest}")
        target.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest)

    if not (dest / "SKILL.md").is_file():
        shutil.rmtree(dest, ignore_errors=True)
        raise ValueError(f"Installed skill has no SKILL.md: {dest}")

    return dest
