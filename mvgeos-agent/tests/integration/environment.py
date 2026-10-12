from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from mvgeos_core.channel import Model
from mvgeos_core.constants import DEFAULT_AGENT_NAME
from mvgeos_runes.rune_runner import RuneRunner
from mvgeos_runes.types import BeforeMvgeStartData, SigilHook

from mvgeos_agent.environment import MvgeEnvironment
from mvgeos_agent.mvge import Mvge


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


def _agent(tome_dir: Path) -> Mvge:
    agent = Mvge(api_key="test-key", tome_dir=tome_dir)
    agent._provider_registry.resolve = MagicMock(  # type: ignore[method-assign]
        return_value=(_mock_model(), MagicMock())
    )
    return agent


@pytest.mark.asyncio
async def test_system_prompt_matches_environment_assemble() -> None:
    """BaseMvge's async prompt path and MvgeEnvironment.assemble_system_prompt
    must produce identical prompts for the same inputs."""
    with tempfile.TemporaryDirectory() as tmp:
        agent = _agent(Path(tmp))
        await agent.initialize()

        assert agent._state is not None
        expected = await agent._environment.assemble_system_prompt(
            runner=agent._runner,
            base_prompt=agent._build_system_prompt(),
            custom_prompt=getattr(agent, "_custom_system_prompt", ""),
            cwd=Path.cwd(),
            spell_names=[s.name for s in agent._build_spells()],
            config_dir=agent.config_dir,
        )

        assert agent._state.system_prompt == expected
        assert await agent._build_system_prompt_async() == expected


@pytest.mark.asyncio
async def test_render_prompt_matches_environment_render() -> None:
    from mvgeos_agent.environment import MvgeEnvironment as Env
    from mvgeos_agent.environment import render_prompt as free_render

    with tempfile.TemporaryDirectory() as tmp:
        agent = _agent(Path(tmp))
        cwd = str(getattr(agent._config_manager, "_project_dir", "") or Path.cwd())
        expected = free_render(
            "You are Mvge",
            ["bash"],
            cwd=cwd,
        )

        assert (
            Env.render_prompt(
                "You are Mvge",
                ["bash"],
                cwd=cwd,
            )
            == expected
        )


@pytest.mark.asyncio
async def test_initialize_fires_before_mvge_start_once() -> None:
    """BEFORE_MVGE_START flows through MvgeEnvironment exactly once per init."""
    with tempfile.TemporaryDirectory() as tmp:
        agent = _agent(Path(tmp))
        agent._load_runes = AsyncMock()  # type: ignore[method-assign]
        agent._runner = RuneRunner()

        calls: list[Any] = []

        def recorder(data: Any) -> None:
            calls.append(data)

        agent._runner.register_handler(SigilHook.BEFORE_MVGE_START, recorder)

        await agent.initialize()

        assert len(calls) == 1
        assert calls[0].agent_name == "default-mvge"


@pytest.mark.asyncio
async def test_agent_initialization_without_rune_omits_steering() -> None:
    """Without steering-bridge, the microkernel renders no <project_context>."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        proj_dir = tmp_path / "project"
        proj_dir.mkdir()
        (proj_dir / ".agents").mkdir()
        (proj_dir / ".agents" / "AGENTS.md").write_text(
            "# Project Rules\nStrict mode always.", encoding="utf-8"
        )

        global_dir = tmp_path / "global"
        global_dir.mkdir()
        (global_dir / "AGENTS.md").write_text("Global dev standard.", encoding="utf-8")

        env = MvgeEnvironment.resolve(
            DEFAULT_AGENT_NAME,
            project_dir=proj_dir,
            global_dir=global_dir,
            config_dir=tmp_path / "config",
        )
        agent = Mvge(api_key="test-key", environment=env, tome_dir=tmp_path / "tomes")
        agent._provider_registry.resolve = MagicMock(
            return_value=(_mock_model(), MagicMock())
        )
        await agent.initialize()

        assert agent._state is not None
        rendered = agent._state.system_prompt
        assert "<project_context>" not in rendered
        assert "Strict mode always." not in rendered
        assert "Global dev standard." not in rendered
        assert "Active spells:" in rendered


@pytest.mark.asyncio
async def test_rune_can_contribute_steering_via_before_mvge_start() -> None:
    """Steering-bridge style injection via BEFORE_MVGE_START lands in prompt."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        env = MvgeEnvironment.resolve(
            DEFAULT_AGENT_NAME,
            project_dir=tmp_path,
            config_dir=tmp_path / "config",
        )
        agent = Mvge(api_key="test-key", environment=env, tome_dir=tmp_path / "tomes")
        agent._provider_registry.resolve = MagicMock(
            return_value=(_mock_model(), MagicMock())
        )
        await agent._load_runes()

        def steering_injector(data: Any) -> None:
            assert isinstance(data, BeforeMvgeStartData)
            data.base_prompt = (
                f"{data.base_prompt}\n\n<project_context>\n"
                "Rune-contributed steering.\n</project_context>"
            )

        assert agent._runner is not None
        agent._runner.register_handler(SigilHook.BEFORE_MVGE_START, steering_injector)
        await agent.initialize()

        assert agent._state is not None
        assert "Rune-contributed steering." in agent._state.system_prompt
        assert "Active spells:" in agent._state.system_prompt


@pytest.mark.asyncio
async def test_before_mvge_start_payload_carries_rehydration_fields() -> None:
    """The BEFORE_MVGE_START payload includes the rehydration fields (§4.3)."""
    with tempfile.TemporaryDirectory() as tmp:
        agent = _agent(Path(tmp))
        agent._load_runes = AsyncMock()  # type: ignore[method-assign]
        agent._runner = RuneRunner()

        seen: list[Any] = []

        def recorder(data: Any) -> None:
            seen.append(data)

        agent._runner.register_handler(SigilHook.BEFORE_MVGE_START, recorder)

        await agent.initialize()

        assert len(seen) == 1
        payload = seen[0]
        assert isinstance(payload, BeforeMvgeStartData)
        assert payload.agent_name == "default-mvge"
        assert payload.base_prompt == agent._environment.resolved_prompt.text
        resolved_path = agent._environment.resolved_prompt.path
        assert payload.system_path == (
            resolved_path.as_posix() if resolved_path else None
        )
        assert payload.runes_paths == [
            p.as_posix() for p in agent._environment.runes_paths
        ]
        # No spells dir exists in this tmp setup, so nothing is recorded.
        assert payload.spells_dir is None
