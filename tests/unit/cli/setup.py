"""Unit tests for the CLI setup and dependency checking command."""

from __future__ import annotations

import os
import platform
import select
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from mvge.cli.commands.setup import (
    _has_build_config,
    _run_streaming,
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
from mvge.runes.types import RuneManifest


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
        patch("mvge.cli.commands.setup._run_streaming") as mock_run,
    ):
        mock_run.return_value = (0, "")
        success, msg = await install_package("ripgrep", dry_run=False)
        assert success is True
        assert "Installed ripgrep via" in msg

        mock_run.return_value = (1, "Package not found")
        success_fail, _ = await install_package("ripgrep", dry_run=False)
        assert success_fail is False

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="winget", timeout=300)
        success_timeout, _ = await install_package("ripgrep", dry_run=False)
        assert success_timeout is False


@pytest.mark.asyncio
async def test_install_python_dep_by_name() -> None:
    """install_python_dep_by_name runs uv add."""
    with patch("shutil.which", return_value="/bin/uv"):
        success_dry, msg_dry = await install_python_dep_by_name("rich", dry_run=True)
        assert success_dry is True
        assert "Would run: uv add rich" in msg_dry

        with patch("mvge.cli.commands.setup._run_streaming", return_value=(0, "")):
            success, msg = await install_python_dep_by_name("rich", dry_run=False)
            assert success is True
            assert "Installed python dep rich via uv" in msg


@pytest.mark.asyncio
async def test_run_uv_editable(tmp_path: Path) -> None:
    """_run_uv_editable runs uv add --editable <path>."""
    success_dry, msg_dry = await _run_uv_editable(tmp_path, dry_run=True)
    assert success_dry is True
    assert "Would run: uv add --editable" in msg_dry

    with patch("mvge.cli.commands.setup._run_streaming", return_value=(0, "")):
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
        patch("mvge.cli.commands.setup._run_uv_editable") as mock_edit,
    ):
        mock_edit.return_value = (True, "Installed via uv add --editable")
        (tmp_path / "pyproject.toml").touch()
        ok, msg = await install_rune_python_deps(tmp_path, manifest)
        assert ok is True
        mock_edit.assert_called_once()

    (tmp_path / "pyproject.toml").unlink()
    with (
        patch("shutil.which", return_value="/bin/uv"),
        patch("mvge.cli.commands.setup.extensions_dir", return_value=tmp_path),
        patch("mvge.cli.commands.setup.install_python_dep_by_name") as mock_dep,
    ):
        mock_dep.return_value = (True, "Installed python dep pytest via uv")
        ok, msg = await install_rune_python_deps(tmp_path, manifest)
        assert ok is True
        mock_dep.assert_called_once_with("pytest", dry_run=False, env_project=tmp_path)

        mock_dep.return_value = (False, "Failed to install pytest")
        ok_fail, _ = await install_rune_python_deps(tmp_path, manifest)
        assert ok_fail is False


