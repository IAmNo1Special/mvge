"""Unit tests for the ``python -m mvgeos_cli`` entry point."""

from __future__ import annotations

import runpy
from pathlib import Path
from unittest.mock import patch

from mvgeos_agent.installer import ProfileInstallResult

import mvgeos_cli
from mvgeos_cli.main import _maybe_bootstrap_profile


def test_main_module_calls_main() -> None:
    """Running the package as __main__ delegates to main()."""
    main_py = Path(mvgeos_cli.__file__).parent / "__main__.py"
    with patch("mvgeos_cli.main.main") as mock_main:
        runpy.run_path(str(main_py), run_name="__main__")
    mock_main.assert_called_once_with()


def test_maybe_bootstrap_profile_already_installed() -> None:
    with patch(
        "mvgeos_agent.agent_name.installed_agent_names",
        return_value=["coding_mvge"],
    ):
        assert _maybe_bootstrap_profile(bootstrap=None) is False
        assert _maybe_bootstrap_profile(bootstrap=True) is False


def test_maybe_bootstrap_profile_explicit_false() -> None:
    with patch("mvgeos_agent.agent_name.installed_agent_names", return_value=[]):
        assert _maybe_bootstrap_profile(bootstrap=False, is_interactive=True) is False


def test_maybe_bootstrap_profile_explicit_true() -> None:
    mock_res = ProfileInstallResult("coding", ["coding_mvge"], ["seeker"], [], [])
    with (
        patch("mvgeos_agent.agent_name.installed_agent_names", return_value=[]),
        patch(
            "mvgeos_agent.installer.install_profile", return_value=mock_res
        ) as mock_install,
    ):
        assert _maybe_bootstrap_profile(bootstrap=True) is True
        mock_install.assert_called_once_with("coding")


def test_maybe_bootstrap_profile_interactive_accepted() -> None:
    mock_res = ProfileInstallResult("coding", ["coding_mvge"], ["seeker"], [], [])
    with (
        patch("mvgeos_agent.agent_name.installed_agent_names", return_value=[]),
        patch("sys.stdin.isatty", return_value=True),
        patch("sys.stdout.isatty", return_value=True),
        patch("builtins.input", return_value="y"),
        patch(
            "mvgeos_agent.installer.install_profile", return_value=mock_res
        ) as mock_install,
    ):
        assert _maybe_bootstrap_profile(bootstrap=None, is_interactive=True) is True
        mock_install.assert_called_once_with("coding")


def test_maybe_bootstrap_profile_interactive_rejected() -> None:
    with (
        patch("mvgeos_agent.agent_name.installed_agent_names", return_value=[]),
        patch("sys.stdin.isatty", return_value=True),
        patch("sys.stdout.isatty", return_value=True),
        patch("builtins.input", return_value="n"),
        patch("mvgeos_agent.installer.install_profile") as mock_install,
    ):
        assert _maybe_bootstrap_profile(bootstrap=None, is_interactive=True) is False
        mock_install.assert_not_called()
