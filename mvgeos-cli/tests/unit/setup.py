"""Unit tests for the CLI setup and dependency checking command."""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from mvgeos_runes.types import RuneManifest
from typer.testing import CliRunner

from mvgeos_cli.commands.setup import (
    _has_build_config,
    _run_uv_editable,
    check_python_dep,
    check_tool_installed,
    collect_rune_dirs,
    get_package_manager_commands,
    get_platform,
    install_missing_deps,
    install_package,
    install_python_dep_by_name,
    install_rune_python_deps,
    setup_app,
)


def test_get_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_platform should classify windows, darwin, or linux."""
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    assert get_platform() == "windows"

    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    assert get_platform() == "darwin"

    monkeypatch.setattr(platform, "system", lambda: "Linux")
    assert get_platform() == "linux"


def test_get_package_manager_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify package manager command templates for different OS."""
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    cmds = get_package_manager_commands()
    assert any("winget" in c for c in cmds)

    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    cmds_darwin = get_package_manager_commands()
    assert any("brew" in c for c in cmds_darwin)

    monkeypatch.setattr(platform, "system", lambda: "Linux")
    cmds_linux = get_package_manager_commands()
    assert any("apt" in c for c in cmds_linux)


def test_check_tool_installed() -> None:
    """check_tool_installed should resolve tool check name and call shutil.which."""
    with patch("shutil.which") as mock_which:
        mock_which.return_value = "/usr/bin/rg"
        assert check_tool_installed("ripgrep") is True
        mock_which.assert_called_with("rg")

        mock_which.return_value = None
        assert check_tool_installed("unknown_tool") is False


def test_check_python_dep() -> None:
    """check_python_dep should return True for installed modules."""
    assert check_python_dep("sys") is True
    assert check_python_dep("non_existent_module_xyz_123") is False


def test_has_build_config(tmp_path: Path) -> None:
    """_has_build_config should check for pyproject.toml or setup.py."""
    assert _has_build_config(tmp_path) is False

    (tmp_path / "pyproject.toml").touch()
    assert _has_build_config(tmp_path) is True

    (tmp_path / "pyproject.toml").unlink()
    (tmp_path / "setup.py").touch()
    assert _has_build_config(tmp_path) is True


@pytest.mark.asyncio
async def test_install_package_dry_run() -> None:
    """install_package with dry_run=True should simulate without running commands."""
    with patch("shutil.which", return_value="/usr/bin/winget"):
        success, msg = await install_package("ripgrep", dry_run=True)
        assert success is True
        assert "Would run" in msg


@pytest.mark.asyncio
async def test_install_package_success_and_failure() -> None:
    """install_package should handle process success, error, and timeout."""
    with (
        patch("shutil.which", return_value="/usr/bin/winget"),
        patch("asyncio.to_thread") as mock_thread,
    ):
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_thread.return_value = mock_res
        success, msg = await install_package("ripgrep", dry_run=False)
        assert success is True
        assert "Installed ripgrep via" in msg

        mock_res.returncode = 1
        mock_res.stderr = "Package not found"
        success_fail, _ = await install_package("ripgrep", dry_run=False)
        assert success_fail is False

        mock_thread.side_effect = subprocess.TimeoutExpired(cmd="winget", timeout=300)
        success_timeout, _ = await install_package("ripgrep", dry_run=False)
        assert success_timeout is False


@pytest.mark.asyncio
async def test_install_python_dep_by_name() -> None:
    """install_python_dep_by_name runs uv add."""
    with patch("shutil.which", return_value="/bin/uv"):
        success_dry, msg_dry = await install_python_dep_by_name("rich", dry_run=True)
        assert success_dry is True
        assert "Would run: uv add rich" in msg_dry

        with patch("asyncio.to_thread") as mock_thread:
            mock_res = MagicMock(returncode=0)
            mock_thread.return_value = mock_res
            success, msg = await install_python_dep_by_name("rich", dry_run=False)
            assert success is True
            assert "Installed python dep rich via uv" in msg


@pytest.mark.asyncio
async def test_run_uv_editable(tmp_path: Path) -> None:
    """_run_uv_editable runs uv add --editable <path>."""
    success_dry, msg_dry = await _run_uv_editable(tmp_path, dry_run=True)
    assert success_dry is True
    assert "Would run: uv add --editable" in msg_dry

    with patch("asyncio.to_thread") as mock_thread:
        mock_res = MagicMock(returncode=0)
        mock_thread.return_value = mock_res
        success, msg = await _run_uv_editable(tmp_path, dry_run=False)
        assert success is True
        assert "Installed via uv add --editable" in msg


