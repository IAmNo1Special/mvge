"""Architecture guard: mvgeos-core has zero first-party dependencies.

Core owns the canonical loop vocabulary. Any import of ``mvgeos_agent``,
``mvgeos_provider``, ``mvgeos_tome``, ``mvgeos_runes``, ``mvgeos_cli``,
``mvgeos_gui``, or ``coding_mvge`` from core source recreates the coupling
the core package exists to eliminate. Leaf packages depend on core, never
the reverse.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_CORE_SRC = Path(__file__).resolve().parents[2] / "src" / "mvgeos_core"

_FIRST_PARTY = (
    "mvgeos_agent",
    "mvgeos_provider",
    "mvgeos_tome",
    "mvgeos_runes",
    "mvgeos_cli",
    "mvgeos_gui",
    "coding_mvge",
)


def _core_sources() -> list[Path]:
    return sorted(_CORE_SRC.rglob("*.py"))


def test_core_never_imports_first_party() -> None:
    """No core module may import first-party packages, even under TYPE_CHECKING."""
    offenders = [
        src
        for src in _core_sources()
        if any(dep in src.read_text(encoding="utf-8") for dep in _FIRST_PARTY)
    ]
    assert offenders == [], (
        "core modules import first-party packages (circular dependency): "
        f"{[str(o.relative_to(_CORE_SRC)) for o in offenders]}"
    )


def test_core_package_importable_without_leaf_side_effects() -> None:
    """Importing core entry modules must not pull in leaf packages.

    Runs in a subprocess because sys.modules is process-global; other tests
    in the same session may have imported the leaf layers already.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys\n"
                "import mvgeos_core\n"
                "import mvgeos_core.abort\n"
                "import mvgeos_core.channel\n"
                "import mvgeos_core.constants\n"
                "import mvgeos_core.dispatcher\n"
                "import mvgeos_core.errors\n"
                "import mvgeos_core.event_bus\n"
                "import mvgeos_core.events\n"
                "import mvgeos_core.invocations\n"
                "import mvgeos_core.loop\n"
                "import mvgeos_core.sandbox\n"
                "import mvgeos_core.spell_schema\n"
                "import mvgeos_core.spells\n"
                "import mvgeos_core.truncate\n"
                "first_party = ('mvgeos_agent', 'mvgeos_provider', 'mvgeos_tome',\n"
                "    'mvgeos_runes', 'mvgeos_cli', 'mvgeos_gui', 'coding_mvge')\n"
                "loaded = [m for m in sys.modules if m.split('.')[0] in first_party]\n"
                "assert not loaded, f'core imports pulled in leaf packages: {loaded}'\n"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, (
        f"core imports failed or pulled in leaf packages: {result.stderr}"
    )
