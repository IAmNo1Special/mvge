from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from mvge.runes.installer import (
    DEFAULT_MARKETPLACE_URL,
    fetch_marketplace_runes,
    install_rune,
    list_installed_runes,
    set_rune_enabled,
    uninstall_rune,
)


def _create_mock_rune_dir(
    path: Path, name: str, python_deps: list[str] | None = None
) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    manifest_data = {
        "name": name,
        "version": "1.0.0",
        "description": f"Test rune {name}",
        "entry_point": "main.py",
    }
    if python_deps is not None:
        manifest_data["python_deps"] = python_deps
    (path / "manifest.json").write_text(json.dumps(manifest_data), encoding="utf-8")
    (path / "main.py").write_text("# entry point\n", encoding="utf-8")
    return path


def test_list_installed_runes_ignores_hidden_dirs(tmp_path: Path) -> None:
    """The shared deps ``.venv`` sits at the extensions dir; listing
    must not present it as an installed rune."""
    _create_mock_rune_dir(tmp_path / "real_rune", "real_rune")
    venv_dir = tmp_path / ".venv"
    venv_dir.mkdir()
    (venv_dir / "pyproject.toml").write_text(
        '[project]\nname = "mvge-extensions"\n', encoding="utf-8"
    )

    installed = list_installed_runes(target_dir=tmp_path)
    assert [r["name"] for r in installed] == ["real_rune"]


def test_install_rune_empty_source(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Rune source cannot be empty"):
        install_rune("", target_dir=tmp_path)


def test_install_rune_local_path(tmp_path: Path) -> None:
    source_dir = tmp_path / "my_local_rune"
    _create_mock_rune_dir(source_dir, "my_local_rune")

    target_dir = tmp_path / "extensions"
    dest = install_rune(str(source_dir), target_dir=target_dir)

    assert dest == target_dir / "my_local_rune"
    assert (dest / "manifest.json").is_file()
    assert (dest / "main.py").is_file()


def test_install_rune_git_url(tmp_path: Path) -> None:
    target_dir = tmp_path / "extensions"
    git_url = "https://github.com/org/sample-rune.git"

    def fake_git_clone(cmd: list[str], **kwargs: object) -> MagicMock:
        if cmd[:2] == ["git", "clone"]:
            dest_dir = Path(cmd[-1])
            _create_mock_rune_dir(dest_dir, "sample-rune")
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=fake_git_clone) as mock_run:
        dest = install_rune(git_url, target_dir=target_dir)

    assert dest == target_dir / "sample-rune"
    assert (dest / "manifest.json").is_file()
    mock_run.assert_called_once()
    assert mock_run.call_args[0][0][:2] == ["git", "clone"]


def test_install_rune_marketplace_success(tmp_path: Path) -> None:
    target_dir = tmp_path / "extensions"
    marketplace_payload = {
        "runes": {
            "openrouter-realm": {
                "name": "openrouter-realm",
                "git": "https://github.com/IAmNo1Special/openrouter-realm.git",
                "description": "OpenRouter provider realm",
            }
        }
    }

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = marketplace_payload
    mock_resp.raise_for_status = MagicMock()

    def fake_git_clone(cmd: list[str], **kwargs: object) -> MagicMock:
        if cmd[:2] == ["git", "clone"]:
            dest_dir = Path(cmd[-1])
            _create_mock_rune_dir(dest_dir, "openrouter-realm")
        return MagicMock(returncode=0)

    with (
        patch("httpx.get", return_value=mock_resp) as mock_get,
        patch("subprocess.run", side_effect=fake_git_clone) as mock_subproc,
    ):
        dest = install_rune("openrouter-realm", target_dir=target_dir)

    assert dest == target_dir / "openrouter-realm"
    assert (dest / "manifest.json").is_file()
    mock_get.assert_called_once_with(DEFAULT_MARKETPLACE_URL, timeout=15.0)
    mock_subproc.assert_called_once()


