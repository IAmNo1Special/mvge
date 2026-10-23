from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from mvgeos_agent.installer import (
    DEFAULT_PROFILES,
    ProfileInstallResult,
    fetch_marketplace_profiles,
    install_profile,
)


def test_default_profiles_definitions() -> None:
    assert "minimal" in DEFAULT_PROFILES
    assert "coding" in DEFAULT_PROFILES
    assert "autonomous" in DEFAULT_PROFILES

    minimal = DEFAULT_PROFILES["minimal"]
    assert minimal["mvges"] == ["coding_mvge"]
    assert "openrouter-realm" in minimal["runes"]
    assert "opencode-realm" in minimal["runes"]
    assert "approval-rune" in minimal["runes"]
    assert "steering-bridge" in minimal["runes"]

    coding = DEFAULT_PROFILES["coding"]
    assert set(minimal["runes"]).issubset(set(coding["runes"]))
    assert "seeker" in coding["runes"]
    assert "skills-bridge" in coding["runes"]
    assert "checkpoint-bridge" in coding["runes"]
    assert "lsp-bridge" in coding["runes"]
    assert "submvge-bridge" in coding["runes"]

    autonomous = DEFAULT_PROFILES["autonomous"]
    assert set(coding["runes"]).issubset(set(autonomous["runes"]))
    assert "okf-bridge" in autonomous["runes"]
    assert "adr-bridge" in autonomous["runes"]
    assert "codeact-rune" in autonomous["runes"]
    assert "eval-bridge" in autonomous["runes"]


def test_fetch_marketplace_profiles_success() -> None:
    custom_profiles = {
        "custom": {
            "name": "custom",
            "mvges": ["custom_mvge"],
            "runes": ["custom-rune"],
        }
    }
    with patch(
        "mvgeos_agent.installer.fetch_catalog",
        return_value={"profiles": custom_profiles},
    ):
        profiles = fetch_marketplace_profiles("https://example.com/index.json")
        assert "custom" in profiles
        assert profiles["custom"]["mvges"] == ["custom_mvge"]


def test_fetch_marketplace_profiles_fallback_on_error() -> None:
    with patch(
        "mvgeos_agent.installer.fetch_catalog",
        side_effect=RuntimeError("Network error"),
    ):
        profiles = fetch_marketplace_profiles("https://example.com/index.json")
        assert profiles == DEFAULT_PROFILES


def test_install_profile_unknown_profile_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown profile 'unknown_xyz'"):
        install_profile(
            "unknown_xyz",
            target_agents_dir=tmp_path / "agents",
            target_extensions_dir=tmp_path / "extensions",
        )


def test_install_profile_success(tmp_path: Path) -> None:
    agents_dir = tmp_path / "agents"
    extensions_dir = tmp_path / "extensions"
    agents_dir.mkdir(parents=True)
    extensions_dir.mkdir(parents=True)

    with (
        patch("mvgeos_agent.installer.install_mvge") as mock_install_mvge,
        patch("mvgeos_agent.installer.install_rune") as mock_install_rune,
    ):
        result: ProfileInstallResult = install_profile(
            "minimal",
            target_agents_dir=agents_dir,
            target_extensions_dir=extensions_dir,
            confirm_python_deps=False,
        )

        assert result.profile == "minimal"
        assert result.installed_mvges == ["coding_mvge"]
        assert len(result.installed_runes) == 3
        assert result.skipped_mvges == []
        assert result.skipped_runes == []

        mock_install_mvge.assert_called_once_with(
            "coding_mvge",
            target_dir=agents_dir,
            marketplace_url="https://raw.githubusercontent.com/IAmNo1Special/mvgeos-marketplace/main/index.json",
            confirm_python_deps=False,
        )
        assert mock_install_rune.call_count == 3


def test_install_profile_skip_already_installed(tmp_path: Path) -> None:
    agents_dir = tmp_path / "agents"
    extensions_dir = tmp_path / "extensions"
    # Pre-create coding_mvge and approval-rune
    (agents_dir / "coding_mvge").mkdir(parents=True)
    (extensions_dir / "approval-rune").mkdir(parents=True)

    with (
        patch("mvgeos_agent.installer.install_mvge") as mock_install_mvge,
        patch("mvgeos_agent.installer.install_rune") as mock_install_rune,
    ):
        result = install_profile(
            "minimal",
            target_agents_dir=agents_dir,
            target_extensions_dir=extensions_dir,
            force=False,
        )

        assert result.skipped_mvges == ["coding_mvge"]
        assert result.installed_mvges == []
        assert "approval-rune" in result.skipped_runes
        assert "opencode-realm" in result.installed_runes
        assert "steering-bridge" in result.installed_runes

        mock_install_mvge.assert_not_called()
        assert mock_install_rune.call_count == 2


def test_install_profile_force_reinstalls(tmp_path: Path) -> None:
    agents_dir = tmp_path / "agents"
    extensions_dir = tmp_path / "extensions"
    (agents_dir / "coding_mvge").mkdir(parents=True)
    (extensions_dir / "approval-rune").mkdir(parents=True)

    with (
        patch("mvgeos_agent.installer.install_mvge") as mock_install_mvge,
        patch("mvgeos_agent.installer.install_rune") as mock_install_rune,
    ):
        result = install_profile(
            "minimal",
            target_agents_dir=agents_dir,
            target_extensions_dir=extensions_dir,
            force=True,
        )

        assert result.skipped_mvges == []
        assert result.skipped_runes == []
        assert result.installed_mvges == ["coding_mvge"]
        assert len(result.installed_runes) == 3

        mock_install_mvge.assert_called_once()
        assert mock_install_rune.call_count == 3
