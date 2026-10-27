from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mvge.agent.mvge import Mvge
from mvge.core.channel import Model
from mvge.core.errors import TomeResumeError
from mvge.core.spells import MvgeSpell
from mvge.tome.handle import TomeHandleFactory
from mvge.tome.types import TomeEntry, TomeEntryType


def _seed_tome(
    tome_dir: Path,
    *,
    model: str | None = None,
    contemplation_level: str | None = None,
    spells: list[str] | None = None,
) -> str:
    factory = TomeHandleFactory(tome_dir)
    write = factory.create_tome(
        "/test",
        model=model,
        contemplation_level=contemplation_level,
        spells=spells,
    )
    return write.tome_id


def _mock_model() -> Model:
    return Model(
        id="test-model",
        name="test-model",
        realm="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        context_window=4096,
        max_tokens=1024,
    )


@pytest.fixture(autouse=True)
def _isolate_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))


@pytest.mark.asyncio
async def test_resume_tome_resolves_prefix_id() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tome_dir = Path(tmp_dir)

        # Create agent to create initial tome
        agent1 = Mvge(api_key="test-key", tome_dir=tome_dir)
        agent1._provider_registry.resolve = MagicMock(
            return_value=(_mock_model(), MagicMock())
        )  # type: ignore[method-assign]
        agent1._provider_registry.create_realm = MagicMock()  # type: ignore[method-assign]
        with patch.object(
            agent1,
            "_build_system_prompt_async",
            new_callable=AsyncMock,
            return_value="sys",
        ):
            await agent1.initialize()

        assert agent1._agent_tome is not None
        actual_id = agent1._agent_tome.tome_id

        # Now resume using prefix ID
        prefix = actual_id[:8]
        agent2 = Mvge(api_key="test-key", tome_dir=tome_dir, tome_resume=prefix)
        agent2._provider_registry.resolve = MagicMock(
            return_value=(_mock_model(), MagicMock())
        )  # type: ignore[method-assign]
        agent2._provider_registry.create_realm = MagicMock()  # type: ignore[method-assign]
        with patch.object(
            agent2,
            "_build_system_prompt_async",
            new_callable=AsyncMock,
            return_value="sys",
        ):
            await agent2.initialize()

        assert agent2._agent_tome is not None
        assert agent2._agent_tome.tome_id == actual_id


@pytest.mark.asyncio
async def test_resume_tome_resolves_full_id() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tome_dir = Path(tmp_dir)

        agent1 = Mvge(api_key="test-key", tome_dir=tome_dir)
        agent1._provider_registry.resolve = MagicMock(
            return_value=(_mock_model(), MagicMock())
        )  # type: ignore[method-assign]
        agent1._provider_registry.create_realm = MagicMock()  # type: ignore[method-assign]
        with patch.object(
            agent1,
            "_build_system_prompt_async",
            new_callable=AsyncMock,
            return_value="sys",
        ):
            await agent1.initialize()

        assert agent1._agent_tome is not None
        actual_id = agent1._agent_tome.tome_id

        agent2 = Mvge(api_key="test-key", tome_dir=tome_dir, tome_resume=actual_id)
        agent2._provider_registry.resolve = MagicMock(
            return_value=(_mock_model(), MagicMock())
        )  # type: ignore[method-assign]
        agent2._provider_registry.create_realm = MagicMock()  # type: ignore[method-assign]
        with patch.object(
            agent2,
            "_build_system_prompt_async",
            new_callable=AsyncMock,
            return_value="sys",
        ):
            await agent2.initialize()

        assert agent2._agent_tome is not None
        assert agent2._agent_tome.tome_id == actual_id


@pytest.mark.asyncio
async def test_resume_tome_resolves_raw_path() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tome_dir = Path(tmp_dir)

        agent1 = Mvge(api_key="test-key", tome_dir=tome_dir)
        agent1._provider_registry.resolve = MagicMock(
            return_value=(_mock_model(), MagicMock())
        )  # type: ignore[method-assign]
        agent1._provider_registry.create_realm = MagicMock()  # type: ignore[method-assign]
        with patch.object(
            agent1,
            "_build_system_prompt_async",
            new_callable=AsyncMock,
            return_value="sys",
        ):
            await agent1.initialize()

        assert agent1._agent_tome is not None
        actual_id = agent1._agent_tome.tome_id
        raw_path = tome_dir / f"{actual_id}.jsonl"

        agent2 = Mvge(api_key="test-key", tome_dir=tome_dir, tome_resume=str(raw_path))
        agent2._provider_registry.resolve = MagicMock(
            return_value=(_mock_model(), MagicMock())
        )  # type: ignore[method-assign]
        agent2._provider_registry.create_realm = MagicMock()  # type: ignore[method-assign]
        with patch.object(
            agent2,
            "_build_system_prompt_async",
            new_callable=AsyncMock,
            return_value="sys",
        ):
            await agent2.initialize()

        assert agent2._agent_tome is not None
        assert agent2._agent_tome.tome_id == actual_id


