from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mvge.agent.environment import MvgeEnvironment
from mvge.agent.rune_lifecycle import RuneLifecycle
from mvge.core.layers import ResolvedLayer, Scope
from mvge.provider.registry import RealmRegistry
from mvge.runes.rune_runner import RuneRunner
from mvge.runes.types import (
    Diagnostic,
    DiagnosticKind,
    RuneContext,
    RuneLoad,
    RuneManifest,
    RuneScope,
    RuneShortcut,
)


def _layer(path: Path | str, scope: Scope = Scope.PROJECT) -> ResolvedLayer:
    """A declared layer for a test that is not about layer resolution."""
    return ResolvedLayer(scope, Path(path))


async def _load(lifecycle: RuneLifecycle) -> RuneRunner:
    with patch(
        "mvge.agent.rune_lifecycle.load_runes_from_paths",
        return_value=([], []),
    ):
        return await lifecycle.load()


def _manifest(name: str = "acme", **kwargs: Any) -> RuneManifest:
    defaults: dict[str, Any] = {
        "version": "1.0.0",
        "description": f"{name} rune",
        "shortcuts": [RuneShortcut(key="ctrl+a", description="acme")],
    }
    defaults.update(kwargs)
    return RuneManifest(name=name, **defaults)


def _diag(name: str = "broken") -> Diagnostic:
    return Diagnostic(
        kind=DiagnosticKind.LOAD_FAILURE,
        rune_name=name,
        message="failed to load",
        scope=RuneScope.USER,
        path="/tmp/runes",
    )


class TestRuneLayers:
    """The lifecycle resolves nothing; layers arrive resolved and scoped.

    Regression: scope used to be inferred by substring-matching each path, so
    a bespoke directory whose name happened to contain a standard layer's
    shape was reported as the agent or user layer, and the retired
    ``.mvgeos/{agent}/runes`` form was still recognised. Scope now comes from
    the layer the resolver placed the directory in.
    """

    @pytest.mark.asyncio
    async def test_load_hands_the_resolver_layers_to_the_loader(self, tmp_path) -> None:
        layers = [
            ResolvedLayer(Scope.USER, tmp_path / "user"),
            ResolvedLayer(Scope.AGENT, tmp_path / "agent"),
        ]
        lifecycle = RuneLifecycle(agent_name="coder", rune_layers=layers)

        with patch(
            "mvge.agent.rune_lifecycle.load_runes_from_paths",
            return_value=([], []),
        ) as mock_load:
            await lifecycle.load()

        mock_load.assert_called_once_with(
            [(tmp_path / "user", Scope.USER), (tmp_path / "agent", Scope.AGENT)],
            "coder",
        )

    @pytest.mark.asyncio
    async def test_a_declared_scope_reaches_the_loader_unchanged(
        self, tmp_path: Path
    ) -> None:
        """The loader is told which layer each directory is.

        Asserted at the loader seam, not on a dataclass, because the
        observable consequence is what the loader does with the pair. The
        decoy sits at exactly the shape the old matcher keyed on -- it
        contains ``agents/<name>/extensions`` -- so had scope been re-derived
        from the path text it would have arrived as the agent layer.
        """
        decoy = tmp_path / "agents" / "coder" / "extensions"
        lifecycle = RuneLifecycle(
            agent_name="coder",
            rune_layers=[ResolvedLayer(Scope.PROJECT, decoy)],
        )

        with patch(
            "mvge.agent.rune_lifecycle.load_runes_from_paths",
            return_value=([], []),
        ) as mock_load:
            await lifecycle.load()

        mock_load.assert_called_once_with([(decoy, Scope.PROJECT)], "coder")

    @pytest.mark.asyncio
    async def test_retired_mvgeos_layout_is_not_upgraded_to_the_agent_layer(
        self, tmp_path: Path
    ) -> None:
        """``.mvgeos`` was a proprietary scheme ADR-0014 retired.

        It used to be substring-matched into the agent and user scopes. No
        legacy upgrade is left, so a directory under it is whatever the
        caller declared -- here, project-scoped -- with nothing added.
        """
        retired = tmp_path / ".mvgeos" / "coder" / "runes"
        lifecycle = RuneLifecycle(
            agent_name="coder",
            rune_layers=[ResolvedLayer(Scope.PROJECT, retired)],
        )

        with patch(
            "mvge.agent.rune_lifecycle.load_runes_from_paths",
            return_value=([], []),
        ) as mock_load:
            await lifecycle.load()

        mock_load.assert_called_once_with([(retired, Scope.PROJECT)], "coder")


