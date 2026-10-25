from __future__ import annotations

import json
from pathlib import Path

import pytest

from mvge.runes.loader import (
    check_python_dep_installed,
    load_factory_from_manifest,
    load_runes_from_paths,
)
from mvge.runes.types import (
    Diagnostic,
    DiagnosticKind,
    RuneManifest,
    RuneScope,
)


def _write_rune(
    rune_dir: Path,
    entry_text: str,
    python_deps: list[str] | None = None,
) -> RuneManifest:
    rune_dir.mkdir(parents=True, exist_ok=True)
    entry = rune_dir / "rune.py"
    entry.write_text(entry_text, encoding="utf-8")
    manifest = RuneManifest(
        name=rune_dir.name,
        version="1.0.0",
        description="",
        entry_point="rune.py",
        python_deps=python_deps or [],
        scope=RuneScope.PROJECT,
    )

    (rune_dir / "manifest.json").write_text(
        json.dumps(
            {
                "name": manifest.name,
                "version": manifest.version,
                "description": manifest.description,
                "entry_point": manifest.entry_point,
                "python_deps": manifest.python_deps,
            }
        ),
        encoding="utf-8",
    )
    return manifest


def test_preflight_missing_python_dep_recorded(tmp_path: Path) -> None:
    """A declared, uninstalled python_dep yields a MISSING_DEP diagnostic."""
    rune_dir = tmp_path / "needs_dep"
    _write_rune(
        rune_dir,
        "import a_missing_module\n\ndef rune_factory(api):\n    pass\n",
        python_deps=["a_missing_module"],
    )
    diagnostics: list[Diagnostic] = []
    factory = load_factory_from_manifest(
        RuneManifest(
            name="needs_dep",
            version="1.0.0",
            description="",
            entry_point="rune.py",
            python_deps=["a_missing_module"],
            scope=RuneScope.PROJECT,
        ),
        rune_dir,
        diagnostics=diagnostics,
    )
    assert factory is None
    missing = [d for d in diagnostics if d.kind == DiagnosticKind.MISSING_DEP]
    assert len(missing) == 1
    assert "a_missing_module" in missing[0].message
    assert "mvgeos setup install" in missing[0].message


def test_preflight_present_python_dep_no_diagnostic(tmp_path: Path) -> None:
    """An installed python_dep (json) is not flagged."""
    rune_dir = tmp_path / "has_dep"
    _write_rune(
        rune_dir,
        "import json\n\ndef rune_factory(api):\n    pass\n",
        python_deps=["json"],
    )
    diagnostics: list[Diagnostic] = []
    load_factory_from_manifest(
        RuneManifest(
            name="has_dep",
            version="1.0.0",
            description="",
            entry_point="rune.py",
            python_deps=["json"],
            scope=RuneScope.PROJECT,
        ),
        rune_dir,
        diagnostics=diagnostics,
    )
    assert not any(d.kind == DiagnosticKind.MISSING_DEP for d in diagnostics)


def test_preflight_multiple_missing_deps(tmp_path: Path) -> None:
    rune_dir = tmp_path / "multi_dep"
    _write_rune(
        rune_dir,
        "def rune_factory(api):\n    pass\n",
        python_deps=["missing_one_xyz", "missing_two_xyz"],
    )
    diagnostics: list[Diagnostic] = []
    load_factory_from_manifest(
        RuneManifest(
            name="multi_dep",
            version="1.0.0",
            description="",
            entry_point="rune.py",
            python_deps=["missing_one_xyz", "missing_two_xyz"],
            scope=RuneScope.PROJECT,
        ),
        rune_dir,
        diagnostics=diagnostics,
    )
    missing = [d for d in diagnostics if d.kind == DiagnosticKind.MISSING_DEP]
    assert {d.rune_name for d in missing} == {"multi_dep"}


def test_load_runes_from_paths_preflight(tmp_path: Path) -> None:
    """load_runes_from_paths surfaces missing-dep diagnostics via the manifest."""
    project = tmp_path / "runes"
    rune_dir = project / "needs_dep"
    _write_rune(
        rune_dir,
        "import a_missing_module\n\ndef rune_factory(api):\n    pass\n",
        python_deps=["a_missing_module"],
    )
    _loads, diagnostics = load_runes_from_paths([(project, RuneScope.PROJECT)])
    assert any(d.kind == DiagnosticKind.MISSING_DEP for d in diagnostics)


