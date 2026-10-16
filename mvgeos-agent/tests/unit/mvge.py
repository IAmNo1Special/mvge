from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from mvgeos_core.abort import AbortSignal
from mvgeos_core.constants import DEFAULT_MODEL
from mvgeos_core.events import QueueMode
from mvgeos_core.invocations import SummonerRequest
from mvgeos_core.spells import ExecutionMode
from mvgeos_provider.base import Realm
from mvgeos_provider.realms import realm_for_model_id
from mvgeos_provider.registry import RealmRegistry
from mvgeos_runes.rune_runner import RuneRunner
from mvgeos_runes.types import RuneLoad, RuneManifest, SpellDefinition
from mvgeos_tome.handle import TomeHandleFactory

from mvgeos_agent import Mvge
from mvgeos_agent.environment import MvgeEnvironment
from mvgeos_agent.mvge import (
    _apply_gateway_allowlist,
    _resolve_api_key,
    _validate_spell_name,
)
from mvgeos_agent.harness._state import MvgeState


def dummy_built_in(command: str) -> str:
    """Built-in command."""
    return command


class ValidRuneSpell(SpellDefinition):
    def __init__(
        self,
        name: str = "valid_spell",
        parameters: dict[str, Any] | None = None,
        source_rune: str | None = None,
    ) -> None:
        super().__init__(
            name=name,
            description="A valid spell",
            parameters=parameters or {},
            execution_mode=ExecutionMode.PARALLEL,
            source_rune=source_rune,
        )

    async def execute(
        self,
        spell_cast_id: str,
        params: dict[str, Any],
        signal: AbortSignal | None = None,
        on_update: Any | None = None,
    ) -> dict[str, Any]:
        return {"result": "ok"}


class ValidSpellWithoutOnUpdate(SpellDefinition):
    async def execute(
        self,
        spell_cast_id: str,
        params: dict[str, Any],
        signal: AbortSignal | None = None,
    ) -> dict[str, Any]:
        return {"result": "ok"}