@pytest.mark.asyncio
async def test_install_python_dep_by_name_targets_shared_project(
    tmp_path: Path,
) -> None:
    """A named dep must install into the shared extensions project, not cwd.

    This is the bare-directory failure: a bare ``uv add <dep>`` needs a
    pyproject.toml in the working directory, so ``mvge setup install``
    from an empty directory fails outright, and from a project directory
    it pollutes that project's manifest instead of the extensions env.
    """
    project_dir = tmp_path / "extensions"
    with (
        patch("shutil.which", return_value="/bin/uv"),
        patch(
            "mvge.cli.commands.setup._run_streaming", return_value=(0, "")
        ) as mock_run,
    ):
        ok, msg = await install_python_dep_by_name("cowsay", env_project=project_dir)
    assert ok is True
    assert "Installed python dep cowsay via uv" in msg
    mock_run.assert_called_once()
    cmd = mock_run.call_args.args[0]
    assert cmd == ["uv", "add", "--project", str(project_dir), "cowsay"]
    manifest = project_dir / "pyproject.toml"
    assert manifest.is_file()
    assert "mvge-extensions" in manifest.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_install_rune_python_deps_targets_shared_project_by_name(
    tmp_path: Path,
) -> None:
    """By-name rune deps must target the shared extensions project."""

    manifest = RuneManifest(
        name="demo",
        version="1.0.0",
        description="test",
        python_deps=["cowsay"],
    )
    rune_dir = tmp_path / "demo-rune"
    rune_dir.mkdir()
    project_dir = tmp_path / "extensions"
    with (
        patch("shutil.which", return_value="/bin/uv"),
        patch("mvge.cli.commands.setup.extensions_dir", return_value=project_dir),
        patch(
            "mvge.cli.commands.setup._run_streaming", return_value=(0, "")
        ) as mock_run,
    ):
        ok, _ = await install_rune_python_deps(rune_dir, manifest)
    assert ok is True
    cmd = mock_run.call_args.args[0]
    assert cmd == ["uv", "add", "--project", str(project_dir), "cowsay"]