def test_install_rune_marketplace_with_subpath_success(tmp_path: Path) -> None:
    target_dir = tmp_path / "extensions"
    marketplace_payload = {
        "runes": {
            "openrouter-realm": {
                "name": "openrouter-realm",
                "git": "https://github.com/IAmNo1Special/mvge-marketplace.git",
                "path": "runes/openrouter-realm",
                "description": "OpenRouter provider realm",
            }
        }
    }

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = marketplace_payload
    mock_resp.raise_for_status = MagicMock()

    def fake_git_clone(cmd: list[str], **kwargs: object) -> MagicMock:
        if cmd[:2] == ["git", "clone"]:
            clone_target = Path(cmd[-1])
            _create_mock_rune_dir(
                clone_target / "runes" / "openrouter-realm", "openrouter-realm"
            )
        return MagicMock(returncode=0)

    with (
        patch("httpx.get", return_value=mock_resp) as mock_get,
        patch("subprocess.run", side_effect=fake_git_clone) as mock_subproc,
    ):
        dest = install_rune("openrouter-realm", target_dir=target_dir)

    assert dest == target_dir / "openrouter-realm"
    assert (dest / "manifest.json").is_file()
    assert (dest / "main.py").is_file()
    mock_get.assert_called_once_with(DEFAULT_MARKETPLACE_URL, timeout=15.0)
    mock_subproc.assert_called_once()
    called_cmd = mock_subproc.call_args[0][0]
    assert called_cmd[:4] == ["git", "clone", "--depth", "1"]


def test_install_rune_marketplace_not_found(tmp_path: Path) -> None:
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"runes": {}}
    mock_resp.raise_for_status = MagicMock()

    with (
        patch("httpx.get", return_value=mock_resp),
        pytest.raises(ValueError, match="unknown-rune"),
    ):
        install_rune("unknown-rune", target_dir=tmp_path)


def test_install_rune_marketplace_fetch_error(tmp_path: Path) -> None:
    with (
        patch("httpx.get", side_effect=httpx.RequestError("Connection failed")),
        pytest.raises(ValueError, match="Failed to fetch marketplace index"),
    ):
        install_rune("openrouter-realm", target_dir=tmp_path)


def test_install_rune_missing_manifest_raises_error(tmp_path: Path) -> None:
    source_dir = tmp_path / "invalid_rune"
    source_dir.mkdir()
    (source_dir / "some_code.py").write_text("# code\n", encoding="utf-8")

    target_dir = tmp_path / "extensions"
    with pytest.raises(ValueError, match="Invalid rune: manifest.json missing"):
        install_rune(str(source_dir), target_dir=target_dir)


