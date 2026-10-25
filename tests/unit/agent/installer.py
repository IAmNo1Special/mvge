from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mvge.agent.installer import (
    DEFAULT_MARKETPLACE_URL,
    fetch_marketplace_mvges,
    install_mvge,
    list_installed_mvges,
    uninstall_mvge,
)


def _create_mock_mvge_dir(
    path: Path, name: str, python_deps: list[str] | None = None
) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    manifest_data = {
        "name": name,
        "version": "1.0.0",
        "description": f"Test mvge {name}",
        "entry_point": "mvge.py",
        "spells": ["read", "write"],
    }
    if python_deps is not None:
        manifest_data["python_deps"] = python_deps
    (path / "manifest.json").write_text(json.dumps(manifest_data), encoding="utf-8")
    (path / "mvge.py").write_text("# entry point\n", encoding="utf-8")
    return path


def test_install_mvge_empty_source(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Mvge source cannot be empty"):
        install_mvge("", target_dir=tmp_path)


def test_install_mvge_local_path(tmp_path: Path) -> None:
    source_dir = tmp_path / "my_local_mvge"
    _create_mock_mvge_dir(source_dir, "my_local_mvge")

    target_dir = tmp_path / "agents"
    dest = install_mvge(str(source_dir), target_dir=target_dir)

    assert dest == target_dir / "my_local_mvge"
    assert (dest / "manifest.json").is_file()
    assert (dest / "mvge.py").is_file()


def test_install_mvge_git_url(tmp_path: Path) -> None:
    target_dir = tmp_path / "agents"
    git_url = "https://github.com/org/sample-mvge.git"

    def fake_git_clone(cmd: list[str], **kwargs: object) -> MagicMock:
        if cmd[:2] == ["git", "clone"]:
            dest_dir = Path(cmd[-1])
            _create_mock_mvge_dir(dest_dir, "sample-mvge")
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=fake_git_clone) as mock_run:
        dest = install_mvge(git_url, target_dir=target_dir)

    assert dest == target_dir / "sample-mvge"
    assert (dest / "manifest.json").is_file()
    mock_run.assert_called_once()
    assert mock_run.call_args[0][0][:2] == ["git", "clone"]


def test_install_mvge_marketplace_success(tmp_path: Path) -> None:
    target_dir = tmp_path / "agents"
    marketplace_payload = {
        "mvges": {
            "coding_mvge": {
                "name": "coding_mvge",
                "git": "https://github.com/IAmNo1Special/coding-mvge.git",
                "description": "Coding agent",
            }
        }
    }

    mock_resp = MagicMock()
    mock_resp.json.return_value = marketplace_payload
    mock_resp.raise_for_status = MagicMock()

    def fake_git_clone(cmd: list[str], **kwargs: object) -> MagicMock:
        if cmd[:2] == ["git", "clone"]:
            dest_dir = Path(cmd[-1])
            _create_mock_mvge_dir(dest_dir, "coding_mvge")
        return MagicMock(returncode=0)

    with (
        patch("httpx.get", return_value=mock_resp) as mock_get,
        patch("subprocess.run", side_effect=fake_git_clone),
    ):
        dest = install_mvge("coding_mvge", target_dir=target_dir)

    assert dest == target_dir / "coding_mvge"
    assert (dest / "manifest.json").is_file()
    mock_get.assert_called_once_with(DEFAULT_MARKETPLACE_URL, timeout=15.0)


def test_install_mvge_marketplace_with_subpath_success(tmp_path: Path) -> None:
    target_dir = tmp_path / "agents"
    marketplace_payload = {
        "mvges": {
            "coding_mvge": {
                "name": "coding_mvge",
                "git": "https://github.com/IAmNo1Special/mvge-marketplace.git",
                "path": "mvges/coding_mvge",
            }
        }
    }

    mock_resp = MagicMock()
    mock_resp.json.return_value = marketplace_payload
    mock_resp.raise_for_status = MagicMock()

    def fake_git_clone(cmd: list[str], **kwargs: object) -> MagicMock:
        if cmd[:2] == ["git", "clone"]:
            dest_dir = Path(cmd[-1])
            mvge_dir = dest_dir / "mvges" / "coding_mvge"
            _create_mock_mvge_dir(mvge_dir, "coding_mvge")
        return MagicMock(returncode=0)

    with (
        patch("httpx.get", return_value=mock_resp) as mock_get,
        patch("subprocess.run", side_effect=fake_git_clone),
    ):
        dest = install_mvge("coding_mvge", target_dir=target_dir)

    assert dest == target_dir / "coding_mvge"
    assert (dest / "manifest.json").is_file()
    mock_get.assert_called_once_with(DEFAULT_MARKETPLACE_URL, timeout=15.0)