@pytest.mark.asyncio
async def test_install_rune_python_deps_targets_shared_project_editable(
    tmp_path: Path,
) -> None:
    """Editable rune installs must target the shared extensions project."""

    manifest = RuneManifest(
        name="demo",
        version="1.0.0",
        description="test",
        python_deps=["cowsay"],
    )
    rune_dir = tmp_path / "demo-rune"
    rune_dir.mkdir()
    (rune_dir / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    project_dir = tmp_path / "extensions"
    with (
        patch("shutil.which", return_value="/bin/uv"),
        patch("mvge.cli.commands.setup.extensions_dir", return_value=project_dir),
        patch(
            "mvge.cli.commands.setup._run_streaming", return_value=(0, "")
        ) as mock_run,
    ):
        ok, _ = await install_rune_python_deps(rune_dir, manifest)
    assert ok is True
    cmd = mock_run.call_args.args[0]
    assert cmd == [
        "uv",
        "add",
        "--project",
        str(project_dir),
        "--editable",
        str(rune_dir),
    ]


@pytest.mark.asyncio
async def test_install_rune_python_deps_dry_run_creates_no_project(
    tmp_path: Path,
) -> None:
    """A dry run must not create the shared extensions project manifest."""

    manifest = RuneManifest(
        name="demo",
        version="1.0.0",
        description="test",
        python_deps=["cowsay"],
    )
    rune_dir = tmp_path / "demo-rune"
    rune_dir.mkdir()
    project_dir = tmp_path / "extensions"
    with (
        patch("shutil.which", return_value="/bin/uv"),
        patch("mvge.cli.commands.setup.extensions_dir", return_value=project_dir),
    ):
        ok, msg = await install_rune_python_deps(rune_dir, manifest, dry_run=True)
    assert ok is True
    assert "--project" in msg
    assert not (project_dir / "pyproject.toml").exists()


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
        patch("mvge.cli.commands.setup.resolve_rune_paths", return_value=[base_dir]),
        patch("mvge.cli.commands.setup.load_manifest", side_effect=fake_load),
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

    with patch("mvge.cli.commands.setup.collect_rune_dirs", return_value=[]):
        res_empty = runner.invoke(setup_app, ["check"])
        assert res_empty.exit_code == 0
        assert "No runes found" in res_empty.output

    rune_mock_no_deps = (
        RuneManifest(name="nodeps", version="1.0.0", description="test"),
        Path("/fake/path"),
    )
    with patch(
        "mvge.cli.commands.setup.collect_rune_dirs", return_value=[rune_mock_no_deps]
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
        patch("mvge.cli.commands.setup.collect_rune_dirs", return_value=[rune_mock]),
        patch("mvge.cli.commands.setup.check_tool_installed", return_value=True),
        patch("mvge.cli.commands.setup.check_python_dep", return_value=True),
    ):
        res_ok = runner.invoke(setup_app, ["check"])
        assert res_ok.exit_code == 0
        assert "All dependencies satisfied" in res_ok.output

    with (
        patch("mvge.cli.commands.setup.collect_rune_dirs", return_value=[rune_mock]),
        patch("mvge.cli.commands.setup.check_tool_installed", return_value=False),
        patch("mvge.cli.commands.setup.check_python_dep", return_value=True),
    ):
        res_missing = runner.invoke(setup_app, ["check"])
        assert res_missing.exit_code == 1
        assert "MISSING" in res_missing.output


def test_install_missing_deps_flow() -> None:
    """Test full install_missing_deps flow with prompt abortion and installs."""
    assert install_missing_deps(agent_name="../bad") == 1

    with patch("mvge.cli.commands.setup.collect_rune_dirs", return_value=[]):
        assert install_missing_deps() == 0

    rune_nodeps = (
        RuneManifest(name="nodeps", version="1.0.0", description="test"),
        Path("/fake"),
    )
    with patch("mvge.cli.commands.setup.collect_rune_dirs", return_value=[rune_nodeps]):
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
        patch("mvge.cli.commands.setup.collect_rune_dirs", return_value=[rune_mock]),
        patch("mvge.cli.commands.setup.check_tool_installed", return_value=True),
        patch("mvge.cli.commands.setup.check_python_dep", return_value=True),
    ):
        assert install_missing_deps() == 0

    with (
        patch("mvge.cli.commands.setup.collect_rune_dirs", return_value=[rune_mock]),
        patch("mvge.cli.commands.setup.check_tool_installed", return_value=False),
        patch("mvge.cli.commands.setup.check_python_dep", return_value=False),
        patch("sys.stdin", _FakeStdin(tty=True, line="")),
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
        patch("mvge.cli.commands.setup.collect_rune_dirs", return_value=[rune_mock]),
        patch("mvge.cli.commands.setup.check_tool_installed", return_value=False),
        patch("mvge.cli.commands.setup.check_python_dep", return_value=False),
        patch(
            "mvge.cli.commands.setup.install_package",
            side_effect=fake_install_pkg,
        ),
        patch(
            "mvge.cli.commands.setup.install_rune_python_deps",
            side_effect=fake_install_rune,
        ),
    ):
        assert install_missing_deps(yes=True) == 0

    async def fake_install_pkg_fail(
        dep: str, dry_run: bool = False
    ) -> tuple[bool, str]:
        return False, f"Failed {dep}"

    with (
        patch("mvge.cli.commands.setup.collect_rune_dirs", return_value=[rune_mock]),
        patch("mvge.cli.commands.setup.check_tool_installed", return_value=False),
        patch("mvge.cli.commands.setup.check_python_dep", return_value=True),
        patch(
            "mvge.cli.commands.setup.install_package",
            side_effect=fake_install_pkg_fail,
        ),
    ):
        assert install_missing_deps(yes=True) == 1


def test_setup_install_cli() -> None:
    """setup_install command CLI test."""
    runner = CliRunner()
    with patch("mvge.cli.commands.setup.install_missing_deps", return_value=0):
        res = runner.invoke(setup_app, ["install", "--yes"])
        assert res.exit_code == 0

    with patch("mvge.cli.commands.setup.install_missing_deps", return_value=1):
        res_fail = runner.invoke(setup_app, ["install", "--yes"])
        assert res_fail.exit_code == 1


def test_setup_profile_command_success() -> None:
    from mvge.agent.installer import ProfileInstallResult

    runner = CliRunner()
    mock_res = ProfileInstallResult(
        profile="coding",
        installed_mvges=["coding_mvge"],
        installed_runes=["seeker", "skills-bridge"],
        skipped_mvges=[],
        skipped_runes=[],
    )
    with patch("mvge.agent.installer.install_profile", return_value=mock_res):
        res = runner.invoke(setup_app, ["profile", "coding", "--yes"])
        assert res.exit_code == 0
        assert "Successfully installed profile 'coding'" in res.output
        assert "coding_mvge" in res.output


