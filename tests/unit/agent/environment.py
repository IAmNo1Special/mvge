from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mvge.agent.config_manager import ConfigLayer, ConfigManager, ConfigValue
from mvge.agent.environment import (
    APPEND_SYSTEM_MD_FILENAME,
    DEFAULT_SYSTEM_PROMPT,
    AgentConfig,
    MvgeEnvironment,
    PromptSource,
    ResolvedPrompt,
    _read_text,
    coerce_agent_config,
    ensure_config_files,
    get_environment_info,
    render_prompt,
    resolve_append_system_prompts,
    resolve_config_dir,
    resolve_system_prompt,
)
from mvge.agent.mvge import Mvge
from mvge.agent.snapshot import RuntimeSnapshot
from mvge.core.constants import DEFAULT_AGENT_NAME
from mvge.core.events import QueueMode
from mvge.core.layers import resolve_rune_paths
from mvge.runes.rune_runner import RuneRunner
from mvge.runes.types import (
    BeforeMvgeStartData,
    Diagnostic,
    DiagnosticKind,
    RuneScope,
    SigilHook,
)


def _cfg(**kwargs: Any) -> dict[str, ConfigValue]:
    return {k: ConfigValue(v, ConfigLayer.DEFAULTS) for k, v in kwargs.items()}


def _mock_runner(*, suppressed: bool = False, catalog: str = "") -> MagicMock:
    runner = MagicMock(spec=RuneRunner)
    runner.emit_chain = AsyncMock(side_effect=lambda hook, initial: initial)
    runner.is_skill_catalog_suppressed = MagicMock(return_value=suppressed)
    runner.get_skill_catalog = MagicMock(return_value=catalog)
    return runner


# ---------------------------------------------------------------------------
# Types and Enums
# ---------------------------------------------------------------------------


class TestPromptSource:
    def test_values_are_descriptive_strings(self) -> None:
        assert PromptSource.CUSTOM_LITERAL == "custom_literal"
        assert PromptSource.CUSTOM_PATH == "custom_path"
        assert PromptSource.PROJECT_MD == "project_md"
        assert PromptSource.AGENT_MD == "agent_md"
        assert PromptSource.BUILTIN == "builtin"


class TestResolvedDataClasses:
    def test_resolved_prompt_is_frozen(self) -> None:
        resolved = ResolvedPrompt(text="hi", source=PromptSource.BUILTIN)
        with pytest.raises(AttributeError):
            resolved.text = "bye"  # type: ignore[misc]

    def test_resolved_prompt_path_defaults_to_none(self) -> None:
        resolved = ResolvedPrompt(text="hi", source=PromptSource.BUILTIN)
        assert resolved.path is None


# ---------------------------------------------------------------------------
# Config Coercion
# ---------------------------------------------------------------------------


