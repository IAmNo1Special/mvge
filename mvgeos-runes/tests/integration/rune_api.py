from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from mvgeos_runes.installer import read_or_create_install_id
from mvgeos_runes.rune_api import RuneAPI
from mvgeos_runes.rune_runner import RuneRunner
from mvgeos_runes.types import (
    RuneLoad,
    RuneManifest,
    Sandbox,
    SigilHook,
    SpellDefinition,
)


class StubSandbox:
    """Sandbox test double satisfying the runes Sandbox protocol."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, set[str] | None]] = []

    def execute_code(
        self,
        code_str: str,
        context_globals: dict[str, Any] | None = None,
        timeout_seconds: float = 5.0,
        allowed_modules: set[str] | None = None,
    ) -> dict[str, Any]:
        self.calls.append((code_str, allowed_modules))
        return {"x": 3}


@pytest.fixture
def sandbox() -> StubSandbox:
    return StubSandbox()


@pytest.fixture
def runner(sandbox: StubSandbox) -> RuneRunner:
    return RuneRunner(sandbox_factory=lambda: sandbox)


@pytest.fixture
def api(runner: RuneRunner) -> RuneAPI:
    return runner.create_api()


class TestRuneAPIRegistration:
    def test_register_spell_delegates_to_runner(
        self, api: RuneAPI, runner: RuneRunner
    ) -> None:
        spell = SpellDefinition(name="test_spell", description="Test")
        api.register_spell(spell)
        assert runner.get_all_registered_spells() == [spell]

    def test_register_spell_first_wins(self, api: RuneAPI, runner: RuneRunner) -> None:
        assert (
            api.register_spell(SpellDefinition(name="dup", description="first")) is True
        )
        assert (
            api.register_spell(SpellDefinition(name="dup", description="second"))
            is False
        )
        assert len(runner.get_all_registered_spells()) == 1
        assert runner.get_all_registered_spells()[0].description == "first"

    def test_register_spell_override(self, api: RuneAPI, runner: RuneRunner) -> None:
        assert (
            api.register_spell(SpellDefinition(name="dup", description="first")) is True
        )
        assert (
            api.register_spell(
                SpellDefinition(name="dup", description="second"), override=True
            )
            is True
        )
        assert len(runner.get_all_registered_spells()) == 1
        assert runner.get_all_registered_spells()[0].description == "second"

    def test_on_registers_handler(self, api: RuneAPI, runner: RuneRunner) -> None:
        def handler(data: dict) -> None:
            return None

        api.on(SigilHook.TURN_START, handler)
        assert handler in runner.get_sigil_handlers(SigilHook.TURN_START)

    def test_get_all_spells(self, api: RuneAPI, runner: RuneRunner) -> None:
        spell = SpellDefinition(name="s1", description="")
        api.register_spell(spell)
        assert api.get_all_spells() == [spell]

    def test_get_active_spells(self, api: RuneAPI, runner: RuneRunner) -> None:
        api.register_spell(SpellDefinition(name="a", description=""))
        api.register_spell(SpellDefinition(name="b", description=""))
        assert api.get_active_spells() == ["a", "b"]

    def test_set_active_spells_delegates_to_runner(
        self, api: RuneAPI, runner: RuneRunner
    ) -> None:
        api.register_spell(SpellDefinition(name="tool_search", description=""))
        api.register_spell(SpellDefinition(name="bash", description=""))
        api.set_active_spells(["tool_search"])
        assert runner.get_active_spells() == ["tool_search"]

    def test_register_command(self, api: RuneAPI, runner: RuneRunner) -> None:
        def handler() -> None:
            return None

        assert (
            api.register_command("my_cmd", description="A command", handler=handler)
            is True
        )
        assert (
            api.register_command("my_cmd", description="Duplicate", handler=handler)
            is False
        )
        cmds = runner.get_commands()
        assert len(cmds) == 1
        assert cmds[0].name == "my_cmd"
        assert cmds[0].description == "A command"
        assert cmds[0].handler is handler

    def test_register_command_override(self, api: RuneAPI, runner: RuneRunner) -> None:
        assert api.register_command("my_cmd", description="Initial") is True
        assert (
            api.register_command("my_cmd", description="Overridden", override=True)
            is True
        )
        cmds = runner.get_commands()
        assert len(cmds) == 1
        assert cmds[0].description == "Overridden"

    def test_register_shortcut(self, api: RuneAPI, runner: RuneRunner) -> None:
        def handler() -> None:
            return None

        assert (
            api.register_shortcut("ctrl+k", description="Test", handler=handler) is True
        )
        assert (
            api.register_shortcut("ctrl+k", description="Duplicate", handler=handler)
            is False
        )
        shortcuts = runner.get_shortcuts()
        assert len(shortcuts) == 1
        assert shortcuts[0].key == "ctrl+k"
        assert shortcuts[0].description == "Test"

    def test_register_shortcut_override(self, api: RuneAPI, runner: RuneRunner) -> None:
        assert api.register_shortcut("ctrl+k", description="Initial") is True
        assert (
            api.register_shortcut("ctrl+k", description="Overridden", override=True)
            is True
        )
        shortcuts = runner.get_shortcuts()
        assert len(shortcuts) == 1
        assert shortcuts[0].description == "Overridden"

    def test_get_shortcuts(self, api: RuneAPI, runner: RuneRunner) -> None:
        assert api.register_shortcut("ctrl+k", description="Test") is True
        assert api.register_shortcut("ctrl+r", description="Reload") is True
        result = api.get_shortcuts()
        assert len(result) == 2
        assert result[0].key == "ctrl+k"
        assert result[1].key == "ctrl+r"

    def test_register_provider(self, api: RuneAPI, runner: RuneRunner) -> None:
        assert api.register_provider("custom", {"api_key": "sekret"}) is True
        assert api.register_provider("custom", {"api_key": "other"}) is False
        assert api.get_registered_providers()["custom"] == {"api_key": "sekret"}

    def test_register_provider_override(self, api: RuneAPI, runner: RuneRunner) -> None:
        assert api.register_provider("custom", {"api_key": "sekret"}) is True
        assert (
            api.register_provider("custom", {"api_key": "new_sekret"}, override=True)
            is True
        )
        assert api.get_registered_providers()["custom"] == {"api_key": "new_sekret"}

    def test_send_message(self, api: RuneAPI, runner: RuneRunner) -> None:
        api.send_message("hello from rune")
        assert runner._message_queue == ["hello from rune"]

    def test_set_session_name(self, api: RuneAPI, runner: RuneRunner) -> None:
        api.set_session_name("rune-session")
        assert runner._session_name == "rune-session"


class TestRuneAPIActiveSpells:
    def test_api_scoped_to_rune_name(self, runner: RuneRunner) -> None:
        api_a = runner.create_api(rune_name="rune_a")
        api_b = runner.create_api(rune_name="rune_b")
        api_a.register_spell(SpellDefinition(name="a", description=""))
        api_b.register_spell(SpellDefinition(name="b", description=""))
        # rune_a narrows its own surface; rune_b is unaffected.
        api_a.set_active_spells(["a"])
        assert set(runner.get_active_spells()) == {"a", "b"}

    def test_two_apis_compose_active_sets(self, runner: RuneRunner) -> None:
        api_a = runner.create_api(rune_name="rune_a")
        api_b = runner.create_api(rune_name="rune_b")
        api_a.register_spell(SpellDefinition(name="a1", description=""))
        api_a.register_spell(SpellDefinition(name="a2", description=""))
        api_b.register_spell(SpellDefinition(name="b1", description=""))
        api_a.set_active_spells(["a1"])
        api_b.set_active_spells(["b1"])
        assert set(runner.get_active_spells()) == {"a1", "b1"}


class TestRuneAPIEventBus:
    def test_on_event_delegates_to_runner(
        self, api: RuneAPI, runner: RuneRunner
    ) -> None:
        handler = MagicMock()
        api.on_event("custom:ch", handler)
        assert "custom:ch" in runner.get_event_channels()

    def test_emit_event_delegates_to_runner(
        self, api: RuneAPI, runner: RuneRunner
    ) -> None:
        handler = MagicMock()
        api.on_event("custom:ch", handler)
        api.emit_event("custom:ch", {"data": 1})
        handler.assert_called_once_with({"data": 1})


class TestRuneAPISandbox:
    def test_sandbox_property_returns_injected_sandbox(
        self, api: RuneAPI, runner: RuneRunner, sandbox: StubSandbox
    ) -> None:
        assert isinstance(api.sandbox, Sandbox)
        assert api.sandbox is sandbox
        assert api.sandbox is runner.sandbox

    def test_sandbox_execute_code_delegates(self, api: RuneAPI) -> None:
        result = api.sandbox.execute_code(
            "x = 1 + 2",
            allowed_modules=set(),
        )
        assert result == {"x": 3}

    def test_sandbox_without_factory_raises(self) -> None:
        runner = RuneRunner()
        with pytest.raises(RuntimeError, match="No sandbox configured"):
            _ = runner.sandbox


class TestRuneAPISkillRegistration:
    def test_register_skill_path_delegates_to_runner(
        self, api: RuneAPI, runner: RuneRunner, tmp_path: Path
    ) -> None:
        p = tmp_path / "skills"
        api.register_skill_path(p)
        assert runner.skill_catalog.get_registered_skill_paths() == [p.resolve()]

    def test_register_skill_delegates_to_runner(
        self, api: RuneAPI, runner: RuneRunner
    ) -> None:
        from mvgeos_runes.types import SkillManifest

        manifest = SkillManifest(name="api-skill", description="API registered skill")
        api.register_skill(manifest)
        assert runner.skill_catalog.get_skills() == [manifest]
        assert api.get_skills() == [manifest]

    def test_context_property_delegates(self, api: RuneAPI, runner: RuneRunner) -> None:
        assert api.context is runner.context

    def test_realm_factory_delegates(self, api: RuneAPI, runner: RuneRunner) -> None:
        def dummy_factory(model_id: str) -> str:
            return f"realm-{model_id}"

        api.register_realm_factory("custom:", dummy_factory)
        factories = api.get_registered_realm_factories()
        assert "custom:" in factories
        assert factories["custom:"] is dummy_factory


class TestRuneAPIGlobalAllowlist:
    def test_get_global_allowlist_defaults_to_none(
        self, api: RuneAPI, runner: RuneRunner
    ) -> None:
        assert api.get_global_spell_allowlist() is None

    def test_rune_api_cannot_write_global_allowlist(
        self, api: RuneAPI, runner: RuneRunner
    ) -> None:
        """Runes must not rewrite the engine-owned global filter (untrusted code)."""
        runner.set_global_spell_allowlist(["tool_search"])
        with pytest.raises(AttributeError):
            api.set_global_spell_allowlist(None)  # type: ignore[attr-defined]
        assert runner.get_global_spell_allowlist() == ["tool_search"]


class TestRuneAPIWidenGlobalAllowlist:
    async def _load_gateway(self, runner: RuneRunner, name: str = "seeker") -> RuneAPI:
        def factory(api: RuneAPI) -> None:
            api.register_spell(SpellDefinition(name="tool_search", description=""))

        manifest = RuneManifest(
            name=name, version="1.0.0", description="", spell_gateway=True
        )
        await runner.load_rune_loads([RuneLoad(manifest=manifest, factory=factory)])
        return runner.create_api(rune_name=name)

    @pytest.mark.asyncio
    async def test_gateway_rune_can_widen(self, runner: RuneRunner) -> None:
        api = await self._load_gateway(runner)
        runner.set_global_spell_allowlist(["tool_search"])
        api.widen_global_allowlist(["weather_lookup"])
        assert set(runner.get_global_spell_allowlist()) == {
            "tool_search",
            "weather_lookup",
        }

    @pytest.mark.asyncio
    async def test_non_gateway_rune_can_widen_own_spells(
        self, api: RuneAPI, runner: RuneRunner
    ) -> None:
        """Widening is additive-only, so any rune may reveal its own spells
        (e.g. heal-my-goap keeping synthesized spells visible)."""
        await self._load_gateway(runner)
        runner.set_global_spell_allowlist(["tool_search"])
        api.widen_global_allowlist(["heal_spell"])
        assert set(runner.get_global_spell_allowlist()) == {
            "tool_search",
            "heal_spell",
        }

    def test_widen_is_noop_without_active_allowlist(
        self, api: RuneAPI, runner: RuneRunner
    ) -> None:
        """Widening must not create the filter: that would let a rune narrow
        the model's view to just its own spells."""
        api.widen_global_allowlist(["weather_lookup"])
        assert runner.get_global_spell_allowlist() is None


