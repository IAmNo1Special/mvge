import json
import sys
import tempfile
from pathlib import Path

import pytest

from mvge.runes.loader import (
    load_factory_from_manifest,
    load_runes_from_paths,
)
from mvge.runes.rune_runner import RuneRunner
from mvge.runes.types import (
    DiagnosticKind,
    RuneManifest,
    RuneScope,
)


def _write_rune(
    parent: Path, name: str, body: str, entry_point: str = "rune.py"
) -> Path:
    rune_dir = parent / name
    rune_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": name,
        "version": "0.1.0",
        "description": "test rune",
        "entry_point": entry_point,
    }
    (rune_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (rune_dir / entry_point).write_text(body, encoding="utf-8")
    return rune_dir


def _poison_body(marker: Path) -> str:
    return (
        f"from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('pwned', encoding='utf-8')\n"
        "def rune_factory(api):\n"
        "    return None\n"
    )


def _lazy_spell_body() -> str:
    return (
        "def rune_factory(api):\n"
        "    from mvge.runes.types import SpellDefinition\n"
        "    class LazySpell(SpellDefinition):\n"
        "        async def execute(self, spell_cast_id, params, signal=None):\n"
        "            import lazy_helper\n"
        "            return {'result': lazy_helper.VALUE}\n"
        "    api.register_spell(LazySpell(name='lazy_spell', description='x'))\n"
    )


class TestShadowedRuneNeverExecutes:
    def test_initial_load_shadowed_module_never_executes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            marker = tmp / "shadowed_marker.txt"
            first_dir = tmp / "first"
            second_dir = tmp / "second"
            _write_rune(
                first_dir,
                "shared",
                "def rune_factory(api):\n    return None\n",
            )
            shadow_dir = _write_rune(second_dir, "shared", _poison_body(marker))

            loads, diagnostics = load_runes_from_paths(
                [
                    (first_dir, RuneScope.USER),
                    (second_dir, RuneScope.USER),
                ]
            )

            assert len(loads) == 1
            assert loads[0].manifest.version == "0.1.0"
            assert not marker.exists()
            assert not (shadow_dir / ".install-id").exists()
            assert any(
                d.kind == DiagnosticKind.SHADOWED_RUNE and d.rune_name == "shared"
                for d in diagnostics
            )

    @pytest.mark.asyncio
    async def test_refresh_shadowed_module_never_executes(self) -> None:
        runner = RuneRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            marker = tmp / "refresh_marker.txt"
            ext1 = tmp / "ext1"
            ext2 = tmp / "ext2"
            _write_rune(
                ext1,
                "shared",
                "def rune_factory(api):\n"
                "    from mvge.runes.types import SpellDefinition\n"
                "    class S(SpellDefinition):\n"
                "        async def execute(self, spell_cast_id, params, signal=None):\n"
                "            return {'result': 'winner'}\n"
                "    api.register_spell(S(name='winner_spell', description='x'))\n",
            )
            _write_rune(ext2, "shared", _poison_body(marker))

            diagnostics = await runner.refresh_runes([ext1, ext2])

            assert not marker.exists()
            assert any(
                d.kind == DiagnosticKind.SHADOWED_RUNE and d.rune_name == "shared"
                for d in diagnostics
            )
            names = {s.name for s in runner.get_all_registered_spells()}
            assert "winner_spell" in names


class TestEntryPointContainment:
    def test_relative_escape_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            marker = tmp / "evil_marker.txt"
            outside = tmp / "evil.py"
            outside.write_text(_poison_body(marker), encoding="utf-8")
            rune_dir = tmp / "rune" / "inner"
            rune_dir.mkdir(parents=True)
            manifest = RuneManifest(
                name="escape_rune",
                version="1.0.0",
                description="Test",
                entry_point="../../evil.py",
                scope=RuneScope.USER,
            )
            diagnostics: list = []

            factory = load_factory_from_manifest(
                manifest, rune_dir, diagnostics=diagnostics
            )

            assert factory is None
            assert not marker.exists()
            assert any(
                d.kind == DiagnosticKind.LOAD_FAILURE and d.rune_name == "escape_rune"
                for d in diagnostics
            )

    def test_absolute_escape_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            marker = tmp / "abs_marker.txt"
            outside = tmp / "evil.py"
            outside.write_text(_poison_body(marker), encoding="utf-8")
            rune_dir = tmp / "rune"
            rune_dir.mkdir()
            manifest = RuneManifest(
                name="abs_rune",
                version="1.0.0",
                description="Test",
                entry_point=str(outside),
                scope=RuneScope.USER,
            )
            diagnostics: list = []

            factory = load_factory_from_manifest(
                manifest, rune_dir, diagnostics=diagnostics
            )

            assert factory is None
            assert not marker.exists()
            assert any(
                d.kind == DiagnosticKind.LOAD_FAILURE and d.rune_name == "abs_rune"
                for d in diagnostics
            )

    def test_symlink_escape_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            marker = tmp / "link_marker.txt"
            outside = tmp / "evil.py"
            outside.write_text(_poison_body(marker), encoding="utf-8")
            rune_dir = tmp / "rune"
            rune_dir.mkdir()
            link = rune_dir / "link.py"
            try:
                link.symlink_to(outside)
            except OSError:
                pytest.skip("symlinks unavailable in this environment")
            manifest = RuneManifest(
                name="link_rune",
                version="1.0.0",
                description="Test",
                entry_point="link.py",
                scope=RuneScope.USER,
            )
            diagnostics: list = []

            factory = load_factory_from_manifest(
                manifest, rune_dir, diagnostics=diagnostics
            )

            assert factory is None
            assert not marker.exists()
            assert any(
                d.kind == DiagnosticKind.LOAD_FAILURE and d.rune_name == "link_rune"
                for d in diagnostics
            )


class TestLazyImportAndSysPathRemoval:
    @pytest.mark.asyncio
    async def test_lazy_import_at_spell_call_time_still_works(self) -> None:
        runner = RuneRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            rune_dir = _write_rune(tmp, "lazy_rune", _lazy_spell_body())
            (rune_dir / "lazy_helper.py").write_text(
                "VALUE = 'lazy-ok'\n", encoding="utf-8"
            )

            loads, _ = load_runes_from_paths([(tmp, RuneScope.USER)])
            await runner.load_rune_loads(loads)

            spells = {s.name: s for s in runner.get_all_registered_spells()}
            assert "lazy_spell" in spells
            result = await spells["lazy_spell"].execute("cast-1", {})
            assert result == {"result": "lazy-ok"}

    @pytest.mark.asyncio
    async def test_lazy_import_still_works_after_refresh(self) -> None:
        runner = RuneRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            ext_dir = tmp / "ext"
            rune_dir = _write_rune(ext_dir, "lazy_rune", _lazy_spell_body())
            (rune_dir / "lazy_helper.py").write_text(
                "VALUE = 'lazy-ok'\n", encoding="utf-8"
            )

            await runner.refresh_runes([ext_dir])

            spells = {s.name: s for s in runner.get_all_registered_spells()}
            assert "lazy_spell" in spells
            result = await spells["lazy_spell"].execute("cast-1", {})
            assert result == {"result": "lazy-ok"}

    @pytest.mark.asyncio
    async def test_sys_path_snapshot_load_plus_unload_unchanged(self) -> None:
        runner = RuneRunner()
        before = list(sys.path)
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                _write_rune(
                    tmp,
                    "path_rune",
                    "def rune_factory(api):\n    return None\n",
                )
                loads, _ = load_runes_from_paths([(tmp, RuneScope.USER)])
                await runner.load_rune_loads(loads)
                await runner.unload_rune("path_rune")
            assert list(sys.path) == before
        finally:
            sys.path[:] = before

    def test_sys_path_snapshot_load_plus_clear_rune_unchanged(self) -> None:
        runner = RuneRunner()
        before = list(sys.path)
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                _write_rune(
                    tmp,
                    "path_rune",
                    "def rune_factory(api):\n    return None\n",
                )
                loads, _ = load_runes_from_paths([(tmp, RuneScope.USER)])
                # load_rune_loads is async; run it synchronously here.
                import asyncio

                asyncio.run(runner.load_rune_loads(loads))
                runner.clear_rune("path_rune")
            assert list(sys.path) == before
        finally:
            sys.path[:] = before