@pytest.mark.asyncio
async def test_install_rune_python_deps(tmp_path: Path) -> None:
    """install_rune_python_deps dispatches to editable or name install."""
    manifest = RuneManifest(
        name="test_rune",
        version="1.0.0",
        description="test",
        python_deps=["pytest"],
    )
    with patch("shutil.which", return_value=None):
        ok, msg = await install_rune_python_deps(tmp_path, manifest)
        assert ok is False
        assert "uv not found" in msg

    with (
        patch("shutil.which", return_value="/bin/uv"),
        patch("mvgeos_cli.commands.setup._run_uv_editable") as mock_edit,
    ):
        mock_edit.return_value = (True, "Installed via uv add --editable")
        (tmp_path / "pyproject.toml").touch()
        ok, msg = await install_rune_python_deps(tmp_path, manifest)
        assert ok is True
        mock_edit.assert_called_once()

    (tmp_path / "pyproject.toml").unlink()
    with (
        patch("shutil.which", return_value="/bin/uv"),
        patch("mvgeos_cli.commands.setup.install_python_dep_by_name") as mock_dep,
    ):
        mock_dep.return_value = (True, "Installed python dep pytest via uv")
        ok, msg = await install_rune_python_deps(tmp_path, manifest)
        assert ok is True
        mock_dep.assert_called_once_with("pytest", dry_run=False)

        mock_dep.return_value = (False, "Failed to install pytest")
        ok_fail, _ = await install_rune_python_deps(tmp_path, manifest)
        assert ok_fail is False


def test_collect_rune_dirs(tmp_path: Path) -> None:
    """collect_rune_dirs scans paths, loads manifests, and deduplicates."""
    base_dir = tmp_path / "runes"
    base_dir.mkdir()
    (base_dir / "not_a_dir.txt").touch()

    rune_dir1 = base_dir / "rune1"
    rune_dir1.mkdir()
    rune_dir2 = base_dir / "rune2"
    rune_dir2.mkdir()
    rune_dir3 = base_dir / "rune3"
    rune_dir3.mkdir()

    m1 = RuneManifest(name="alpha", version="1.0.0", description="a", enabled=True)
    m2 = RuneManifest(name="alpha", version="1.0.0", description="dup", enabled=True)
    m3 = RuneManifest(name="beta", version="1.0.0", description="b", enabled=False)

    def fake_load(p: Path) -> RuneManifest | None:
        if p == rune_dir1:
            return m1
        if p == rune_dir2:
            return m2
        if p == rune_dir3:
            return m3
        return None

    with (
        patch("mvgeos_cli.commands.setup.resolve_rune_paths", return_value=[base_dir]),
        patch("mvgeos_cli.commands.setup.load_manifest", side_effect=fake_load),
    ):
        results = collect_rune_dirs("coding_mvge", None)
        assert len(results) == 1
        assert results[0][0].name == "alpha"
        assert results[0][1] == rune_dir1


def test_setup_check_cli() -> None:
    """setup_check should validate dependencies and exit with 0 or 1."""
    runner = CliRunner()

    res_invalid = runner.invoke(setup_app, ["check", "--agent-name", "../bad"])
    assert res_invalid.exit_code != 0

    with patch("mvgeos_cli.commands.setup.collect_rune_dirs", return_value=[]):
        res_empty = runner.invoke(setup_app, ["check"])
        assert res_empty.exit_code == 0
        assert "No runes found" in res_empty.output

    rune_mock_no_deps = (
        RuneManifest(name="nodeps", version="1.0.0", description="test"),
        Path("/fake/path"),
    )
    with patch(
        "mvgeos_cli.commands.setup.collect_rune_dirs", return_value=[rune_mock_no_deps]
    ):
        res_nodeps = runner.invoke(setup_app, ["check"])
        assert res_nodeps.exit_code == 0
        assert "No dependencies declared" in res_nodeps.output

    rune_mock = (
        RuneManifest(
            name="demo",
            version="1.0.0",
            description="test",
            system_deps=["ripgrep"],
            python_deps=["rich"],
        ),
        Path("/fake/path"),
    )
    with (
        patch("mvgeos_cli.commands.setup.collect_rune_dirs", return_value=[rune_mock]),
        patch("mvgeos_cli.commands.setup.check_tool_installed", return_value=True),
        patch("mvgeos_cli.commands.setup.check_python_dep", return_value=True),
    ):
        res_ok = runner.invoke(setup_app, ["check"])
        assert res_ok.exit_code == 0
        assert "All dependencies satisfied" in res_ok.output

    with (
        patch("mvgeos_cli.commands.setup.collect_rune_dirs", return_value=[rune_mock]),
        patch("mvgeos_cli.commands.setup.check_tool_installed", return_value=False),
        patch("mvgeos_cli.commands.setup.check_python_dep", return_value=True),
    ):
        res_missing = runner.invoke(setup_app, ["check"])
        assert res_missing.exit_code == 1
        assert "MISSING" in res_missing.output


