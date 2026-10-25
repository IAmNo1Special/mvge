import sys
import tempfile
from pathlib import Path

from mvge.runes.loader import load_factory_from_manifest
from mvge.runes.types import (
    DiagnosticKind,
    RuneManifest,
    RuneScope,
)


def test_auto_discover_venv_site_packages_windows() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        rune_dir = Path(tmpdir) / "test_rune"
        rune_dir.mkdir()

        # Create Windows venv site-packages layout
        site_pkg = rune_dir / ".venv" / "Lib" / "site-packages"
        site_pkg.mkdir(parents=True)

        # Place a dummy module in the rune's venv site-packages
        dep_module = site_pkg / "dummy_rune_dep.py"
        dep_module.write_text("FLAG = 'loaded_from_win_venv'", encoding="utf-8")

        # Create rune main entry point importing the dummy module
        entry_point = rune_dir / "main.py"
        entry_point.write_text(
            "import dummy_rune_dep\n"
            "def rune_factory(api):\n"
            "    return dummy_rune_dep.FLAG\n",
            encoding="utf-8",
        )

        manifest = RuneManifest(
            name="test_rune",
            version="1.0.0",
            description="Test rune",
            entry_point="main.py",
            scope=RuneScope.PROJECT,
        )

        factory = load_factory_from_manifest(manifest, rune_dir)

        assert factory is not None
        assert factory(None) == "loaded_from_win_venv"
        assert str(site_pkg.resolve()) in sys.path


def test_auto_discover_venv_site_packages_posix() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        rune_dir = Path(tmpdir) / "test_rune"
        rune_dir.mkdir()

        # Create POSIX venv site-packages layout
        site_pkg = rune_dir / ".venv" / "lib" / "python3.12" / "site-packages"
        site_pkg.mkdir(parents=True)

        # Place a dummy module in the rune's venv site-packages
        dep_module = site_pkg / "dummy_posix_dep.py"
        dep_module.write_text("FLAG = 'loaded_from_posix_venv'", encoding="utf-8")

        # Create rune main entry point importing the dummy module
        entry_point = rune_dir / "main.py"
        entry_point.write_text(
            "import dummy_posix_dep\n"
            "def rune_factory(api):\n"
            "    return dummy_posix_dep.FLAG\n",
            encoding="utf-8",
        )

        manifest = RuneManifest(
            name="test_rune",
            version="1.0.0",
            description="Test rune",
            entry_point="main.py",
            scope=RuneScope.PROJECT,
        )

        factory = load_factory_from_manifest(manifest, rune_dir)

        assert factory is not None
        assert factory(None) == "loaded_from_posix_venv"
        assert str(site_pkg.resolve()) in sys.path


def test_diagnostic_message_includes_declared_python_deps() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        rune_dir = Path(tmpdir) / "test_rune_failing"
        rune_dir.mkdir()

        entry_point = rune_dir / "main.py"
        entry_point.write_text(
            "import missing_dependency_abc\ndef rune_factory(api):\n    pass\n",
            encoding="utf-8",
        )

        manifest = RuneManifest(
            name="test_rune_failing",
            version="1.0.0",
            description="Failing rune",
            entry_point="main.py",
            python_deps=["missing_dependency_abc", "another_dep"],
            scope=RuneScope.PROJECT,
        )

        diagnostics: list = []
        factory = load_factory_from_manifest(
            manifest, rune_dir, diagnostics=diagnostics
        )

        assert factory is None
        # Pre-flight records a MISSING_DEP for each uninstalled declared dep
        # and skips the module import entirely (no LOAD_FAILURE).
        missing = [d for d in diagnostics if d.kind == DiagnosticKind.MISSING_DEP]
        assert len(missing) == 2
        assert missing[0].rune_name == "test_rune_failing"
        assert "mvgeos setup install" in missing[0].message


def test_auto_discover_parent_venv_site_packages() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace_dir = Path(tmpdir)
        rune_dir = workspace_dir / "runes" / "test_nested_rune"
        rune_dir.mkdir(parents=True)

        # Create Windows venv site-packages layout in parent workspace
        site_pkg = workspace_dir / ".venv" / "Lib" / "site-packages"
        site_pkg.mkdir(parents=True)

        dep_module = site_pkg / "dummy_parent_dep.py"
        dep_module.write_text("FLAG = 'loaded_from_parent_venv'", encoding="utf-8")

        entry_point = rune_dir / "main.py"
        entry_point.write_text(
            "import dummy_parent_dep\n"
            "def rune_factory(api):\n"
            "    return dummy_parent_dep.FLAG\n",
            encoding="utf-8",
        )

        manifest = RuneManifest(
            name="test_nested_rune",
            version="1.0.0",
            description="Nested rune in workspace",
            entry_point="main.py",
            scope=RuneScope.PROJECT,
        )

        factory = load_factory_from_manifest(manifest, rune_dir)

        assert factory is not None
        assert factory(None) == "loaded_from_parent_venv"
        assert str(site_pkg.resolve()) in sys.path