class TestLoad:
    @pytest.mark.asyncio
    async def test_load_returns_runner_with_bound_context(self) -> None:
        lifecycle = RuneLifecycle(
            agent_name="tester",
            api_key="secret",
            cwd="/proj",
            mode="cli",
        )

        with (
            patch(
                "mvge.agent.rune_lifecycle.load_runes_from_paths",
                return_value=([], []),
            ),
        ):
            runner = await lifecycle.load()

        assert isinstance(runner, RuneRunner)
        assert lifecycle.runner is runner
        assert runner.context == RuneContext(
            cwd="/proj",
            project_root=str(Path("/proj").resolve()),
            mode="cli",
            agent_name="tester",
            api_key="secret",
        )

    @pytest.mark.asyncio
    async def test_load_reuses_existing_runner(self) -> None:
        existing = RuneRunner()
        lifecycle = RuneLifecycle(agent_name="tester", runner=existing)

        with (
            patch(
                "mvge.agent.rune_lifecycle.load_runes_from_paths",
                return_value=([], []),
            ),
        ):
            runner = await lifecycle.load()

        assert runner is existing

    @pytest.mark.asyncio
    async def test_second_load_reuses_same_runner(self) -> None:
        lifecycle = RuneLifecycle(agent_name="tester")

        empty: tuple[list[Any], list[Any]] = ([], [])
        with patch(
            "mvge.agent.rune_lifecycle.load_runes_from_paths",
            return_value=empty,
        ):
            first = await lifecycle.load()
            second = await lifecycle.load()

        assert first is second

    @pytest.mark.asyncio
    async def test_load_registers_manifest_shortcuts(self) -> None:
        loads = [RuneLoad(manifest=_manifest())]
        lifecycle = RuneLifecycle(agent_name="tester")

        with (
            patch(
                "mvge.agent.rune_lifecycle.load_runes_from_paths",
                return_value=(loads, []),
            ) as mock_load,
        ):
            runner = await lifecycle.load()

        mock_load.assert_called_once_with([], "tester")
        assert [m.name for m in runner.loaded_manifests] == ["acme"]
        assert [sc.key for sc in runner.get_shortcuts()] == ["ctrl+a"]

    @pytest.mark.asyncio
    async def test_load_retains_diagnostics_without_loads(self) -> None:
        diag = _diag()
        lifecycle = RuneLifecycle(agent_name="tester")

        with (
            patch(
                "mvge.agent.rune_lifecycle.load_runes_from_paths",
                return_value=([], [diag]),
            ),
        ):
            runner = await lifecycle.load()

        assert runner.diagnostics == [diag]

    @pytest.mark.asyncio
    async def test_load_registers_providers_into_registry(self) -> None:
        def factory(api: Any) -> None:
            api.register_provider("acme", {"baseUrl": "https://acme.test"})

        loads = [RuneLoad(manifest=_manifest(), factory=factory)]
        registry = MagicMock(spec=RealmRegistry)
        lifecycle = RuneLifecycle(agent_name="tester", provider_registry=registry)

        with (
            patch(
                "mvge.agent.rune_lifecycle.load_runes_from_paths",
                return_value=(loads, []),
            ),
        ):
            await lifecycle.load()

        registry.register_provider.assert_called_once_with(
            "acme", {"baseUrl": "https://acme.test"}
        )

    @pytest.mark.asyncio
    async def test_load_without_registry_skips_provider_registration(self) -> None:
        def factory(api: Any) -> None:
            api.register_provider("acme", {"baseUrl": "https://acme.test"})

        loads = [RuneLoad(manifest=_manifest(), factory=factory)]
        lifecycle = RuneLifecycle(agent_name="tester")

        with (
            patch(
                "mvge.agent.rune_lifecycle.load_runes_from_paths",
                return_value=(loads, []),
            ),
        ):
            await lifecycle.load()

    @pytest.mark.asyncio
    async def test_environment_refreshed_after_load(self, tmp_path: Path) -> None:
        environment = MvgeEnvironment.resolve(
            "tester",
            project_dir=tmp_path,
            config_dir=tmp_path / "agent_config",
        )
        diag = _diag()
        lifecycle = RuneLifecycle(
            agent_name="tester",
            environment=environment,
        )

        with (
            patch(
                "mvge.agent.rune_lifecycle.load_runes_from_paths",
                return_value=([], [diag]),
            ),
        ):
            runner = await lifecycle.load()

        refreshed = lifecycle.environment
        assert refreshed is not environment
        assert refreshed is not None
        assert refreshed.runner is runner
        expected_diags = runner.diagnostics + runner.skill_catalog.skill_diagnostics
        assert refreshed.diagnostics == expected_diags


