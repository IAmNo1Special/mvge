import concurrent.futures
import json
import py_compile
import sys
import tempfile
from importlib.util import cache_from_source
from pathlib import Path
from unittest.mock import patch

import pytest

from mvge.core.layers import resolve_rune_paths
from mvge.core.spells import ExecutionMode
from mvge.runes.loader import (
    _module_importable,
    load_factory_from_manifest,
    load_manifests,
    load_runes_from_paths,
)
from mvge.runes.manifest import load_manifest
from mvge.runes.types import (
    Diagnostic,
    DiagnosticKind,
    RuneManifest,
    RuneScope,
    SigilHook,
    SpellDefinition,
)


def test_load_manifest_valid() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        rune_dir = Path(tmpdir) / "test_rune"
        rune_dir.mkdir()
        manifest_file = rune_dir / "manifest.json"
        manifest_data = (
            '{"name": "test_rune", "version": "1.0.0", '
            '"description": "Test rune", "hooks": ["before_invocation"], '
            '"entry_point": "main.py"}'
        )
        manifest_file.write_text(manifest_data, encoding="utf-8")

        manifest = load_manifest(rune_dir)

        assert manifest is not None
        assert manifest.name == "test_rune"
        assert manifest.version == "1.0.0"
        assert manifest.description == "Test rune"
        assert manifest.hooks == [SigilHook.BEFORE_INVOCATION]
        assert manifest.entry_point == "main.py"


def test_load_manifest_missing_file() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        rune_dir = Path(tmpdir) / "test_rune"
        rune_dir.mkdir()

        manifest = load_manifest(rune_dir)

        assert manifest is None


def test_load_manifest_invalid_json() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        rune_dir = Path(tmpdir) / "test_rune"
        rune_dir.mkdir()
        manifest_file = rune_dir / "manifest.json"
        manifest_file.write_text("{invalid json}", encoding="utf-8")

        manifest = load_manifest(rune_dir)

        assert manifest is None


def test_load_manifest_missing_required_fields() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        rune_dir = Path(tmpdir) / "test_rune"
        rune_dir.mkdir()
        manifest_file = rune_dir / "manifest.json"
        manifest_file.write_text('{"version": "1.0.0"}', encoding="utf-8")

        manifest = load_manifest(rune_dir)

        assert manifest is None


def test_load_manifest_with_shortcuts_dict() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        rune_dir = Path(tmpdir) / "test_rune"
        rune_dir.mkdir()
        manifest_file = rune_dir / "manifest.json"
        manifest_data = (
            '{"name": "test_rune", "version": "1.0.0", '
            '"description": "Test", "hooks": [], '
            '"shortcuts": [{"key": "ctrl+k", "description": "Clear"}]}'
        )
        manifest_file.write_text(manifest_data, encoding="utf-8")

        manifest = load_manifest(rune_dir)

        assert manifest is not None
        assert len(manifest.shortcuts) == 1
        assert manifest.shortcuts[0].key == "ctrl+k"
        assert manifest.shortcuts[0].description == "Clear"


def test_load_manifest_with_shortcuts_strings() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        rune_dir = Path(tmpdir) / "test_rune"
        rune_dir.mkdir()
        manifest_file = rune_dir / "manifest.json"
        manifest_data = (
            '{"name": "test_rune", "version": "1.0.0", '
            '"description": "Test", "hooks": [], '
            '"shortcuts": ["ctrl+k", "ctrl+r"]}'
        )
        manifest_file.write_text(manifest_data, encoding="utf-8")

        manifest = load_manifest(rune_dir)

        assert manifest is not None
        assert len(manifest.shortcuts) == 2
        assert manifest.shortcuts[0].key == "ctrl+k"
        assert manifest.shortcuts[1].key == "ctrl+r"


def test_load_manifest_defaults() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        rune_dir = Path(tmpdir) / "test_rune"
        rune_dir.mkdir()
        manifest_file = rune_dir / "manifest.json"
        manifest_data = (
            '{"name": "test_rune", "version": "1.0.0", '
            '"description": "Test", "hooks": []}'
        )
        manifest_file.write_text(manifest_data, encoding="utf-8")

        manifest = load_manifest(rune_dir)

        assert manifest is not None
        assert manifest.shortcuts == []
        assert manifest.system_deps == []
        assert manifest.python_deps == []
        assert manifest.enabled is True


