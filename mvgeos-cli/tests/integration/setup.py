from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from mvgeos_core.constants import DEFAULT_AGENT_NAME
from mvgeos_runes.types import RuneManifest
from typer.testing import CliRunner

from mvgeos_cli.commands.setup import (
    PACKAGE_MAP,
    TOOL_CHECK,
    check_python_dep,
    check_tool_installed,
    collect_rune_dirs,
    get_package_manager_commands,
    get_platform,
    install_missing_deps,
    install_package,
    install_rune_python_deps,
)
from mvgeos_cli.main import app


def test_get_platform_windows() -> None:
    with patch("platform.system", return_value="Windows"):
        assert get_platform() == "windows"


def test_get_platform_darwin() -> None:
    with patch("platform.system", return_value="Darwin"):
        assert get_platform() == "darwin"


def test_get_platform_linux() -> None:
    with patch("platform.system", return_value="Linux"):
        assert get_platform() == "linux"


def test_get_package_manager_commands_windows() -> None:
    with patch("mvgeos_cli.commands.setup.get_platform", return_value="windows"):
        cmds = get_package_manager_commands()
        assert cmds[0][0] == "winget"
        assert cmds[1][0] == "choco"
        assert cmds[2][0] == "scoop"


def test_get_package_manager_commands_darwin() -> None:
    with patch("mvgeos_cli.commands.setup.get_platform", return_value="darwin"):
        cmds = get_package_manager_commands()
        assert cmds[0][0] == "brew"


def test_get_package_manager_commands_linux() -> None:
    with patch("mvgeos_cli.commands.setup.get_platform", return_value="linux"):
        cmds = get_package_manager_commands()
        assert cmds[0][0] == "apt"
        assert cmds[1][0] == "dnf"
        assert cmds[2][0] == "pacman"


def test_check_tool_installed_known_tool() -> None:
    with patch("shutil.which", return_value="/usr/bin/rg"):
        assert check_tool_installed("ripgrep") is True


def test_check_tool_installed_unknown_tool() -> None:
    with patch("shutil.which", return_value=None):
        assert check_tool_installed("nonexistent_tool") is False


def test_check_python_dep_installed() -> None:
    assert check_python_dep("json") is True


def test_check_python_dep_missing() -> None:
    assert check_python_dep("nonexistent_module_xyz") is False


def test_tool_check_mapping() -> None:
    assert TOOL_CHECK["ripgrep"] == "rg"
    assert TOOL_CHECK["git"] == "git"
    assert TOOL_CHECK["fd"] == "fd"
    assert TOOL_CHECK["bat"] == "bat"
    assert TOOL_CHECK["fzf"] == "fzf"


def test_package_map_structure() -> None:
    assert "ripgrep" in PACKAGE_MAP
    assert "windows" in PACKAGE_MAP["ripgrep"]
    assert "darwin" in PACKAGE_MAP["ripgrep"]
    assert "linux" in PACKAGE_MAP["ripgrep"]
    assert "BurntSushi.ripgrep.MSVC" in PACKAGE_MAP["ripgrep"]["windows"]


def test_collect_rune_dirs_with_manifest() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runes_dir = Path(tmpdir) / "runes"
        rune_dir = runes_dir / "my_rune"
        rune_dir.mkdir(parents=True)
        manifest_data = (
            '{"name": "my_rune", "version": "1.0.0", "description": "T", '
            '"hooks": [], "system_deps": ["ripgrep"], "python_deps": ["foo"]}'
        )
        (rune_dir / "manifest.json").write_text(manifest_data, encoding="utf-8")

        with patch(
            "mvgeos_cli.commands.setup.resolve_rune_paths",
            return_value=[runes_dir],
        ):
            results = collect_rune_dirs(DEFAULT_AGENT_NAME, None)
            assert len(results) == 1
            manifest, path = results[0]
            assert manifest.name == "my_rune"
            assert manifest.system_deps == ["ripgrep"]
            assert manifest.python_deps == ["foo"]
            assert path == rune_dir