def test_preflight_does_not_flag_venv_bundled_dep(tmp_path: Path) -> None:
    """A dep bundled in the rune's own .venv must not be reported missing."""
    rune_dir = tmp_path / "bundled_dep"
    rune_dir.mkdir(parents=True)
    site_pkg = rune_dir / ".venv" / "Lib" / "site-packages"
    site_pkg.mkdir(parents=True)
    (site_pkg / "bundled_module.py").write_text("FLAG = 'bundled'", encoding="utf-8")
    (rune_dir / "rune.py").write_text(
        "import bundled_module\n\n"
        "def rune_factory(api):\n    return bundled_module.FLAG\n",
        encoding="utf-8",
    )
    (rune_dir / "manifest.json").write_text(
        json.dumps(
            {
                "name": "bundled_dep",
                "version": "1.0.0",
                "entry_point": "rune.py",
                "python_deps": ["bundled_module"],
            }
        ),
        encoding="utf-8",
    )
    diagnostics: list[Diagnostic] = []
    factory = load_factory_from_manifest(
        RuneManifest(
            name="bundled_dep",
            version="1.0.0",
            description="",
            entry_point="rune.py",
            python_deps=["bundled_module"],
            scope=RuneScope.PROJECT,
        ),
        rune_dir,
        diagnostics=diagnostics,
    )
    assert factory is not None
    assert not any(d.kind == DiagnosticKind.MISSING_DEP for d in diagnostics)


def _install_fake_dist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dist_name: str,
    top_levels: list[str],
    version: str = "1.0",
) -> None:
    """Create a fake installed distribution with importable top-level modules.

    Both the ``.dist-info`` metadata and the top-level ``.py`` modules live in
    ``tmp_path``, which is prepended to ``sys.path`` so ``importlib.metadata``
    and the real import system resolve them exactly like an installed package.
    """
    dist_info = tmp_path / f"{dist_name.replace('-', '_')}-{version}.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.4\nName: {dist_name}\nVersion: {version}\n",
        encoding="utf-8",
    )
    (dist_info / "top_level.txt").write_text(
        "\n".join(top_levels) + "\n", encoding="utf-8"
    )
    for module in top_levels:
        (tmp_path / f"{module}.py").write_text("PRESENT = True\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))


def test_preflight_dist_name_differs_from_import_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``python-dotenv`` is satisfied when the ``dotenv`` module is installed."""
    _install_fake_dist(tmp_path, monkeypatch, "python-dotenv", ["dotenv"])
    assert check_python_dep_installed("python-dotenv") is True


def test_preflight_direct_url_dep_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PEP 508 direct reference resolves via its distribution name."""
    _install_fake_dist(tmp_path, monkeypatch, "mygopkg", ["mygopkg"])
    assert (
        check_python_dep_installed("mygopkg @ git+https://example.com/mygopkg@main")
        is True
    )


def test_preflight_direct_url_dep_missing() -> None:
    """A direct reference with no installed distribution is still missing."""
    assert (
        check_python_dep_installed("mygopkg @ git+https://example.com/mygopkg@main")
        is False
    )


def test_preflight_version_pin_uses_dist_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``PyYAML>=6.0`` is satisfied by the installed ``pyyaml`` distribution."""
    _install_fake_dist(tmp_path, monkeypatch, "pyyaml", ["yaml"])
    assert check_python_dep_installed("PyYAML>=6.0") is True


def test_preflight_extras_do_not_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Extras are ignored for the presence check."""
    _install_fake_dist(tmp_path, monkeypatch, "python-dotenv", ["dotenv"])
    assert check_python_dep_installed("python-dotenv[cli]") is True


def test_preflight_inapplicable_marker_counts_as_satisfied() -> None:
    """A dependency whose marker excludes this interpreter is vacuous."""
    assert check_python_dep_installed("mygopkg; python_version < '2.0'") is True


def test_preflight_applicable_marker_still_gates() -> None:
    """A dependency whose marker applies is still checked normally."""
    assert check_python_dep_installed("mygopkg; python_version >= '3.0'") is False


def test_preflight_invalid_spec_falls_back_to_legacy() -> None:
    """A string that is not a valid requirement never crashes the check."""
    assert check_python_dep_installed("!!!not a requirement!!!") is False


def test_preflight_version_pin_not_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any installed distribution satisfies the dep; pins are not enforced."""
    _install_fake_dist(
        tmp_path, monkeypatch, "python-dotenv", ["dotenv"], version="1.0"
    )
    assert check_python_dep_installed("python-dotenv>=99.0") is True


def test_preflight_direct_url_dep_no_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An installed direct-reference dep produces no MISSING_DEP diagnostic."""
    _install_fake_dist(tmp_path, monkeypatch, "mygopkg", ["mygopkg"])
    dep = "mygopkg @ git+https://example.com/mygopkg@main"
    rune_dir = tmp_path / "url_dep"
    _write_rune(rune_dir, "def rune_factory(api):\n    pass\n", python_deps=[dep])
    diagnostics: list[Diagnostic] = []
    load_factory_from_manifest(
        RuneManifest(
            name="url_dep",
            version="1.0.0",
            description="",
            entry_point="rune.py",
            python_deps=[dep],
            scope=RuneScope.PROJECT,
        ),
        rune_dir,
        diagnostics=diagnostics,
    )
    assert not any(d.kind == DiagnosticKind.MISSING_DEP for d in diagnostics)