def test_load_manifests_with_shortcuts() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        ext_dir = Path(tmpdir)
        rune_dir = ext_dir / "rune_with_shortcuts"
        rune_dir.mkdir()
        manifest_data = (
            '{"name": "sc_rune", "version": "1.0.0", '
            '"description": "Rune with shortcuts", "hooks": [], '
            '"shortcuts": [{"key": "ctrl+s", "description": "Save"}]}'
        )
        (rune_dir / "manifest.json").write_text(manifest_data, encoding="utf-8")

        manifests = load_manifests(ext_dir)
        assert len(manifests) == 1
        assert len(manifests[0].shortcuts) == 1
        assert manifests[0].shortcuts[0].key == "ctrl+s"


def test_rune_loader_load_all() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        extensions_dir = Path(tmpdir)

        rune1_dir = extensions_dir / "rune1"
        rune1_dir.mkdir()
        rune1_data = (
            '{"name": "rune1", "version": "1.0.0", '
            '"description": "Rune 1", "hooks": [], "entry_point": "main.py"}'
        )
        (rune1_dir / "manifest.json").write_text(rune1_data, encoding="utf-8")

        rune2_dir = extensions_dir / "rune2"
        rune2_dir.mkdir()
        rune2_data = (
            '{"name": "rune2", "version": "2.0.0", '
            '"description": "Rune 2", "hooks": ["after_invocation"], '
            '"entry_point": "run.py"}'
        )
        (rune2_dir / "manifest.json").write_text(rune2_data, encoding="utf-8")

        manifests = load_manifests(extensions_dir)

        assert len(manifests) == 2
        names = {m.name for m in manifests}
        assert names == {"rune1", "rune2"}


def test_load_manifests_empty_dir() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        extensions_dir = Path(tmpdir)

        manifests = load_manifests(extensions_dir)

        assert manifests == []


def test_load_manifests_nonexistent_dir() -> None:
    manifests = load_manifests(Path("/nonexistent/path"))

    assert manifests == []


def test_load_manifests_skips_files() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        ext_dir = Path(tmpdir)
        (ext_dir / "README.txt").write_text("hello", encoding="utf-8")
        assert load_manifests(ext_dir) == []


def test_discover_rune_site_packages_posix_structure(tmp_path: Path) -> None:
    from mvge.runes.loader import discover_rune_site_packages

    rune_dir = tmp_path / "my_rune"
    venv_dir = rune_dir / ".venv"
    lib_dir = venv_dir / "lib"
    posix_sp = lib_dir / "site-packages"
    posix_sp.mkdir(parents=True)
    python_sp = lib_dir / "python3.13" / "site-packages"
    python_sp.mkdir(parents=True)

    paths = discover_rune_site_packages(rune_dir)
    assert posix_sp in paths
    assert python_sp in paths


def test_load_factory_from_manifest_no_entry_point() -> None:
    manifest = RuneManifest(name="test", version="1.0", description="", entry_point="")
    factory = load_factory_from_manifest(manifest, Path("/tmp"))
    assert factory is None


def test_sigil_hook_enum_values() -> None:
    assert SigilHook.BEFORE_INVOCATION.value == "before_invocation"
    assert SigilHook.AFTER_INVOCATION.value == "after_invocation"
    assert SigilHook.BEFORE_SPELL_CAST.value == "before_spell_cast"
    assert SigilHook.AFTER_SPELL_RESULT.value == "after_spell_result"
    assert SigilHook.BEFORE_PROVIDER_REQUEST.value == "before_provider_request"
    assert SigilHook.AFTER_PROVIDER_RESPONSE.value == "after_provider_response"
    assert SigilHook.BEFORE_PROVIDER_HEADERS.value == "before_provider_headers"
    assert SigilHook.TURN_START.value == "turn_start"
    assert SigilHook.TURN_END.value == "turn_end"
    assert SigilHook.SESSION_START.value == "session_start"
    assert SigilHook.SESSION_SHUTDOWN.value == "session_shutdown"
    assert SigilHook.SESSION_BEFORE_SWITCH.value == "session_before_switch"
    assert SigilHook.SESSION_BEFORE_FORK.value == "session_before_fork"
    assert SigilHook.CONTEXT_TRANSFORM.value == "context_transform"


def test_load_manifest_with_system_deps() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        rune_dir = Path(tmpdir) / "test_rune"
        rune_dir.mkdir()
        manifest_file = rune_dir / "manifest.json"
        manifest_data = (
            '{"name": "test_rune", "version": "1.0.0", '
            '"description": "Test rune", "hooks": [], '
            '"system_deps": ["ripgrep", "git"]}'
        )
        manifest_file.write_text(manifest_data, encoding="utf-8")

        manifest = load_manifest(rune_dir)

        assert manifest is not None
        assert manifest.system_deps == ["ripgrep", "git"]


