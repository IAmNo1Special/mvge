from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from mvge.agent import (
    Mvge,
    MvgeAgent,
    ReloadResult,
)
from mvge.agent.environment import MvgeEnvironment
from mvge.core.channel import MvgeResponse
from mvge.core.events import (
    ContemplationLevel,
    QueueMode,
)
from mvge.core.invocations import MvgeInvocation


class DummyAgent:
    """Mock agent implementing the MvgeAgent protocol without inheriting Mvge."""

    def __init__(self) -> None:
        self.queue_mode: QueueMode = QueueMode.ONE_AT_A_TIME
        self.environment = MvgeEnvironment.resolve(
            agent_name="dummy", allow_unknown_agent=True
        )

    @property
    def tome_id(self) -> str | None:
        return "tome-123"

    @property
    def session_id(self) -> str | None:
        return self.tome_id

    @property
    def model_id(self) -> str:
        return "test/model"

    @property
    def model_registry(self) -> Any:
        return MagicMock()

    @property
    def contemplation_level(self) -> ContemplationLevel | str:
        return "medium"

    @property
    def mana_used(self) -> int | None:
        return 42

    @property
    def enabled_spells(self) -> list[str]:
        return ["read", "write"]

    @property
    def available_spells(self) -> list[str]:
        return ["read", "write", "bash"]

    @property
    def runner(self) -> Any:
        return None

    def set_enabled_spells(self, spell_names: list[str]) -> None:
        pass

    def set_plan_mode(self, enabled: bool) -> None:
        pass

    @property
    def registered_providers(self) -> list[str]:
        return ["openrouter"]

    def on(
        self,
        event_type: Any,
        callback: Any,
    ) -> Any:
        return lambda: None

    def steer(self, text: str) -> None:
        pass

    def follow_up(self, text: str) -> None:
        pass

    def abort(self) -> None:
        pass

    async def initialize(self) -> None:
        pass

    async def run(self, prompt: str) -> MvgeInvocation:
        return MvgeResponse(content=[{"type": "text", "text": "ok"}])

    async def switch_model(self, model_id: str) -> None:
        pass

    async def set_contemplation_level(self, level: Any) -> None:
        pass

    async def reset_session(self, *, resume_tome_id: str | None = None) -> None:
        pass

    def build_snapshot(self) -> Any:
        return None

    async def fork_tome(self, entry_id: str | None = None) -> str:
        return "forked-tome"

    async def checkout_leaf(self, leaf_id: str) -> None:
        pass

    async def list_leaves(self) -> list[str]:
        return ["leaf-1"]

    async def undo(self) -> str | None:
        return "target-1"

    async def compact(self) -> str:
        return "compacted"

    async def reload(self) -> ReloadResult:
        return ReloadResult(ok=True, message="Reload complete.")

    def get_skills_catalog(self) -> list[dict[str, str]]:
        return []

    async def activate_skill(self, name: str) -> str:
        return f"Activated {name}"

    def get_registered_commands(self) -> list[Any]:
        return []

    async def close(self) -> None:
        pass


class TestMvgeAgentProtocol:
    def test_mvge_conforms(self) -> None:
        agent = Mvge(api_key="test-key")
        assert isinstance(agent, MvgeAgent)
        assert agent.session_id == agent.tome_id
        assert agent.model_id == agent._model_id
        assert agent.contemplation_level == agent._contemplation_level
        assert agent.mana_used is None
        assert isinstance(agent.enabled_spells, list)
        assert isinstance(agent.get_registered_commands(), list)

    def test_coding_mvge_conforms(self) -> None:
        # Hermetic: marketplace mvges resolve from ~/.agents, absent on clean
        # CI runners, so inject empty spells to isolate from global state.
        agent = Mvge(name="coding_mvge", api_key="test-key", spells=[])
        assert isinstance(agent, MvgeAgent)
        assert agent.session_id == agent.tome_id
        assert agent.model_id == agent._model_id
        assert isinstance(agent.enabled_spells, list)
        assert isinstance(agent.get_registered_commands(), list)

    def test_dummy_agent_conforms(self) -> None:
        dummy = DummyAgent()
        assert isinstance(dummy, MvgeAgent)
        assert dummy.session_id == "tome-123"
        assert dummy.mana_used == 42
        assert dummy.model_id == "test/model"
        assert dummy.get_registered_commands() == []
        assert dummy.runner is None

    def test_incomplete_agent_fails_conformance(self) -> None:
        class IncompleteAgent:
            pass

        assert not isinstance(IncompleteAgent(), MvgeAgent)

    @pytest.mark.asyncio
    async def test_mvge_mana_used_with_state(self) -> None:
        agent = Mvge(api_key="test-key")
        state_mock = MagicMock()
        state_mock.mana_used = 1337
        agent._state = state_mock  # type: ignore[attr-defined]
        assert agent.mana_used == 1337

    @pytest.mark.asyncio
    async def test_mvge_load_runes_method(self) -> None:
        agent = Mvge(api_key="test-key")
        agent._load_runes = AsyncMock()  # type: ignore[method-assign]
        await agent.load_runes()
        agent._load_runes.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mvge_available_spells_and_set_enabled(self) -> None:
        agent = Mvge(api_key="test-key")
        assert isinstance(agent.available_spells, list)
        agent.set_enabled_spells(["read"])
        assert agent.enabled_spells == ["read"]

    @pytest.mark.asyncio
    async def test_mvge_reset_session(self) -> None:
        agent = Mvge(api_key="test-key")
        agent.close = AsyncMock()  # type: ignore[method-assign]
        agent.initialize = AsyncMock()  # type: ignore[method-assign]
        await agent.reset_session(resume_tome_id="new-tome-456")
        agent.close.assert_awaited_once()
        assert agent._tome_resume == "new-tome-456"
        agent.initialize.assert_awaited_once()
