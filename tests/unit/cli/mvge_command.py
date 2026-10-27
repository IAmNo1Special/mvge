from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from mvge.cli.commands.mvge import mvge_app
from mvge.cli.main import app

runner = CliRunner()


def test_mvge_install_success(tmp_path: Path) -> None:
    dest_path = tmp_path / "coding_mvge"
    with patch(
        "mvge.cli.commands.mvge.install_mvge", return_value=dest_path
    ) as mock_install:
        result = runner.invoke(mvge_app, ["install", "coding_mvge"])
        assert result.exit_code == 0
        assert "Successfully installed mvge 'coding_mvge'" in result.stdout
        # Unset forwards None, not False: the installer prompts on a TTY and
        # fails closed without one. Defaulting to False would silently skip
        # the Spells' own dependencies for anyone who did not pass a flag.
        mock_install.assert_called_once_with("coding_mvge", confirm_python_deps=None)


def test_mvge_install_confirms_python_deps_when_asked(tmp_path: Path) -> None:
    dest_path = tmp_path / "coding_mvge"
    with patch(
        "mvge.cli.commands.mvge.install_mvge", return_value=dest_path
    ) as mock_install:
        result = runner.invoke(
            mvge_app, ["install", "coding_mvge", "--confirm-python-deps"]
        )
        assert result.exit_code == 0
        mock_install.assert_called_once_with("coding_mvge", confirm_python_deps=True)


def test_mvge_install_declines_python_deps_when_asked(tmp_path: Path) -> None:
    dest_path = tmp_path / "coding_mvge"
    with patch(
        "mvge.cli.commands.mvge.install_mvge", return_value=dest_path
    ) as mock_install:
        result = runner.invoke(
            mvge_app, ["install", "coding_mvge", "--no-confirm-python-deps"]
        )
        assert result.exit_code == 0
        mock_install.assert_called_once_with("coding_mvge", confirm_python_deps=False)


def test_mvge_install_exposes_a_tri_state_deps_flag() -> None:
    """The flag is the only non-interactive way to install a Mvge's deps.

    Asserted on the declared option string rather than the rendered help,
    which rich wraps and truncates to the terminal width.
    """
    import typer

    group = typer.main.get_command(mvge_app)
    commands = group.commands
    install = (
        commands["install"]
        if isinstance(commands, dict)
        else next(c for c in commands if c.name == "install")
    )
    deps = next(p for p in install.params if p.name == "confirm_python_deps")
    assert deps.opts + deps.secondary_opts == [
        "--confirm-python-deps",
        "--no-confirm-python-deps",
    ]
    # Unset must stay None so the installer keeps prompting on a TTY.
    assert deps.default is None


def test_mvge_install_error() -> None:
    with patch(
        "mvge.cli.commands.mvge.install_mvge", side_effect=ValueError("Not found")
    ):
        result = runner.invoke(mvge_app, ["install", "unknown_mvge"])
        assert result.exit_code != 0
        assert "Not found" in result.stdout


def test_mvge_list_empty() -> None:
    with patch("mvge.cli.commands.mvge.list_installed_mvges", return_value=[]):
        result = runner.invoke(mvge_app, ["list"])
        assert result.exit_code == 0
        assert "No agent mvges installed" in result.stdout


def test_mvge_list_with_items() -> None:
    mock_items = [
        {
            "name": "coding_mvge",
            "version": "0.2.6",
            "description": "Coding agent",
            "spells": ["bash", "read", "write"],
        }
    ]
    with patch("mvge.cli.commands.mvge.list_installed_mvges", return_value=mock_items):
        result = runner.invoke(mvge_app, ["list"])
        assert result.exit_code == 0
        assert "coding_mvge" in result.stdout
        assert "0.2.6" in result.stdout
        assert "bash, read, write" in result.stdout


def test_mvge_uninstall_success() -> None:
    with patch("mvge.cli.commands.mvge.uninstall_mvge", return_value=True):
        result = runner.invoke(mvge_app, ["uninstall", "coding_mvge"])
        assert result.exit_code == 0
        assert "Successfully uninstalled mvge 'coding_mvge'" in result.stdout


def test_mvge_uninstall_not_found() -> None:
    with patch("mvge.cli.commands.mvge.uninstall_mvge", return_value=False):
        result = runner.invoke(mvge_app, ["uninstall", "nonexistent"])
        assert result.exit_code == 1
        assert "Mvge 'nonexistent' is not installed" in result.stdout


def test_agent_alias_removed() -> None:
    """The `agent` command was renamed to `mvge`; the old name is gone."""
    group_names = [group.name for group in app.registered_groups]
    assert "mvge" in group_names
    assert "agent" not in group_names


def test_mvge_command_reachable_via_app(tmp_path: Path) -> None:
    dest_path = tmp_path / "coding_mvge"
    with patch("mvge.cli.commands.mvge.install_mvge", return_value=dest_path):
        result = runner.invoke(app, ["mvge", "install", "coding_mvge"])
        assert result.exit_code == 0
        assert "Successfully installed mvge 'coding_mvge'" in result.stdout


def test_mvge_list_exception() -> None:
    with patch(
        "mvge.cli.commands.mvge.list_installed_mvges",
        side_effect=RuntimeError("db error"),
    ):
        result = runner.invoke(mvge_app, ["list"])
        assert result.exit_code != 0
        assert "db error" in result.stdout


def test_mvge_uninstall_exception() -> None:
    with patch(
        "mvge.cli.commands.mvge.uninstall_mvge", side_effect=RuntimeError("io error")
    ):
        result = runner.invoke(mvge_app, ["uninstall", "failing"])
        assert result.exit_code != 0
        assert "io error" in result.stdout


def test_mvge_callback_no_subcommand() -> None:
    result = runner.invoke(mvge_app, [])
    assert result.exit_code == 0
    assert "Usage:" in result.stdout or "mvge" in result.stdout