class TestAgentConfigCoercion:
    def test_empty_config_yields_no_model(self) -> None:
        # Realm-first (ADR-0028): an empty config names no Realm and no
        # model, so it resolves to no model -- never to one Realm's
        # model for every Summoner.
        cfg = coerce_agent_config({})
        assert cfg.model_id == ""
        assert cfg.realm_preference == ""
        assert cfg.temperature == 0.7
        assert cfg.max_tokens == 4096
        assert cfg.contemplation_level == "medium"
        assert cfg.contemplation_budget is None
        assert cfg.exclude_contemplation is False
        assert cfg.queue_mode is QueueMode.ONE_AT_A_TIME
        assert cfg.spell_names is None
        assert cfg.runes_paths == resolve_rune_paths(DEFAULT_AGENT_NAME, None)

    def test_opencode_realm_preference_yields_opencode_default(self) -> None:
        cfg = coerce_agent_config(_cfg(realm="opencode"))
        assert cfg.model_id == "opencode/space-bunny-free"
        assert cfg.realm_preference == "opencode"

    def test_openrouter_realm_preference_yields_openrouter_default(
        self,
    ) -> None:
        cfg = coerce_agent_config(_cfg(realm="openrouter"))
        assert cfg.model_id == "openrouter/free"
        assert cfg.realm_preference == "openrouter"

    def test_explicit_model_wins_over_realm_preference(self) -> None:
        cfg = coerce_agent_config(_cfg(model="opencode/glm-5", realm="openrouter"))
        assert cfg.model_id == "opencode/glm-5"

    def test_unknown_realm_preference_is_an_error_not_a_fallback(
        self,
    ) -> None:
        with pytest.raises(ValueError, match="has no default model"):
            coerce_agent_config(_cfg(realm="groq"))

    def test_agent_config_is_frozen(self) -> None:
        cfg = coerce_agent_config({})
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.temperature = 0.1  # type: ignore[misc]

    def test_int_temperature_coerced_to_float(self) -> None:
        cfg = coerce_agent_config(_cfg(temperature=0))
        assert cfg.temperature == 0.0
        assert isinstance(cfg.temperature, float)

    def test_numeric_string_max_tokens_coerced(self) -> None:
        cfg = coerce_agent_config(_cfg(max_tokens="2048"))
        assert cfg.max_tokens == 2048

    def test_float_max_tokens_coerced(self) -> None:
        cfg = coerce_agent_config(_cfg(max_tokens=8192.0))
        assert cfg.max_tokens == 8192

    def test_string_budget_coerced(self) -> None:
        cfg = coerce_agent_config(_cfg(contemplation_budget="512"))
        assert cfg.contemplation_budget == 512

    def test_truthy_exclude_contemplation(self) -> None:
        cfg = coerce_agent_config(_cfg(exclude_contemplation=1))
        assert cfg.exclude_contemplation is True

    def test_non_string_model_coerced(self) -> None:
        cfg = coerce_agent_config(_cfg(model=12345))
        assert cfg.model_id == "12345"

    def test_queue_mode_string_coerced(self) -> None:
        cfg = coerce_agent_config(_cfg(queue_mode="all"))
        assert cfg.queue_mode is QueueMode.ALL

    def test_queue_mode_enum_passes_through(self) -> None:
        cfg = coerce_agent_config(_cfg(queue_mode=QueueMode.ALL))
        assert cfg.queue_mode is QueueMode.ALL

    def test_boolean_temperature_falls_back(self) -> None:
        cfg = coerce_agent_config(_cfg(temperature=True))
        assert cfg.temperature == 0.7

    def test_garbage_temperature_falls_back(self) -> None:
        cfg = coerce_agent_config(_cfg(temperature="invalid"))
        assert cfg.temperature == 0.7

    def test_boolean_max_tokens_falls_back(self) -> None:
        cfg = coerce_agent_config(_cfg(max_tokens=True))
        assert cfg.max_tokens == 4096

    def test_none_max_tokens_falls_back(self) -> None:
        cfg = coerce_agent_config(_cfg(max_tokens=None))
        assert cfg.max_tokens == 4096

    def test_unknown_contemplation_level_falls_back(self) -> None:
        cfg = coerce_agent_config(_cfg(contemplation_level="ultra_high"))
        assert cfg.contemplation_level == "medium"

    def test_list_budget_falls_back(self) -> None:
        cfg = coerce_agent_config(_cfg(contemplation_budget=[1, 2]))
        assert cfg.contemplation_budget is None

    def test_boolean_budget_falls_back(self) -> None:
        cfg = coerce_agent_config(_cfg(contemplation_budget=True))
        assert cfg.contemplation_budget is None

    def test_invalid_queue_mode_falls_back(self) -> None:
        cfg = coerce_agent_config(_cfg(queue_mode="bogus"))
        assert cfg.queue_mode is QueueMode.ONE_AT_A_TIME

    def test_spell_names_non_empty_preserved(self) -> None:
        cfg = coerce_agent_config(_cfg(spells_enabled=["bash", "read"]))
        assert cfg.spell_names == ["bash", "read"]

    def test_spell_names_input_list_copied(self) -> None:
        spells = ["bash"]
        cfg = coerce_agent_config(_cfg(spells_enabled=spells))
        spells.append("write")
        assert cfg.spell_names == ["bash"]

    def test_spell_names_empty_normalized_to_none(self) -> None:
        cfg = coerce_agent_config(_cfg(spells_enabled=[]))
        assert cfg.spell_names is None

    def test_spell_names_missing_key_yields_none(self) -> None:
        cfg = coerce_agent_config({})
        assert cfg.spell_names is None

    def test_rune_paths_constructor_wins_over_config(self) -> None:
        """A constructor ``rune_paths`` replaces the *configured* one only.

        It never replaces the layering. The user and agent layers are the
        stack, not a default to be swapped out: ADR-0014 decision 6 is that
        naming a directory extends the stack, and does not narrow it away as
        a side effect of being set.
        """
        cfg = coerce_agent_config(
            _cfg(rune_paths=["/from/config"]),
            runes_paths=["/from/ctor"],
        )

        assert cfg.runes_paths == [
            *resolve_rune_paths(DEFAULT_AGENT_NAME),
            Path("/from/ctor"),
        ]
        assert Path("/from/config") not in cfg.runes_paths

    def test_rune_paths_config_used_without_constructor(self) -> None:
        """A configured ``rune_paths`` extends the stack.

        Regression: this used to yield exactly ``["/from/config"]``. Naming
        one directory silently dropped the user and agent layers, so the
        layering was replaced as a side effect of being configured at all.
        """
        cfg = coerce_agent_config(_cfg(rune_paths=["/from/config"]))

        assert cfg.runes_paths == [
            *resolve_rune_paths(DEFAULT_AGENT_NAME),
            Path("/from/config"),
        ]

    def test_rune_paths_config_extends_rather_than_replaces(self) -> None:
        """The stack survives a configured ``rune_paths`` intact.

        Stated directly, because the whole point is what is *still* there
        rather than what was added.
        """
        stack = resolve_rune_paths(DEFAULT_AGENT_NAME)

        cfg = coerce_agent_config(_cfg(rune_paths=["/from/config"]))

        assert cfg.runes_paths[: len(stack)] == stack
        assert cfg.runes_paths[len(stack) :] == [Path("/from/config")]

    def test_rune_paths_tilde_expanded(self) -> None:
        cfg = coerce_agent_config(_cfg(rune_paths=["~/runes"]))

        assert cfg.runes_paths[-1] == Path("~/runes").expanduser()

    def test_rune_paths_default_resolves_agent_name_and_extension_dir(self) -> None:
        cfg = coerce_agent_config({}, agent_name="my-agent", extension_dir="/ext")
        assert cfg.runes_paths == resolve_rune_paths("my-agent", "/ext")

    def test_rune_paths_project_dir_anchors_project_layer(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        cfg = coerce_agent_config({}, project_dir=project)
        assert cfg.runes_paths == resolve_rune_paths(
            DEFAULT_AGENT_NAME, None, project_dir=project
        )
        assert project / ".agents" / "extensions" in cfg.runes_paths

    def test_rune_paths_project_layer_omitted_without_project_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".agents" / "extensions").mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        cfg = coerce_agent_config({})
        assert all(p.is_absolute() for p in cfg.runes_paths)