def _watcher_factory(
    log: list[MagicMock],
) -> Callable[..., MagicMock]:
    def _make(*args: Any, **kwargs: Any) -> MagicMock:
        watcher = MagicMock(start=AsyncMock(), stop=AsyncMock())
        log.append(watcher)
        return watcher

    return _make


class TestStartAndShutdown:
    @pytest.mark.asyncio
    async def test_start_creates_watchers_for_existing_paths_only(
        self, tmp_path: Path
    ) -> None:
        runes_dir = tmp_path / "runes"
        runes_dir.mkdir()
        missing = tmp_path / "missing"
        lifecycle = RuneLifecycle(
            agent_name="tester",
            rune_layers=[_layer(runes_dir), _layer(missing)],
        )
        with (
            patch(
                "mvge.agent.rune_lifecycle.load_runes_from_paths",
                return_value=([], []),
            ),
        ):
            await lifecycle.load()

        created: list[MagicMock] = []
        with patch(
            "mvge.agent.rune_lifecycle.RuneWatcher",
            side_effect=_watcher_factory(created),
        ):
            await lifecycle.start()

        assert len(created) == 1
        created[0].start.assert_awaited_once()
        assert lifecycle.watchers == created

    @pytest.mark.asyncio
    async def test_start_passes_resolved_path_and_runner(self, tmp_path: Path) -> None:
        runes_dir = tmp_path / "runes"
        runes_dir.mkdir()
        lifecycle = RuneLifecycle(agent_name="tester", rune_layers=[_layer(runes_dir)])
        with (
            patch(
                "mvge.agent.rune_lifecycle.load_runes_from_paths",
                return_value=([], []),
            ),
        ):
            runner = await lifecycle.load()

        created: list[MagicMock] = []
        with patch(
            "mvge.agent.rune_lifecycle.RuneWatcher",
            side_effect=_watcher_factory(created),
        ) as watcher_cls:
            await lifecycle.start()

        watcher_cls.assert_called_once_with(runes_dir, runner, reload_callback=None)

    @pytest.mark.asyncio
    async def test_start_skips_already_watched_paths(self, tmp_path: Path) -> None:
        runes_dir = tmp_path / "runes"
        runes_dir.mkdir()
        lifecycle = RuneLifecycle(agent_name="tester", rune_layers=[_layer(runes_dir)])
        with (
            patch(
                "mvge.agent.rune_lifecycle.load_runes_from_paths",
                return_value=([], []),
            ),
        ):
            await lifecycle.load()

        created: list[MagicMock] = []
        with patch(
            "mvge.agent.rune_lifecycle.RuneWatcher",
            side_effect=_watcher_factory(created),
        ) as watcher_cls:
            await lifecycle.start()
            await lifecycle.start()

        assert watcher_cls.call_count == 1
        assert len(lifecycle.watchers) == 1

    @pytest.mark.asyncio
    async def test_start_before_load_still_starts_watchers(
        self, tmp_path: Path
    ) -> None:
        runes_dir = tmp_path / "runes"
        runes_dir.mkdir()
        lifecycle = RuneLifecycle(agent_name="tester", rune_layers=[_layer(runes_dir)])

        created: list[MagicMock] = []
        with patch(
            "mvge.agent.rune_lifecycle.RuneWatcher",
            side_effect=_watcher_factory(created),
        ):
            await lifecycle.start()

        assert len(created) == 1
        created[0].start.assert_awaited_once()
        assert lifecycle.runner is not None
        assert lifecycle.watchers == created

    @pytest.mark.asyncio
    async def test_shutdown_stops_all_watchers_and_clears(self, tmp_path: Path) -> None:
        runes_dir = tmp_path / "runes"
        runes_dir.mkdir()
        lifecycle = RuneLifecycle(agent_name="tester", rune_layers=[_layer(runes_dir)])
        with (
            patch(
                "mvge.agent.rune_lifecycle.load_runes_from_paths",
                return_value=([], []),
            ),
        ):
            await lifecycle.load()

        created: list[MagicMock] = []
        with patch(
            "mvge.agent.rune_lifecycle.RuneWatcher",
            side_effect=_watcher_factory(created),
        ):
            await lifecycle.start()

        await lifecycle.shutdown()

        created[0].stop.assert_awaited_once()
        assert lifecycle.watchers == []

    @pytest.mark.asyncio
    async def test_restart_after_shutdown(self, tmp_path: Path) -> None:
        runes_dir = tmp_path / "runes"
        runes_dir.mkdir()
        lifecycle = RuneLifecycle(agent_name="tester", rune_layers=[_layer(runes_dir)])
        with (
            patch(
                "mvge.agent.rune_lifecycle.load_runes_from_paths",
                return_value=([], []),
            ),
        ):
            await lifecycle.load()

        created: list[MagicMock] = []
        with patch(
            "mvge.agent.rune_lifecycle.RuneWatcher",
            side_effect=_watcher_factory(created),
        ):
            await lifecycle.start()
            await lifecycle.shutdown()
            await lifecycle.start()

        assert len(created) == 2
        assert lifecycle.watchers == [created[1]]

    @pytest.mark.asyncio
    async def test_shutdown_without_watchers_is_noop(self) -> None:
        lifecycle = RuneLifecycle(agent_name="tester")

        await lifecycle.shutdown()

        assert lifecycle.watchers == []

    @pytest.mark.asyncio
    async def test_shutdown_attempts_every_watcher_despite_failure(
        self, tmp_path: Path
    ) -> None:
        """shutdown() is not fail-fast within an attempt: every watcher is
        attempted, errors are collected and raised together, and the failed
        watcher stays registered so a retry attempts it again."""
        runes_a = tmp_path / "runes-a"
        runes_b = tmp_path / "runes-b"
        runes_a.mkdir()
        runes_b.mkdir()
        lifecycle = RuneLifecycle(
            agent_name="tester", rune_layers=[_layer(runes_a), _layer(runes_b)]
        )
        with (
            patch(
                "mvge.agent.rune_lifecycle.load_runes_from_paths",
                return_value=([], []),
            ),
        ):
            await lifecycle.load()

        created: list[MagicMock] = []
        with patch(
            "mvge.agent.rune_lifecycle.RuneWatcher",
            side_effect=_watcher_factory(created),
        ):
            await lifecycle.start()
        assert len(created) == 2
        # Poison the first watcher; the second must still be attempted.
        created[0].stop = AsyncMock(side_effect=RuntimeError("watcher 0 wedged"))

        with pytest.raises(RuntimeError, match="watcher 0 wedged"):
            await lifecycle.shutdown()

        for watcher in created:
            watcher.stop.assert_awaited_once()
        # The stopped watcher is dropped; the failed one stays for retry.
        assert lifecycle.watchers == [created[0]]