class ValidSpellWithKwargs(SpellDefinition):
    async def execute(
        self,
        spell_cast_id: str,
        params: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        return {"result": "ok"}


class ValidSpellWithArgsKwargs(SpellDefinition):
    async def execute(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"result": "ok"}


class SpellMissingExecute:
    def __init__(self, name: str, parameters: dict[str, Any] | None = None) -> None:
        self.name = name
        self.parameters = parameters or {}


class SpellNonCallableExecute(SpellDefinition):
    execute = "not_a_callable"  # type: ignore[assignment]


class SpellZeroArgExecute(SpellDefinition):
    async def execute(self) -> dict[str, Any]:  # type: ignore[override]
        return {}


class SpellPositionalOnlyTwoArgs(SpellDefinition):
    async def execute(  # type: ignore[override]
        self, spell_cast_id: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        return {}


class SpellExtraRequiredArg(SpellDefinition):
    async def execute(  # type: ignore[override]
        self,
        spell_cast_id: str,
        params: dict[str, Any],
        extra_required: str,
        signal: AbortSignal | None = None,
    ) -> dict[str, Any]:
        return {}


def _create_mock_runner(spells: list[SpellDefinition]) -> MagicMock:
    runner = MagicMock()
    runner.get_all_registered_spells.return_value = spells
    runner.get_active_spells.return_value = [s.name for s in spells]
    runner.get_global_spell_allowlist.return_value = None
    return runner


class TestMvgeBuildSpellsValidation:
    def test_build_spells_with_valid_rune_spells(self) -> None:
        agent = Mvge(api_key="k", spells=[dummy_built_in])
        spell_1 = ValidRuneSpell("rune_tool_1", parameters={})
        spell_2 = ValidRuneSpell(
            "rune_tool_2",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        )
        agent._runner = _create_mock_runner([spell_1, spell_2])

        spells = agent._build_spells()
        names = [s.name for s in spells]
        assert "dummy_built_in" in names
        assert "rune_tool_1" in names
        assert "rune_tool_2" in names

    def test_build_spells_accepts_valid_signature_variants(self) -> None:
        agent = Mvge(api_key="k", spells=[])
        s1 = ValidSpellWithoutOnUpdate("s1", {})
        s2 = ValidSpellWithKwargs("s2", {})
        s3 = ValidSpellWithArgsKwargs("s3", {})
        agent._runner = _create_mock_runner([s1, s2, s3])

        spells = agent._build_spells()
        names = [s.name for s in spells]
        assert names == ["s1", "s2", "s3"]

    def test_rejects_non_dict_parameter_schema(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        agent = Mvge(api_key="k", spells=[])
        s1 = ValidRuneSpell("s1")
        s1.parameters = "not_a_dict"  # type: ignore[assignment]
        s2 = ValidRuneSpell("s2")
        s2.parameters = [1, 2, 3]  # type: ignore[assignment]
        s3 = ValidRuneSpell("s3")
        s3.parameters = 123  # type: ignore[assignment]
        s4 = ValidRuneSpell("s4")
        s4.parameters = None  # type: ignore[assignment]
        agent._runner = _create_mock_runner([s1, s2, s3, s4])

        with caplog.at_level(logging.WARNING):
            spells = agent._build_spells()

        assert len(spells) == 0
        assert "Rune spell 's1' has an invalid parameter schema" in caplog.text
        assert "Rune spell 's2' has an invalid parameter schema" in caplog.text
        assert "Rune spell 's3' has an invalid parameter schema" in caplog.text
        assert "Rune spell 's4' has an invalid parameter schema" in caplog.text

    def test_rejects_malformed_json_schema_structure(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        agent = Mvge(api_key="k", spells=[])
        # Bad type (not 'object')
        s1 = ValidRuneSpell("s1", parameters={"type": "string"})
        # Bad properties (not a dict)
        s2 = ValidRuneSpell("s2", parameters={"properties": "invalid"})
        # Bad property definition (not a dict)
        s3 = ValidRuneSpell("s3", parameters={"properties": {"x": "not_a_dict"}})
        # Bad required (not a list/sequence)
        s4 = ValidRuneSpell("s4", parameters={"required": "field"})
        # Bad required items (not strings)
        s5 = ValidRuneSpell("s5", parameters={"required": [123]})
        # Bad properties key (not a string)
        s6 = ValidRuneSpell("s6", parameters={"properties": {123: {}}})  # type: ignore[dict-item]

        agent._runner = _create_mock_runner([s1, s2, s3, s4, s5, s6])

        with caplog.at_level(logging.WARNING):
            spells = agent._build_spells()

        assert len(spells) == 0
        assert "Rune spell 's1' has an invalid parameter schema" in caplog.text
        assert "Rune spell 's2' has an invalid parameter schema" in caplog.text
        assert "Rune spell 's3' has an invalid parameter schema" in caplog.text
        assert "Rune spell 's4' has an invalid parameter schema" in caplog.text
        assert "Rune spell 's5' has an invalid parameter schema" in caplog.text
        assert "Rune spell 's6' has an invalid parameter schema" in caplog.text

    def test_rejects_incompatible_execute_signature(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        agent = Mvge(api_key="k", spells=[])
        s1 = SpellNonCallableExecute("s1", {})
        s2 = SpellZeroArgExecute("s2", {})
        s3 = SpellPositionalOnlyTwoArgs("s3", {})
        s4 = SpellExtraRequiredArg("s4", {})
        s5 = SpellMissingExecute("s5", {})

        agent._runner = _create_mock_runner([s1, s2, s3, s4, s5])  # type: ignore[list-item]

        with caplog.at_level(logging.WARNING):
            spells = agent._build_spells()

        assert len(spells) == 0
        assert "Rune spell 's1' execution signature does not conform" in caplog.text
        assert "Rune spell 's2' execution signature does not conform" in caplog.text
        assert "Rune spell 's3' execution signature does not conform" in caplog.text
        assert "Rune spell 's4' execution signature does not conform" in caplog.text
        assert "Rune spell 's5' execution signature does not conform" in caplog.text

    def test_rejects_uninspectable_signature(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import patch

        agent = Mvge(api_key="k", spells=[])
        s1 = ValidRuneSpell("s1")
        agent._runner = _create_mock_runner([s1])

        with (
            patch("inspect.signature", side_effect=ValueError("Cannot inspect")),
            caplog.at_level(logging.WARNING),
        ):
            spells = agent._build_spells()

        assert len(spells) == 0
        assert "Rune spell 's1' execution signature does not conform" in caplog.text


class TestMvgeBuildSpellsCollisionHandling:
    def test_collision_with_builtin_spell_prefixes_rune_name(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        agent = Mvge(api_key="k", spells=[dummy_built_in])
        # dummy_built_in has name "dummy_built_in"
        colliding_rune_spell = ValidRuneSpell(
            name="dummy_built_in", source_rune="custom_rune"
        )
        agent._runner = _create_mock_runner([colliding_rune_spell])

        with caplog.at_level(logging.WARNING):
            spells = agent._build_spells()

        names = [s.name for s in spells]
        assert "dummy_built_in" in names
        assert "custom_rune_dummy_built_in" in names
        assert (
            "Rune spell 'dummy_built_in' collides with existing spell; "
            "renaming to 'custom_rune_dummy_built_in'." in caplog.text
        )

    def test_collision_without_source_rune_uses_rune_prefix(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        agent = Mvge(api_key="k", spells=[dummy_built_in])
        colliding_rune_spell = ValidRuneSpell(name="dummy_built_in", source_rune=None)
        agent._runner = _create_mock_runner([colliding_rune_spell])

        with caplog.at_level(logging.WARNING):
            spells = agent._build_spells()

        names = [s.name for s in spells]
        assert "dummy_built_in" in names
        assert "rune_dummy_built_in" in names
        assert (
            "Rune spell 'dummy_built_in' collides with existing spell; "
            "renaming to 'rune_dummy_built_in'." in caplog.text
        )

    def test_collision_between_two_rune_spells_prefixes_second(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        agent = Mvge(api_key="k", spells=[])
        s1 = ValidRuneSpell(name="find_items", source_rune="rune_one")
        s2 = ValidRuneSpell(name="find_items", source_rune="rune_two")
        agent._runner = _create_mock_runner([s1, s2])

        with caplog.at_level(logging.WARNING):
            spells = agent._build_spells()

        names = [s.name for s in spells]
        assert "find_items" in names
        assert "rune_two_find_items" in names
        assert (
            "Rune spell 'find_items' collides with existing spell; "
            "renaming to 'rune_two_find_items'." in caplog.text
        )

    def test_double_collision_skipped_with_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        agent = Mvge(api_key="k", spells=[])
        s0 = ValidRuneSpell(name="spell", source_rune="first")
        s1 = ValidRuneSpell(name="custom_rune_spell", source_rune="second")
        # s2 is "spell" with source_rune="custom_rune" -> would become
        # "custom_rune_spell" which collides with s1
        s2 = ValidRuneSpell(name="spell", source_rune="custom_rune")
        agent._runner = _create_mock_runner([s0, s1, s2])

        with caplog.at_level(logging.WARNING):
            spells = agent._build_spells()

        names = [s.name for s in spells]
        assert names == ["spell", "custom_rune_spell"]
        assert (
            "Prefixed rune spell 'custom_rune_spell' still collides with an "
            "existing spell and will be skipped." in caplog.text
        )

    def test_build_spells_idempotent_across_multiple_calls(self) -> None:
        agent = Mvge(api_key="k", spells=[dummy_built_in])
        colliding_rune_spell = ValidRuneSpell(
            name="dummy_built_in", source_rune="custom_rune"
        )
        agent._runner = _create_mock_runner([colliding_rune_spell])

        spells_1 = agent._build_spells()
        spells_2 = agent._build_spells()

        assert [s.name for s in spells_1] == [
            "dummy_built_in",
            "custom_rune_dummy_built_in",
        ]
        assert [s.name for s in spells_2] == [
            "dummy_built_in",
            "custom_rune_dummy_built_in",
        ]


class TestMvgeBuildSpellsNameValidation:
    def test_validate_spell_name_rules(self) -> None:
        assert _validate_spell_name("valid_name") is True
        assert _validate_spell_name("valid-name-123") is True
        assert _validate_spell_name("UPPER_case") is True
        assert _validate_spell_name("tool1") is True

        assert _validate_spell_name("invalid.name") is False
        assert _validate_spell_name("invalid name") is False
        assert _validate_spell_name("invalid/name") is False
        assert _validate_spell_name("") is False
        assert _validate_spell_name(None) is False
        assert _validate_spell_name(123) is False

    def test_rune_spell_with_invalid_name_skipped(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        agent = Mvge(api_key="k", spells=[])
        bad_spell = ValidRuneSpell(name="knowledge_skill.consolidate")
        agent._runner = _create_mock_runner([bad_spell])

        with caplog.at_level(logging.WARNING):
            spells = agent._build_spells()

        assert len(spells) == 0
        assert (
            "Rune spell 'knowledge_skill.consolidate' has an invalid name"
            in caplog.text
        )

    def test_injected_spell_with_invalid_name_skipped(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        def invalid_dot_spell() -> str:
            return "ok"

        invalid_dot_spell.__name__ = "invalid.spell.name"

        agent = Mvge(api_key="k", spells=[invalid_dot_spell])

        with caplog.at_level(logging.WARNING):
            spells = agent._build_spells()

        assert len(spells) == 0
        assert "Spell 'invalid.spell.name' has an invalid name" in caplog.text

    def test_make_stream_fn_passes_system_prompt_to_channel_config(self) -> None:
        from mvgeos_core.channel import Model

        from mvgeos_agent.harness._state import MvgeState

        agent = Mvge(api_key="k", spells=[])
        model = Model(id="m", name="n", realm="r", base_url="", api_key="")
        mock_realm = MagicMock()
        mock_realm.stream.return_value = []
        state = MvgeState(system_prompt="Custom System Prompt with Skills")

        stream_fn = agent._make_stream_fn(
            model=model,
            realm=mock_realm,
            state=state,
            temperature=0.5,
            max_tokens=1000,
        )
        stream_fn([])

        assert mock_realm.stream.called
        call_config = mock_realm.stream.call_args.kwargs["config"]
        assert call_config.system_prompt == "Custom System Prompt with Skills"


class TestMvgeNoLongerOwnsTurnLoop:
    def test_make_stream_removed(self) -> None:
        assert not hasattr(Mvge, "_make_stream")


class TestMvgeQueueMode:
    def test_default_queue_mode_is_one_at_a_time(self) -> None:
        agent = Mvge(api_key="test-key")
        assert agent.queue_mode == QueueMode.ONE_AT_A_TIME

    def test_queue_mode_can_be_set_to_all(self) -> None:
        agent = Mvge(api_key="test-key")
        agent.queue_mode = QueueMode.ALL
        assert agent.queue_mode == QueueMode.ALL

    def test_queue_mode_accepts_string(self) -> None:
        agent = Mvge(api_key="test-key")
        agent.queue_mode = "one-at-a-time"
        assert agent.queue_mode == QueueMode.ONE_AT_A_TIME
        agent.queue_mode = "all"
        assert agent.queue_mode == QueueMode.ALL


class TestMvgePropertiesAndMethods:
    def test_basic_properties(self, tmp_path: Path) -> None:
        agent = Mvge(
            api_key="test-key",
            name="test_mvge",
            tome_dir=tmp_path / "sessions",
            spells=[],
        )
        assert agent.name == "test_mvge"
        assert agent.tome_dir == tmp_path / "sessions"
        assert agent.tome_id is None
        assert agent.mana_used is None
        assert isinstance(agent.config_dir, Path)
        assert agent.environment is not None
        assert isinstance(agent.spells, list)
        assert agent.event_bus is not None
        assert isinstance(agent.diagnostics, list)
        assert isinstance(agent.available_spells, list)
        assert isinstance(agent.registered_commands, list)
        assert isinstance(agent.registered_shortcuts, list)
        assert isinstance(agent.registered_providers, list)
        assert agent.model_registry is not None
        assert agent.model_id is not None
        assert agent.contemplation_level is not None

    def test_runner_property_exposes_engine_owned_runner(self) -> None:
        agent = Mvge(api_key="test-key", spells=[])
        assert agent.runner is None
        runner = RuneRunner()
        agent.set_runner(runner)
        assert agent.runner is runner

    def test_set_enabled_spells(self) -> None:
        agent = Mvge(api_key="test-key", spells=[dummy_built_in])
        assert "dummy_built_in" in agent.available_spells
        agent.set_enabled_spells(["dummy_built_in"])
        assert "dummy_built_in" in agent.enabled_spells
        agent.set_enabled_spells([])
        assert agent.enabled_spells == []

    def test_build_spells_respects_global_allowlist(self) -> None:
        agent = Mvge(api_key="test-key", spells=[dummy_built_in])
        spell_1 = ValidRuneSpell("tool_search", parameters={})
        spell_2 = ValidRuneSpell("bash", parameters={})
        agent._runner = _create_mock_runner([spell_1, spell_2])
        agent._runner.get_global_spell_allowlist.return_value = ["tool_search"]

        spells = agent._build_spells()
        names = [s.name for s in spells]
        assert "tool_search" in names
        assert "bash" not in names
        assert "dummy_built_in" not in names

    def test_build_spells_global_allowlist_none_shows_all(self) -> None:
        agent = Mvge(api_key="test-key", spells=[dummy_built_in])
        spell_1 = ValidRuneSpell("tool_search", parameters={})
        agent._runner = _create_mock_runner([spell_1])
        agent._runner.get_global_spell_allowlist.return_value = None

        spells = agent._build_spells()
        names = [s.name for s in spells]
        assert "dummy_built_in" in names
        assert "tool_search" in names

    def test_build_spells_global_allowlist_and_filter_are_conjunctive(self) -> None:
        agent = Mvge(api_key="test-key", spells=[])
        spell_1 = ValidRuneSpell("tool_search", parameters={})
        spell_2 = ValidRuneSpell("bash", parameters={})
        agent._runner = _create_mock_runner([spell_1, spell_2])
        agent._runner.get_global_spell_allowlist.return_value = [
            "tool_search",
            "bash",
        ]
        agent.set_enabled_spells(["tool_search"])

        spells = agent._build_spells()
        names = [s.name for s in spells]
        assert names == ["tool_search"]

    def test_build_spells_global_allowlist_none_with_disabled_runner(self) -> None:
        agent = Mvge(api_key="test-key", spells=[dummy_built_in])
        agent._runner = None

        spells = agent._build_spells()
        names = [s.name for s in spells]
        assert names == ["dummy_built_in"]

    def test_set_environment_and_config_manager(self) -> None:
        agent = Mvge(api_key="test-key", spells=[])
        env = agent.environment
        agent.set_environment(env)
        assert agent.environment == env
        agent.set_config_manager(None)


def _gateway_manifest(name: str, gateway: bool = True) -> RuneManifest:
    return RuneManifest(
        name=name, version="1.0.0", description="", spell_gateway=gateway
    )


async def _gateway_runner() -> RuneRunner:
    runner = RuneRunner()

    def seeker_factory(api: Any) -> None:
        api.register_spell(SpellDefinition(name="tool_search", description=""))

    def other_factory(api: Any) -> None:
        api.register_spell(SpellDefinition(name="weather_lookup", description=""))

    await runner.load_rune_loads(
        [
            RuneLoad(manifest=_gateway_manifest("seeker"), factory=seeker_factory),
            RuneLoad(
                manifest=_gateway_manifest("other", gateway=False),
                factory=other_factory,
            ),
        ]
    )
    return runner


class TestApplyGatewayAllowlist:
    @pytest.mark.asyncio
    async def test_gateway_allowlist_engaged(self) -> None:
        runner = await _gateway_runner()
        assert _apply_gateway_allowlist(runner) == "seeker"
        assert runner.get_global_spell_allowlist() == ["tool_search"]

    @pytest.mark.asyncio
    async def test_explicit_allowlist_not_clobbered(self) -> None:
        runner = await _gateway_runner()
        runner.set_global_spell_allowlist(["bash"])
        assert _apply_gateway_allowlist(runner) is None
        assert runner.get_global_spell_allowlist() == ["bash"]

    @pytest.mark.asyncio
    async def test_no_gateway_no_allowlist(self) -> None:
        runner = RuneRunner()
        await runner.load_rune_loads(
            [RuneLoad(manifest=_gateway_manifest("plain", gateway=False))]
        )
        assert _apply_gateway_allowlist(runner) is None
        assert runner.get_global_spell_allowlist() is None

    @pytest.mark.asyncio
    async def test_gateway_end_to_end_build_spells(self) -> None:
        runner = await _gateway_runner()
        agent = Mvge(api_key="test-key", spells=[])
        agent._runner = runner  # type: ignore[assignment]
        _apply_gateway_allowlist(runner)

        names = [s.name for s in agent._build_spells()]
        assert "tool_search" in names
        assert "weather_lookup" not in names

        gateway_api = runner.create_api(rune_name="seeker")
        gateway_api.widen_global_allowlist(["weather_lookup"])
        names = [s.name for s in agent._build_spells()]
        assert "weather_lookup" in names

    @pytest.mark.asyncio
    async def test_widen_cannot_create_allowlist(self) -> None:
        """A rune must not narrow the model's view by widening a filter
        that was never enabled."""
        runner = await _gateway_runner()
        other_api = runner.create_api(rune_name="other")
        other_api.widen_global_allowlist(["weather_lookup"])
        assert runner.get_global_spell_allowlist() is None


class TestActiveSpellsDirRecording:
    """Mvge records the winning spell-discovery directory (§4.3)."""

    def test_explicit_spell_list_records_no_dir(self) -> None:
        agent = Mvge(api_key="k", spells=[dummy_built_in])
        assert agent._active_spells_dir is None

    def test_caller_local_spells_dir_recorded(self, tmp_path: Path) -> None:
        spells_dir = tmp_path / "spells"
        spells_dir.mkdir()
        (spells_dir / "greet.py").write_text(
            "def greet(name: str) -> str:\n    '''Greet someone.'''\n    return name\n",
            encoding="utf-8",
        )

        agent = Mvge(api_key="k", caller_dir=tmp_path)

        assert agent._active_spells_dir == spells_dir
        assert [s.name for s in agent._spells] == ["greet"]
        assert agent._environment.active_spells_dir == spells_dir

    def test_missing_spells_dir_records_none(self, tmp_path: Path) -> None:
        agent = Mvge(api_key="k", caller_dir=tmp_path)
        assert agent._active_spells_dir is None
        assert agent._environment.active_spells_dir is None


@pytest.mark.asyncio
async def test_initialize_records_project_dir_as_tome_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression (Major #9): the tome created during initialize() must record
    the agent's project directory as its cwd, not the host process cwd.

    The GUI embeds the engine in-process with --project differing from the
    process cwd. Recording Path.cwd() made
    TomeService.list_tomes_for_project() filter the tome out, so successful
    turns never appeared as session rows in Sessions / Recent Sessions.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    agent_name = "cwd-probe"
    config_dir = tmp_path / ".agents" / "agents" / agent_name
    config_dir.mkdir(parents=True)
    (config_dir / "SYSTEM.md").write_text("# Probe\n", encoding="utf-8")
    (tmp_path / "runes").mkdir()

    env = MvgeEnvironment.resolve(
        agent_name=agent_name,
        project_dir=project_dir,
        config_dir=config_dir,
        caller_dir=tmp_path,
    )
    registry = RealmRegistry()
    # Registered under the default model's Realm prefix, so this test does not
    # have to change every time the default model does.
    registry.register_realm_factory(
        realm_for_model_id(DEFAULT_MODEL), lambda **kwargs: MagicMock(spec=Realm)
    )
    agent = Mvge(
        api_key="k",
        environment=env,
        tome_dir=tmp_path / "tomes",
        runes_paths=[str(tmp_path / "runes")],
        provider_registry=registry,
    )
    await agent.initialize()
    try:
        meta = TomeHandleFactory(tmp_path / "tomes").open_tome(agent.tome_id)
        assert meta is not None
        assert meta.cwd == str(project_dir)
    finally:
        await agent.close()


def test_api_key_resolution_never_crosses_realms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Realm's credential is read only for that Realm.

    An OpenRouter key in the environment used to be read first, unconditionally,
    so an Mvge whose model named any other Realm sent it to that Realm's host.
    Since ``opencode`` became the default Realm that was the ordinary first-run
    path, not an edge case.
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret")
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    monkeypatch.delenv("MVGEOS_API_KEY", raising=False)

    assert _resolve_api_key(realm="opencode") == ""
    assert _resolve_api_key(realm="openrouter") == "sk-or-secret"

    # An explicit key is not a guess about which Realm was meant, so it stands.
    assert _resolve_api_key("sk-explicit", realm="opencode") == "sk-explicit"