# ---------------------------------------------------------------------------
# Prompt & Guidelines Discovery Chain
# ---------------------------------------------------------------------------


class TestPromptDiscoveryPrecedence:
    def test_custom_literal_wins_over_all(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / ".agents").mkdir(parents=True)
        (project_dir / ".agents" / "SYSTEM.md").write_text(
            "Project prompt", encoding="utf-8"
        )

        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "SYSTEM.md").write_text("Agent prompt", encoding="utf-8")

        resolved = resolve_system_prompt(
            agent_name="test",
            custom="Custom literal",
            config_dir=agent_dir,
            project_dir=project_dir,
        )
        assert resolved.text == "Custom literal"
        assert resolved.source == PromptSource.CUSTOM_LITERAL
        assert resolved.path is None

    def test_custom_path_wins_over_project(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / ".agents").mkdir(parents=True)
        (project_dir / ".agents" / "SYSTEM.md").write_text(
            "Project prompt", encoding="utf-8"
        )

        custom_file = tmp_path / "custom_prompt.txt"
        custom_file.write_text("Custom path prompt", encoding="utf-8")

        resolved = resolve_system_prompt(
            agent_name="test",
            custom=str(custom_file),
            project_dir=project_dir,
        )
        assert resolved.text == "Custom path prompt"
        assert resolved.source == PromptSource.CUSTOM_PATH
        assert resolved.path == custom_file

    def test_project_wins_over_agent(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / ".agents").mkdir(parents=True)
        (project_dir / ".agents" / "SYSTEM.md").write_text(
            "Project prompt", encoding="utf-8"
        )

        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "SYSTEM.md").write_text("Agent prompt", encoding="utf-8")

        resolved = resolve_system_prompt(
            agent_name="test",
            config_dir=agent_dir,
            project_dir=project_dir,
        )
        assert resolved.text == "Project prompt"
        assert resolved.source == PromptSource.PROJECT_MD
        assert resolved.path == project_dir / ".agents" / "SYSTEM.md"

    def test_agent_wins_over_builtin(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "SYSTEM.md").write_text("Agent prompt", encoding="utf-8")

        resolved = resolve_system_prompt(
            agent_name="test",
            config_dir=agent_dir,
        )
        assert resolved.text == "Agent prompt"
        assert resolved.source == PromptSource.AGENT_MD
        assert resolved.path == agent_dir / "SYSTEM.md"

    def test_caller_system_prompt_dir(self, tmp_path: Path) -> None:
        caller_dir = tmp_path / "caller_pkg"
        sys_prompt_dir = caller_dir / "system_prompt"
        sys_prompt_dir.mkdir(parents=True)
        (sys_prompt_dir / "SYSTEM.md").write_text(
            "System prompt from subdir", encoding="utf-8"
        )

        resolved = resolve_system_prompt(
            agent_name="test",
            caller_dir=caller_dir,
        )
        assert resolved.text == "System prompt from subdir"
        assert resolved.source == PromptSource.AGENT_MD
        assert resolved.path == sys_prompt_dir / "SYSTEM.md"

    def test_builtin_when_nothing_exists(self, tmp_path: Path) -> None:
        resolved = resolve_system_prompt(
            agent_name="test",
            config_dir=tmp_path / "nonexistent",
        )
        assert resolved.text == DEFAULT_SYSTEM_PROMPT
        assert resolved.source == PromptSource.BUILTIN
        assert resolved.path is None