def test_collect_rune_dirs_skips_disabled() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runes_dir = Path(tmpdir) / "runes"
        rune_dir = runes_dir / "disabled_rune"
        rune_dir.mkdir(parents=True)
        manifest_data = (
            '{"name": "disabled_rune", "version": "1.0.0", "description": "T", '
            '"hooks": [], "enabled": false}'
        )
        (rune_dir / "manifest.json").write_text(manifest_data, encoding="utf-8")

        with patch(
            "mvgeos_cli.commands.setup.resolve_rune_paths",
            return_value=[runes_dir],
        ):
            results = collect_rune_dirs(DEFAULT_AGENT_NAME, None)
            assert results == []


def test_install_rune_python_deps_no_uv() -> None:
    import asyncio

    with patch("shutil.which", return_value=None):
        success, msg = asyncio.run(install_rune_python_deps(Path("/tmp/rune"), None))
        assert success is False
        assert "uv not found" in msg


def test_install_rune_python_deps_dry_run() -> None:
    import asyncio

    with patch("shutil.which", return_value="/usr/bin/uv"):
        success, msg = asyncio.run(
            install_rune_python_deps(Path("/tmp/rune"), None, dry_run=True)
        )
        assert success is True
        assert "uv add --editable" in msg


def test_install_rune_python_deps_by_name_without_build_config() -> None:
    import asyncio

    from mvgeos_runes.types import RuneManifest

    manifest = RuneManifest(
        name="heal-my-goap",
        version="1.0.0",
        description="",
        python_deps=["heal_my_goap"],
    )
    with (
        tempfile.TemporaryDirectory() as tmpdir,
        patch("shutil.which", return_value="/usr/bin/uv"),
    ):
        rune_dir = Path(tmpdir) / "heal-my-goap"
        rune_dir.mkdir()
        # No pyproject.toml / setup.py -> install declared deps by name.
        success, msg = asyncio.run(
            install_rune_python_deps(rune_dir, manifest, dry_run=True)
        )
        assert success is True
        assert "uv add heal_my_goap" in msg