def test_install_rune_declares_python_deps_in_the_extensions_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Declared deps are added to the extensions project, not the caller's.

    Regression: the installer shelled out to a bare ``uv add``, which
    resolves against the current working directory, so installing a rune
    rewrote the pyproject.toml and uv.lock of whatever project the user
    was standing in.
    """
    source_dir = tmp_path / "rune_with_deps"
    _create_mock_rune_dir(
        source_dir, "rune_with_deps", python_deps=["fastapi>=0.100.0", "pydantic>=2.0"]
    )

    global_dir = tmp_path / "global_agents"
    monkeypatch.setenv("MVGE_GLOBAL_DIR", str(global_dir))

    target_dir = tmp_path / "extensions"
    with patch("subprocess.run") as mock_subproc:
        dest = install_rune(
            str(source_dir),
            target_dir=target_dir,
            confirm_python_deps=True,
        )

    assert dest == target_dir / "rune_with_deps"
    mock_subproc.assert_called_once_with(
        [
            "uv",
            "add",
            "--project",
            str(global_dir / "extensions"),
            "fastapi>=0.100.0",
            "pydantic>=2.0",
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def test_install_rune_installs_the_rune_without_touching_deps_by_default(
    tmp_path: Path,
) -> None:
    """With no confirmation, the rune installs and the deps are skipped.

    The manifest is authored by whoever published the rune, so its
    python_deps is an instruction to fetch packages from PyPI. Nothing
    runs without a yes.
    """
    source_dir = tmp_path / "rune_with_deps"
    _create_mock_rune_dir(
        source_dir, "rune_with_deps", python_deps=["fastapi>=0.100.0"]
    )

    target_dir = tmp_path / "extensions"
    with patch("subprocess.run") as mock_subproc:
        dest = install_rune(str(source_dir), target_dir=target_dir)

    assert (dest / "manifest.json").is_file()
    mock_subproc.assert_not_called()


def test_fetch_marketplace_runes_success() -> None:
    marketplace_payload = {
        "runes": {
            "test-rune": {
                "name": "test-rune",
                "git": "https://github.com/org/test-rune.git",
                "description": "A test rune",
            }
        }
    }
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = marketplace_payload
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.get", return_value=mock_resp) as mock_get:
        runes = fetch_marketplace_runes()

    assert runes == marketplace_payload["runes"]
    mock_get.assert_called_once_with(DEFAULT_MARKETPLACE_URL, timeout=15.0)


def test_fetch_marketplace_runes_custom_url_and_timeout() -> None:
    custom_url = "https://example.com/custom-market.json"
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"runes": {"custom": {"name": "custom"}}}
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.get", return_value=mock_resp) as mock_get:
        runes = fetch_marketplace_runes(marketplace_url=custom_url, timeout=5.0)

    assert runes == {"custom": {"name": "custom"}}
    mock_get.assert_called_once_with(custom_url, timeout=5.0)


def test_fetch_marketplace_runes_network_error() -> None:
    with patch("httpx.get", side_effect=httpx.ConnectError("Connection failed")):
        runes = fetch_marketplace_runes()

    assert runes == {}


def test_fetch_marketplace_runes_http_status_error() -> None:
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "404 Not Found", request=MagicMock(), response=MagicMock()
    )

    with patch("httpx.get", return_value=mock_resp):
        runes = fetch_marketplace_runes()

    assert runes == {}


def test_fetch_marketplace_runes_non_dict_payload() -> None:
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = ["not", "a", "dict"]

    with patch("httpx.get", return_value=mock_resp):
        runes = fetch_marketplace_runes()

    assert runes == {}


def test_list_installed_runes_nonexistent_directory(tmp_path: Path) -> None:
    missing_dir = tmp_path / "does_not_exist"
    assert list_installed_runes(missing_dir) == []


def test_list_installed_runes_success(tmp_path: Path) -> None:
    # Subdir with full manifest
    beta_dir = tmp_path / "beta-rune"
    beta_dir.mkdir()
    beta_manifest = {
        "name": "beta-rune",
        "version": "2.0.0",
        "description": "Beta extension rune",
        "runtime": "python",
        "enabled": False,
        "hooks": ["pre_turn"],
        "python_deps": ["pydantic>=2.0"],
        "type": "planner",
    }
    (beta_dir / "manifest.json").write_text(json.dumps(beta_manifest), encoding="utf-8")

    # Subdir with empty manifest (testing fallbacks)
    alpha_dir = tmp_path / "alpha-rune"
    alpha_dir.mkdir()
    (alpha_dir / "manifest.json").write_text("{}", encoding="utf-8")

    # Subdir without manifest.json (should be ignored)
    ignored_dir = tmp_path / "ignored-dir"
    ignored_dir.mkdir()

    # Regular file (should be ignored)
    (tmp_path / "not-a-dir.txt").write_text("file", encoding="utf-8")

    installed = list_installed_runes(tmp_path)

    assert len(installed) == 2
    # Sorted by name: alpha-rune first, then beta-rune
    assert installed[0] == {
        "name": "alpha-rune",
        "version": "unknown",
        "description": "",
        "runtime": "python",
        "enabled": True,
        "path": str(alpha_dir),
        "hooks": [],
        "python_deps": [],
        "commands": [],
        "type": "",
        "types": [],
        "created_at": "",
        "updated_at": "",
        "ui": {},
    }
    assert installed[1] == {
        "name": "beta-rune",
        "version": "2.0.0",
        "description": "Beta extension rune",
        "runtime": "python",
        "enabled": False,
        "path": str(beta_dir),
        "hooks": ["pre_turn"],
        "python_deps": ["pydantic>=2.0"],
        "commands": [],
        "type": "planner",
        "types": ["planner"],
        "created_at": "",
        "updated_at": "",
        "ui": {},
    }


def test_list_installed_runes_corrupt_manifest(tmp_path: Path) -> None:
    corrupt_dir = tmp_path / "corrupt-rune"
    corrupt_dir.mkdir()
    (corrupt_dir / "manifest.json").write_text("NOT_JSON{", encoding="utf-8")

    installed = list_installed_runes(tmp_path)
    assert installed == []


def test_uninstall_rune_directory(tmp_path: Path) -> None:
    target_rune = tmp_path / "my-rune"
    target_rune.mkdir()
    (target_rune / "manifest.json").write_text("{}", encoding="utf-8")

    assert uninstall_rune("my-rune", target_dir=tmp_path) is True
    assert not target_rune.exists()


def test_uninstall_rune_file(tmp_path: Path) -> None:
    target_file = tmp_path / "single-file-rune"
    target_file.write_text("# single file", encoding="utf-8")

    assert uninstall_rune("single-file-rune", target_dir=tmp_path) is True
    assert not target_file.exists()


def test_uninstall_rune_nonexistent(tmp_path: Path) -> None:
    assert uninstall_rune("nonexistent-rune", target_dir=tmp_path) is False


def test_fetch_marketplace_runes_non_dict_runes_field() -> None:
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"runes": "not_a_dict"}
    mock_resp.raise_for_status.return_value = None

    with patch("httpx.get", return_value=mock_resp):
        res = fetch_marketplace_runes()
        assert res == {}


def test_list_installed_runes_non_dict_manifest(tmp_path: Path) -> None:
    non_dict_dir = tmp_path / "array-rune"
    non_dict_dir.mkdir()
    (non_dict_dir / "manifest.json").write_text('["not_a_dict"]', encoding="utf-8")

    installed = list_installed_runes(tmp_path)
    assert installed == []


def test_install_rune_local_path_overwrite(tmp_path: Path) -> None:
    source_dir = tmp_path / "src_rune"
    _create_mock_rune_dir(source_dir, "src_rune")

    target_dir = tmp_path / "extensions"
    existing_dest = target_dir / "src_rune"
    existing_dest.mkdir(parents=True)
    (existing_dest / "old.txt").write_text("old", encoding="utf-8")

    dest = install_rune(str(source_dir), target_dir=target_dir)
    assert dest == existing_dest
    assert not (dest / "old.txt").exists()
    assert (dest / "manifest.json").is_file()

    # Test file overwrite branch
    import shutil

    if dest.is_dir():
        shutil.rmtree(dest)
    else:
        dest.unlink(missing_ok=True)
    dest.write_text("file", encoding="utf-8")
    dest2 = install_rune(str(source_dir), target_dir=target_dir)
    assert dest2.is_dir()


def test_install_rune_git_url_overwrite(tmp_path: Path) -> None:
    target_dir = tmp_path / "extensions"
    existing_dest = target_dir / "git-rune"
    existing_dest.mkdir(parents=True)
    (existing_dest / "old.txt").write_text("old", encoding="utf-8")

    def fake_git_clone(cmd: list[str], **kwargs: object) -> MagicMock:
        # Clone targets the staging dir (last arg), not the final dest.
        _create_mock_rune_dir(Path(cmd[-1]), "git-rune")
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=fake_git_clone):
        dest = install_rune(
            "https://github.com/org/git-rune.git", target_dir=target_dir
        )
        assert dest == existing_dest

    # Test file overwrite branch
    import shutil

    shutil.rmtree(existing_dest)
    existing_dest.write_text("file", encoding="utf-8")
    with patch("subprocess.run", side_effect=fake_git_clone):
        dest = install_rune(
            "https://github.com/org/git-rune.git", target_dir=target_dir
        )
        assert dest.is_dir()


def test_install_rune_marketplace_overwrite(tmp_path: Path) -> None:
    target_dir = tmp_path / "extensions"
    existing_dest = target_dir / "market-rune"
    existing_dest.mkdir(parents=True)
    (existing_dest / "old.txt").write_text("old", encoding="utf-8")

    marketplace_payload = {
        "runes": {
            "market-rune": {
                "name": "market-rune",
                "git": "https://github.com/org/market-rune.git",
            }
        }
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = marketplace_payload
    mock_resp.raise_for_status.return_value = None

    def fake_git_clone(cmd: list[str], **kwargs: object) -> MagicMock:
        # Clone targets the staging dir (last arg), not the final dest.
        _create_mock_rune_dir(Path(cmd[-1]), "market-rune")
        return MagicMock(returncode=0)

    with (
        patch("httpx.get", return_value=mock_resp),
        patch("subprocess.run", side_effect=fake_git_clone),
    ):
        dest = install_rune("market-rune", target_dir=target_dir)
        assert dest == existing_dest
        assert not (dest / "old.txt").exists()


def test_install_rune_survives_a_dependency_install_failure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed dependency install still leaves a usable rune, and is reported.

    The rune itself is already on disk, so the install has succeeded as
    far as the rune is concerned; the dependency failure is a warning,
    not a rollback. It used to be discarded entirely, leaving a rune that
    failed at load with a bare ImportError pointing nowhere near the cause.
    """
    source_dir = tmp_path / "dep_rune"
    _create_mock_rune_dir(source_dir, "dep_rune", python_deps=["failing-dep"])

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        if cmd[:2] == ["uv", "add"]:
            raise subprocess.CalledProcessError(1, cmd, stderr="resolution failed")
        return MagicMock(returncode=0)

    with (
        patch("subprocess.run", side_effect=fake_run),
        caplog.at_level("WARNING"),
    ):
        dest = install_rune(
            str(source_dir),
            target_dir=tmp_path / "extensions",
            confirm_python_deps=True,
        )

    assert (dest / "manifest.json").is_file()
    assert "failing-dep" in caplog.text