def test_setup_profile_command_flag_syntax() -> None:
    from mvge.agent.installer import ProfileInstallResult

    runner = CliRunner()
    mock_res = ProfileInstallResult(
        profile="minimal",
        installed_mvges=["coding_mvge"],
        installed_runes=["approval-rune"],
        skipped_mvges=[],
        skipped_runes=[],
    )
    with patch("mvge.agent.installer.install_profile", return_value=mock_res):
        res = runner.invoke(setup_app, ["--profile", "minimal"])
        assert res.exit_code == 0
        assert "Successfully installed profile 'minimal'" in res.output


def test_setup_profile_command_failure() -> None:
    runner = CliRunner()
    with patch(
        "mvge.agent.installer.install_profile",
        side_effect=ValueError("Unknown profile 'fake'"),
    ):
        res = runner.invoke(setup_app, ["profile", "fake"])
        assert res.exit_code == 1
        assert "Failed to install profile 'fake'" in res.output


@pytest.mark.asyncio
async def test_run_uv_add_failures() -> None:
    import subprocess

    from mvge.cli.commands.setup import install_python_dep_by_name

    with patch("shutil.which", return_value="/bin/uv"):
        # Failed returncode
        with patch(
            "mvge.cli.commands.setup._run_streaming", return_value=(1, "error msg")
        ):
            ok, msg = await install_python_dep_by_name("pkg")
            assert not ok
            assert "error msg" in msg

        # Timeout
        with patch(
            "mvge.cli.commands.setup._run_streaming",
            side_effect=subprocess.TimeoutExpired(cmd="uv", timeout=1),
        ):
            ok, msg = await install_python_dep_by_name("pkg")
            assert not ok
            assert "timed out" in msg

        # Generic error
        with patch(
            "mvge.cli.commands.setup._run_streaming",
            side_effect=OSError("uv missing"),
        ):
            ok, msg = await install_python_dep_by_name("pkg")
            assert not ok
            assert "uv missing" in msg


@pytest.mark.asyncio
async def test_run_uv_editable_failures(tmp_path: Path) -> None:
    import subprocess

    from mvge.cli.commands.setup import _run_uv_editable

    # Failed returncode
    with patch(
        "mvge.cli.commands.setup._run_streaming",
        return_value=(1, "editable error"),
    ):
        ok, msg = await _run_uv_editable(tmp_path)
        assert not ok
        assert "editable error" in msg

    # Timeout
    with patch(
        "mvge.cli.commands.setup._run_streaming",
        side_effect=subprocess.TimeoutExpired(cmd="uv", timeout=1),
    ):
        ok, msg = await _run_uv_editable(tmp_path)
        assert not ok
        assert "timed out" in msg

    # Generic error
    with patch(
        "mvge.cli.commands.setup._run_streaming",
        side_effect=OSError("uv missing"),
    ):
        ok, msg = await _run_uv_editable(tmp_path)
        assert not ok
        assert "uv missing" in msg


# --- Rerun-8 O1: non-TTY prompt guard + streaming installs ---