class TestRuneAPIInstallId:
    @pytest.mark.asyncio
    async def test_factory_api_carries_rune_install_id(
        self, runner: RuneRunner, tmp_path: Path
    ) -> None:
        rune_dir = tmp_path / "my-rune"
        rune_dir.mkdir()
        expected = read_or_create_install_id(rune_dir)
        seen: list[str | None] = []

        def factory(api: RuneAPI) -> None:
            seen.append(api.install_id)

        manifest = RuneManifest(
            name="my-rune",
            version="0.1.0",
            description="test rune",
            path=str(rune_dir),
        )
        await runner.load_rune_loads([RuneLoad(manifest=manifest, factory=factory)])
        assert seen == [expected]

    def test_api_without_loaded_rune_has_no_install_id(
        self, runner: RuneRunner
    ) -> None:
        assert runner.create_api(rune_name="unknown").install_id is None

    @pytest.mark.asyncio
    async def test_factory_api_mints_install_id_when_missing(
        self, runner: RuneRunner, tmp_path: Path
    ) -> None:
        """Test factory api mints install id when missing.

        The host mints the installer-owned id at load when the installer
        never did (or the file was lost), so policy binding always has an
        id to stamp; a fresh mint invalidates prior grants via mismatch,
        which is the fail-closed direction.
        """
        rune_dir = tmp_path / "my-rune"
        rune_dir.mkdir()
        seen: list[str | None] = []

        def factory(api: RuneAPI) -> None:
            seen.append(api.install_id)

        manifest = RuneManifest(
            name="my-rune",
            version="0.1.0",
            description="test rune",
            path=str(rune_dir),
        )
        await runner.load_rune_loads([RuneLoad(manifest=manifest, factory=factory)])
        assert len(seen) == 1
        minted = seen[0]
        assert minted
        assert (rune_dir / ".install-id").read_text(encoding="utf-8").strip() == minted