def test_uninstall_rune_rejects_traversal_name(tmp_path: Path) -> None:
    target_dir = tmp_path / "extensions"
    target_dir.mkdir()
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    (sibling / "keep.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid rune name"):
        uninstall_rune("..", target_dir=target_dir)

    assert (sibling / "keep.txt").is_file()


def test_install_rune_git_url_rejects_traversal_name(tmp_path: Path) -> None:
    target_dir = tmp_path / "extensions"
    with (
        patch("subprocess.run") as mock_run,
        pytest.raises(ValueError, match="Invalid rune name"),
    ):
        install_rune("https://github.com/org/..", target_dir=target_dir)
    mock_run.assert_not_called()


def test_install_rune_local_path_uses_manifest_name(tmp_path: Path) -> None:
    """Same latent BUG-4 as mvges: the install directory must come from the
    manifest ``name``, not the source directory basename."""
    source_dir = tmp_path / "weird_rune_dir"
    _create_mock_rune_dir(source_dir, "canonical_rune_name")
    target_dir = tmp_path / "extensions"

    dest = install_rune(str(source_dir), target_dir=target_dir)

    assert dest == target_dir / "canonical_rune_name"
    assert (dest / "manifest.json").is_file()
    assert not (target_dir / "weird_rune_dir").exists()
    assert uninstall_rune("canonical_rune_name", target_dir=target_dir) is True