def test_load_manifest_system_deps_non_list_defaults_to_empty() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        rune_dir = Path(tmpdir) / "test_rune"
        rune_dir.mkdir()
        manifest_file = rune_dir / "manifest.json"
        manifest_data = (
            '{"name": "test_rune", "version": "1.0.0", '
            '"description": "Test rune", "hooks": [], '
            '"system_deps": "not-a-list"}'
        )
        manifest_file.write_text(manifest_data, encoding="utf-8")

        manifest = load_manifest(rune_dir)

        assert manifest is not None
        assert manifest.system_deps == []


def test_load_manifest_system_deps_filters_non_strings() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        rune_dir = Path(tmpdir) / "test_rune"
        rune_dir.mkdir()
        manifest_file = rune_dir / "manifest.json"
        manifest_data = (
            '{"name": "test_rune", "version": "1.0.0", '
            '"description": "Test rune", "hooks": [], '
            '"system_deps": ["ripgrep", 123, null, "git"]}'
        )
        manifest_file.write_text(manifest_data, encoding="utf-8")

        manifest = load_manifest(rune_dir)

        assert manifest is not None
        assert manifest.system_deps == ["ripgrep", "git"]


def test_load_manifest_with_python_deps() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        rune_dir = Path(tmpdir) / "test_rune"
        rune_dir.mkdir()
        manifest_file = rune_dir / "manifest.json"
        manifest_data = (
            '{"name": "test_rune", "version": "1.0.0", '
            '"description": "Test rune", "hooks": [], '
            '"python_deps": ["heal_my_goap", "aiohttp"]}'
        )
        manifest_file.write_text(manifest_data, encoding="utf-8")

        manifest = load_manifest(rune_dir)

        assert manifest is not None
        assert manifest.python_deps == ["heal_my_goap", "aiohttp"]


def test_load_manifest_enabled_false() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        rune_dir = Path(tmpdir) / "test_rune"
        rune_dir.mkdir()
        manifest_file = rune_dir / "manifest.json"
        manifest_data = (
            '{"name": "test_rune", "version": "1.0.0", '
            '"description": "Test rune", "hooks": [], "enabled": false}'
        )
        manifest_file.write_text(manifest_data, encoding="utf-8")

        manifest = load_manifest(rune_dir)

        assert manifest is not None
        assert manifest.enabled is False


def test_load_manifest_enabled_non_bool_defaults_true() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        rune_dir = Path(tmpdir) / "test_rune"
        rune_dir.mkdir()
        manifest_file = rune_dir / "manifest.json"
        manifest_data = (
            '{"name": "test_rune", "version": "1.0.0", '
            '"description": "Test rune", "hooks": [], "enabled": "yes"}'
        )
        manifest_file.write_text(manifest_data, encoding="utf-8")

        manifest = load_manifest(rune_dir)
        assert manifest is not None
        assert manifest.enabled is True


def test_load_manifests_skips_disabled_runes() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        extensions_dir = Path(tmpdir)

        disabled_dir = extensions_dir / "disabled_rune"
        disabled_dir.mkdir()
        (disabled_dir / "manifest.json").write_text(
            '{"name": "disabled_rune", "version": "1.0.0", '
            '"hooks": [], "enabled": false}',
            encoding="utf-8",
        )

        manifests = load_manifests(extensions_dir)
        assert manifests == []


@pytest.mark.asyncio
async def test_spell_definition_execute_not_implemented() -> None:
    spell = SpellDefinition(
        name="test",
        description="test",
        parameters={},
        execution_mode=ExecutionMode.PARALLEL,
    )
    with pytest.raises(NotImplementedError):
        await spell.execute("cast-1", {})


@pytest.mark.asyncio
async def test_spell_definition_execute_with_handler() -> None:
    async def handler_with_id(spell_cast_id: str, params: dict, **kwargs) -> dict:
        return {"id": spell_cast_id, "params": params}

    spell1 = SpellDefinition(name="test1", description="test", handler=handler_with_id)
    result1 = await spell1.execute("cast-1", {"key": "val"})
    assert result1 == {"id": "cast-1", "params": {"key": "val"}}

    async def handler_params_only(params: dict, **kwargs) -> dict:
        return {"params": params}

    spell2 = SpellDefinition(
        name="test2", description="test", handler=handler_params_only
    )
    result2 = await spell2.execute("cast-2", {"num": 42})
    assert result2 == {"params": {"num": 42}}


