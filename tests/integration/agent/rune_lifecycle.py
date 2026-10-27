from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mvge.agent.mvge import Mvge
from mvge.agent.rune_lifecycle import RuneLifecycle
from mvge.core.layers import ResolvedLayer, Scope
from mvge.runes.rune_runner import RuneRunner

_RUNE_PY = """
from mvge.runes.types import SpellDefinition


class EchoSpell(SpellDefinition):
    async def execute(self, spell_cast_id, params, signal=None, on_update=None):
        return {"status": "ok"}


def rune_factory(api):
    api.register_spell(EchoSpell(name="echo_spell", description="Echo"))
    api.register_command("echo-cmd", "Echo command")
    api.register_shortcut("ctrl-e", "Echo shortcut")
    api.register_provider("acme", {"baseUrl": "https://acme.test/v1"})
"""


@pytest.fixture
def runes_dir(tmp_path: Path) -> Path:
    root = tmp_path / "runes"
    rune_dir = root / "echo-rune"
    rune_dir.mkdir(parents=True)
    manifest = {
        "name": "echo-rune",
        "version": "1.0.0",
        "description": "Echo rune",
        "entry_point": "rune.py",
    }
    (rune_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (rune_dir / "rune.py").write_text(_RUNE_PY, encoding="utf-8")
    return root


def _runner_state(runner: RuneRunner) -> dict[str, Any]:
    return {
        "manifests": sorted(m.name for m in runner.loaded_manifests),
        "spells": sorted(s.name for s in runner.get_all_registered_spells()),
        "commands": [c.name for c in runner.get_commands()],
        "shortcuts": sorted(sc.key for sc in runner.get_shortcuts()),
        "providers": dict(sorted(runner.get_registered_providers().items())),
        "active_spells": runner.get_active_spells(),
        "skills": [s.name for s in runner.skill_catalog.get_skills()],
        "context": runner.context,
        "diagnostics": [
            (d.kind.value, d.rune_name, d.message) for d in runner.diagnostics
        ],
        "skill_diagnostics": [
            (d.kind.value, d.skill_name, d.message)
            for d in runner.skill_catalog.skill_diagnostics
        ],
    }


@pytest.mark.asyncio
async def test_standalone_load_builds_full_runner(runes_dir: Path) -> None:
    class _Registry:
        def __init__(self) -> None:
            self.configs: dict[str, dict] = {}

        def register_provider(self, name: str, config: dict) -> None:
            self.configs[name] = config

    registry = _Registry()
    lifecycle = RuneLifecycle(
        agent_name="tester",
        api_key="key-1",
        rune_layers=[ResolvedLayer(Scope.PROJECT, runes_dir)],
        provider_registry=registry,
    )
    runner = await lifecycle.load()

    state = _runner_state(runner)
    assert state["manifests"] == ["echo-rune"]
    assert "echo_spell" in state["spells"]
    assert state["commands"] == ["echo-cmd"]
    assert state["shortcuts"] == ["ctrl-e"]
    assert state["providers"] == {"acme": {"baseUrl": "https://acme.test/v1"}}
    assert "echo_spell" in state["active_spells"]
    assert registry.configs == {"acme": {"baseUrl": "https://acme.test/v1"}}
    assert runner.context.agent_name == "tester"
    assert runner.context.api_key == "key-1"


@pytest.mark.asyncio
async def test_lifecycle_loads_exactly_the_layers_it_was_given(
    runes_dir: Path,
) -> None:
    """Loading reads the declared stack and nothing else.

    This replaces a comparison against a verbatim copy of the old inline
    loader. That copy was the duplication ADR-0014 removes: two derivations
    of the same search paths, which is how a Rune came to install
    successfully and then load nowhere. There is now one derivation, so
    there is nothing to compare against -- the property worth asserting is
    that the layers handed in are the layers loaded from.
    """
    empty_root = runes_dir.parent / "empty_other"
    empty_root.mkdir()
    layers = [
        ResolvedLayer(Scope.USER, empty_root),
        ResolvedLayer(Scope.PROJECT, runes_dir),
    ]
    lifecycle = RuneLifecycle(agent_name="tester", api_key="key-1", rune_layers=layers)

    loaded = await lifecycle.load()

    assert _runner_state(loaded)["manifests"] == ["echo-rune"]
    assert lifecycle.runner is loaded


@pytest.mark.asyncio
async def test_base_mvge_load_matches_standalone_lifecycle(runes_dir: Path) -> None:
    agent = Mvge(api_key="key-2", runes_paths=[str(runes_dir)])
    lifecycle = RuneLifecycle(
        agent_name=agent.environment.agent_name,
        api_key="key-2",
        rune_layers=[ResolvedLayer(Scope.PROJECT, runes_dir)],
    )
    standalone = await lifecycle.load()
    try:
        await agent._load_runes()

        assert agent._runner is not None
        assert _runner_state(agent._runner) == _runner_state(standalone)
        assert agent._rune_lifecycle is not None
        assert agent.environment.diagnostics == agent.diagnostics
        assert len(lifecycle.watchers) == 0

        started = agent._rune_lifecycle.watchers
        assert len(started) == 1
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_base_mvge_close_stops_watchers(runes_dir: Path) -> None:
    agent = Mvge(api_key="key-3", runes_paths=[str(runes_dir)])
    await agent._load_runes()

    lifecycle = agent._rune_lifecycle
    assert lifecycle is not None
    assert len(lifecycle.watchers) == 1

    await agent.close()

    assert lifecycle.watchers == []
    assert agent._rune_lifecycle is None
    assert agent._runner is None


@pytest.mark.asyncio
async def test_standalone_watchers_start_and_stop(runes_dir: Path) -> None:
    empty_root = runes_dir.parent / "empty"
    empty_root.mkdir()
    missing = runes_dir.parent / "missing"

    lifecycle = RuneLifecycle(
        agent_name="watcher-agent",
        rune_layers=[
            ResolvedLayer(Scope.PROJECT, runes_dir),
            ResolvedLayer(Scope.PROJECT, missing),
        ],
    )
    await lifecycle.load()
    await lifecycle.start()

    assert len(lifecycle.watchers) == 1

    await lifecycle.shutdown()
    assert lifecycle.watchers == []

    await lifecycle.shutdown()


@pytest.mark.asyncio
async def test_rune_resources_discover_invoked(tmp_path: Path) -> None:
    runes_root = tmp_path / "runes_disc"
    disc_rune = runes_root / "disc-rune"
    disc_rune.mkdir(parents=True)
    (disc_rune / "manifest.json").write_text(
        json.dumps(
            {
                "name": "disc-rune",
                "version": "1.0.0",
                "description": "Resource discover rune",
                "entry_point": "rune.py",
            }
        ),
        encoding="utf-8",
    )
    (disc_rune / "rune.py").write_text(
        """
from mvge.runes.types import (
    ResourcesDiscoverData,
    SigilHook,
)

def rune_factory(api):
    async def on_discover(data: ResourcesDiscoverData) -> ResourcesDiscoverData:
        api.send_message("resource_discover_invoked")
        return data

    api.on(SigilHook.RESOURCES_DISCOVER, on_discover)
""",
        encoding="utf-8",
    )

    lifecycle = RuneLifecycle(
        agent_name="disc-agent",
        rune_layers=[ResolvedLayer(Scope.PROJECT, runes_root)],
        cwd=str(tmp_path),
    )
    runner = await lifecycle.load()
    assert "resource_discover_invoked" in runner._message_queue
