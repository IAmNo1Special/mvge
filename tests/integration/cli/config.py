from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from mvge.cli.commands.config import config_app
from mvge.cli.main import app
from mvge.core.constants import DEFAULT_AGENT_NAME
from mvge.provider.model_registry import CatalogRefresh, RefreshStatus

runner = CliRunner()


def test_config_no_subcommand_prints_help() -> None:
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0
    assert "Configuration management" in result.stdout
    assert "Commands" in result.stdout
    assert "Missing command" not in result.output


class TestConfigCommands:
    def test_config_app_exists(self) -> None:
        assert config_app is not None

    @patch("mvge.cli.commands.config.ConfigManager")
    def test_config_show(self, mock_manager_cls: MagicMock) -> None:
        runner = CliRunner()
        mock_mgr = MagicMock()
        mock_mgr.load.return_value = {
            "model": MagicMock(value="test-model", layer=MagicMock(value="agent")),
        }
        mock_manager_cls.return_value = mock_mgr

        result = runner.invoke(config_app, ["show", "--agent-name", DEFAULT_AGENT_NAME])
        assert result.exit_code == 0
        assert "test-model" in result.stdout
        mock_manager_cls.assert_called_once_with(agent_name=DEFAULT_AGENT_NAME)

    @patch("mvge.cli.commands.config.ConfigManager")
    def test_config_show_default_agent(self, mock_manager_cls: MagicMock) -> None:
        runner = CliRunner()
        mock_mgr = MagicMock()
        mock_mgr.load.return_value = {}
        mock_manager_cls.return_value = mock_mgr

        result = runner.invoke(config_app, ["show"])
        assert result.exit_code == 0
        mock_manager_cls.assert_called_once_with(agent_name="default-mvge")

    @patch("mvge.cli.commands.config.ConfigManager")
    def test_config_set(self, mock_manager_cls: MagicMock) -> None:
        runner = CliRunner()
        mock_mgr = MagicMock()
        mock_manager_cls.return_value = mock_mgr

        result = runner.invoke(
            config_app,
            ["set", "model", "new-model", "--agent-name", DEFAULT_AGENT_NAME],
        )
        assert result.exit_code == 0
        assert "Set model = new-model" in result.stdout
        mock_mgr.set.assert_called_once_with("model", "new-model")

    @patch("mvge.cli.commands.config.ConfigManager")
    def test_config_set_json_value(self, mock_manager_cls: MagicMock) -> None:
        runner = CliRunner()
        mock_mgr = MagicMock()
        mock_manager_cls.return_value = mock_mgr

        result = runner.invoke(
            config_app,
            [
                "set",
                "spells_enabled",
                '["bash", "read"]',
                "--agent-name",
                DEFAULT_AGENT_NAME,
            ],
        )
        assert result.exit_code == 0
        mock_mgr.set.assert_called_once_with("spells_enabled", ["bash", "read"])

    @patch("mvge.cli.commands.config.ConfigManager")
    def test_config_set_invalid_temperature(self, mock_manager_cls: MagicMock) -> None:
        runner = CliRunner()
        mock_mgr = MagicMock()
        mock_mgr.set.side_effect = ValueError(
            "Invalid temperature value: expected float"
        )
        mock_manager_cls.return_value = mock_mgr

        result = runner.invoke(
            config_app,
            ["set", "temperature", "invalid", "--agent-name", DEFAULT_AGENT_NAME],
        )
        assert result.exit_code == 1
        assert "Invalid temperature value: expected float" in result.output

    @patch("mvge.cli.commands.config.ConfigManager")
    def test_config_get(self, mock_manager_cls: MagicMock) -> None:
        runner = CliRunner()
        mock_mgr = MagicMock()
        mock_val = MagicMock()
        mock_val.value = "test-model"
        mock_val.layer = MagicMock(value="agent")
        mock_mgr.get.return_value = mock_val
        mock_manager_cls.return_value = mock_mgr

        result = runner.invoke(
            config_app, ["get", "model", "--agent-name", DEFAULT_AGENT_NAME]
        )
        assert result.exit_code == 0
        assert "test-model" in result.stdout

    @patch("mvge.cli.commands.config.ConfigManager")
    def test_config_reset(self, mock_manager_cls: MagicMock) -> None:
        runner = CliRunner()
        mock_mgr = MagicMock()
        mock_manager_cls.return_value = mock_mgr

        result = runner.invoke(
            config_app, ["reset", "--agent-name", DEFAULT_AGENT_NAME]
        )
        assert result.exit_code == 0
        assert "Configuration reset to defaults" in result.stdout
        mock_mgr.reset.assert_called_once()

    @patch("mvge.cli.commands.config.ConfigManager")
    def test_config_path(self, mock_manager_cls: MagicMock) -> None:
        runner = CliRunner()
        mock_mgr = MagicMock()
        mock_mgr.agent_config_path = Path(
            f"/home/user/.agents/agents/{DEFAULT_AGENT_NAME}/config.json"
        )
        mock_manager_cls.return_value = mock_mgr

        result = runner.invoke(config_app, ["path", "--agent-name", DEFAULT_AGENT_NAME])
        assert result.exit_code == 0
        assert DEFAULT_AGENT_NAME in result.stdout
        assert "config.json" in result.stdout