@pytest.mark.asyncio
async def test_resume_tome_missing_id_raises_error() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tome_dir = Path(tmp_dir)

        agent = Mvge(
            api_key="test-key", tome_dir=tome_dir, tome_resume="nonexistent_id"
        )
        agent._provider_registry.resolve = MagicMock(
            return_value=(_mock_model(), MagicMock())
        )  # type: ignore[method-assign]
        agent._provider_registry.create_realm = MagicMock()  # type: ignore[method-assign]
        with (
            patch.object(
                agent,
                "_build_system_prompt_async",
                new_callable=AsyncMock,
                return_value="sys",
            ),
            pytest.raises(TomeResumeError) as exc_info,
        ):
            await agent.initialize()

        assert "nonexistent_id" in str(exc_info.value)


@pytest.mark.asyncio
async def test_resume_matching_config_succeeds_without_warnings() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tome_dir = Path(tmp_dir)
        tome_id = _seed_tome(
            tome_dir,
            model="test-model",
            contemplation_level="medium",
            spells=["spell_a", "spell_b"],
        )

        class SpellAgent(Mvge):
            def _build_spells(self) -> list[MvgeSpell]:
                return [
                    MvgeSpell(name="spell_a", description="a", parameters={}),
                    MvgeSpell(name="spell_b", description="b", parameters={}),
                ]

        agent = SpellAgent(
            api_key="test-key",
            tome_dir=tome_dir,
            tome_resume=tome_id,
        )
        agent._provider_registry.resolve = MagicMock(
            return_value=(_mock_model(), MagicMock())
        )  # type: ignore[method-assign]
        agent._provider_registry.create_realm = MagicMock()  # type: ignore[method-assign]
        with patch.object(
            agent,
            "_build_system_prompt_async",
            new_callable=AsyncMock,
            return_value="sys",
        ):
            await agent.initialize()

        assert agent._agent_tome is not None
        assert agent._agent_tome.tome_id == tome_id
        # No resume diagnostics
        assert len(agent._resume_diagnostics) == 0


@pytest.mark.asyncio
async def test_resume_mismatched_model_non_strict_emits_diagnostic() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tome_dir = Path(tmp_dir)
        tome_id = _seed_tome(
            tome_dir,
            model="different-model",
            contemplation_level="medium",
            spells=[],
        )

        agent = Mvge(
            api_key="test-key",
            tome_dir=tome_dir,
            tome_resume=tome_id,
            strict_resume=False,
        )
        agent._provider_registry.resolve = MagicMock(
            return_value=(_mock_model(), MagicMock())
        )  # type: ignore[method-assign]
        agent._provider_registry.create_realm = MagicMock()  # type: ignore[method-assign]
        with patch.object(
            agent,
            "_build_system_prompt_async",
            new_callable=AsyncMock,
            return_value="sys",
        ):
            await agent.initialize()

        assert agent._agent_tome is not None
        assert agent._agent_tome.tome_id == tome_id
        diags = agent.diagnostics
        assert any("different-model" in d.message for d in diags)


@pytest.mark.asyncio
async def test_resume_mismatched_model_strict_raises_tome_incompatible() -> None:
    from mvge.core.errors import TomeIncompatibleError

    with tempfile.TemporaryDirectory() as tmp_dir:
        tome_dir = Path(tmp_dir)
        tome_id = _seed_tome(
            tome_dir,
            model="different-model",
            contemplation_level="medium",
            spells=[],
        )

        agent = Mvge(
            api_key="test-key",
            tome_dir=tome_dir,
            tome_resume=tome_id,
            strict_resume=True,
        )
        agent._provider_registry.resolve = MagicMock(
            return_value=(_mock_model(), MagicMock())
        )  # type: ignore[method-assign]
        agent._provider_registry.create_realm = MagicMock()  # type: ignore[method-assign]
        with (
            patch.object(
                agent,
                "_build_system_prompt_async",
                new_callable=AsyncMock,
                return_value="sys",
            ),
            pytest.raises(TomeIncompatibleError) as exc_info,
        ):
            await agent.initialize()

        assert "different-model" in str(exc_info.value)
        assert exc_info.value.tome_id == tome_id
        assert exc_info.value.model_mismatch == ("different-model", "test-model")