class TestLoadRunesFromPaths:
    def test_dedup_by_name_first_wins(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmpdir1,
            tempfile.TemporaryDirectory() as tmpdir2,
        ):
            rune1_dir = Path(tmpdir1) / "shared_name"
            rune1_dir.mkdir()
            (rune1_dir / "manifest.json").write_text(
                '{"name": "shared_name", "version": "1.0.0", '
                '"description": "First", "hooks": []}',
                encoding="utf-8",
            )

            rune2_dir = Path(tmpdir2) / "shared_name"
            rune2_dir.mkdir()
            (rune2_dir / "manifest.json").write_text(
                '{"name": "shared_name", "version": "2.0.0", '
                '"description": "Second", "hooks": []}',
                encoding="utf-8",
            )

            loads, diagnostics = load_runes_from_paths(
                [
                    (Path(tmpdir1), RuneScope.PROJECT),
                    (Path(tmpdir2), RuneScope.USER),
                ]
            )
            assert len(loads) == 1
            assert loads[0].manifest.version == "1.0.0"
            assert loads[0].manifest.scope == RuneScope.PROJECT

            shadowed = [
                d for d in diagnostics if d.kind == DiagnosticKind.SHADOWED_RUNE
            ]
            assert len(shadowed) == 1
            assert shadowed[0].rune_name == "shared_name"

    def test_winner_scope_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            rune_dir = Path(tmpdir) / "my_rune"
            rune_dir.mkdir()
            (rune_dir / "manifest.json").write_text(
                '{"name": "my_rune", "version": "1.0.0", '
                '"description": "Test", "hooks": []}',
                encoding="utf-8",
            )

            loads, diagnostics = load_runes_from_paths(
                [(Path(tmpdir), RuneScope.AGENT)]
            )
            assert len(loads) == 1
            assert loads[0].manifest.scope == RuneScope.AGENT
            assert loads[0].manifest.path == str(rune_dir)

    def test_parse_warning_for_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            rune_dir = Path(tmpdir) / "bad_rune"
            rune_dir.mkdir()
            (rune_dir / "manifest.json").write_text("{invalid json}", encoding="utf-8")

            loads, diagnostics = load_runes_from_paths([(Path(tmpdir), RuneScope.USER)])
            assert len(loads) == 0
            parse_warnings = [
                d for d in diagnostics if d.kind == DiagnosticKind.PARSE_WARNING
            ]
            assert len(parse_warnings) == 1
            assert parse_warnings[0].rune_name == "bad_rune"

    def test_load_failure_for_missing_entry_point(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            rune_dir = Path(tmpdir) / "broken_rune"
            rune_dir.mkdir()
            (rune_dir / "manifest.json").write_text(
                '{"name": "broken_rune", "version": "1.0.0", '
                '"description": "Broken", "hooks": [], '
                '"entry_point": "nonexistent.py"}',
                encoding="utf-8",
            )

            loads, diagnostics = load_runes_from_paths([(Path(tmpdir), RuneScope.USER)])
            assert len(loads) == 1
            assert loads[0].factory is None
            load_failures = [
                d for d in diagnostics if d.kind == DiagnosticKind.LOAD_FAILURE
            ]
            assert len(load_failures) == 1
            assert load_failures[0].rune_name == "broken_rune"

    def test_multiple_scopes_no_duplicates(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmpdir1,
            tempfile.TemporaryDirectory() as tmpdir2,
        ):
            rune1_dir = Path(tmpdir1) / "rune_a"
            rune1_dir.mkdir()
            (rune1_dir / "manifest.json").write_text(
                '{"name": "rune_a", "version": "1.0.0", '
                '"description": "A", "hooks": []}',
                encoding="utf-8",
            )

            rune2_dir = Path(tmpdir2) / "rune_b"
            rune2_dir.mkdir()
            (rune2_dir / "manifest.json").write_text(
                '{"name": "rune_b", "version": "1.0.0", '
                '"description": "B", "hooks": []}',
                encoding="utf-8",
            )

            loads, diagnostics = load_runes_from_paths(
                [
                    (Path(tmpdir1), RuneScope.PROJECT),
                    (Path(tmpdir2), RuneScope.USER),
                ]
            )
            assert len(loads) == 2
            names = {load.manifest.name for load in loads}
            assert names == {"rune_a", "rune_b"}


def test_load_factory_supports_module_level_dataclass() -> None:
    """An entry point may define a ``@dataclass`` at module scope.

    ``dataclasses`` resolves annotations through
    ``sys.modules[cls.__module__].__dict__`` while the defining module is still
    executing, so the loader has to register the module before running it. It
    did not, and any entry point using a dataclass (or ``NamedTuple``, or
    ``get_type_hints``) died with ``'NoneType' object has no attribute
    '__dict__'`` -- a real rune, ``session-title``, was unloadable because of it.
    """
    orig_sys_path = list(sys.path)
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            rune_dir = Path(tmpdir) / "dataclass_rune"
            rune_dir.mkdir()
            (rune_dir / "manifest.json").write_text(
                '{"name": "dataclass_rune", "version": "1.0.0", '
                '"description": "Test", "entry_point": "rune.py"}',
                encoding="utf-8",
            )
            (rune_dir / "rune.py").write_text(
                "from __future__ import annotations\n"
                "\n"
                "from dataclasses import dataclass\n"
                "\n"
                "@dataclass\n"
                "class State:\n"
                "    name: str | None = None\n"
                "\n"
                "def rune_factory(api):\n"
                "    pass\n",
                encoding="utf-8",
            )

            manifest = load_manifest(rune_dir)
            assert manifest is not None
            diagnostics: list[Diagnostic] = []
            factory = load_factory_from_manifest(manifest, rune_dir, diagnostics)

            assert not diagnostics, [d.message for d in diagnostics]
            assert factory is not None
            assert factory.__name__ == "rune_factory"
    finally:
        sys.modules.pop("mvgeos_rune_dataclass_rune", None)
        sys.path.clear()
        sys.path.extend(orig_sys_path)


def test_failed_entry_point_does_not_leave_module_in_sys_modules() -> None:
    """A half-initialised module must not be left behind for the next import.

    Once a name is in ``sys.modules`` every later import trusts it, so a
    failed load that leaves the entry behind would turn one clear error into a
    permanently broken rune.
    """
    orig_sys_path = list(sys.path)
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            rune_dir = Path(tmpdir) / "boom_rune"
            rune_dir.mkdir()
            (rune_dir / "manifest.json").write_text(
                '{"name": "boom_rune", "version": "1.0.0", '
                '"description": "Test", "entry_point": "rune.py"}',
                encoding="utf-8",
            )
            (rune_dir / "rune.py").write_text(
                "raise RuntimeError('boom')\n", encoding="utf-8"
            )

            manifest = load_manifest(rune_dir)
            assert manifest is not None
            diagnostics: list[Diagnostic] = []
            factory = load_factory_from_manifest(manifest, rune_dir, diagnostics)

            assert factory is None
            assert diagnostics, "a failing entry point must report a diagnostic"
            assert "mvgeos_rune_boom_rune" not in sys.modules
    finally:
        sys.modules.pop("mvgeos_rune_boom_rune", None)
        sys.path.clear()
        sys.path.extend(orig_sys_path)


def test_load_factory_from_manifest_with_local_import() -> None:
    orig_sys_path = list(sys.path)
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            rune_dir = Path(tmpdir) / "imported_rune"
            rune_dir.mkdir()
            (rune_dir / "manifest.json").write_text(
                '{"name": "imported_rune", "version": "1.0.0", '
                '"description": "Test", "entry_point": "main.py"}',
                encoding="utf-8",
            )
            (rune_dir / "helper_mod.py").write_text(
                "HELPER_VALUE = 123\n", encoding="utf-8"
            )
            (rune_dir / "main.py").write_text(
                "import helper_mod\n\ndef rune_factory(api):\n    pass\n",
                encoding="utf-8",
            )

            manifest = load_manifest(rune_dir)
            assert manifest is not None
            factory = load_factory_from_manifest(manifest, rune_dir)
            assert factory is not None
            assert str(rune_dir.resolve()) in sys.path
    finally:
        sys.path.clear()
        sys.path.extend(orig_sys_path)


def test_load_factory_from_manifest_nested_entrypoint_with_imports() -> None:
    orig_sys_path = list(sys.path)
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            rune_dir = Path(tmpdir) / "nested_rune"
            rune_dir.mkdir()
            src_dir = rune_dir / "src"
            src_dir.mkdir()

            (rune_dir / "manifest.json").write_text(
                '{"name": "nested_rune", "version": "1.0.0", '
                '"description": "Test", "entry_point": "src/main.py"}',
                encoding="utf-8",
            )
            (rune_dir / "root_helper.py").write_text(
                "ROOT_VAL = 'root'\n", encoding="utf-8"
            )
            (src_dir / "nested_helper.py").write_text(
                "NESTED_VAL = 'nested'\n", encoding="utf-8"
            )
            (src_dir / "main.py").write_text(
                "import root_helper\n"
                "import nested_helper\n\n"
                "def rune_factory(api):\n"
                "    pass\n",
                encoding="utf-8",
            )

            manifest = load_manifest(rune_dir)
            assert manifest is not None
            factory = load_factory_from_manifest(manifest, rune_dir)
            assert factory is not None
            assert str(src_dir.resolve()) in sys.path
            assert str(rune_dir.resolve()) in sys.path
    finally:
        sys.path.clear()
        sys.path.extend(orig_sys_path)


def test_load_manifest_runtime_field() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        rune_dir = Path(tmpdir) / "ts_rune"
        rune_dir.mkdir()
        manifest_file = rune_dir / "manifest.json"
        manifest_data = (
            '{"name": "ts_rune", "version": "1.0.0", '
            '"description": "TypeScript rune", '
            '"entry_point": "index.ts", "runtime": "typescript"}'
        )
        manifest_file.write_text(manifest_data, encoding="utf-8")

        manifest = load_manifest(rune_dir)
        assert manifest is not None
        assert manifest.name == "ts_rune"
        assert manifest.runtime == "typescript"


def test_load_factory_skips_incompatible_runtime() -> None:
    from mvge.runes.types import Diagnostic

    with tempfile.TemporaryDirectory() as tmpdir:
        rune_dir = Path(tmpdir) / "ts_rune"
        rune_dir.mkdir()
        ts_file = rune_dir / "index.ts"
        ts_file.write_text("console.log('hi');", encoding="utf-8")

        manifest = RuneManifest(
            name="ts_rune",
            version="1.0.0",
            description="TypeScript rune",
            entry_point="index.ts",
            runtime="typescript",
        )
        diagnostics: list[Diagnostic] = []
        factory = load_factory_from_manifest(
            manifest, rune_dir, diagnostics=diagnostics
        )

        assert factory is None
        assert len(diagnostics) == 1
        assert diagnostics[0].kind == DiagnosticKind.INCOMPATIBLE_RUNTIME
        assert "targets 'typescript' runtime" in diagnostics[0].message
        assert diagnostics[0].rune_name == "ts_rune"


def _write_manifest(tmpdir: str, data: str) -> Path:
    rune_dir = Path(tmpdir) / "gw_rune"
    rune_dir.mkdir(exist_ok=True)
    (rune_dir / "manifest.json").write_text(data, encoding="utf-8")
    return rune_dir


def test_load_manifest_spell_gateway_true() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        rune_dir = _write_manifest(
            tmpdir,
            '{"name": "seeker", "version": "1.0.0", "spell_gateway": true}',
        )
        manifest = load_manifest(rune_dir)
        assert manifest is not None
        assert manifest.spell_gateway is True


def test_load_manifest_spell_gateway_defaults_false() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        rune_dir = _write_manifest(tmpdir, '{"name": "plain", "version": "1.0.0"}')
        manifest = load_manifest(rune_dir)
        assert manifest is not None
        assert manifest.spell_gateway is False


def test_load_manifest_spell_gateway_non_bool_defaults_false() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        rune_dir = _write_manifest(
            tmpdir,
            '{"name": "weird", "version": "1.0.0", "spell_gateway": "yes"}',
        )
        manifest = load_manifest(rune_dir)
        assert manifest is not None
        assert manifest.spell_gateway is False


def _write_rune_entry(rune_dir: Path, entry_name: str, code: str) -> None:
    rune_dir.mkdir(parents=True, exist_ok=True)
    (rune_dir / entry_name).write_text(code, encoding="utf-8")


def _basic_manifest(
    name: str = "r", entry_point: str = "rune.py", **kwargs: object
) -> RuneManifest:
    return RuneManifest(
        name=name,
        version="1.0.0",
        description="Test rune",
        entry_point=entry_point,
        **kwargs,  # type: ignore[arg-type]
    )


def test_module_importable_probe_error_is_not_importable() -> None:
    """A find_spec probe error is treated as not importable."""
    with patch("importlib.util.find_spec", side_effect=ValueError("bogus module name")):
        assert _module_importable("not a module name") is False


def test_load_factory_skips_incompatible_runtime_without_diagnostics() -> None:
    """The TypeScript skip works without a diagnostics sink."""
    with tempfile.TemporaryDirectory() as tmpdir:
        rune_dir = Path(tmpdir) / "ts_rune"
        _write_rune_entry(rune_dir, "index.ts", "console.log('hi');")
        manifest = _basic_manifest(
            name="ts_rune", entry_point="index.ts", runtime="typescript"
        )

        assert load_factory_from_manifest(manifest, rune_dir) is None


def test_load_factory_missing_deps_without_diagnostics() -> None:
    """Missing python_deps aborts the load even without a diagnostics sink."""
    with tempfile.TemporaryDirectory() as tmpdir:
        rune_dir = Path(tmpdir) / "r"
        _write_rune_entry(rune_dir, "rune.py", "def rune_factory(api):\n    pass\n")
        manifest = _basic_manifest(python_deps=["definitely-not-a-real-dep-xyz-123"])

        assert load_factory_from_manifest(manifest, rune_dir) is None


def test_load_factory_missing_entry_without_diagnostics() -> None:
    """A missing entry point aborts the load without a diagnostics sink."""
    with tempfile.TemporaryDirectory() as tmpdir:
        manifest = _basic_manifest(entry_point="nope.py")

        assert load_factory_from_manifest(manifest, Path(tmpdir)) is None


def test_load_factory_unloadable_entry_returns_none() -> None:
    """An entry point with no import loader yields no factory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        rune_dir = Path(tmpdir) / "r"
        _write_rune_entry(rune_dir, "rune.txt", "not python\n")
        manifest = _basic_manifest(entry_point="rune.txt")

        assert load_factory_from_manifest(manifest, rune_dir) is None


def test_load_factory_exec_error_without_diagnostics() -> None:
    """An entry point that raises on import yields no factory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        rune_dir = Path(tmpdir) / "r"
        _write_rune_entry(rune_dir, "rune.py", "raise RuntimeError('boom')\n")
        manifest = _basic_manifest()

        assert load_factory_from_manifest(manifest, rune_dir) is None


def test_load_factory_survives_vanishing_stale_bytecode() -> None:
    """A pyc that disappears between the stale check and the delete must not
    kill the load: the bytecode cleanup is best-effort, not load-critical."""
    real_unlink = Path.unlink

    def _vanishing_unlink(self: Path, *args: object, **kwargs: object) -> None:
        # Simulate the race: the file passed is_file() but is gone by the
        # time unlink runs. Only a missing_ok delete tolerates that.
        if kwargs.get("missing_ok", False):
            real_unlink(self, *args, **kwargs)
            return
        raise FileNotFoundError(str(self))

    with tempfile.TemporaryDirectory() as tmpdir:
        rune_dir = Path(tmpdir) / "r"
        _write_rune_entry(rune_dir, "rune.py", "def rune_factory(api):\n    pass\n")
        manifest = _basic_manifest()
        # Plant a stale pyc so the cleanup path actually runs.
        entry = rune_dir / "rune.py"
        py_compile.compile(
            str(entry),
            cfile=cache_from_source(str(entry)),
            doraise=True,
        )
        with patch.object(Path, "unlink", _vanishing_unlink):
            factory = load_factory_from_manifest(manifest, rune_dir)

        assert factory is not None
        assert callable(factory)


def test_load_factory_concurrent_loads_do_not_race_bytecode_cleanup() -> None:
    """Concurrent loads of the same rune entry point must all succeed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        rune_dir = Path(tmpdir) / "r"
        _write_rune_entry(rune_dir, "rune.py", "def rune_factory(api):\n    pass\n")
        manifest = _basic_manifest()
        errors: list[BaseException] = []

        def _load() -> None:
            try:
                assert load_factory_from_manifest(manifest, rune_dir) is not None
            except BaseException as exc:  # noqa: BLE001 - collected for the assertion
                errors.append(exc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(_load) for _ in range(16)]
            for future in futures:
                future.result()

        assert errors == []


def test_load_factory_missing_rune_factory_without_diagnostics() -> None:
    """An entry point without a rune_factory export yields no factory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        rune_dir = Path(tmpdir) / "r"
        _write_rune_entry(rune_dir, "rune.py", "VALUE = 42\n")
        manifest = _basic_manifest()

        assert load_factory_from_manifest(manifest, rune_dir) is None


def test_load_factory_zero_arg_factory_without_diagnostics() -> None:
    """A rune_factory taking no arguments is rejected."""
    with tempfile.TemporaryDirectory() as tmpdir:
        rune_dir = Path(tmpdir) / "r"
        _write_rune_entry(rune_dir, "rune.py", "def rune_factory():\n    pass\n")
        manifest = _basic_manifest()

        assert load_factory_from_manifest(manifest, rune_dir) is None


def test_load_factory_signature_probe_error_is_tolerated() -> None:
    """An uninspectable factory signature does not block loading."""
    with tempfile.TemporaryDirectory() as tmpdir:
        rune_dir = Path(tmpdir) / "r"
        _write_rune_entry(rune_dir, "rune.py", "def rune_factory(api):\n    pass\n")
        manifest = _basic_manifest()

        with patch(
            "mvge.runes.loader.inspect.signature",
            side_effect=ValueError("no signature"),
        ):
            factory = load_factory_from_manifest(manifest, rune_dir)

        assert callable(factory)


def test_load_manifests_skips_unparseable_dir_without_diagnostics() -> None:
    """Unparseable manifest directories are skipped without a diagnostics sink."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ext = Path(tmpdir) / "extensions"
        bad = ext / "bad_rune"
        bad.mkdir(parents=True)
        (bad / "manifest.json").write_text("{invalid json", encoding="utf-8")

        assert load_manifests(ext) == []


def test_cross_home_does_not_load_cwd_project_runes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REGRESSION (item 1) — project layer needs an explicit anchor.

    With HOME/USERPROFILE pointed at an isolated home and the CWD holding
    ``.agents/extensions/<marker rune>``, resolving the default rune paths
    and loading from them must not load the CWD-anchored rune.

    Decided 2026-09-22 (Malcom): the project layer is anchored to an
    explicit project directory, never to the ambient working directory.
    ``resolve_rune_paths()`` without ``project_dir`` omits the project
    layer entirely.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("MVGEOS_GLOBAL_DIR", str(home / "global"))

    cwd_dir = tmp_path / "cwd"
    marker = cwd_dir / ".agents" / "extensions" / "cwd_marker"
    marker.mkdir(parents=True)
    (marker / "manifest.json").write_text(
        '{"name": "cwd_marker", "version": "1.0.0", '
        '"description": "marker", "entry_point": "rune.py"}',
        encoding="utf-8",
    )
    (marker / "rune.py").write_text(
        "def create_rune(api):\n    return object()\n", encoding="utf-8"
    )
    monkeypatch.chdir(cwd_dir)

    paths = resolve_rune_paths("test-agent")
    loads, _diagnostics = load_runes_from_paths(
        [(path, RuneScope.PROJECT) for path in paths]
    )
    assert "cwd_marker" not in [load.manifest.name for load in loads]


def test_load_manifest_with_ui_contributions() -> None:
    """Rune UI contributions (views, canvas_tabs) are parsed into typed structures."""
    with tempfile.TemporaryDirectory() as tmpdir:
        rune_dir = Path(tmpdir) / "test_rune"
        rune_dir.mkdir()
        manifest_data = {
            "name": "test-ui-rune",
            "version": "1.0.0",
            "description": "Test UI Contributions",
            "entry_point": "rune.py",
            "ui": {
                "views": [
                    {
                        "id": "skills",
                        "title": "Skills",
                        "icon": "auto_awesome",
                        "order": 50,
                        "description": "Skill Manager",
                    }
                ],
                "canvas_tabs": [
                    {
                        "id": "review",
                        "title": "Review",
                        "icon": "rate_review",
                        "order": 20,
                    }
                ],
            },
        }
        (rune_dir / "manifest.json").write_text(
            json.dumps(manifest_data), encoding="utf-8"
        )
        manifest = load_manifest(rune_dir)

        assert manifest is not None
        assert len(manifest.ui.views) == 1
        view = manifest.ui.views[0]
        assert view.id == "skills"
        assert view.title == "Skills"
        assert view.icon == "auto_awesome"
        assert view.order == 50
        assert view.description == "Skill Manager"

        assert len(manifest.ui.canvas_tabs) == 1
        tab = manifest.ui.canvas_tabs[0]
        assert tab.id == "review"
        assert tab.title == "Review"
        assert tab.icon == "rate_review"
        assert tab.order == 20


def test_load_manifest_ui_contributions_defaults() -> None:
    """Missing or non-dict ui field defaults to empty RuneUIContributions."""
    with tempfile.TemporaryDirectory() as tmpdir:
        rune_dir = Path(tmpdir) / "test_rune"
        rune_dir.mkdir()
        manifest_data = {
            "name": "test-no-ui",
            "version": "1.0.0",
            "description": "No UI",
            "entry_point": "rune.py",
        }
        (rune_dir / "manifest.json").write_text(
            json.dumps(manifest_data), encoding="utf-8"
        )
        manifest = load_manifest(rune_dir)

        assert manifest is not None
        assert manifest.ui.views == []
        assert manifest.ui.canvas_tabs == []