def test_install_rune_python_deps_editable_when_build_config_present() -> None:
    import asyncio

    from mvgeos_runes.types import RuneManifest

    manifest = RuneManifest(
        name="pkg-rune",
        version="1.0.0",
        description="",
        python_deps=["some_pkg"],
    )
    with (
        tempfile.TemporaryDirectory() as tmpdir,
        patch("shutil.which", return_value="/usr/bin/uv"),
    ):
        rune_dir = Path(tmpdir) / "pkg-rune"
        rune_dir.mkdir()
        (rune_dir / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        # A build config exists -> fall back to editable install.
        success, msg = asyncio.run(
            install_rune_python_deps(rune_dir, manifest, dry_run=True)
        )
        assert success is True
        assert "uv add --editable" in msg


def test_install_package_dry_run() -> None:
    import asyncio

    with patch("shutil.which", return_value="/usr/bin/winget"):
        result = asyncio.run(install_package("ripgrep", dry_run=True))
        assert result[0] is True
        assert "Would run" in result[1]


def test_setup_check_command_no_runes() -> None:
    runner = CliRunner()
    with patch("mvgeos_cli.commands.setup.collect_rune_dirs", return_value=[]):
        result = runner.invoke(app, ["setup", "check"])
        assert result.exit_code == 0
        assert "No runes found" in result.output


def test_setup_check_command_no_deps() -> None:
    from mvgeos_runes.types import RuneManifest

    runner = CliRunner()
    manifest = RuneManifest(name="r", version="1", description="")
    with patch(
        "mvgeos_cli.commands.setup.collect_rune_dirs",
        return_value=[(manifest, Path("/tmp/r"))],
    ):
        result = runner.invoke(app, ["setup", "check"])
        assert result.exit_code == 0
        assert "No dependencies declared by runes" in result.output


def test_setup_check_command_all_installed() -> None:
    from mvgeos_runes.types import RuneManifest

    runner = CliRunner()
    manifest = RuneManifest(
        name="r", version="1", description="", system_deps=["git"], python_deps=["json"]
    )
    with (
        patch(
            "mvgeos_cli.commands.setup.collect_rune_dirs",
            return_value=[(manifest, Path("/tmp/r"))],
        ),
        patch("mvgeos_cli.commands.setup.check_tool_installed", return_value=True),
        patch("mvgeos_cli.commands.setup.check_python_dep", return_value=True),
    ):
        result = runner.invoke(app, ["setup", "check"])
        assert result.exit_code == 0
        assert "All dependencies satisfied" in result.output


def test_setup_check_command_missing_system() -> None:
    from mvgeos_runes.types import RuneManifest

    runner = CliRunner()
    manifest = RuneManifest(
        name="r", version="1", description="", system_deps=["ripgrep"]
    )
    with (
        patch(
            "mvgeos_cli.commands.setup.collect_rune_dirs",
            return_value=[(manifest, Path("/tmp/r"))],
        ),
        patch("mvgeos_cli.commands.setup.check_tool_installed", return_value=False),
    ):
        result = runner.invoke(app, ["setup", "check"])
        assert result.exit_code == 1
        assert "missing" in result.output


def test_setup_check_command_missing_python() -> None:
    from mvgeos_runes.types import RuneManifest

    runner = CliRunner()
    manifest = RuneManifest(
        name="r", version="1", description="", python_deps=["heal_my_goap"]
    )
    with (
        patch(
            "mvgeos_cli.commands.setup.collect_rune_dirs",
            return_value=[(manifest, Path("/tmp/r"))],
        ),
        patch("mvgeos_cli.commands.setup.check_python_dep", return_value=False),
    ):
        result = runner.invoke(app, ["setup", "check"])
        assert result.exit_code == 1
        assert "Python Dependencies" in result.output


def test_setup_install_command_no_missing() -> None:
    from mvgeos_runes.types import RuneManifest

    runner = CliRunner()
    manifest = RuneManifest(name="r", version="1", description="", system_deps=["git"])
    with (
        patch(
            "mvgeos_cli.commands.setup.collect_rune_dirs",
            return_value=[(manifest, Path("/tmp/r"))],
        ),
        patch("mvgeos_cli.commands.setup.check_tool_installed", return_value=True),
    ):
        result = runner.invoke(app, ["setup", "install"])
        assert result.exit_code == 0
        assert "All dependencies already installed" in result.output


def test_setup_install_command_dry_run_system() -> None:
    from mvgeos_runes.types import RuneManifest

    runner = CliRunner()
    manifest = RuneManifest(
        name="r", version="1", description="", system_deps=["ripgrep"]
    )
    with (
        patch(
            "mvgeos_cli.commands.setup.collect_rune_dirs",
            return_value=[(manifest, Path("/tmp/r"))],
        ),
        patch("mvgeos_cli.commands.setup.check_tool_installed", return_value=False),
        patch("shutil.which", return_value="/usr/bin/winget"),
    ):
        result = runner.invoke(app, ["setup", "install", "--dry-run", "--yes"])
        assert result.exit_code == 0
        assert "Would run" in result.output


def test_setup_install_command_dry_run_python() -> None:
    from mvgeos_runes.types import RuneManifest

    runner = CliRunner()
    manifest = RuneManifest(
        name="r", version="1", description="", python_deps=["heal_my_goap"]
    )
    with (
        patch(
            "mvgeos_cli.commands.setup.collect_rune_dirs",
            return_value=[(manifest, Path("/tmp/r"))],
        ),
        patch("mvgeos_cli.commands.setup.check_python_dep", return_value=False),
        patch("shutil.which", return_value="/usr/bin/uv"),
    ):
        result = runner.invoke(app, ["setup", "install", "--dry-run", "--yes"])
        assert result.exit_code == 0
        assert "uv add heal_my_goap" in result.output


def test_setup_install_command_yes_skips_prompt() -> None:
    runner = CliRunner()
    manifest = RuneManifest(name="r", version="1", description="", system_deps=["git"])
    with (
        patch(
            "mvgeos_cli.commands.setup.collect_rune_dirs",
            return_value=[(manifest, Path("/tmp/r"))],
        ),
        patch("mvgeos_cli.commands.setup.check_tool_installed", return_value=True),
    ):
        result = runner.invoke(app, ["setup", "install", "--yes"])
        assert result.exit_code == 0
        assert "All dependencies already installed" in result.output


def test_setup_install_command_piped_stdin_confirm() -> None:
    runner = CliRunner()
    manifest = RuneManifest(
        name="r", version="1", description="", system_deps=["ripgrep"]
    )
    with (
        patch(
            "mvgeos_cli.commands.setup.collect_rune_dirs",
            return_value=[(manifest, Path("/tmp/r"))],
        ),
        patch("mvgeos_cli.commands.setup.check_tool_installed", return_value=False),
        patch("shutil.which", return_value="/usr/bin/winget"),
    ):
        result = runner.invoke(app, ["setup", "install", "--dry-run"], input="y\n")
        assert result.exit_code == 0
        assert "Would run" in result.output


def test_setup_install_command_piped_stdin_abort() -> None:
    runner = CliRunner()
    manifest = RuneManifest(
        name="r", version="1", description="", system_deps=["ripgrep"]
    )
    with (
        patch(
            "mvgeos_cli.commands.setup.collect_rune_dirs",
            return_value=[(manifest, Path("/tmp/r"))],
        ),
        patch("mvgeos_cli.commands.setup.check_tool_installed", return_value=False),
    ):
        result = runner.invoke(app, ["setup", "install"], input="n\n")
        assert result.exit_code == 0
        assert "Aborted" in result.output


def test_install_missing_deps_no_runes() -> None:
    with patch("mvgeos_cli.commands.setup.collect_rune_dirs", return_value=[]):
        assert install_missing_deps(DEFAULT_AGENT_NAME) == 0


def test_install_missing_deps_no_declared_deps() -> None:
    from mvgeos_runes.types import RuneManifest

    manifest = RuneManifest(name="r", version="1", description="")
    with patch(
        "mvgeos_cli.commands.setup.collect_rune_dirs",
        return_value=[(manifest, Path("/tmp/r"))],
    ):
        assert install_missing_deps(DEFAULT_AGENT_NAME) == 0


def test_install_missing_deps_all_installed() -> None:
    from mvgeos_runes.types import RuneManifest

    manifest = RuneManifest(name="r", version="1", description="", python_deps=["json"])
    with (
        patch(
            "mvgeos_cli.commands.setup.collect_rune_dirs",
            return_value=[(manifest, Path("/tmp/r"))],
        ),
        patch("mvgeos_cli.commands.setup.check_python_dep", return_value=True),
    ):
        assert install_missing_deps(DEFAULT_AGENT_NAME) == 0


def test_setup_no_subcommand_defaults_to_check() -> None:
    runner = CliRunner()
    with patch("mvgeos_cli.commands.setup.collect_rune_dirs", return_value=[]):
        result = runner.invoke(app, ["setup"])
        assert result.exit_code == 0
        assert "No runes found" in result.output


def test_setup_explicit_check_subcommand() -> None:
    runner = CliRunner()
    with patch("mvgeos_cli.commands.setup.collect_rune_dirs", return_value=[]):
        result = runner.invoke(app, ["setup", "check"])
        assert result.exit_code == 0
        assert "No runes found" in result.output