def test_install_rune_local_path_rejects_manifest_name_traversal(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "evil_rune_src"
    _create_mock_rune_dir(source_dir, "../evil")
    target_dir = tmp_path / "extensions"

    with pytest.raises(ValueError, match="Invalid rune name"):
        install_rune(str(source_dir), target_dir=target_dir)
    assert list(target_dir.iterdir()) == []


def test_set_rune_enabled_disables_rune(tmp_path: Path) -> None:
    target_rune = tmp_path / "my-rune"
    target_rune.mkdir()
    (target_rune / "manifest.json").write_text(
        json.dumps({"name": "my-rune", "version": "1.0.0", "enabled": True}),
        encoding="utf-8",
    )

    assert set_rune_enabled("my-rune", False, target_dir=tmp_path) is True
    manifest = json.loads((target_rune / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["enabled"] is False


def test_set_rune_enabled_enables_rune(tmp_path: Path) -> None:
    target_rune = tmp_path / "my-rune"
    target_rune.mkdir()
    (target_rune / "manifest.json").write_text(
        json.dumps({"name": "my-rune", "version": "1.0.0", "enabled": False}),
        encoding="utf-8",
    )

    assert set_rune_enabled("my-rune", True, target_dir=tmp_path) is True
    manifest = json.loads((target_rune / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["enabled"] is True


def test_set_rune_enabled_nonexistent_returns_false(tmp_path: Path) -> None:
    assert set_rune_enabled("nonexistent-rune", False, target_dir=tmp_path) is False


def test_list_installed_runes_exposes_manifest_commands(tmp_path: Path) -> None:
    rune_dir = tmp_path / "extensions" / "selfmod-bridge"
    rune_dir.mkdir(parents=True)
    (rune_dir / "manifest.json").write_text(
        json.dumps(
            {
                "name": "selfmod-bridge",
                "version": "0.1.0",
                "description": "Self modification bridge",
                "commands": ["selfmod"],
            }
        ),
        encoding="utf-8",
    )
    installed = list_installed_runes(target_dir=tmp_path / "extensions")
    assert len(installed) == 1
    assert installed[0]["commands"] == ["selfmod"]


def test_list_installed_runes_commands_defaults_to_empty(tmp_path: Path) -> None:
    rune_dir = tmp_path / "extensions" / "plain-rune"
    rune_dir.mkdir(parents=True)
    (rune_dir / "manifest.json").write_text(
        json.dumps({"name": "plain-rune", "version": "0.1.0"}),
        encoding="utf-8",
    )
    installed = list_installed_runes(target_dir=tmp_path / "extensions")
    assert len(installed) == 1
    assert installed[0]["commands"] == []


def test_install_rune_falls_back_to_local_candidate_on_clone_failure(
    tmp_path: Path,
) -> None:
    local_rune = tmp_path / "candidate_dir"
    _create_mock_rune_dir(local_rune, "openrouter-realm")
    target_dir = tmp_path / "extensions"
    marketplace_payload = {
        "runes": {
            "openrouter-realm": {
                "name": "openrouter-realm",
                "git": "https://github.com/IAmNo1Special/openrouter-realm.git",
                "path": "runes/openrouter-realm",
                "description": "OpenRouter provider realm",
            }
        }
    }

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = marketplace_payload
    mock_resp.raise_for_status = MagicMock()

    with (
        patch("httpx.get", return_value=mock_resp),
        patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, ["git", "clone"]),
        ),
        patch("mvge.runes.installer.local_catalog_candidate", return_value=local_rune),
        patch("mvge.runes.installer.install_python_deps"),
    ):
        dest = install_rune("openrouter-realm", target_dir=target_dir)

    assert dest == target_dir / "openrouter-realm"
    assert (dest / "manifest.json").is_file()