class TestAppendSystemDiscoveryAndPrecedence:
    def test_append_system_cascade_order(self, tmp_path: Path) -> None:
        global_dir = tmp_path / "global"
        global_dir.mkdir()
        (global_dir / APPEND_SYSTEM_MD_FILENAME).write_text(
            "Global Append Rules", encoding="utf-8"
        )

        project_dir = tmp_path / "project"
        project_agents = project_dir / ".agents"
        project_agents.mkdir(parents=True)
        (project_agents / APPEND_SYSTEM_MD_FILENAME).write_text(
            "Project Append Rules", encoding="utf-8"
        )

        caller_dir = tmp_path / "caller"
        caller_sys = caller_dir / "system_prompt"
        caller_sys.mkdir(parents=True)
        (caller_sys / APPEND_SYSTEM_MD_FILENAME).write_text(
            "Caller Append Rules", encoding="utf-8"
        )

        resolved = resolve_system_prompt(
            agent_name="test",
            global_dir=global_dir,
            project_dir=project_dir,
            caller_dir=caller_dir,
        )

        expected_order = (
            f"{DEFAULT_SYSTEM_PROMPT}\n\n"
            "Global Append Rules\n\n"
            "Project Append Rules\n\n"
            "Caller Append Rules"
        )
        assert resolved.text == expected_order

    def test_append_system_skips_empty_files(self, tmp_path: Path) -> None:
        global_dir = tmp_path / "global"
        global_dir.mkdir()
        (global_dir / APPEND_SYSTEM_MD_FILENAME).write_text("   \n", encoding="utf-8")

        project_dir = tmp_path / "project"
        project_agents = project_dir / ".agents"
        project_agents.mkdir(parents=True)
        (project_agents / APPEND_SYSTEM_MD_FILENAME).write_text(
            "Project Only", encoding="utf-8"
        )

        resolved = resolve_system_prompt(
            agent_name="test",
            global_dir=global_dir,
            project_dir=project_dir,
        )

        assert resolved.text == f"{DEFAULT_SYSTEM_PROMPT}\n\nProject Only"

    def test_append_system_caller_root_fallback(self, tmp_path: Path) -> None:
        caller_dir = tmp_path / "caller"
        caller_dir.mkdir()
        (caller_dir / APPEND_SYSTEM_MD_FILENAME).write_text(
            "Caller Direct Rules", encoding="utf-8"
        )

        resolved = resolve_system_prompt(
            agent_name="test",
            caller_dir=caller_dir,
            global_dir=tmp_path / "empty_global",
            project_dir=tmp_path / "empty_project",
        )

        assert resolved.text == f"{DEFAULT_SYSTEM_PROMPT}\n\nCaller Direct Rules"

    def test_append_system_helper_direct(self, tmp_path: Path) -> None:
        global_dir = tmp_path / "global"
        global_dir.mkdir()
        (global_dir / APPEND_SYSTEM_MD_FILENAME).write_text("G", encoding="utf-8")

        results = resolve_append_system_prompts(global_dir=global_dir)
        assert len(results) == 1
        assert results[0][0] == global_dir / APPEND_SYSTEM_MD_FILENAME
        assert results[0][1] == "G"


# ---------------------------------------------------------------------------
# Seeding and Config Directory
# ---------------------------------------------------------------------------


