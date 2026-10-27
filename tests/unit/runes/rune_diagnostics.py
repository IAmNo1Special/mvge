import asyncio
from pathlib import Path

import pytest

from mvge.core.spells import ExecutionMode
from mvge.runes.loader import load_factory_from_manifest
from mvge.runes.manifest import load_manifest
from mvge.runes.rune_runner import _safe_call_handler_async
from mvge.runes.types import (
    RuneManifest,
    SigilHook,
)


@pytest.mark.asyncio
async def test_safe_call_handler_async_re_raises_cancelled_error() -> None:
    """Verify that asyncio.CancelledError is re-raised and never swallowed."""

    async def cancelling_handler(_data: dict) -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _safe_call_handler_async(cancelling_handler, SigilHook.AGENT_START, {})


@pytest.mark.asyncio
async def test_safe_call_handler_async_records_diagnostics() -> None:
    """Verify that handler exceptions record stack traces in diagnostics."""

    def failing_handler(_data: dict) -> None:
        raise RuntimeError("Sigil crash")

    diagnostics = []
    with pytest.raises(RuntimeError, match="Sigil crash"):
        await _safe_call_handler_async(
            failing_handler, SigilHook.AGENT_START, {}, diagnostics
        )
    assert len(diagnostics) == 1
    assert "Sigil crash" in diagnostics[0].message
    assert "Traceback" in diagnostics[0].message


def test_load_manifest_execution_mode_fallback(tmp_path: Path) -> None:
    """Verify that invalid execution_mode strings fallback to PARALLEL."""
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(
        '{"name": "test_rune", "version": "1.0.0", "execution_mode": "invalid_mode"}',
        encoding="utf-8",
    )

    manifest = load_manifest(tmp_path)
    assert manifest is not None
    assert manifest.execution_mode == ExecutionMode.PARALLEL


def test_load_factory_from_manifest_signature_validation(tmp_path: Path) -> None:
    """Verify loading factory checks function callability and param signature."""
    entry_file = tmp_path / "rune.py"
    entry_file.write_text("def rune_factory(): pass", encoding="utf-8")

    manifest = RuneManifest(
        name="bad_sig_rune",
        version="1.0.0",
        description="",
        entry_point="rune.py",
    )

    diagnostics = []
    factory = load_factory_from_manifest(manifest, tmp_path, diagnostics)
    assert factory is None
    assert len(diagnostics) == 1
    assert "expects at least 1 argument" in diagnostics[0].message