class _RecordingConsole:
    """Stand-in for the Rich console that records printed lines."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.file = self

    def print(self, *args: Any, **kwargs: Any) -> None:
        self.lines.append(" ".join(str(arg) for arg in args))

    def flush(self) -> None:
        return None

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


class _FakeStdin:
    """Stdin double with a fixed TTY answer and one readline result."""

    def __init__(self, *, tty: bool, line: str) -> None:
        self._tty = tty
        self._line = line
        self.seekable_result = True

    def isatty(self) -> bool:
        return self._tty

    def readline(self) -> str:
        return self._line

    def seekable(self) -> bool:
        return self.seekable_result


def _missing_deps_rune() -> tuple[RuneManifest, Path]:
    return (
        RuneManifest(
            name="demo",
            version="1.0.0",
            description="test",
            system_deps=["ripgrep"],
            python_deps=["rich"],
        ),
        Path("/fake/path"),
    )


def _patch_missing_deps(
    monkeypatch: pytest.MonkeyPatch, console: _RecordingConsole
) -> None:
    monkeypatch.setattr(
        "mvge.cli.commands.setup.collect_rune_dirs",
        lambda *args, **kwargs: [_missing_deps_rune()],
    )
    monkeypatch.setattr(
        "mvge.cli.commands.setup.check_tool_installed", lambda dep: False
    )
    monkeypatch.setattr("mvge.cli.commands.setup.check_python_dep", lambda dep: False)
    monkeypatch.setattr("mvge.cli.commands.setup.console", console)


def test_install_missing_deps_non_tty_refuses_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-TTY without --yes refuses fast and never reads stdin."""
    console = _RecordingConsole()
    _patch_missing_deps(monkeypatch, console)
    monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=False, line=""))
    # Open-but-silent pipe: select reports nothing readable.
    monkeypatch.setattr(select, "select", lambda r, w, x, timeout=0: ([], [], []))
    with patch("builtins.input", side_effect=AssertionError("input must not run")):
        result = install_missing_deps(yes=False, dry_run=False)
    assert result == 1
    assert "--yes" in console.text


def test_install_missing_deps_dev_null_refuses_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-TTY stdin at EOF refuses fast instead of trace-backing."""
    console = _RecordingConsole()
    _patch_missing_deps(monkeypatch, console)
    monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=False, line=""))
    # EOF is readable, but readline returns "" (no line was ever written).
    monkeypatch.setattr(select, "select", lambda r, w, x, timeout=0: ([r[0]], [], []))
    with patch("builtins.input", side_effect=AssertionError("input must not run")):
        result = install_missing_deps(yes=False, dry_run=False)
    assert result == 1
    assert "--yes" in console.text


def test_install_missing_deps_piped_y_proceeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The documented `echo y | mvge setup install` pattern still works."""
    console = _RecordingConsole()
    _patch_missing_deps(monkeypatch, console)
    monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=False, line="y\n"))
    monkeypatch.setattr(select, "select", lambda r, w, x, timeout=0: ([r[0]], [], []))

    async def fake_install_pkg(dep: str, dry_run: bool = False) -> tuple[bool, str]:
        return True, f"Installed {dep}"

    async def fake_install_rune(
        rune_dir: Path,
        manifest: RuneManifest | None = None,
        dry_run: bool = False,
    ) -> tuple[bool, str]:
        return True, "Installed python deps"

    with (
        patch(
            "mvge.cli.commands.setup.install_package",
            side_effect=fake_install_pkg,
        ),
        patch(
            "mvge.cli.commands.setup.install_rune_python_deps",
            side_effect=fake_install_rune,
        ),
    ):
        assert install_missing_deps(yes=False, dry_run=False) == 0


def test_install_missing_deps_unselectable_seekable_stdin_reads_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A seekable stdin without a fileno (e.g. CliRunner) is read, not refused."""
    console = _RecordingConsole()
    _patch_missing_deps(monkeypatch, console)
    monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=False, line="n\n"))

    def _no_select(*args: Any, **kwargs: Any) -> Any:
        raise OSError("no fileno")

    monkeypatch.setattr(select, "select", _no_select)
    with patch(
        "mvge.cli.commands.setup.install_package",
        side_effect=AssertionError("install must not run"),
    ):
        assert install_missing_deps(yes=False, dry_run=False) == 0
    assert "Aborted" in console.text


def test_install_missing_deps_unselectable_non_seekable_stdin_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-seekable stdin without select support refuses fast."""
    console = _RecordingConsole()
    _patch_missing_deps(monkeypatch, console)
    fake = _FakeStdin(tty=False, line="y\n")
    fake.seekable_result = False
    monkeypatch.setattr(sys, "stdin", fake)

    def _no_select(*args: Any, **kwargs: Any) -> Any:
        raise OSError("no fileno")

    monkeypatch.setattr(select, "select", _no_select)
    assert install_missing_deps(yes=False, dry_run=False) == 1
    assert "--yes" in console.text