class TestConfigDirAndSeeding:
    def test_resolve_config_dir_defaults_to_dotagents(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        # This asserts the default location, so no override may be in play.
        monkeypatch.delenv("MVGE_GLOBAL_DIR", raising=False)

        resolved = resolve_config_dir("my-mvge")
        assert resolved == tmp_path / ".agents" / "agents" / "my-mvge"

    def test_resolve_config_dir_explicit_wins(self, tmp_path: Path) -> None:
        assert resolve_config_dir("my-mvge", tmp_path) == tmp_path

    def test_ensure_config_files_creates_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))

        config_dir = ensure_config_files("test-mvge")
        assert (config_dir / "SYSTEM.md").exists()

        system_text = (config_dir / "SYSTEM.md").read_text(encoding="utf-8")
        assert DEFAULT_SYSTEM_PROMPT in system_text

    def test_ensure_config_files_never_overwrites(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))

        config_dir = ensure_config_files("test-mvge")
        (config_dir / "SYSTEM.md").write_text("Custom prompt", encoding="utf-8")

        ensure_config_files("test-mvge")
        assert (config_dir / "SYSTEM.md").read_text(encoding="utf-8") == "Custom prompt"


# ---------------------------------------------------------------------------
# Template Rendering & Environment Info
# ---------------------------------------------------------------------------


class TestPromptRendering:
    def test_render_prompt_basic(self) -> None:
        rendered = render_prompt(
            body="Base body",
            spells=["bash", "read"],
            cwd="/test/dir",
        )
        assert "Base body" in rendered
        assert "Active spells:" in rendered
        assert "  - bash" in rendered
        assert "  - read" in rendered
        assert "Environment:" in rendered
        assert "Working Directory: /test/dir" in rendered

    def test_render_prompt_no_spells(self) -> None:
        rendered = render_prompt(
            body="Base body",
            spells=[],
        )
        assert "(none)" in rendered

    def test_render_prompt_append_text(self) -> None:
        rendered = render_prompt(
            body="Base",
            append_text="Extra footer note",
        )
        assert "Extra footer note" in rendered

    def test_get_environment_info_contains_os_and_shell(self) -> None:
        lines = get_environment_info("/custom/cwd")
        joined = "\n".join(lines)
        assert "Operating System:" in joined
        assert "Shell:" in joined
        assert "Working Directory: /custom/cwd" in joined


# ---------------------------------------------------------------------------
# MvgeEnvironment.resolve & assemble_system_prompt
# ---------------------------------------------------------------------------