@pytest.mark.asyncio
async def test_resume_missing_spells_strict_raises_tome_incompatible() -> None:
    from mvge.core.errors import TomeIncompatibleError

    with tempfile.TemporaryDirectory() as tmp_dir:
        tome_dir = Path(tmp_dir)
        tome_id = _seed_tome(
            tome_dir,
            model="test-model",
            contemplation_level="medium",
            spells=["required_custom_spell", "bash"],
        )

        class LimitedAgent(Mvge):
            def _build_spells(self) -> list[MvgeSpell]:
                return [MvgeSpell(name="bash", description="b", parameters={})]

        agent = LimitedAgent(
            api_key="test-key",
            tome_dir=tome_dir,
            tome_resume=tome_id,
            strict_resume=True,
        )
        agent._provider_registry.resolve = MagicMock(
            return_value=(_mock_model(), MagicMock())
        )  # type: ignore[method-assign]
        agent._provider_registry.create_realm = MagicMock()  # type: ignore[method-assign]
        with (
            patch.object(
                agent,
                "_build_system_prompt_async",
                new_callable=AsyncMock,
                return_value="sys",
            ),
            pytest.raises(TomeIncompatibleError) as exc_info,
        ):
            await agent.initialize()

        assert "required_custom_spell" in str(exc_info.value)
        assert "required_custom_spell" in exc_info.value.missing_spells


@pytest.mark.asyncio
async def test_resume_missing_spells_non_strict_emits_diagnostic() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tome_dir = Path(tmp_dir)
        tome_id = _seed_tome(
            tome_dir,
            model="test-model",
            contemplation_level="medium",
            spells=["required_custom_spell", "bash"],
        )

        class LimitedAgent(Mvge):
            def _build_spells(self) -> list[MvgeSpell]:
                return [MvgeSpell(name="bash", description="b", parameters={})]

        agent = LimitedAgent(
            api_key="test-key",
            tome_dir=tome_dir,
            tome_resume=tome_id,
            strict_resume=False,
        )
        agent._provider_registry.resolve = MagicMock(
            return_value=(_mock_model(), MagicMock())
        )  # type: ignore[method-assign]
        agent._provider_registry.create_realm = MagicMock()  # type: ignore[method-assign]
        with patch.object(
            agent,
            "_build_system_prompt_async",
            new_callable=AsyncMock,
            return_value="sys",
        ):
            await agent.initialize()

        assert agent._agent_tome is not None
        diags = agent.diagnostics
        assert any("required_custom_spell" in d.message for d in diags)


@pytest.mark.asyncio
async def test_resume_force_fork_branches_incompatible_session() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tome_dir = Path(tmp_dir)
        factory = TomeHandleFactory(tome_dir)
        tome_id = _seed_tome(
            tome_dir,
            model="old-model",
            contemplation_level="low",
            spells=["old_spell"],
        )
        seed_write = factory.open_write(tome_id)
        e1 = TomeEntry(
            id="turn-1",
            parent_id=None,
            type=TomeEntryType.MESSAGE,
            timestamp=1000.0,
            payload={"role": "user", "content": "turn 1"},
        )
        seed_write.append(e1)
        seed_write.append_leaf(e1.id)

        class NewAgent(Mvge):
            def _build_spells(self) -> list[MvgeSpell]:
                return [MvgeSpell(name="new_spell", description="n", parameters={})]

        agent = NewAgent(
            api_key="test-key",
            tome_dir=tome_dir,
            tome_resume=tome_id,
            force_fork_resume=True,
        )
        agent._provider_registry.resolve = MagicMock(
            return_value=(_mock_model(), MagicMock())
        )  # type: ignore[method-assign]
        agent._provider_registry.create_realm = MagicMock()  # type: ignore[method-assign]
        with patch.object(
            agent,
            "_build_system_prompt_async",
            new_callable=AsyncMock,
            return_value="sys",
        ):
            await agent.initialize()

        assert agent._agent_tome is not None
        # Forked tome has a new ID and points to parent
        assert agent._agent_tome.tome_id != tome_id
        assert agent._agent_tome.metadata.parent_tome_id == tome_id
        assert agent._agent_tome.metadata.model == "test-model"
        assert agent._agent_tome.metadata.spells == ["new_spell"]

        # Ancestor history preserved
        entries = factory.get_entries(agent._agent_tome.tome_id)
        assert len(entries) >= 1
        assert entries[0].id == e1.id


@pytest.mark.asyncio
async def test_validate_tome_compatibility_helper() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tome_dir = Path(tmp_dir)
        tome_id = _seed_tome(
            tome_dir,
            model="other-model",
            contemplation_level="high",
            spells=["spell_x"],
        )

        agent = Mvge(api_key="test-key", tome_dir=tome_dir, model_id="test-model")
        agent._provider_registry.resolve = MagicMock(
            return_value=(_mock_model(), MagicMock())
        )  # type: ignore[method-assign]

        report = agent.validate_tome_compatibility(tome_id)
        assert not report.compatible
        assert report.model_mismatch == ("other-model", "test-model")
        assert "spell_x" in report.missing_spells
        assert report.contemplation_mismatch == ("high", "medium")
