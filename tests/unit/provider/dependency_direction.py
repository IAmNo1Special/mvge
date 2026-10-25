"""Architecture guard: the provider layer must not depend on the agent layer.

mvgeos-provider sits above mvgeos-core and below mvgeos-agent in the
workspace dependency graph (provider declares core as a runtime dependency;
agent declares provider). Any import of ``mvge.agent`` from provider
source recreates a circular dependency that only resolves because the
monorepo installs everything editable into one environment.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_PROVIDER_SRC = Path(__file__).resolve().parents[3] / "src" / "mvge" / "provider"


def _provider_sources() -> list[Path]:
    return sorted(_PROVIDER_SRC.rglob("*.py"))


def test_provider_never_imports_agent() -> None:
    """No provider module may import mvge.agent, even under TYPE_CHECKING."""
    offenders = [
        src
        for src in _provider_sources()
        if "mvge.agent" in src.read_text(encoding="utf-8")
    ]
    assert offenders == [], (
        "provider modules import the agent layer (circular dependency): "
        f"{[str(o.relative_to(_PROVIDER_SRC)) for o in offenders]}"
    )


def test_provider_package_importable_without_agent_side_effects() -> None:
    """Importing provider entry modules must not pull in mvge.agent.

    Runs in a subprocess because sys.modules is process-global; other tests
    in the same session may have imported the agent layer already.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys\n"
                "import mvge.provider.base\n"
                "import mvge.provider.model_registry\n"
                "import mvge.provider.registry\n"
                "import mvge.provider.retry\n"
                "import mvge.provider.sse\n"
                "loaded = [m for m in sys.modules if "
                "m == 'mvge.agent' or m.startswith('mvge.agent.')]\n"
                "assert not loaded, "
                "f'provider imports pulled in mvge.agent: {loaded}'\n"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, (
        f"provider imports failed or pulled in agent: {result.stderr}"
    )
