"""Unit tests for the skill installer."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from mvge.runes.skill_installer import install_skill


def _create_mock_skill_dir(path: Path, name: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(f"# {name}\n\nTest skill.\n", encoding="utf-8")
    return path


def test_install_skill_from_local_path(tmp_path: Path) -> None:
    source = _create_mock_skill_dir(tmp_path / "my-skill", "my-skill")
    target = tmp_path / "skills"

    dest = install_skill(str(source), target_dir=target)

    assert dest == target / "my-skill"
    assert (dest / "SKILL.md").is_file()


def test_install_skill_from_local_path_with_name(tmp_path: Path) -> None:
    source = _create_mock_skill_dir(tmp_path / "src-skill", "my-skill")
    target = tmp_path / "skills"

    dest = install_skill(str(source), name="renamed-skill", target_dir=target)

    assert dest == target / "renamed-skill"
    assert (dest / "SKILL.md").is_file()


def test_install_skill_rejects_missing_skill_md(tmp_path: Path) -> None:
    source = tmp_path / "not-a-skill"
    source.mkdir()
    target = tmp_path / "skills"

    with pytest.raises(ValueError, match="SKILL.md"):
        install_skill(str(source), target_dir=target)


def test_install_skill_rejects_nonexistent_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        install_skill(str(tmp_path / "nope"), target_dir=tmp_path / "skills")


def test_install_skill_from_git_url(tmp_path: Path) -> None:
    target = tmp_path / "skills"

    def fake_clone(url: str, dest: Path) -> None:
        _create_mock_skill_dir(dest, "cloned-skill")

    with patch("mvge.runes.skill_installer._clone_git_repo", side_effect=fake_clone):
        dest = install_skill(
            "https://github.com/example/skill-repo.git", target_dir=target
        )

    assert dest == target / "skill-repo"
    assert (dest / "SKILL.md").is_file()


def test_install_skill_refuses_overwrite(tmp_path: Path) -> None:
    source = _create_mock_skill_dir(tmp_path / "my-skill", "my-skill")
    target = tmp_path / "skills"
    install_skill(str(source), target_dir=target)

    with pytest.raises(ValueError, match="already installed"):
        install_skill(str(source), target_dir=target)


def test_install_skill_from_scp_style_git_url(tmp_path: Path) -> None:
    """SCP-style git URLs derive the skill name from the path segment."""

    def fake_clone(url: str, dest: Path) -> None:
        _create_mock_skill_dir(dest, "cloned-skill")

    with patch("mvge.runes.skill_installer._clone_git_repo", side_effect=fake_clone):
        dest = install_skill(
            "git@github.com:example/skill-repo", target_dir=tmp_path / "skills"
        )

    assert dest == tmp_path / "skills" / "skill-repo"
    assert (dest / "SKILL.md").is_file()


def test_install_skill_from_git_url_without_dotgit_suffix(tmp_path: Path) -> None:
    """Git URLs without a .git suffix keep the last path segment as the name."""

    def fake_clone(url: str, dest: Path) -> None:
        _create_mock_skill_dir(dest, "cloned-skill")

    with patch("mvge.runes.skill_installer._clone_git_repo", side_effect=fake_clone):
        dest = install_skill(
            "https://example.com/skills/my-skill", target_dir=tmp_path / "skills"
        )

    assert dest == tmp_path / "skills" / "my-skill"
    assert (dest / "SKILL.md").is_file()


@pytest.mark.parametrize("bad_name", [".", "..", "a/b", "a\\b", "a\x00b"])
def test_install_skill_rejects_invalid_names(tmp_path: Path, bad_name: str) -> None:
    """Dot-only, path-separator, and NUL names are rejected.

    A blank name is not invalid: it falls back to the source directory name.
    """
    source = _create_mock_skill_dir(tmp_path / "my-skill", "my-skill")

    with pytest.raises(ValueError, match="Invalid skill name"):
        install_skill(str(source), name=bad_name, target_dir=tmp_path / "skills")


def test_install_skill_git_url_refuses_overwrite(tmp_path: Path) -> None:
    """Installing the same git URL twice raises instead of overwriting."""

    def fake_clone(url: str, dest: Path) -> None:
        _create_mock_skill_dir(dest, "cloned-skill")

    with patch("mvge.runes.skill_installer._clone_git_repo", side_effect=fake_clone):
        install_skill(
            "https://github.com/example/skill-repo.git",
            target_dir=tmp_path / "skills",
        )
        with pytest.raises(ValueError, match="already installed"):
            install_skill(
                "https://github.com/example/skill-repo.git",
                target_dir=tmp_path / "skills",
            )


def test_install_skill_clone_failure_raises_value_error(tmp_path: Path) -> None:
    """A failed git clone surfaces as a ValueError, not a subprocess error."""

    def boom(url: str, dest: Path) -> None:
        raise subprocess.CalledProcessError(1, ["git", "clone"], stderr=b"nope")

    with (
        patch("mvge.runes.skill_installer._clone_git_repo", side_effect=boom),
        pytest.raises(ValueError, match="Failed to clone"),
    ):
        install_skill(
            "https://github.com/example/skill-repo.git",
            target_dir=tmp_path / "skills",
        )


def test_install_skill_rejects_file_source(tmp_path: Path) -> None:
    """A source path that is a file, not a directory, is rejected."""
    src_file = tmp_path / "SKILL.md"
    src_file.write_text("# x\n", encoding="utf-8")

    with pytest.raises(ValueError, match="not a directory"):
        install_skill(str(src_file), target_dir=tmp_path / "skills")


def test_install_skill_blank_name_falls_back_to_source_dir(tmp_path: Path) -> None:
    """A blank name override falls back to the source directory name."""
    source = _create_mock_skill_dir(tmp_path / "my-skill", "my-skill")

    dest = install_skill(str(source), name="", target_dir=tmp_path / "skills")

    assert dest == tmp_path / "skills" / "my-skill"


def test_name_from_git_url_colon_no_slash() -> None:
    from mvge.runes.skill_installer import _name_from_git_url

    assert _name_from_git_url("git@example.com:single-skill.git") == "single-skill"


def test_clone_git_repo(tmp_path: Path) -> None:
    from mvge.runes.skill_installer import _clone_git_repo

    with patch("subprocess.run") as mock_run:
        _clone_git_repo("https://example.com/repo.git", tmp_path)
        mock_run.assert_called_once()
