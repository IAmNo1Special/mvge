"""Unit tests for session compatibility validation."""

from __future__ import annotations

from mvge.agent.compatibility import validate_session_compatibility
from mvge.runes.types import DiagnosticKind
from mvge.tome.types import TomeEntry, TomeEntryType, TomeMetadata


def _make_meta(
    tome_id: str = "tome-1",
    model: str | None = "openrouter/auto",
    contemplation: str | None = "medium",
    spells: tuple[str, ...] = ("read_file", "write_file"),
) -> TomeMetadata:
    return TomeMetadata(
        id=tome_id,
        created_at="2026-01-01T00:00:00Z",
        cwd="/workspace",
        model=model,
        contemplation_level=contemplation,
        spells=list(spells),
    )


def test_compatibility_all_match() -> None:
    """When session matches active configuration, report is compatible."""
    meta = _make_meta()
    report = validate_session_compatibility(
        meta,
        expected_model="openrouter/auto",
        expected_contemplation="medium",
        expected_spells=["read_file", "write_file"],
    )
    assert report.compatible is True
    assert bool(report) is True
    assert len(report.diagnostics) == 0
    assert report.model_mismatch is None
    assert report.contemplation_mismatch is None
    assert report.missing_spells == []


def test_compatibility_model_mismatch() -> None:
    """Model mismatch should yield DiagnosticKind.MODEL_MISMATCH."""
    meta = _make_meta(model="openai/gpt-4o")
    report = validate_session_compatibility(
        meta,
        expected_model="anthropic/claude-3.5-sonnet",
    )
    assert report.compatible is False
    assert bool(report) is False
    assert report.model_mismatch == ("openai/gpt-4o", "anthropic/claude-3.5-sonnet")
    kinds = [d.kind for d in report.diagnostics]
    assert DiagnosticKind.MODEL_MISMATCH in kinds
    assert DiagnosticKind.INCOMPATIBLE_SESSION in kinds


def test_compatibility_model_fallback_from_entries() -> None:
    """If metadata has no model, extract model from entries payload."""
    meta = _make_meta(model=None)
    entries = [
        TomeEntry(
            id="e1",
            parent_id=None,
            type=TomeEntryType.MESSAGE,
            timestamp=1000.0,
            payload={"model": "mistral/large"},
        )
    ]
    report = validate_session_compatibility(
        meta,
        expected_model="anthropic/claude-3.5-sonnet",
        entries=entries,
    )
    assert report.compatible is False
    assert report.model_mismatch == ("mistral/large", "anthropic/claude-3.5-sonnet")


def test_compatibility_contemplation_mismatch() -> None:
    """Contemplation mismatch yields DiagnosticKind.CONTEMPLATION_MISMATCH."""
    meta = _make_meta(contemplation="high")
    report = validate_session_compatibility(
        meta,
        expected_contemplation="low",
    )
    assert report.compatible is False
    assert report.contemplation_mismatch == ("high", "low")
    kinds = [d.kind for d in report.diagnostics]
    assert DiagnosticKind.CONTEMPLATION_MISMATCH in kinds


def test_compatibility_contemplation_fallback_from_entries() -> None:
    """If metadata has no contemplation, extract from entries payload."""
    meta = _make_meta(contemplation=None)
    entries1 = [
        TomeEntry(
            id="e1",
            parent_id=None,
            type=TomeEntryType.MESSAGE,
            timestamp=1000.0,
            payload={"contemplationLevel": "high"},
        )
    ]
    report1 = validate_session_compatibility(
        meta,
        expected_contemplation="high",
        entries=entries1,
    )
    assert report1.compatible is True

    entries2 = [
        TomeEntry(
            id="e2",
            parent_id=None,
            type=TomeEntryType.MESSAGE,
            timestamp=1000.0,
            payload={"contemplation_level": "medium"},
        )
    ]
    report2 = validate_session_compatibility(
        meta,
        expected_contemplation="high",
        entries=entries2,
    )
    assert report2.compatible is False
    assert report2.contemplation_mismatch == ("medium", "high")


def test_compatibility_missing_and_extra_spells() -> None:
    """Session missing spells makes session incompatible, extra spells are fine."""
    meta = _make_meta(spells=("read_file", "bash", "deploy"))
    report = validate_session_compatibility(
        meta,
        expected_spells=["read_file", "write_file"],
    )
    assert report.compatible is False
    assert set(report.missing_spells) == {"bash", "deploy"}
    assert report.extra_spells == ["write_file"]
    kinds = [d.kind for d in report.diagnostics]
    assert DiagnosticKind.MISSING_SPELL in kinds


def test_compatibility_spells_fallback_from_entries() -> None:
    """If metadata has no spells, discover spells from various entry formats."""
    meta = _make_meta(spells=())
    entries = [
        TomeEntry(
            id="e1",
            parent_id=None,
            type=TomeEntryType.MESSAGE,
            timestamp=1000.0,
            payload={"spell_name": "spell_one"},
        ),
        TomeEntry(
            id="e2",
            parent_id=None,
            type=TomeEntryType.MESSAGE,
            timestamp=1000.0,
            payload={"name": "spell_two"},
        ),
        TomeEntry(
            id="e3",
            parent_id=None,
            type=TomeEntryType.MESSAGE,
            timestamp=1000.0,
            payload={
                "content": [
                    {
                        "type": "spell_cast",
                        "spell_cast": {"name": "spell_three"},
                    }
                ]
            },
        ),
        TomeEntry(
            id="e4",
            parent_id=None,
            type=TomeEntryType.MESSAGE,
            timestamp=1000.0,
            payload={
                "tool_calls": [
                    {
                        "function": {"name": "spell_four"},
                    }
                ]
            },
        ),
    ]
    report = validate_session_compatibility(
        meta,
        expected_spells=["spell_one", "spell_two", "spell_three", "spell_four"],
        entries=entries,
    )
    assert report.compatible is True
    assert report.missing_spells == []