def test_install_missing_deps_tty_eof_aborts_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EOF on a TTY prompt is a clean abort, not a traceback."""
    console = _RecordingConsole()
    _patch_missing_deps(monkeypatch, console)
    monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=True, line=""))
    with patch("builtins.input", side_effect=EOFError):
        result = install_missing_deps(yes=False, dry_run=False)
    assert result == 0
    assert "Aborted" in console.text


def test_install_missing_deps_tty_decline_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TTY prompt answering 'n' still declines without installing."""
    console = _RecordingConsole()
    _patch_missing_deps(monkeypatch, console)
    monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=True, line=""))
    with (
        patch("builtins.input", return_value="n"),
        patch(
            "mvge.cli.commands.setup.install_package",
            side_effect=AssertionError("install must not run"),
        ),
        patch(
            "mvge.cli.commands.setup.install_rune_python_deps",
            side_effect=AssertionError("install must not run"),
        ),
    ):
        assert install_missing_deps(yes=False, dry_run=False) == 0
    assert "Aborted" in console.text


@pytest.mark.asyncio
async def test_install_python_dep_progress_printed_before_subprocess() -> None:
    """The dep progress line is printed before the subprocess starts."""
    console = _RecordingConsole()
    seen_at_call: list[str] = []

    def fake_run(cmd: list[str], timeout: float) -> tuple[int, str]:
        seen_at_call.extend(console.lines)
        return (0, "")

    with (
        patch("shutil.which", return_value="/bin/uv"),
        patch("mvge.cli.commands.setup.console", console),
        patch("mvge.cli.commands.setup._run_streaming", side_effect=fake_run),
    ):
        ok, _ = await install_python_dep_by_name("aiohttp")
    assert ok is True
    progress = "\n".join(seen_at_call)
    assert "aiohttp" in progress
    assert "600s" in progress


@pytest.mark.asyncio
async def test_install_python_dep_timeout_text_names_dep() -> None:
    """On timeout the printed text names the dep and the timeout itself."""
    console = _RecordingConsole()
    with (
        patch("shutil.which", return_value="/bin/uv"),
        patch("mvge.cli.commands.setup.console", console),
        patch(
            "mvge.cli.commands.setup._run_streaming",
            side_effect=subprocess.TimeoutExpired(cmd="uv", timeout=600),
        ),
    ):
        ok, msg = await install_python_dep_by_name("aiohttp")
    assert ok is False
    assert "timed out" in msg
    # The timeout wording (not just the dep name, which the old Trying
    # line already printed) must reach the output on a timeout.
    assert "timed out" in console.text
    assert "aiohttp" in console.text
    assert "600s" in console.text


def test_run_streaming_streams_output_and_returns_tail() -> None:
    """_run_streaming streams child lines and returns code plus tail."""
    console = _RecordingConsole()
    with patch("mvge.cli.commands.setup.console", console):
        code, tail = _run_streaming(
            [sys.executable, "-c", "print('one'); print('two')"],
            timeout=30.0,
        )
    assert code == 0
    assert "one" in console.text
    assert "two" in console.text
    assert "two" in tail