def test_install_missing_deps_flow() -> None:
    """Test full install_missing_deps flow with prompt abortion and installs."""
    assert install_missing_deps(agent_name="../bad") == 1

    with patch("mvgeos_cli.commands.setup.collect_rune_dirs", return_value=[]):
        assert install_missing_deps() == 0

    rune_nodeps = (
        RuneManifest(name="nodeps", version="1.0.0", description="test"),
        Path("/fake"),
    )
    with patch(
        "mvgeos_cli.commands.setup.collect_rune_dirs", return_value=[rune_nodeps]
    ):
        assert install_missing_deps() == 0

    rune_mock = (
        RuneManifest(
            name="demo",
            version="1.0.0",
            description="test",
            system_deps=["ripgrep"],
            python_deps=["rich"],
        ),
        Path("/fake/path"),
    )

    with (
        patch("mvgeos_cli.commands.setup.collect_rune_dirs", return_value=[rune_mock]),
        patch("mvgeos_cli.commands.setup.check_tool_installed", return_value=True),
        patch("mvgeos_cli.commands.setup.check_python_dep", return_value=True),
    ):
        assert install_missing_deps() == 0

    with (
        patch("mvgeos_cli.commands.setup.collect_rune_dirs", return_value=[rune_mock]),
        patch("mvgeos_cli.commands.setup.check_tool_installed", return_value=False),
        patch("mvgeos_cli.commands.setup.check_python_dep", return_value=False),
        patch("builtins.input", return_value="n"),
    ):
        assert install_missing_deps(yes=False, dry_run=False) == 0

    async def fake_install_pkg(dep: str, dry_run: bool = False) -> tuple[bool, str]:
        return True, f"Installed {dep}"

    async def fake_install_rune(
        rune_dir: Path, manifest: RuneManifest | None = None, dry_run: bool = False
    ) -> tuple[bool, str]:
        return True, "Installed python deps"

    with (
        patch("mvgeos_cli.commands.setup.collect_rune_dirs", return_value=[rune_mock]),
        patch("mvgeos_cli.commands.setup.check_tool_installed", return_value=False),
        patch("mvgeos_cli.commands.setup.check_python_dep", return_value=False),
        patch(
            "mvgeos_cli.commands.setup.install_package",
            side_effect=fake_install_pkg,
        ),
        patch(
            "mvgeos_cli.commands.setup.install_rune_python_deps",
            side_effect=fake_install_rune,
        ),
    ):
        assert install_missing_deps(yes=True) == 0

    async def fake_install_pkg_fail(
        dep: str, dry_run: bool = False
    ) -> tuple[bool, str]:
        return False, f"Failed {dep}"

    with (
        patch("mvgeos_cli.commands.setup.collect_rune_dirs", return_value=[rune_mock]),
        patch("mvgeos_cli.commands.setup.check_tool_installed", return_value=False),
        patch("mvgeos_cli.commands.setup.check_python_dep", return_value=True),
        patch(
            "mvgeos_cli.commands.setup.install_package",
            side_effect=fake_install_pkg_fail,
        ),
    ):
        assert install_missing_deps(yes=True) == 1


def test_setup_install_cli() -> None:
    """setup_install command CLI test."""
    runner = CliRunner()
    with patch("mvgeos_cli.commands.setup.install_missing_deps", return_value=0):
        res = runner.invoke(setup_app, ["install", "--yes"])
        assert res.exit_code == 0

    with patch("mvgeos_cli.commands.setup.install_missing_deps", return_value=1):
        res_fail = runner.invoke(setup_app, ["install", "--yes"])
        assert res_fail.exit_code == 1


def test_setup_profile_command_success() -> None:
    from mvgeos_agent.installer import ProfileInstallResult

    runner = CliRunner()
    mock_res = ProfileInstallResult(
        profile="coding",
        installed_mvges=["coding_mvge"],
        installed_runes=["seeker", "skills-bridge"],
        skipped_mvges=[],
        skipped_runes=[],
    )
    with patch("mvgeos_agent.installer.install_profile", return_value=mock_res):
        res = runner.invoke(setup_app, ["profile", "coding", "--yes"])
        assert res.exit_code == 0
        assert "Successfully installed profile 'coding'" in res.output
        assert "coding_mvge" in res.output


def test_setup_profile_command_flag_syntax() -> None:
    from mvgeos_agent.installer import ProfileInstallResult

    runner = CliRunner()
    mock_res = ProfileInstallResult(
        profile="minimal",
        installed_mvges=["coding_mvge"],
        installed_runes=["approval-rune"],
        skipped_mvges=[],
        skipped_runes=[],
    )
    with patch("mvgeos_agent.installer.install_profile", return_value=mock_res):
        res = runner.invoke(setup_app, ["--profile", "minimal"])
        assert res.exit_code == 0
        assert "Successfully installed profile 'minimal'" in res.output


def test_setup_profile_command_failure() -> None:
    runner = CliRunner()
    with patch(
        "mvgeos_agent.installer.install_profile",
        side_effect=ValueError("Unknown profile 'fake'"),
    ):
        res = runner.invoke(setup_app, ["profile", "fake"])
        assert res.exit_code == 1
        assert "Failed to install profile 'fake'" in res.output
