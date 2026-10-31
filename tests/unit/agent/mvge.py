from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from mvge.agent import Mvge
from mvge.agent.environment import MvgeEnvironment
from mvge.agent.mvge import (
    _apply_gateway_allowlist,
    _resolve_api_key,
    _validate_spell_name,
)
from mvge.core.abort import AbortSignal
from mvge.core.errors import GatewayLoadError
from mvge.core.events import QueueMode
from mvge.core.spells import ExecutionMode
from mvge.provider.base import Realm
from mvge.provider.realms import realm_for_model_id
from mvge.provider.registry import RealmRegistry
from mvge.runes.rune_runner import RuneRunner
from mvge.runes.trust import WorkspaceTrustStatus
from mvge.runes.types import (
    Diagnostic,
    DiagnosticKind,
    RuneLoad,
    RuneManifest,
    SpellDefinition,
)
from mvge.tome.handle import TomeHandleFactory


def dummy_built_in(command: str) -> str:
    """Built-in command."""
    return command


class _UntrustedStore:
    def check_trust(self, path: Any) -> WorkspaceTrustStatus:
        return WorkspaceTrustStatus.DECLINED


class _TrustedStore:
    def check_trust(self, path: Any) -> WorkspaceTrustStatus:
        return WorkspaceTrustStatus.TRUSTED