class TestConfigRefreshModels:
    @contextmanager
    def _patched_refresh(self, refresh: CatalogRefresh) -> Iterator[AsyncMock]:
        mock_refresh = AsyncMock(return_value=refresh)
        with patch("mvge.cli.commands.config.refresh_catalog", new=mock_refresh):
            yield mock_refresh

    def test_reports_added_removed_and_no_longer_free(self) -> None:
        refresh = CatalogRefresh(
            status=RefreshStatus.REFRESHED,
            total=412,
            added=("new/free:free",),
            removed=("old/withdrawn",),
            no_longer_free=("nvidia/now-paid:free",),
            cache_path=Path("/home/user/.agents/models.json"),
        )
        with self._patched_refresh(refresh) as mock_refresh:
            result = runner.invoke(config_app, ["refresh-models"])

        assert result.exit_code == 0
        assert "412" in result.stdout
        assert "new/free:free" in result.stdout
        assert "old/withdrawn" in result.stdout
        assert "nvidia/now-paid:free" in result.stdout
        assert str(Path("/home/user/.agents/models.json")) in result.stdout
        mock_refresh.assert_awaited_once_with(force_refresh=False)

    def test_network_failure_is_not_reported_as_zero_updates(self) -> None:
        refresh = CatalogRefresh(
            status=RefreshStatus.UNREACHABLE,
            total=0,
            added=(),
            removed=(),
            no_longer_free=(),
            cache_path=Path("/home/user/.agents/models.json"),
        )
        with self._patched_refresh(refresh):
            result = runner.invoke(config_app, ["refresh-models"])

        assert result.exit_code == 1
        assert "could not reach" in result.output.lower()
        assert "shipped catalog" in result.output.lower()
        assert "0 models" not in result.output

    def test_fresh_cache_is_not_a_refresh(self) -> None:
        refresh = CatalogRefresh(
            status=RefreshStatus.CACHE_FRESH,
            total=412,
            added=(),
            removed=(),
            no_longer_free=(),
            cache_path=Path("/home/user/.agents/models.json"),
        )
        with self._patched_refresh(refresh) as mock_refresh:
            result = runner.invoke(config_app, ["refresh-models"])

        assert result.exit_code == 0
        assert "--force" in result.stdout
        mock_refresh.assert_awaited_once_with(force_refresh=False)

    def test_force_bypasses_the_cache_ttl(self) -> None:
        refresh = CatalogRefresh(
            status=RefreshStatus.REFRESHED,
            total=412,
            added=(),
            removed=(),
            no_longer_free=(),
            cache_path=Path("/home/user/.agents/models.json"),
        )
        with self._patched_refresh(refresh) as mock_refresh:
            result = runner.invoke(config_app, ["refresh-models", "--force"])

        assert result.exit_code == 0
        mock_refresh.assert_awaited_once_with(force_refresh=True)