def test_install_mvge_marketplace_not_found(tmp_path: Path) -> None:
    target_dir = tmp_path / "agents"
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"mvges": {}}
    mock_resp.raise_for_status = MagicMock()

    with (
        patch("httpx.get", return_value=mock_resp),
        pytest.raises(ValueError, match="unknown-mvge"),
    ):
        install_mvge("unknown-mvge", target_dir=target_dir)


def test_install_mvge_marketplace_fetch_error(tmp_path: Path) -> None:
    target_dir = tmp_path / "agents"
    with (
        patch("httpx.get", side_effect=RuntimeError("connection dropped")),
        pytest.raises(ValueError, match="Failed to fetch marketplace index"),
    ):
        install_mvge("coding_mvge", target_dir=target_dir)


def test_fetch_marketplace_mvges_success() -> None:
    marketplace_payload = {
        "mvges": {
            "coding_mvge": {
                "name": "coding_mvge",
                "version": "0.2.6",
                "description": "Coding agent",
            }
        }
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = marketplace_payload
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.get", return_value=mock_resp) as mock_get:
        mvges = fetch_marketplace_mvges()

    assert mvges == marketplace_payload["mvges"]
    mock_get.assert_called_once_with(DEFAULT_MARKETPLACE_URL, timeout=15.0)


def test_fetch_marketplace_mvges_network_error() -> None:
    with patch("httpx.get", side_effect=Exception("network down")):
        mvges = fetch_marketplace_mvges()
    assert mvges == {}


def test_list_installed_mvges(tmp_path: Path) -> None:
    target_dir = tmp_path / "agents"
    _create_mock_mvge_dir(target_dir / "mvge_a", "mvge_a")
    _create_mock_mvge_dir(target_dir / "mvge_b", "mvge_b")
    (target_dir / "not_a_dir.txt").write_text("hello", encoding="utf-8")

    installed = list_installed_mvges(target_dir=target_dir)
    assert len(installed) == 2
    assert installed[0]["name"] == "mvge_a"
    assert installed[1]["name"] == "mvge_b"
    assert installed[0]["spells"] == ["read", "write"]


def test_uninstall_mvge(tmp_path: Path) -> None:
    target_dir = tmp_path / "agents"
    _create_mock_mvge_dir(target_dir / "mvge_a", "mvge_a")

    assert (target_dir / "mvge_a").is_dir()
    uninstalled = uninstall_mvge("mvge_a", target_dir=target_dir)
    assert uninstalled is True
    assert not (target_dir / "mvge_a").exists()

    uninstalled_again = uninstall_mvge("mvge_a", target_dir=target_dir)
    assert uninstalled_again is False


def test_list_installed_mvges_nonexistent_dir(tmp_path: Path) -> None:
    assert list_installed_mvges(target_dir=tmp_path / "does_not_exist") == []


def test_list_installed_mvges_corrupt_manifest_and_spells_dir(tmp_path: Path) -> None:
    target_dir = tmp_path / "agents"
    mvge_dir = target_dir / "custom_mvge"
    mvge_dir.mkdir(parents=True)
    (mvge_dir / "manifest.json").write_text("{not valid json", encoding="utf-8")
    spells_dir = mvge_dir / "spells"
    spells_dir.mkdir()
    (spells_dir / "bash.py").write_text("# bash", encoding="utf-8")
    (spells_dir / "read.py").write_text("# read", encoding="utf-8")
    (spells_dir / "_ignored.py").write_text("# ignored", encoding="utf-8")

    installed = list_installed_mvges(target_dir=target_dir)
    assert len(installed) == 1
    assert installed[0]["name"] == "custom_mvge"
    assert installed[0]["spells"] == ["bash", "read"]


def test_uninstall_mvge_file(tmp_path: Path) -> None:
    target_dir = tmp_path / "agents"
    target_dir.mkdir(parents=True)
    file_mvge = target_dir / "file_mvge"
    file_mvge.write_text("not a dir", encoding="utf-8")

    assert uninstall_mvge("file_mvge", target_dir=target_dir) is True
    assert not file_mvge.exists()


def test_install_mvge_overwrites_existing_dest(tmp_path: Path) -> None:
    source_dir = tmp_path / "src_mvge"
    _create_mock_mvge_dir(source_dir, "src_mvge")
    target_dir = tmp_path / "agents"
    target_dir.mkdir(parents=True)
    existing_dest = target_dir / "src_mvge"
    existing_dest.mkdir()
    (existing_dest / "old.txt").write_text("old", encoding="utf-8")

    dest = install_mvge(str(source_dir), target_dir=target_dir)
    assert dest == existing_dest
    assert not (dest / "old.txt").exists()
    assert (dest / "manifest.json").is_file()


def test_install_mvge_dest_exists_as_file(tmp_path: Path) -> None:
    source_dir = tmp_path / "src_mvge2"
    _create_mock_mvge_dir(source_dir, "src_mvge2")
    target_dir = tmp_path / "agents"
    target_dir.mkdir(parents=True)
    existing_file = target_dir / "src_mvge2"
    existing_file.write_text("file", encoding="utf-8")

    dest = install_mvge(str(source_dir), target_dir=target_dir)
    assert dest.is_dir()
    assert (dest / "manifest.json").is_file()


def test_install_mvge_missing_manifest_raises(tmp_path: Path) -> None:
    source_dir = tmp_path / "no_manifest_mvge"
    source_dir.mkdir()
    (source_dir / "test.py").write_text("pass", encoding="utf-8")

    target_dir = tmp_path / "agents"
    with pytest.raises(ValueError, match="Invalid mvge: manifest.json missing"):
        install_mvge(str(source_dir), target_dir=target_dir)


def test_install_mvge_with_python_deps_and_pyproject(tmp_path: Path) -> None:
    source_dir = tmp_path / "deps_mvge"
    _create_mock_mvge_dir(source_dir, "deps_mvge", python_deps=["dep1", "dep2"])
    (source_dir / "pyproject.toml").write_text(
        "[project]\nname='deps_mvge'", encoding="utf-8"
    )

    target_dir = tmp_path / "agents"
    with patch("subprocess.run") as mock_run:
        dest = install_mvge(
            str(source_dir), target_dir=target_dir, confirm_python_deps=True
        )

    assert dest == target_dir / "deps_mvge"
    calls = [c[0][0] for c in mock_run.call_args_list]
    assert ["uv", "add", "dep1", "dep2"] in calls
    assert ["uv", "add", "--editable", str(dest)] in calls


def test_install_mvge_marketplace_alt_name_hyphen(tmp_path: Path) -> None:
    target_dir = tmp_path / "agents"
    marketplace_payload = {
        "mvges": {
            "coding_mvge": {
                "name": "coding_mvge",
                "git": "https://github.com/example/coding-mvge.git",
            }
        }
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = marketplace_payload
    mock_resp.raise_for_status = MagicMock()

    def fake_git_clone(cmd: list[str], **kwargs: object) -> MagicMock:
        if cmd[:2] == ["git", "clone"]:
            dest_dir = Path(cmd[-1])
            _create_mock_mvge_dir(dest_dir, "coding-mvge")
        return MagicMock(returncode=0)

    with (
        patch("httpx.get", return_value=mock_resp),
        patch("subprocess.run", side_effect=fake_git_clone),
    ):
        dest = install_mvge("coding-mvge", target_dir=target_dir)

    assert dest == target_dir / "coding-mvge"
    assert (dest / "manifest.json").is_file()


def test_fetch_marketplace_mvges_invalid_format() -> None:
    mock_resp = MagicMock()
    mock_resp.json.return_value = ["not", "a", "dict"]
    mock_resp.raise_for_status = MagicMock()
    with patch("httpx.get", return_value=mock_resp):
        assert fetch_marketplace_mvges() == {}

    mock_resp.json.return_value = {"mvges": "not a dict"}
    with patch("httpx.get", return_value=mock_resp):
        assert fetch_marketplace_mvges() == {}


def test_install_mvge_source_equals_dest(tmp_path: Path) -> None:
    target_dir = tmp_path / "agents"
    agent_dir = target_dir / "existing_agent"
    _create_mock_mvge_dir(agent_dir, "existing_agent")

    dest = install_mvge(str(agent_dir), target_dir=target_dir)
    assert dest.resolve() == agent_dir.resolve()


def test_install_mvge_git_url_no_dot_git_and_dest_exists(tmp_path: Path) -> None:
    target_dir = tmp_path / "agents"
    git_url = "https://github.com/org/no-dot-git"
    existing = target_dir / "no-dot-git"
    existing.mkdir(parents=True)
    (existing / "temp.txt").write_text("temp", encoding="utf-8")

    def fake_git_clone(cmd: list[str], **kwargs: object) -> MagicMock:
        if cmd[:2] == ["git", "clone"]:
            dest_dir = Path(cmd[-1])
            _create_mock_mvge_dir(dest_dir, "no-dot-git")
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=fake_git_clone):
        dest = install_mvge(git_url, target_dir=target_dir)

    assert dest == existing
    assert not (dest / "temp.txt").exists()
    assert (dest / "manifest.json").is_file()


def test_install_mvge_marketplace_dest_exists(tmp_path: Path) -> None:
    target_dir = tmp_path / "agents"
    target_dir.mkdir(parents=True)
    existing = target_dir / "coding_mvge"
    existing.mkdir()
    (existing / "old.txt").write_text("old", encoding="utf-8")

    marketplace_payload = {
        "mvges": {
            "coding_mvge": {
                "name": "coding_mvge",
                "git": "https://github.com/example/coding-mvge.git",
            }
        }
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = marketplace_payload
    mock_resp.raise_for_status = MagicMock()

    def fake_git_clone(cmd: list[str], **kwargs: object) -> MagicMock:
        if cmd[:2] == ["git", "clone"]:
            dest_dir = Path(cmd[-1])
            _create_mock_mvge_dir(dest_dir, "coding_mvge")
        return MagicMock(returncode=0)

    with (
        patch("httpx.get", return_value=mock_resp),
        patch("subprocess.run", side_effect=fake_git_clone),
    ):
        dest = install_mvge("coding_mvge", target_dir=target_dir)

    assert dest == existing
    assert not (dest / "old.txt").exists()


def test_install_mvge_pyproject_install_error_tolerated(tmp_path: Path) -> None:
    source_dir = tmp_path / "error_mvge"
    _create_mock_mvge_dir(source_dir, "error_mvge")
    (source_dir / "pyproject.toml").write_text(
        "[project]\nname='error_mvge'", encoding="utf-8"
    )

    target_dir = tmp_path / "agents"

    def fail_uv_add_install(cmd: list[str], **kwargs: object) -> MagicMock:
        if "add" in cmd and "--editable" in cmd:
            raise OSError("uv add error")
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=fail_uv_add_install):
        dest = install_mvge(str(source_dir), target_dir=target_dir)

    assert dest == target_dir / "error_mvge"


def test_fetch_marketplace_mvges_with_agents_key() -> None:
    marketplace_payload = {"agents": {"demo_agent": {"name": "demo_agent"}}}
    mock_resp = MagicMock()
    mock_resp.json.return_value = marketplace_payload
    mock_resp.raise_for_status = MagicMock()
    with patch("httpx.get", return_value=mock_resp):
        mvges = fetch_marketplace_mvges()
    assert mvges == marketplace_payload["agents"]


def test_install_mvge_marketplace_with_agents_fallback_and_existing_dest_file(
    tmp_path: Path,
) -> None:
    target_dir = tmp_path / "agents"
    target_dir.mkdir(parents=True)
    existing_dest = target_dir / "fallback_mvge"
    existing_dest.write_text("dummy", encoding="utf-8")

    marketplace_payload = {
        "agents": {
            "fallback_mvge": {
                "name": "fallback_mvge",
                "git": "https://github.com/example/fallback-mvge.git",
            }
        }
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = marketplace_payload
    mock_resp.raise_for_status = MagicMock()

    def fake_git_clone(cmd: list[str], **kwargs: object) -> MagicMock:
        if cmd[:2] == ["git", "clone"]:
            dest_dir = Path(cmd[-1])
            _create_mock_mvge_dir(dest_dir, "fallback_mvge")
        return MagicMock(returncode=0)

    with (
        patch("httpx.get", return_value=mock_resp),
        patch("subprocess.run", side_effect=fake_git_clone),
    ):
        dest = install_mvge("fallback_mvge", target_dir=target_dir)

    assert dest == existing_dest
    assert dest.is_dir()
    assert (dest / "manifest.json").is_file()


def test_install_mvge_git_url_with_existing_dest_file(tmp_path: Path) -> None:
    target_dir = tmp_path / "agents"
    target_dir.mkdir(parents=True)
    existing_dest = target_dir / "git_agent"
    existing_dest.write_text("dummy", encoding="utf-8")

    def fake_git_clone(cmd: list[str], **kwargs: object) -> MagicMock:
        if cmd[:2] == ["git", "clone"]:
            dest_dir = Path(cmd[-1])
            _create_mock_mvge_dir(dest_dir, "git_agent")
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=fake_git_clone):
        dest = install_mvge(
            "https://github.com/example/git_agent.git", target_dir=target_dir
        )

    assert dest == existing_dest
    assert dest.is_dir()
    assert (dest / "manifest.json").is_file()


def test_install_mvge_git_failure_fallback_to_local_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target_dir = tmp_path / "agents"
    local_marketplace = tmp_path / "local-market"
    local_mvge = local_marketplace / "mvges" / "coding_mvge"
    _create_mock_mvge_dir(local_mvge, "coding_mvge")

    monkeypatch.setenv("MVGE_MARKETPLACE_DIR", str(local_marketplace))

    marketplace_payload = {
        "mvges": {
            "coding_mvge": {
                "name": "coding_mvge",
                "git": "https://github.com/example/fail.git",
                "path": "mvges/coding_mvge",
            }
        }
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = marketplace_payload
    mock_resp.raise_for_status = MagicMock()

    def fail_git(cmd: list[str], **kwargs: object) -> MagicMock:
        if cmd[:2] == ["git", "clone"]:
            raise subprocess.CalledProcessError(1, cmd)
        return MagicMock(returncode=0)

    with (
        patch("httpx.get", return_value=mock_resp),
        patch("subprocess.run", side_effect=fail_git),
    ):
        dest = install_mvge("coding_mvge", target_dir=target_dir)

    assert dest == target_dir / "coding_mvge"
    assert (dest / "manifest.json").is_file()


def test_install_mvge_git_failure_no_local_dir_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target_dir = tmp_path / "agents"
    monkeypatch.delenv("MVGE_MARKETPLACE_DIR", raising=False)

    marketplace_payload = {
        "mvges": {
            "coding_mvge": {
                "name": "coding_mvge",
                "git": "https://github.com/example/fail.git",
                "path": "mvges/coding_mvge",
            }
        }
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = marketplace_payload
    mock_resp.raise_for_status = MagicMock()

    def fail_git(cmd: list[str], **kwargs: object) -> MagicMock:
        if cmd[:2] == ["git", "clone"]:
            raise subprocess.CalledProcessError(1, cmd)
        return MagicMock(returncode=0)

    with (
        patch("httpx.get", return_value=mock_resp),
        patch("subprocess.run", side_effect=fail_git),
        patch("pathlib.Path.home", return_value=tmp_path / "fake_home"),
        pytest.raises(subprocess.CalledProcessError),
    ):
        install_mvge("coding_mvge", target_dir=target_dir)


def test_install_mvge_uv_add_deps_error_tolerated(tmp_path: Path) -> None:
    source_dir = tmp_path / "deps_err_mvge"
    _create_mock_mvge_dir(source_dir, "deps_err_mvge", python_deps=["pkg_a", "pkg_b"])

    target_dir = tmp_path / "agents"

    def fail_uv_add(cmd: list[str], **kwargs: object) -> MagicMock:
        if cmd[:2] == ["uv", "add"]:
            raise subprocess.CalledProcessError(1, cmd)
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=fail_uv_add):
        dest = install_mvge(
            str(source_dir), target_dir=target_dir, confirm_python_deps=True
        )

    assert dest == target_dir / "deps_err_mvge"
    assert (dest / "manifest.json").is_file()


def test_uninstall_mvge_rejects_traversal_name(tmp_path: Path) -> None:
    target_dir = tmp_path / "agents"
    target_dir.mkdir()
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    (sibling / "keep.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid mvge name"):
        uninstall_mvge("..", target_dir=target_dir)

    assert (sibling / "keep.txt").is_file()


def test_install_mvge_git_url_rejects_traversal_name(tmp_path: Path) -> None:
    target_dir = tmp_path / "agents"
    with (
        patch("subprocess.run") as mock_run,
        pytest.raises(ValueError, match="Invalid mvge name"),
    ):
        install_mvge("https://github.com/org/..", target_dir=target_dir)
    mock_run.assert_not_called()


def test_install_mvge_marketplace_rejects_traversal_name(tmp_path: Path) -> None:
    target_dir = tmp_path / "agents"
    marketplace_payload = {
        "mvges": {"../evil": {"git": "https://example.com/evil.git"}}
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = marketplace_payload
    mock_resp.raise_for_status = MagicMock()

    with (
        patch("httpx.get", return_value=mock_resp),
        patch("subprocess.run") as mock_run,
        pytest.raises(ValueError, match="Invalid mvge name"),
    ):
        install_mvge("../evil", target_dir=target_dir)

    mock_run.assert_not_called()
    assert not (tmp_path / "evil").exists()


def test_install_mvge_git_clone_uses_option_separator(tmp_path: Path) -> None:
    target_dir = tmp_path / "agents"
    git_url = "https://github.com/org/sample-mvge.git"

    def fake_git_clone(cmd: list[str], **kwargs: object) -> MagicMock:
        dest_dir = Path(cmd[-1])
        _create_mock_mvge_dir(dest_dir, "sample-mvge")
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=fake_git_clone) as mock_run:
        install_mvge(git_url, target_dir=target_dir)

    clone_cmd = mock_run.call_args[0][0]
    assert clone_cmd[:3] == ["git", "clone", "--"]
    assert clone_cmd[3] == git_url


def test_install_mvge_python_deps_skipped_by_default_non_interactive(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "deps_mvge"
    _create_mock_mvge_dir(source_dir, "deps_mvge", python_deps=["dep1"])
    target_dir = tmp_path / "agents"

    with patch("subprocess.run") as mock_run:
        dest = install_mvge(str(source_dir), target_dir=target_dir)

    calls = [c[0][0] for c in mock_run.call_args_list]
    assert ["uv", "add", "dep1"] not in calls
    assert (dest / "manifest.json").is_file()


def test_install_mvge_local_path_uses_manifest_name(tmp_path: Path) -> None:
    """BUG-4: the install directory must come from the manifest ``name``,
    not the source directory basename, so install/list/uninstall agree."""
    source_dir = tmp_path / "weird_dir_name"
    _create_mock_mvge_dir(source_dir, "canonical_mvge_name")
    target_dir = tmp_path / "agents"

    dest = install_mvge(str(source_dir), target_dir=target_dir)

    assert dest == target_dir / "canonical_mvge_name"
    assert (dest / "manifest.json").is_file()
    assert not (target_dir / "weird_dir_name").exists()
    # Round trip: the name `list` shows is the name `uninstall` removes.
    assert uninstall_mvge("canonical_mvge_name", target_dir=target_dir) is True
    assert not (target_dir / "canonical_mvge_name").exists()


def test_install_mvge_local_path_rejects_manifest_name_traversal(
    tmp_path: Path,
) -> None:
    """A hostile manifest ``name`` must never escape the target directory."""
    source_dir = tmp_path / "evil_src"
    _create_mock_mvge_dir(source_dir, "../evil")
    target_dir = tmp_path / "agents"

    with pytest.raises(ValueError, match="Invalid mvge name"):
        install_mvge(str(source_dir), target_dir=target_dir)
    assert list(target_dir.iterdir()) == []
    assert not (tmp_path / "evil").exists()


def test_install_mvge_local_path_rejects_missing_manifest_name(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "noname_src"
    source_dir.mkdir()
    (source_dir / "manifest.json").write_text(
        json.dumps({"version": "1.0.0"}), encoding="utf-8"
    )
    target_dir = tmp_path / "agents"

    with pytest.raises(ValueError, match="manifest.json missing 'name'"):
        install_mvge(str(source_dir), target_dir=target_dir)


def test_install_mvge_git_url_uses_manifest_name(tmp_path: Path) -> None:
    """BUG-4 (git branch): the install directory comes from the cloned
    manifest ``name``, not the repo URL basename."""
    target_dir = tmp_path / "agents"
    git_url = "https://github.com/org/weird-repo-name.git"

    def fake_git_clone(cmd: list[str], **kwargs: object) -> MagicMock:
        if cmd[:2] == ["git", "clone"]:
            _create_mock_mvge_dir(Path(cmd[-1]), "canonical_git_mvge")
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=fake_git_clone):
        dest = install_mvge(git_url, target_dir=target_dir)

    assert dest == target_dir / "canonical_git_mvge"
    assert (dest / "manifest.json").is_file()
    assert not (target_dir / "weird-repo-name").exists()
