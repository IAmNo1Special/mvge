import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from mvge.agent import MvgeAgent
from mvge.cli.agent_factory import (
    create_agent,
    default_agent_factory,
    get_default_agent_factory,
    resolve_agent_factory,
    set_default_agent_factory,
)
from mvge.cli.commands.build import _assemble
from mvge.cli.main import _run_agent
from mvge.core.channel import MvgeResponse
from mvge.core.events import QueueMode


class MockAgent:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.initialized = False
        self.closed = False
        self.queue_mode = QueueMode.ONE_AT_A_TIME
        self.model_id = kwargs.get("model", "mock-model")
        self.contemplation_level = "medium"
        self.mana_used = 10
        self.enabled_spells = ["read", "write"]
        self.registered_providers = ["mock-realm"]
        self.tome_id = "mock-tome-123"
        self.session_id = "mock-tome-123"
        self.environment = MagicMock()
        self.environment.diagnostics = []

    def on(self, event_type: Any, callback: Any) -> Any:
        return lambda: None

    def steer(self, text: str) -> None:
        pass

    def follow_up(self, text: str) -> None:
        pass

    def abort(self) -> None:
        pass

    async def initialize(self) -> None:
        self.initialized = True

    async def run(self, prompt: str) -> Any:
        return MvgeResponse(
            stop_reason="stop",
            content=[{"type": "text", "text": f"Mock reply to: {prompt}"}],
        )

    async def switch_model(self, model_id: str) -> None:
        self.model_id = model_id

    def build_snapshot(self) -> Any:
        mock_snap = MagicMock()
        mock_snap.agent_name = "mock-agent"
        return mock_snap

    async def load_runes(self) -> None:
        pass

    async def close(self) -> None:
        self.closed = True


class TestAgentFactoryRegistry:
    def test_default_factory_resolution(self) -> None:
        factory = resolve_agent_factory()
        assert callable(factory)
        assert factory == get_default_agent_factory()

    def test_custom_factory_override_and_reset(self) -> None:
        mock_factory = MagicMock(return_value=MockAgent())
        try:
            set_default_agent_factory(mock_factory)
            assert get_default_agent_factory() == mock_factory
            assert resolve_agent_factory() == mock_factory
            assert resolve_agent_factory(None) == mock_factory
        finally:
            set_default_agent_factory(None)
            assert get_default_agent_factory() == default_agent_factory

    def test_explicit_factory_precedence(self) -> None:
        custom_factory = MagicMock(return_value=MockAgent())
        resolved = resolve_agent_factory(custom_factory)
        assert resolved == custom_factory

    @pytest.mark.asyncio
    async def test_create_agent_with_mock_factory(self) -> None:
        mock_instance = MockAgent()
        factory = MagicMock(return_value=mock_instance)

        agent = await create_agent(
            model="test/model",
            api_key="sk-or-test-key",
            spells="read,write",
            temperature=0.5,
            max_tokens=2048,
            contemplation="high",
            agent_factory=factory,
        )

        assert agent is mock_instance
        assert mock_instance.initialized is True
        factory.assert_called_once()
        call_kwargs = factory.call_args.kwargs
        assert call_kwargs["api_key"] == "sk-or-test-key"
        assert call_kwargs["spells"] == ["read", "write"]

    @pytest.mark.asyncio
    async def test_create_agent_with_default_factory(self) -> None:
        from unittest.mock import AsyncMock

        from mvge.provider.registry import get_default_realm_registry

        registry = get_default_realm_registry()
        mock_realm = MagicMock()
        mock_realm.close = AsyncMock()
        registry.register_realm_factory("openrouter", lambda **kw: mock_realm)
        try:
            agent = await create_agent(
                model="nvidia/nemotron-3-ultra-550b-a55b:free",
                api_key="sk-or-test-key",
                spells="read,write",
            )
            assert isinstance(agent, MvgeAgent)
            assert agent.model_id == "nvidia/nemotron-3-ultra-550b-a55b:free"
            await agent.close()
        finally:
            registry.unregister_realm_factory("openrouter")


class TestCliCommandsDecoupling:
    def test_no_static_coding_mvge_imports_in_cli(self) -> None:
        import mvge.cli.commands.build as build_mod
        import mvge.cli.commands.repl as repl_mod
        import mvge.cli.commands.tui as tui_mod
        import mvge.cli.main as main_mod

        for mod in (main_mod, repl_mod, tui_mod, build_mod):
            assert "CodingMvge" not in mod.__dict__, (
                f"{mod.__name__} should not statically import CodingMvge"
            )

    @pytest.mark.asyncio
    async def test_print_mode_reaches_the_agent_with_no_credential(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The one-shot path must not re-demand a key the Realm already has.

        This is a third gate, after the callback's own check and the library's:
        ``validate_api_key`` is called again inside ``_run_agent``, and it was
        called with no ``model_id``, so it had no Realm to reason about and could
        only ask for a key unconditionally. The failure that was guarded was a
        Summoner with no credentials reaching it.

        That failure mode no longer has a zero-credential default -- ADR-0020 --
        so the test now guards the mirror: a Summoner who *has* set
        ``OPENROUTER_API_KEY`` must not be stopped at a third gate that cannot
        see it. Asserted on the response being printed, so it covers the whole
        one-shot path rather than the exit code alone.
        """
        mock_instance = MockAgent()
        factory = MagicMock(return_value=mock_instance)

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-v1-test-key"}):
            exit_code = await _run_agent(
                incantation="hello world",
                model_id="openrouter/free",
                api_key="sk-or-v1-test-key",
                temperature=0.7,
                max_tokens=1000,
                contemplation_level="low",
                spells_enabled=["read"],
                extension_dir=None,
                resume=None,
                provider_name=None,
                tome_dir=None,
                tui=False,
                agent_factory=factory,
            )

        assert exit_code == 0
        assert "API key is required" not in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_run_agent_print_mode_with_mock_factory(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        mock_instance = MockAgent()
        factory = MagicMock(return_value=mock_instance)

        exit_code = await _run_agent(
            incantation="hello world",
            model_id="test-model",
            api_key="sk-or-test-key",
            temperature=0.7,
            max_tokens=1000,
            contemplation_level="low",
            spells_enabled=["read"],
            extension_dir=None,
            resume=None,
            provider_name=None,
            tome_dir=None,
            tui=False,
            agent_factory=factory,
        )

        assert exit_code == 0
        assert mock_instance.initialized is True
        assert mock_instance.closed is True
        captured = capsys.readouterr()
        assert "Mock reply to: hello world" in captured.out

    @pytest.mark.asyncio
    async def test_assemble_build_with_mock_factory(self) -> None:
        mock_instance = MockAgent()
        factory = MagicMock(return_value=mock_instance)

        snapshot = await _assemble(
            agent_name="default-mvge",
            extension_dir=None,
            agent_factory=factory,
        )

        assert snapshot.agent_name == "mock-agent"
        factory.assert_called_once()