class TestWorkspaceTrust:
    @pytest.mark.asyncio
    async def test_untrusted_workspace_does_not_steer(self) -> None:
        """Trust status must never become a model-answered turn: print
        mode displays the last invocation of a run, so a steering turn
        queued before the user's prompt displaces the user's answer."""
        agent = Mvge(api_key="test", spells=[], trust_store=_UntrustedStore())
        agent._harness = MagicMock()
        await agent._check_workspace_trust()
        agent._harness.steer.assert_not_called()
        assert agent.workspace_trust_status == WorkspaceTrustStatus.DECLINED

    @pytest.mark.asyncio
    async def test_trusted_workspace_reports_trusted(self) -> None:
        agent = Mvge(api_key="test", spells=[], trust_store=_TrustedStore())
        await agent._check_workspace_trust()
        assert agent.workspace_trust_status == WorkspaceTrustStatus.TRUSTED


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
        overrides={"model": "openrouter/free"},
    )
    registry = RealmRegistry()
    # Registered under the default model's Realm prefix, so this test does not
    # have to change every time the default model does.
    registry.register_realm_factory(
        realm_for_model_id("openrouter/free"), lambda **kwargs: MagicMock(spec=Realm)
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


@pytest.mark.asyncio
async def test_initialize_failure_stops_watchers_it_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression (rerun 9, Group C): a failed initialize() must not leak
    its hot-reload watchers.

    initialize() starts the rune watchers in _load_runes() and only then
    resolves the model's Realm. With no Realm registered it raises
    NoRealmRegisteredError -- and the CLI's bootstrap self-heal then
    installs the missing Realm's rune into the still-watched extensions
    directory. On watchdog 7 that in-place tree copy races the observer's
    _recursive_simulate walk (KeyError on a directory created between
    its registration and simulation walks), kills the singleton inotify
    thread, and silently disables hot reload for the rest of the process.
    Stopping the watchers on any initialize() failure keeps the install
    off a live watch, so the race has nothing to fire on.
    """
    from mvge.provider.base import NoRealmRegisteredError

    monkeypatch.setenv("HOME", str(tmp_path))
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    agent_name = "watcher-leak-probe"
    config_dir = tmp_path / ".agents" / "agents" / agent_name
    config_dir.mkdir(parents=True)
    (config_dir / "SYSTEM.md").write_text("# Probe\n", encoding="utf-8")
    extensions_dir = tmp_path / "runes"
    extensions_dir.mkdir()

    env = MvgeEnvironment.resolve(
        agent_name=agent_name,
        project_dir=project_dir,
        config_dir=config_dir,
        caller_dir=tmp_path,
        overrides={"model": "pollinations/openai"},
    )
    # Empty registry: no Realm factory for pollinations, so the resolve
    # after _load_runes() raises, exactly as on a Realm-less first run.
    registry = RealmRegistry()
    agent = Mvge(
        api_key="dummy",
        environment=env,
        tome_dir=tmp_path / "tomes",
        runes_paths=[str(extensions_dir)],
        provider_registry=registry,
    )
    with pytest.raises(NoRealmRegisteredError):
        await agent.initialize()

    assert agent._rune_lifecycle is not None
    assert agent._rune_lifecycle.watchers == []
    # Cleanup after a failed initialize must stay safe to call.
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
    monkeypatch.delenv("MVGE_API_KEY", raising=False)

    assert _resolve_api_key(realm="opencode") == ""
    assert _resolve_api_key(realm="openrouter") == "sk-or-secret"

    # An explicit key is not a guess about which Realm was meant, so it stands.
    assert _resolve_api_key("sk-explicit", realm="opencode") == "sk-explicit"


@pytest.mark.asyncio
async def test_mvge_workspace_untrusted_emits_sigil_without_steering(
    tmp_path: Path,
) -> None:
    from mvge.runes.trust import WorkspaceTrustStatus, WorkspaceTrustStore
    from mvge.runes.types import SigilHook

    project_dir = tmp_path / "untrusted_proj"
    project_dir.mkdir()
    trust_file = tmp_path / "trust.json"
    store = WorkspaceTrustStore(trust_file=trust_file)
    store.set_trust(project_dir, WorkspaceTrustStatus.DECLINED)

    env = MvgeEnvironment.resolve(
        agent_name="test_agent",
        project_dir=project_dir,
        config_dir=tmp_path / "cfg",
        caller_dir=tmp_path,
        overrides={"model": "openrouter/free"},
    )
    registry = RealmRegistry()
    registry.register_realm_factory(
        realm_for_model_id("openrouter/free"), lambda **kwargs: MagicMock(spec=Realm)
    )

    untrusted_events: list[dict[str, Any]] = []

    async def on_untrusted(data: Any) -> None:
        untrusted_events.append(data)

    agent = Mvge(
        api_key="k",
        environment=env,
        tome_dir=tmp_path / "tomes",
        provider_registry=registry,
        trust_store=store,
    )
    runner = RuneRunner()
    runner.register_handler(SigilHook.WORKSPACE_UNTRUSTED, on_untrusted)
    agent.set_runner(runner)

    await agent.initialize()
    try:
        assert len(untrusted_events) == 1
        assert untrusted_events[0].workspace_root == str(project_dir)
        assert untrusted_events[0].status == "declined"
        # The status must not become a model-answered turn: a steered
        # turn queued before the user's prompt would displace the
        # user's answer in print mode.
        assert agent.harness.state.steer_queue == []
        assert agent.workspace_trust_status == WorkspaceTrustStatus.DECLINED
    finally:
        await agent.close()


def bash() -> str:
    """Run a bash command."""
    return "ok"


_GATEWAY_META_SPELLS = (
    "search",
    "activate",
    "execute",
    "unload",
    "tool_search",
    "skill_search",
    "skill_execute",
    "mcp_search",
)


async def _child_scope_runner() -> RuneRunner:
    """A gateway runner (8 seeker-style meta spells) plus a bridge spell."""
    runner = RuneRunner()

    def seeker_factory(api: Any) -> None:
        for name in _GATEWAY_META_SPELLS:
            api.register_spell(SpellDefinition(name=name, description=""))

    def bridge_factory(api: Any) -> None:
        api.register_spell(SpellDefinition(name="manage_mvges", description=""))

    await runner.load_rune_loads(
        [
            RuneLoad(manifest=_gateway_manifest("seeker"), factory=seeker_factory),
            RuneLoad(
                manifest=_gateway_manifest("bridge", gateway=False),
                factory=bridge_factory,
            ),
        ]
    )
    return runner


async def _zero_spell_gateway_runner(
    diagnostics: list[Diagnostic] | None,
) -> RuneRunner:
    """A designated gateway rune that registered no spells."""
    runner = RuneRunner()
    await runner.load_rune_loads(
        [RuneLoad(manifest=_gateway_manifest("seeker"), factory=None)],
        diagnostics=diagnostics,
    )
    return runner


class TestChildSpellScopeThroughGateway:
    """D1: a child's dispatch set must equal its declared scope at spawn."""

    @pytest.mark.asyncio
    async def test_child_allowed_spell_survives_gateway(self) -> None:
        runner = await _child_scope_runner()
        assert _apply_gateway_allowlist(runner) == "seeker"
        agent = Mvge(api_key="test-key", spells=[bash], allowed_spells=["bash"])
        agent._runner = runner  # type: ignore[assignment]
        agent._apply_child_scope(runner)
        names = [s.name for s in agent._build_spells()]
        assert names == ["bash"]

    @pytest.mark.asyncio
    async def test_child_allowed_empty_dispatch_is_empty(self) -> None:
        runner = await _child_scope_runner()
        assert _apply_gateway_allowlist(runner) == "seeker"
        agent = Mvge(api_key="test-key", spells=[bash], allowed_spells=[])
        agent._runner = runner  # type: ignore[assignment]
        agent._apply_child_scope(runner)
        assert [s.name for s in agent._build_spells()] == []

    @pytest.mark.asyncio
    async def test_unknown_allowed_spell_is_summon_error(self) -> None:
        runner = await _child_scope_runner()
        assert _apply_gateway_allowlist(runner) == "seeker"
        agent = Mvge(
            api_key="test-key",
            spells=[bash],
            allowed_spells=["definitely_not_a_spell"],
        )
        agent._runner = runner  # type: ignore[assignment]
        with pytest.raises(ValueError, match="definitely_not_a_spell"):
            agent._apply_child_scope(runner)

    @pytest.mark.asyncio
    async def test_builtin_allowed_name_validates_before_any_parent_build(
        self,
    ) -> None:
        # No spell build runs before the scope step: the validation source
        # is the agent's own discovered spells plus registered rune spells,
        # never the discoverable-builtin cache populated by a build.
        runner = await _child_scope_runner()
        assert _apply_gateway_allowlist(runner) == "seeker"
        agent = Mvge(api_key="test-key", spells=[bash], allowed_spells=["bash"])
        agent._runner = runner  # type: ignore[assignment]
        agent._apply_child_scope(runner)
        assert [s.name for s in agent._build_spells()] == ["bash"]

        runner2 = await _child_scope_runner()
        assert _apply_gateway_allowlist(runner2) == "seeker"
        agent2 = Mvge(
            api_key="test-key",
            spells=[bash],
            allowed_spells=["definitely_not_a_spell"],
        )
        agent2._runner = runner2  # type: ignore[assignment]
        with pytest.raises(ValueError, match="definitely_not_a_spell"):
            agent2._apply_child_scope(runner2)

    @pytest.mark.asyncio
    async def test_plan_mode_does_not_reject_non_readonly_allowed(self) -> None:
        # Re-review note: validation must use the unfiltered union, not
        # the plan-mode-filtered available_spells property, or a plan-mode
        # child could never be allowed a non-read-only spell such as bash.
        runner = await _child_scope_runner()
        assert _apply_gateway_allowlist(runner) == "seeker"
        agent = Mvge(api_key="test-key", spells=[bash], allowed_spells=["bash"])
        agent._runner = runner  # type: ignore[assignment]
        agent._plan_mode = True
        agent._apply_child_scope(runner)
        assert "bash" in (runner.get_global_spell_allowlist() or [])

    @pytest.mark.asyncio
    async def test_designated_gateway_with_no_spells_missing_dep_fails(
        self,
    ) -> None:
        runner = await _zero_spell_gateway_runner(
            [
                Diagnostic(
                    kind=DiagnosticKind.MISSING_DEP,
                    rune_name="seeker",
                    message="Rune 'seeker' requires python dependency "
                    "'aiohttp' which is not installed.",
                )
            ]
        )
        assert _apply_gateway_allowlist(runner) is None
        agent = Mvge(api_key="test-key", spells=[bash], allowed_spells=["bash"])
        agent._runner = runner  # type: ignore[assignment]
        with pytest.raises(GatewayLoadError) as exc_info:
            agent._apply_child_scope(runner)
        assert exc_info.value.code == "gateway_load_failed"
        assert exc_info.value.gateway_rune_name == "seeker"
        assert "seeker" in str(exc_info.value)
        assert "mvge setup install" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_designated_gateway_with_no_spells_load_failure_fails(
        self,
    ) -> None:
        runner = await _zero_spell_gateway_runner(
            [
                Diagnostic(
                    kind=DiagnosticKind.LOAD_FAILURE,
                    rune_name="seeker",
                    message="Failed to execute rune module seeker: boom",
                )
            ]
        )
        assert _apply_gateway_allowlist(runner) is None
        agent = Mvge(api_key="test-key", spells=[bash])
        agent._runner = runner  # type: ignore[assignment]
        with pytest.raises(GatewayLoadError) as exc_info:
            agent._apply_child_scope(runner)
        assert exc_info.value.code == "gateway_load_failed"
        assert "seeker" in str(exc_info.value)
        assert "mvge info" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_designated_gateway_with_no_spells_no_diagnostic_starts(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        runner = await _zero_spell_gateway_runner(None)
        assert _apply_gateway_allowlist(runner) is None
        agent = Mvge(api_key="test-key", spells=[bash], allowed_spells=["bash"])
        agent._runner = runner  # type: ignore[assignment]
        with caplog.at_level(logging.WARNING):
            agent._apply_child_scope(runner)
        assert runner.get_global_spell_allowlist() is None
        assert any("seeker" in record.message for record in caplog.records)
        assert [s.name for s in agent._build_spells()] == ["bash"]

    @pytest.mark.asyncio
    async def test_gateway_absent_is_still_full_view(self) -> None:
        runner = RuneRunner()

        def bridge_factory(api: Any) -> None:
            api.register_spell(SpellDefinition(name="manage_mvges", description=""))

        await runner.load_rune_loads(
            [
                RuneLoad(
                    manifest=_gateway_manifest("bridge", gateway=False),
                    factory=bridge_factory,
                )
            ]
        )
        assert _apply_gateway_allowlist(runner) is None
        agent = Mvge(api_key="test-key", spells=[bash])
        agent._runner = runner  # type: ignore[assignment]
        agent._apply_child_scope(runner)
        assert runner.get_global_spell_allowlist() is None
        assert sorted(s.name for s in agent._build_spells()) == [
            "bash",
            "manage_mvges",
        ]

    @pytest.mark.asyncio
    async def test_widen_is_noop_when_no_allowlist_active(self) -> None:
        # Runner.widen_global_allowlist would CREATE an allowlist; the
        # child-scope step must not call it unless one is already active,
        # or a no-gateway child would be narrowed to its allowed set
        # instead of keeping its full view filtered by allowed_spells.
        runner = await _child_scope_runner()
        agent = Mvge(api_key="test-key", spells=[bash], allowed_spells=["bash"])
        agent._runner = runner  # type: ignore[assignment]
        agent._apply_child_scope(runner)
        assert runner.get_global_spell_allowlist() is None
        assert [s.name for s in agent._build_spells()] == ["bash"]