class TestMvgeEnvironmentResolve:
    def test_resolve_defaults(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "agent_config"
        env = MvgeEnvironment.resolve(
            DEFAULT_AGENT_NAME, project_dir=tmp_path, config_dir=config_dir
        )

        assert env.agent_name == DEFAULT_AGENT_NAME
        assert env.model_id == ""
        assert env.temperature == 0.7
        assert env.max_tokens == 4096
        assert env.contemplation_level == "medium"
        assert env.spell_names == ConfigManager.DEFAULTS["spells_enabled"]
        assert env.resolved_prompt.source == PromptSource.BUILTIN
        assert env.config["realm"].layer == ConfigLayer.DEFAULTS
        assert env.config["realm"].value == ""

    def test_resolve_with_overrides(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "agent_config"
        overrides = {
            "model": "google/gemini-2.5-flash",
            "temperature": 0.2,
            "max_tokens": 2048,
            "contemplation_level": "high",
            "queue_mode": "all",
        }
        env = MvgeEnvironment.resolve(
            DEFAULT_AGENT_NAME,
            project_dir=tmp_path,
            config_dir=config_dir,
            overrides=overrides,
        )

        assert env.model_id == "google/gemini-2.5-flash"
        assert env.temperature == 0.2
        assert env.max_tokens == 2048
        assert env.contemplation_level == "high"
        assert env.queue_mode is QueueMode.ALL
        assert env.config["model"].layer == ConfigLayer.CONSTRUCTOR

    def test_resolve_project_prompt(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "agent_config"
        agents_dir = tmp_path / ".agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "SYSTEM.md").write_text("Custom Project System Prompt")

        env = MvgeEnvironment.resolve(
            DEFAULT_AGENT_NAME, project_dir=tmp_path, config_dir=config_dir
        )

        assert env.resolved_prompt.source == PromptSource.PROJECT_MD
        assert env.resolved_prompt.text == "Custom Project System Prompt"

    def test_resolve_is_frozen(self, tmp_path: Path) -> None:
        env = MvgeEnvironment.resolve(
            DEFAULT_AGENT_NAME,
            project_dir=tmp_path,
            config_dir=tmp_path / "agent_config",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            env.temperature = 0.5  # type: ignore[misc]


class TestMvgeEnvironmentAssemble:
    @pytest.mark.asyncio
    async def test_without_runner_returns_base_prompt(self, tmp_path: Path) -> None:
        env = MvgeEnvironment.resolve(
            DEFAULT_AGENT_NAME, project_dir=tmp_path, config_dir=tmp_path / "cfg"
        )
        assembled = await env.assemble_system_prompt(base_prompt="You are Mvge.")
        assert "You are Mvge." in assembled
        assert "Active spells:" in assembled

    @pytest.mark.asyncio
    async def test_emits_before_mvge_start_once(self, tmp_path: Path) -> None:
        runner = _mock_runner()
        env = MvgeEnvironment.resolve(
            DEFAULT_AGENT_NAME, project_dir=tmp_path, config_dir=tmp_path / "cfg"
        )

        await env.assemble_system_prompt(runner=runner)

        runner.emit_chain.assert_awaited_once()
        call_args = runner.emit_chain.await_args.args
        assert call_args[0] == SigilHook.BEFORE_MVGE_START

    @pytest.mark.asyncio
    async def test_sigil_payload_carries_agent_inputs(self, tmp_path: Path) -> None:
        runner = _mock_runner()
        env = MvgeEnvironment.resolve(
            DEFAULT_AGENT_NAME, project_dir=tmp_path, config_dir=tmp_path / "cfg"
        )

        await env.assemble_system_prompt(
            runner=runner,
            base_prompt="Base prompt.",
            custom_prompt="Custom body",
            spell_names=["bash", "read"],
            cwd="/proj",
            config_dir="/cfg/test-agent",
        )

        payload = runner.emit_chain.await_args.args[1]
        assert payload.base_prompt == "Base prompt."
        assert payload.spell_names == ["bash", "read"]
        assert payload.config_dir == "/cfg/test-agent"
        assert payload.custom_prompt == "Custom body"
        assert payload.agent_name == DEFAULT_AGENT_NAME
        assert payload.cwd == "/proj"

    @pytest.mark.asyncio
    async def test_runner_can_modify_base_prompt(self, tmp_path: Path) -> None:
        async def injector(hook: SigilHook, data: Any) -> Any:
            assert isinstance(data, BeforeMvgeStartData)
            data.base_prompt = "Runes were here."
            return data

        runner = _mock_runner()
        runner.emit_chain = AsyncMock(side_effect=injector)
        env = MvgeEnvironment.resolve(
            DEFAULT_AGENT_NAME, project_dir=tmp_path, config_dir=tmp_path / "cfg"
        )

        result = await env.assemble_system_prompt(runner=runner)
        assert "Runes were here." in result
        assert "Active spells:" in result


# ---------------------------------------------------------------------------
# Snapshots and BaseMvge Parity
# ---------------------------------------------------------------------------


class TestMvgeEnvironmentSnapshot:
    def test_agent_build_snapshot_reports_the_agents_own_model(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An agent's snapshot names its model, not its config file's.

        build_snapshot re-resolves the environment, which reports the
        config's model; since ADR-0028 the config carries no model for
        a Summoner who never set one, so an explicitly modelled agent
        reported an empty model in its own snapshot.
        """
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        agent = Mvge(api_key="test-key", model_id="openrouter/free")
        snap = agent.build_snapshot()

        assert snap.model == "openrouter/free"

    def test_mvge_environment_build_snapshot(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "agent_config"
        env = MvgeEnvironment.resolve(
            DEFAULT_AGENT_NAME, project_dir=tmp_path, config_dir=config_dir
        )
        snapshot = env.build_snapshot()

        assert isinstance(snapshot, RuntimeSnapshot)
        assert snapshot.agent_name == DEFAULT_AGENT_NAME
        assert snapshot.model == env.model_id
        assert snapshot.prompt is not None
        assert snapshot.prompt.source == PromptSource.BUILTIN.value

    @pytest.mark.asyncio
    async def test_base_mvge_diagnostics_property_and_zero_load_retention(
        self,
    ) -> None:
        diag = Diagnostic(
            kind=DiagnosticKind.LOAD_FAILURE,
            rune_name="broken_rune",
            message="failed to import module",
            scope=RuneScope.USER,
        )

        with patch(
            "mvge.agent.rune_lifecycle.load_runes_from_paths",
            return_value=([], [diag]),
        ):
            agent = Mvge(api_key="test-key")
            await agent._load_runes()

            assert len(agent.diagnostics) == 1
            assert agent.diagnostics[0].rune_name == "broken_rune"
            assert agent.environment.diagnostics == agent.diagnostics

    def test_base_mvge_matches_environment_coercion(self, tmp_path: Path) -> None:
        custom_defaults = {
            "model": "parity-model",
            "temperature": "invalid",
            "max_tokens": "2048",
            "contemplation_level": "high",
            "contemplation_budget": "256",
            "exclude_contemplation": True,
            "queue_mode": "all",
            "spells_enabled": ["bash", "read"],
            "rune_paths": [str(tmp_path / "runes")],
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

        assert isinstance(env.agent_config, AgentConfig)
        assert agent._model_id == env.model_id
        assert agent._temperature == env.temperature
        assert agent._max_tokens == env.max_tokens
        assert agent._contemplation_level == env.contemplation_level
        assert agent._contemplation_budget == env.contemplation_budget
        assert agent._exclude_contemplation == env.exclude_contemplation
        assert agent._queue_mode == env.queue_mode
        assert agent._spell_names == env.spell_names
        assert agent._runes_paths == env.runes_paths


class TestSteeringDecoupled:
    """Repository steering lives in steering-bridge, not the microkernel.

    Discovery (``resolve_workspace_agents_file``,
    ``resolve_global_agents_file``, ``resolve_scoped_agents_file``) and
    ``<project_context>`` rendering moved to the ``steering-bridge``
    marketplace rune, which contributes its section dynamically via the
    ``BEFORE_MVGE_START`` sigil. Core rendering stays Layer 2 only.
    """

    def test_read_text_strip_frontmatter(self, tmp_path: Path) -> None:
        f = tmp_path / "with_fm.md"
        f.write_text(
            "---\nkind: agents\nversion: 1.0\n---\n# Real Content\nBody text",
            encoding="utf-8",
        )
        assert _read_text(f, strip_frontmatter=True) == "# Real Content\nBody text"
        assert _read_text(f, strip_frontmatter=False).startswith("---")

        f_only_fm = tmp_path / "only_fm.md"
        f_only_fm.write_text("---\nkind: agents\n---", encoding="utf-8")
        assert _read_text(f_only_fm, strip_frontmatter=True) == ""

        f_no_fm = tmp_path / "no_fm.md"
        f_no_fm.write_text("Plain markdown", encoding="utf-8")
        assert _read_text(f_no_fm, strip_frontmatter=True) == "Plain markdown"

    def test_render_prompt_ignores_agents_files(self, tmp_path: Path) -> None:
        global_dir = tmp_path / "global"
        global_dir.mkdir(parents=True)
        (global_dir / "AGENTS.md").write_text(
            "Global instructions here",
            encoding="utf-8",
        )

        cwd_dir = tmp_path / "workspace"
        dot_agents = cwd_dir / ".agents"
        dot_agents.mkdir(parents=True)
        (dot_agents / "AGENTS.md").write_text(
            "Workspace instructions here",
            encoding="utf-8",
        )
        (cwd_dir / "AGENTS.md").write_text("Root rules", encoding="utf-8")

        pkg_core = cwd_dir / "mvge-core"
        pkg_core.mkdir()
        (pkg_core / "AGENTS.md").write_text("Core secret internals", encoding="utf-8")

        rendered = render_prompt(
            body="You are a Mvge.",
            cwd=cwd_dir,
        )

        # Layer 2 scaffolding still renders
        assert "You are a Mvge." in rendered
        assert "Active spells:" in rendered
        assert "Environment:" in rendered
        # Steering content is never inlined by the microkernel
        assert "<project_context>" not in rendered
        assert "<global_instructions" not in rendered
        assert "<project_instructions" not in rendered
        assert "Global instructions here" not in rendered
        assert "Workspace instructions here" not in rendered
        assert "Core secret internals" not in rendered
        assert "- Global Rules:" not in rendered
        assert "- Project Rules:" not in rendered
        assert "Subpackage Rules" not in rendered

    def test_render_prompt_omits_self_mod_section(self, tmp_path: Path) -> None:
        rendered = render_prompt(
            body="You are a Mvge.",
            cwd=tmp_path,
        )

        # The self-modification section moved to the rune; the engine no
        # longer emits it.
        assert "Self-Modification & Customization:" not in rendered
        assert "System Instructions:" not in rendered

    def test_render_prompt_rejects_removed_self_mod_kwargs(
        self, tmp_path: Path
    ) -> None:
        # §4.1 breaking change: spells_dir, runes_paths, system_path are gone.
        with pytest.raises(TypeError):
            render_prompt(  # type: ignore[call-arg]
                body="You are a Mvge.",
                cwd=tmp_path,
                runes_paths=[tmp_path / "runes"],
            )
        with pytest.raises(TypeError):
            render_prompt(  # type: ignore[call-arg]
                body="You are a Mvge.",
                cwd=tmp_path,
                system_path=tmp_path / "SYSTEM.md",
            )
        with pytest.raises(TypeError):
            render_prompt(  # type: ignore[call-arg]
                body="You are a Mvge.",
                cwd=tmp_path,
                spells_dir=tmp_path / "spells",
            )

    def test_environment_resolve_with_global_dir(self, tmp_path: Path) -> None:
        global_dir = tmp_path / "global"
        global_dir.mkdir(parents=True)
        (global_dir / "AGENTS.md").write_text("Global system rule", encoding="utf-8")

        env = MvgeEnvironment.resolve(
            DEFAULT_AGENT_NAME,
            project_dir=tmp_path,
            global_dir=global_dir,
        )
        assert env.global_dir == global_dir
        # Steering is rune-owned; the microkernel no longer inlines it.
        rendered = env.render_system_prompt()
        assert "<global_instructions" not in rendered
        assert "Global system rule" not in rendered


class TestSigilPayload:
    """BEFORE_MVGE_START payload carries the rune rehydration fields (§4.3)."""

    def test_resolve_accepts_active_spells_dir(self, tmp_path: Path) -> None:
        spells_dir = tmp_path / "spells"
        spells_dir.mkdir()
        env = MvgeEnvironment.resolve(DEFAULT_AGENT_NAME, active_spells_dir=spells_dir)
        assert env.active_spells_dir == spells_dir

    def test_resolve_defaults_active_spells_dir_to_none(self) -> None:
        env = MvgeEnvironment.resolve(DEFAULT_AGENT_NAME)
        assert env.active_spells_dir is None

    def test_build_sigil_payload_populates_rehydration_fields(
        self, tmp_path: Path
    ) -> None:
        spells_dir = tmp_path / "spells"
        spells_dir.mkdir()
        env = MvgeEnvironment.resolve(DEFAULT_AGENT_NAME, active_spells_dir=spells_dir)

        payload = env.build_sigil_payload(
            base_prompt="Be helpful.",
            spell_names=["bash"],
            config_dir=tmp_path / "config",
            custom_prompt="Custom.",
            cwd=tmp_path,
        )

        assert payload.base_prompt == "Be helpful."
        assert payload.spell_names == ["bash"]
        assert payload.config_dir == str(tmp_path / "config")
        assert payload.custom_prompt == "Custom."
        assert payload.agent_name == DEFAULT_AGENT_NAME
        assert payload.cwd == str(tmp_path)
        assert payload.spells_dir == spells_dir.as_posix()
        expected_system_path = (
            env.resolved_prompt.path.as_posix() if env.resolved_prompt.path else None
        )
        assert payload.system_path == expected_system_path
        assert payload.runes_paths == [p.as_posix() for p in env.runes_paths]

    def test_build_sigil_payload_spells_dir_defaults_to_none(
        self, tmp_path: Path
    ) -> None:
        """§4.4: payload coverage includes the spells_dir=None case."""
        env = MvgeEnvironment.resolve(DEFAULT_AGENT_NAME)
        assert env.active_spells_dir is None

        payload = env.build_sigil_payload(
            base_prompt="Be helpful.",
            spell_names=[],
            config_dir=tmp_path,
        )

        assert payload.spells_dir is None

    def test_build_sigil_payload_explicit_dir_beats_environment(
        self, tmp_path: Path
    ) -> None:
        env = MvgeEnvironment.resolve(DEFAULT_AGENT_NAME)
        assert env.active_spells_dir is None
        override = tmp_path / "other-spells"
        override.mkdir()

        payload = env.build_sigil_payload(
            base_prompt="Be helpful.",
            spell_names=[],
            config_dir=tmp_path,
            active_spells_dir=override,
        )

        assert payload.spells_dir == override.as_posix()
