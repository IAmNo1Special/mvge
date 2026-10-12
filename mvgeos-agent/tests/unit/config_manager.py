from __future__ import annotations

import json
from pathlib import Path

import pytest
from mvgeos_core.constants import DEFAULT_AGENT_NAME

from mvgeos_agent.agent_name import is_known_agent, validate_agent_name
from mvgeos_agent.config_manager import ConfigLayer, ConfigManager


def _make_mgr(tmp_path: Path, agent_name: str = DEFAULT_AGENT_NAME) -> ConfigManager:
    """Create a ConfigManager with isolated paths for testing."""
    return ConfigManager(
        agent_name=agent_name,
        project_dir=tmp_path,
        agent_config_base=tmp_path / "agent_configs",
    )


class TestConfigManagerDefaults:
    def test_defaults_are_used_when_no_files_exist(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        merged = mgr.load()
        assert merged["model"].value == ConfigManager.DEFAULTS["model"]
        assert merged["max_tokens"].value == ConfigManager.DEFAULTS["max_tokens"]
        assert merged["model"].layer == ConfigLayer.DEFAULTS

    def test_defaults_can_be_overridden(self, tmp_path: Path) -> None:
        custom_defaults = {"model": "custom-model", "max_tokens": 100}
        mgr = ConfigManager(
            agent_name=DEFAULT_AGENT_NAME,
            project_dir=tmp_path,
            defaults=custom_defaults,
            agent_config_base=tmp_path / "agent_configs",
        )
        merged = mgr.load()
        assert merged["model"].value == "custom-model"


class TestConfigManagerProvenance:
    def test_provenance_defaults(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        merged = mgr.load()
        for key in ConfigManager.DEFAULTS:
            assert merged[key].layer == ConfigLayer.DEFAULTS

    def test_provenance_agent_scope(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        mgr.agent_config_path.parent.mkdir(parents=True, exist_ok=True)
        mgr.agent_config_path.write_text(
            json.dumps({"model": "agent-model"}), encoding="utf-8"
        )
        merged = mgr.load()
        assert merged["model"].value == "agent-model"
        assert merged["model"].layer == ConfigLayer.AGENT

    def test_provenance_project(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        project_dir = tmp_path / ".agents"
        project_dir.mkdir(parents=True)
        project_dir.joinpath("config.json").write_text(
            json.dumps({"model": "project-model"}), encoding="utf-8"
        )
        merged = mgr.load()
        assert merged["model"].value == "project-model"
        assert merged["model"].layer == ConfigLayer.PROJECT

    def test_provenance_constructor_overrides(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        mgr_with_overrides = mgr.with_overrides(model="override-model")
        merged = mgr_with_overrides.load()
        assert merged["model"].value == "override-model"
        assert merged["model"].layer == ConfigLayer.CONSTRUCTOR


class TestConfigManagerPrecedence:
    def test_agent_overrides_defaults(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        mgr.agent_config_path.parent.mkdir(parents=True, exist_ok=True)
        mgr.agent_config_path.write_text(
            json.dumps({"model": "agent-model"}), encoding="utf-8"
        )
        merged = mgr.load()
        assert merged["model"].value == "agent-model"
        assert merged["model"].layer == ConfigLayer.AGENT

    def test_project_overrides_agent(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        mgr.agent_config_path.parent.mkdir(parents=True, exist_ok=True)
        mgr.agent_config_path.write_text(
            json.dumps({"model": "agent-model"}), encoding="utf-8"
        )
        project_dir = tmp_path / ".agents"
        project_dir.mkdir(parents=True)
        project_dir.joinpath("config.json").write_text(
            json.dumps({"model": "project-model"}), encoding="utf-8"
        )
        merged = mgr.load()
        assert merged["model"].value == "project-model"
        assert merged["model"].layer == ConfigLayer.PROJECT

    def test_constructor_overrides_all(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        mgr.agent_config_path.parent.mkdir(parents=True, exist_ok=True)
        mgr.agent_config_path.write_text(
            json.dumps({"model": "agent-model"}), encoding="utf-8"
        )
        project_dir = tmp_path / ".agents"
        project_dir.mkdir(parents=True)
        project_dir.joinpath("config.json").write_text(
            json.dumps({"model": "project-model"}), encoding="utf-8"
        )
        mgr_with_overrides = mgr.with_overrides(model="constructor-model")
        merged = mgr_with_overrides.load()
        assert merged["model"].value == "constructor-model"
        assert merged["model"].layer == ConfigLayer.CONSTRUCTOR

    def test_mixed_provenance(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        mgr.agent_config_path.parent.mkdir(parents=True, exist_ok=True)
        mgr.agent_config_path.write_text(
            json.dumps({"model": "agent-model"}), encoding="utf-8"
        )
        merged = mgr.load()
        # model comes from agent, max_tokens from defaults
        assert merged["model"].layer == ConfigLayer.AGENT
        assert merged["max_tokens"].layer == ConfigLayer.DEFAULTS

    def test_deep_merge_nested_dicts(self, tmp_path: Path) -> None:
        """Deep merge preserves sibling keys from lower layers."""
        mgr = ConfigManager(
            agent_name=DEFAULT_AGENT_NAME,
            project_dir=tmp_path,
            defaults={
                "settings": {"a": 1, "b": 2},
                "model": "default-model",
            },
            agent_config_base=tmp_path / "agent_configs",
        )
        mgr.agent_config_path.parent.mkdir(parents=True, exist_ok=True)
        mgr.agent_config_path.write_text(
            json.dumps({"settings": {"b": 99, "c": 3}}), encoding="utf-8"
        )
        merged = mgr.load()
        # Deep merge: agent's "settings" overrides leaf keys only
        assert merged["settings"].value == {"a": 1, "b": 99, "c": 3}


class TestConfigManagerAgentScope:
    def test_ensure_agent_config_seeds_defaults(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        result = mgr.ensure_agent_config()
        assert result.exists()
        data = json.loads(result.read_text(encoding="utf-8"))
        assert data == ConfigManager.DEFAULTS

    def test_ensure_agent_config_idempotent(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        mgr.ensure_agent_config()
        mgr.agent_config_path.write_text(
            json.dumps({"model": "custom"}), encoding="utf-8"
        )
        mgr.ensure_agent_config()
        data = json.loads(mgr.agent_config_path.read_text(encoding="utf-8"))
        assert data["model"] == "custom"

    def test_set_writes_to_agent_scope(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        mgr.set("model", "new-model")
        data = json.loads(mgr.agent_config_path.read_text(encoding="utf-8"))
        assert data["model"] == "new-model"

    def test_reset_clears_agent_scope(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        mgr.set("model", "custom-model")
        mgr.reset()
        data = json.loads(mgr.agent_config_path.read_text(encoding="utf-8"))
        assert data == ConfigManager.DEFAULTS

    def test_get_single_value_with_provenance(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        val = mgr.get("model")
        assert val.value == ConfigManager.DEFAULTS["model"]
        assert val.layer == ConfigLayer.DEFAULTS


class TestConfigManagerConfigFileLocations:
    def test_agent_config_path_uses_agent_name(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path, agent_name="my-agent")
        assert "my-agent" in str(mgr.agent_config_path)
        assert str(mgr.agent_config_path).endswith("config.json")

    def test_project_config_path_in_project(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        assert str(mgr.project_config_path) == str(tmp_path / ".agents" / "config.json")


class TestConfigManagerProjectScope:
    def test_set_project_writes_to_project_file(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        mgr.set_project("temperature", 0.9)
        assert mgr.project_config_path.exists()
        data = json.loads(mgr.project_config_path.read_text(encoding="utf-8"))
        assert data["temperature"] == 0.9

    def test_save_project_config_saves_multiple_keys(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        mgr.save_project_config(
            {
                "temperature": 0.5,
                "max_tokens": 2048,
                "contemplation_level": "high",
                "spells_enabled": ["bash", "read"],
                "project_name": "test-project",
            }
        )
        data = mgr.load_project_config()
        assert data["temperature"] == 0.5
        assert data["max_tokens"] == 2048
        assert data["contemplation_level"] == "high"
        assert data["spells_enabled"] == ["bash", "read"]
        assert data["project_name"] == "test-project"

    def test_save_project_config_preserves_existing_keys(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        mgr.set_project("model", "custom-model")
        mgr.save_project_config({"temperature": 0.3})
        data = mgr.load_project_config()
        assert data["model"] == "custom-model"
        assert data["temperature"] == 0.3

    def test_project_name_validation(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        import pytest

        with pytest.raises(
            ValueError, match="Invalid project_name value: expected string"
        ):
            mgr.validate_value("project_name", 123)
        assert mgr.validate_value("project_name", "my-project") == "my-project"


class TestConfigManagerValidation:
    def test_set_invalid_temperature_type(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        import pytest

        with pytest.raises(
            ValueError, match="Invalid temperature value: expected float"
        ):
            mgr.set("temperature", "invalid")

    def test_set_invalid_temperature_bool(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        import pytest

        with pytest.raises(
            ValueError, match="Invalid temperature value: expected float"
        ):
            mgr.set("temperature", True)

    def test_set_invalid_temperature_range(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        import pytest

        with pytest.raises(
            ValueError,
            match="Invalid temperature value: expected float between 0.0 and 2.0",
        ):
            mgr.set("temperature", 3.5)

    def test_set_invalid_max_tokens_type(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        import pytest

        with pytest.raises(ValueError, match="Invalid max_tokens value: expected int"):
            mgr.set("max_tokens", "invalid")

    def test_set_invalid_max_tokens_bool(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        import pytest

        with pytest.raises(ValueError, match="Invalid max_tokens value: expected int"):
            mgr.set("max_tokens", True)

    def test_set_invalid_max_tokens_negative(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        import pytest

        with pytest.raises(
            ValueError, match="Invalid max_tokens value: expected positive integer"
        ):
            mgr.set("max_tokens", -10)

    def test_set_unknown_key(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        import pytest

        with pytest.raises(
            ValueError, match="Unknown configuration key: 'invalid_key'"
        ):
            mgr.set("invalid_key", "foo")

    def test_set_valid_values(self, tmp_path: Path) -> None:
        mgr = _make_mgr(tmp_path)
        mgr.set("temperature", 0.8)
        assert (
            json.loads(mgr.agent_config_path.read_text(encoding="utf-8"))["temperature"]
            == 0.8
        )
        mgr.set("max_tokens", 8192)
        assert (
            json.loads(mgr.agent_config_path.read_text(encoding="utf-8"))["max_tokens"]
            == 8192
        )


class TestMvgeConfigResilience:
    def test_agent_startup_handles_invalid_config_types(self, tmp_path: Path) -> None:
        from mvgeos_agent.environment import MvgeEnvironment
        from mvgeos_agent.mvge import Mvge

        custom_defaults = {
            "temperature": "invalid",
            "max_tokens": None,
            "contemplation_level": "ultra_high",
            "contemplation_budget": [1, 2],
        }
        mgr = ConfigManager(
            agent_name=DEFAULT_AGENT_NAME,
            project_dir=tmp_path,
            defaults=custom_defaults,
            agent_config_base=tmp_path / "agent_configs",
        )
        env = MvgeEnvironment.resolve(
            DEFAULT_AGENT_NAME,
            project_dir=tmp_path,
            config_manager=mgr,
        )
        agent = Mvge(api_key="test-key", environment=env)
        assert agent._temperature == 0.7
        assert agent._max_tokens == 4096
        assert agent._contemplation_level == "medium"
        assert agent._contemplation_budget is None


class TestAgentValidation:
    def test_default_agent_is_known(self) -> None:
        assert is_known_agent(DEFAULT_AGENT_NAME) is True

    def test_validate_default_agent_succeeds(self) -> None:
        validate_agent_name(DEFAULT_AGENT_NAME)

    def test_invalid_agent_name_syntax_raises(self) -> None:
        for invalid_name in ("", "   ", "../invalid", "invalid/path", "bad name!"):
            assert is_known_agent(invalid_name) is False
            with pytest.raises(ValueError, match="Invalid agent name"):
                validate_agent_name(invalid_name)

    def test_nonexistent_agent_raises_value_error(self, tmp_path: Path) -> None:
        fake_base = tmp_path / "agents"
        fake_base.mkdir()
        assert is_known_agent("nonexistent", agent_config_base=fake_base) is False
        with pytest.raises(ValueError, match="Unknown agent 'nonexistent'"):
            validate_agent_name("nonexistent", agent_config_base=fake_base)

    def test_existing_agent_directory_is_known(self, tmp_path: Path) -> None:
        fake_base = tmp_path / "agents"
        fake_base.mkdir()
        custom_agent_dir = fake_base / "custom-agent"
        custom_agent_dir.mkdir()
        assert is_known_agent("custom-agent", agent_config_base=fake_base) is True
        validate_agent_name("custom-agent", agent_config_base=fake_base)

    def test_existing_agent_config_file_is_known(self, tmp_path: Path) -> None:
        fake_base = tmp_path / "agents"
        custom_agent_dir = fake_base / "custom-agent-2"
        custom_agent_dir.mkdir(parents=True)
        (custom_agent_dir / "config.json").write_text("{}", encoding="utf-8")
        assert is_known_agent("custom-agent-2", agent_config_base=fake_base) is True
        validate_agent_name("custom-agent-2", agent_config_base=fake_base)

    def test_is_known_agent_with_project_dir(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / ".agents" / "agents" / "my-agent"
        agent_dir.mkdir(parents=True)
        assert is_known_agent("my-agent", project_dir=tmp_path) is True
        assert is_known_agent("unknown", project_dir=tmp_path) is False

    def test_allow_create_skips_existence_check(self, tmp_path: Path) -> None:
        fake_base = tmp_path / "agents"
        fake_base.mkdir()
        validate_agent_name("new-agent", agent_config_base=fake_base, allow_create=True)
        validate_agent_name("valid_agent-123", allow_create=True)
