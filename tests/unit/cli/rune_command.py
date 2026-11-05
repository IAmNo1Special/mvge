from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from mvge.cli.commands.rune import rune_app

runner = CliRunner()


def test_rune_install_success(tmp_path: Path) -> None:
    dest_path = tmp_path / "openrouter-realm"
    with patch(
        "mvge.cli.commands.rune.install_rune", return_value=dest_path
    ) as mock_install:
        result = runner.invoke(
            rune_app, ["install", "openrouter-realm", "--confirm-python-deps"]
        )
        assert result.exit_code == 0
        assert "Successfully installed rune 'openrouter-realm'" in result.stdout
        mock_install.assert_called_once_with(
            "openrouter-realm", confirm_python_deps=True
        )


def test_rune_install_error() -> None:
    with patch(
        "mvge.cli.commands.rune.install_rune", side_effect=ValueError("Not found")
    ):
        result = runner.invoke(rune_app, ["install", "unknown-rune"])
        assert result.exit_code != 0
        assert "Not found" in result.stdout


def test_rune_consent_success() -> None:
    with patch(
        "mvge.cli.commands.rune.consent_rune", return_value=["realm", "hook"]
    ) as mock_consent:
        result = runner.invoke(rune_app, ["consent", "pollinations-realm"])
        assert result.exit_code == 0
        assert "Consented to 'pollinations-realm'" in result.stdout
        assert "realm" in result.stdout
        mock_consent.assert_called_once_with("pollinations-realm")


def test_rune_consent_not_installed() -> None:
    with patch(
        "mvge.cli.commands.rune.consent_rune",
        side_effect=ValueError("Rune 'unknown-rune' is not installed."),
    ):
        result = runner.invoke(rune_app, ["consent", "unknown-rune"])
        assert result.exit_code != 0
        assert "not installed" in result.stdout


def test_rune_consent_all() -> None:
    mock_items = [
        {"name": "pollinations-realm"},
        {"name": "approval-rune"},
    ]
    with (
        patch("mvge.cli.commands.rune.list_installed_runes", return_value=mock_items),
        patch(
            "mvge.cli.commands.rune.consent_rune",
            side_effect=[["realm", "hook"], ["hook"]],
        ) as mock_consent,
    ):
        result = runner.invoke(rune_app, ["consent", "--all"])
        assert result.exit_code == 0
        assert "Consented to 'pollinations-realm'" in result.stdout
        assert "Consented to 'approval-rune'" in result.stdout
        assert mock_consent.call_count == 2


def test_rune_consent_needs_name_or_all() -> None:
    result = runner.invoke(rune_app, ["consent"])
    assert result.exit_code != 0


def test_rune_uninstall_success() -> None:
    with patch("mvge.cli.commands.rune.uninstall_rune", return_value=True):
        result = runner.invoke(rune_app, ["uninstall", "openrouter-realm"])
        assert result.exit_code == 0
        assert "Successfully uninstalled rune 'openrouter-realm'" in result.stdout


def test_rune_uninstall_not_installed() -> None:
    with patch("mvge.cli.commands.rune.uninstall_rune", return_value=False):
        result = runner.invoke(rune_app, ["uninstall", "unknown-rune"])
        assert result.exit_code != 0
        assert "not installed" in result.stdout


def test_rune_list_empty() -> None:
    with patch("mvge.cli.commands.rune.list_installed_runes", return_value=[]):
        result = runner.invoke(rune_app, ["list"])
        assert result.exit_code == 0
        assert "No extension runes installed" in result.stdout


def test_rune_list_empty_names_resolved_dir(tmp_path: Path) -> None:
    """The empty message must name the directory actually searched, not
    a hardcoded ~/.agents/extensions, or a relocated global dir
    misleads the user about where runes would be found."""
    relocated = tmp_path / "relocated" / "extensions"
    with (
        patch("mvge.cli.commands.rune.list_installed_runes", return_value=[]),
        patch("mvge.cli.commands.rune.extensions_dir", return_value=relocated),
    ):
        result = runner.invoke(rune_app, ["list"])
    assert result.exit_code == 0
    # Rich wraps long paths in the narrow test console; unwrap first.
    assert str(relocated) in result.stdout.replace("\n", "")
    assert "~/.agents/extensions" not in result.stdout


def test_rune_list_with_items() -> None:
    mock_items = [
        {
            "name": "openrouter-realm",
            "version": "0.1.0",
            "description": "Official OpenRouter provider realm",
            "types": ["RealmProvider"],
        }
    ]
    with patch("mvge.cli.commands.rune.list_installed_runes", return_value=mock_items):
        result = runner.invoke(rune_app, ["list"])
        assert result.exit_code == 0
        assert "openrouter-realm" in result.stdout
        assert "0.1.0" in result.stdout
        assert "RealmProvider" in result.stdout
        assert "Official OpenRouter" in result.stdout


def test_rune_list_error() -> None:
    with patch(
        "mvge.cli.commands.rune.list_installed_runes",
        side_effect=RuntimeError("Corrupt manifest"),
    ):
        result = runner.invoke(rune_app, ["list"])
        assert result.exit_code == 1
        assert "Corrupt manifest" in result.stdout
