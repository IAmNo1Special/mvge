"""Phase 5a live-backend tests: real bwrap confinement, tmp_path only.

Skipped wherever bubblewrap is not installed: bwrap is a
platform tool, absent on non-Linux runners and minimal
containers, and no in-repo fake can prove mount-namespace
confinement. Installing bubblewrap removes the skip. The
fail-closed path (no backend at all) is covered
deterministically in the unit tier and needs no skip.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from mvge.runes.subprocess_policy import policy_for_spell, run_sandboxed
from mvge.runes.types import SpellDefinition

pytestmark = pytest.mark.skipif(
    shutil.which("bwrap") is None,
    reason=(
        "bwrap is not installed on this runner; the live test "
        "needs a real bubblewrap binary. Install bubblewrap to "
        "run it."
    ),
)


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "data").mkdir(parents=True)
    return ws


def test_bwrap_denies_outside_roots_and_allows_inside(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    policy = policy_for_spell(
        SpellDefinition(
            name="writer",
            description="live bwrap writer",
            write_roots=("data",),
        ),
        ws,
        declared_capabilities=["spell", "filesystem"],
    )
    allowed = ws / "data" / "ok.txt"
    denied = ws / "no.txt"
    ok = run_sandboxed(
        [sys.executable, "-c", f"open({str(allowed)!r}, 'w').write('ok')"],
        policy,
    )
    assert ok.enforcement == "full"
    assert ok.returncode == 0
    assert allowed.read_text(encoding="utf-8") == "ok"
    bad = run_sandboxed(
        [sys.executable, "-c", f"open({str(denied)!r}, 'w').write('no')"],
        policy,
    )
    assert bad.enforcement == "full"
    assert bad.returncode != 0
    # The traceback proves the child actually ran and the
    # filesystem denied it: a bwrap startup failure must fail
    # this test, never pass it vacuously.
    assert "Traceback" in bad.stderr
    assert not denied.exists()


def test_bwrap_read_only_spell_cannot_write(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    policy = policy_for_spell(
        SpellDefinition(
            name="reader",
            description="live bwrap read-only spell",
            read_only=True,
            write_roots=("data",),
        ),
        ws,
        declared_capabilities=["spell", "filesystem"],
    )
    target = ws / "data" / "no.txt"
    result = run_sandboxed(
        [sys.executable, "-c", f"open({str(target)!r}, 'w').write('no')"],
        policy,
    )
    assert result.enforcement == "full"
    assert result.returncode != 0
    # As above: the traceback proves the child ran and was
    # denied, so a bwrap startup failure cannot pass vacuously.
    assert "Traceback" in result.stderr
    assert not target.exists()