def test_run_streaming_kills_child_on_timeout(tmp_path: Path) -> None:
    """On timeout _run_streaming kills the child instead of abandoning it."""
    console = _RecordingConsole()
    pid_file = tmp_path / "child.pid"
    child_code = (
        "import os, time\n"
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
        "print('streamed-line', flush=True)\n"
        "time.sleep(60)\n"
    )
    with patch("mvge.cli.commands.setup.console", console):
        start = time.monotonic()
        with pytest.raises(subprocess.TimeoutExpired):
            _run_streaming([sys.executable, "-c", child_code], timeout=1.0)
        elapsed = time.monotonic() - start
    assert elapsed < 20.0
    assert "streamed-line" in console.text
    pid = int(pid_file.read_text())
    deadline = time.monotonic() + 5.0
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        except OSError as exc:
            # Windows reports a dead process as WinError 87, not ESRCH.
            if platform.system() == "Windows" and getattr(exc, "winerror", None) == 87:
                break
            raise
        if time.monotonic() > deadline:
            raise AssertionError("child still alive after timeout kill")
        time.sleep(0.05)


def _extensions_venv_with_module(tmp_path: Path, module: str) -> Path:
    """Create a fake shared extensions project whose venv holds ``module``."""
    extensions_dir = tmp_path / "extensions"
    site_pkg = extensions_dir / ".venv" / "lib" / "site-packages"
    site_pkg.mkdir(parents=True)
    (site_pkg / f"{module}.py").write_text("VALUE = 1\n", encoding="utf-8")
    return extensions_dir


def test_setup_check_sees_extensions_venv_dep(tmp_path: Path) -> None:
    """setup check must report a dep present only in the extensions venv.

    This is the rerun-10 disagreement: ``setup install`` writes deps into
    the shared extensions venv, so a check that probes only the host
    interpreter still reports them MISSING after a successful install.
    The dep below exists nowhere else: it is a synthetic module that
    lives only in the fake extensions venv.
    """
    extensions_dir = _extensions_venv_with_module(
        tmp_path, "setupcheck_only_in_ext_venv"
    )
    rune_mock = (
        RuneManifest(
            name="demo",
            version="1.0.0",
            description="test",
            python_deps=["setupcheck_only_in_ext_venv"],
        ),
        tmp_path / "demo-rune",
    )
    runner = CliRunner()
    with (
        patch("mvge.cli.commands.setup.extensions_dir", return_value=extensions_dir),
        patch("mvge.cli.commands.setup.collect_rune_dirs", return_value=[rune_mock]),
    ):
        res = runner.invoke(setup_app, ["check"])
    assert res.exit_code == 0
    assert "setupcheck_only_in_ext_venv: OK" in res.output


def test_setup_check_missing_dep_not_in_extensions_venv(tmp_path: Path) -> None:
    """Negative control: a dep absent from host and extensions venv is MISSING."""
    extensions_dir = tmp_path / "extensions"
    (extensions_dir / ".venv" / "lib" / "site-packages").mkdir(parents=True)
    rune_mock = (
        RuneManifest(
            name="demo",
            version="1.0.0",
            description="test",
            python_deps=["setupcheck_definitely_absent_dep_xyz"],
        ),
        tmp_path / "demo-rune",
    )
    runner = CliRunner()
    with (
        patch("mvge.cli.commands.setup.extensions_dir", return_value=extensions_dir),
        patch("mvge.cli.commands.setup.collect_rune_dirs", return_value=[rune_mock]),
    ):
        res = runner.invoke(setup_app, ["check"])
    assert res.exit_code == 1
    assert "setupcheck_definitely_absent_dep_xyz: MISSING" in res.output


def test_install_missing_deps_sees_extensions_venv_dep(tmp_path: Path) -> None:
    """install must agree with check: a venv-only dep is already installed."""
    extensions_dir = _extensions_venv_with_module(
        tmp_path, "setupcheck_only_in_ext_venv"
    )
    rune_mock = (
        RuneManifest(
            name="demo",
            version="1.0.0",
            description="test",
            python_deps=["setupcheck_only_in_ext_venv"],
        ),
        tmp_path / "demo-rune",
    )
    with (
        patch("mvge.cli.commands.setup.extensions_dir", return_value=extensions_dir),
        patch("mvge.cli.commands.setup.collect_rune_dirs", return_value=[rune_mock]),
    ):
        assert install_missing_deps() == 0
