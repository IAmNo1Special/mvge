from __future__ import annotations

import json
from pathlib import Path

import pytest

from mvgeos_runes.loader import load_runes_from_paths
from mvgeos_runes.rune_runner import RuneRunner
from mvgeos_runes.types import (
    RuneLoad,
    RuneManifest,
    RuneScope,
    SpellDefinition,
)


def _dummy_spell(name: str) -> SpellDefinition:
    return SpellDefinition(
        name=name,
        description=f"dummy {name}",
        parameters={},
        handler=lambda: "ok",
    )


def test_gateway_rune_loads_last_in_load_runes_from_paths(tmp_path: Path) -> None:
    # Create two extensions: "aaa_gateway" with spell_gateway: true,
    # and "zzz_worker" with spell_gateway: false
    ext_dir = tmp_path / "extensions"
    ext_dir.mkdir()

    gw_dir = ext_dir / "aaa_gateway"
    gw_dir.mkdir()
    (gw_dir / "manifest.json").write_text(
        json.dumps(
            {
                "name": "aaa_gateway",
                "version": "1.0.0",
                "description": "Gateway",
                "spell_gateway": True,
            }
        )
    )

    worker_dir = ext_dir / "zzz_worker"
    worker_dir.mkdir()
    (worker_dir / "manifest.json").write_text(
        json.dumps(
            {
                "name": "zzz_worker",
                "version": "1.0.0",
                "description": "Worker",
                "spell_gateway": False,
            }
        )
    )

    loads, diags = load_runes_from_paths([(ext_dir, RuneScope.USER)])
    assert len(loads) == 2
    # Even though "aaa_gateway" is alphabetically first, it must be ordered LAST
    assert loads[0].manifest.name == "zzz_worker"
    assert loads[1].manifest.name == "aaa_gateway"


@pytest.mark.asyncio
async def test_gateway_rune_loads_last_in_load_rune_loads() -> None:
    runner = RuneRunner()
    load_order: list[str] = []

    def gw_factory(api: object) -> None:
        load_order.append("gateway")

    def worker_factory(api: object) -> None:
        load_order.append("worker")

    loads = [
        RuneLoad(
            manifest=RuneManifest(
                name="gw", version="1.0.0", description="", spell_gateway=True
            ),
            factory=gw_factory,
        ),
        RuneLoad(
            manifest=RuneManifest(
                name="worker",
                version="1.0.0",
                description="",
                spell_gateway=False,
            ),
            factory=worker_factory,
        ),
    ]

    await runner.load_rune_loads(loads)
    assert load_order == ["worker", "gateway"]
    assert runner.gateway_rune_name == "gw"
    assert runner.has_spell_gateway is True


@pytest.mark.asyncio
async def test_narrow_global_allowlist_removes_spells_except_gateway() -> None:
    runner = RuneRunner()

    gw_api = runner.create_api(rune_name="gw")
    gw_api.register_spell(_dummy_spell("search"))
    gw_api.register_spell(_dummy_spell("activate"))

    worker_api = runner.create_api(rune_name="worker")
    worker_api.register_spell(_dummy_spell("read_file"))
    worker_api.register_spell(_dummy_spell("write_file"))

    # Designate gw as spell gateway
    await runner.load_rune_loads(
        [
            RuneLoad(
                manifest=RuneManifest(
                    name="gw", version="1.0.0", description="", spell_gateway=True
                ),
            )
        ]
    )

    runner.set_global_spell_allowlist(["search", "activate", "read_file", "write_file"])
    version_before = runner.spell_version

    # Narrowing write_file and attempting to narrow search
    remaining = runner.narrow_global_allowlist(["write_file", "search"])

    # "search" cannot be removed because it belongs to the gateway
    # "write_file" is removed
    assert "search" in remaining
    assert "activate" in remaining
    assert "read_file" in remaining
    assert "write_file" not in remaining
    assert runner.spell_version > version_before


def test_has_spell_gateway_and_discoverable_spells() -> None:
    runner = RuneRunner()
    assert runner.has_spell_gateway is False

    api = runner.create_api()
    assert api.has_spell_gateway is False

    # Set discoverable builtin spells
    class DummyBuiltin:
        name = "bash"
        description = "run bash"
        read_only = False

    runner.set_discoverable_builtin_spells([DummyBuiltin()])

    # Register worker spell
    worker_api = runner.create_api(rune_name="worker")
    worker_api.register_spell(_dummy_spell("worker_spell"))

    # Register gateway spell
    gw_api = runner.create_api(rune_name="gw")
    gw_api.register_spell(_dummy_spell("search"))
    runner._gateway_rune_name = "gw"

    assert runner.has_spell_gateway is True
    assert api.has_spell_gateway is True

    discoverable = api.get_discoverable_spells()
    discoverable_names = {s.name for s in discoverable}
    assert "bash" in discoverable_names
    assert "worker_spell" in discoverable_names
    assert "search" not in discoverable_names


@pytest.mark.asyncio
async def test_gateway_deterministic_selection_multiple_claimants() -> None:
    runner = RuneRunner()
    # gw_z is loaded first in loads list, but gw_a has alphabetical priority
    # within same scope
    loads = [
        RuneLoad(
            manifest=RuneManifest(
                name="gw_z",
                version="1.0.0",
                description="",
                spell_gateway=True,
                scope=RuneScope.USER,
            ),
        ),
        RuneLoad(
            manifest=RuneManifest(
                name="gw_a",
                version="1.0.0",
                description="",
                spell_gateway=True,
                scope=RuneScope.USER,
            ),
        ),
    ]
    await runner.load_rune_loads(loads)
    assert runner.gateway_rune_name == "gw_a"
    assert any(
        "gw_z" in d.message and "already claimed" in d.message
        for d in runner.diagnostics
    )


@pytest.mark.asyncio
async def test_gateway_explicit_designation_wins() -> None:
    runner = RuneRunner()
    runner.set_designated_gateway_rune("gw_custom")
    loads = [
        RuneLoad(
            manifest=RuneManifest(
                name="gw_a",
                version="1.0.0",
                description="",
                spell_gateway=True,
                scope=RuneScope.PROJECT,
            ),
        ),
        RuneLoad(
            manifest=RuneManifest(
                name="gw_custom",
                version="1.0.0",
                description="",
                spell_gateway=True,
                scope=RuneScope.USER,
            ),
        ),
    ]
    await runner.load_rune_loads(loads)
    assert runner.gateway_rune_name == "gw_custom"
