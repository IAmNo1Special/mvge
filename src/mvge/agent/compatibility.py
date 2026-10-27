from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from mvge.runes.types import (
    Diagnostic,
    DiagnosticKind,
)
from mvge.tome.types import TomeEntry, TomeMetadata


@dataclass
class SessionCompatibilityReport:
    """Diagnostic report describing compatibility with runtime config."""

    compatible: bool
    tome_id: str
    diagnostics: list[Diagnostic] = field(default_factory=list)
    model_mismatch: tuple[str, str] | None = None
    contemplation_mismatch: tuple[str, str] | None = None
    missing_spells: list[str] = field(default_factory=list)
    extra_spells: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.compatible


def validate_session_compatibility(
    metadata: TomeMetadata,
    *,
    expected_model: str | None = None,
    expected_contemplation: str | None = None,
    expected_spells: Sequence[str] | None = None,
    entries: Sequence[TomeEntry] | None = None,
) -> SessionCompatibilityReport:
    """Validate whether session metadata matches the active runtime configuration.

    Generates structured Diagnostic instances for mismatches.
    """
    diagnostics: list[Diagnostic] = []
    tome_id = metadata.id

    # 1. Model Compatibility
    recorded_model = metadata.model
    if recorded_model is None and entries:
        for entry in entries:
            m = entry.payload.get("model")
            if isinstance(m, str) and m:
                recorded_model = m
                break

    model_mismatch: tuple[str, str] | None = None
    if (
        recorded_model is not None
        and expected_model is not None
        and recorded_model != expected_model
    ):
        model_mismatch = (recorded_model, expected_model)
        diagnostics.append(
            Diagnostic(
                kind=DiagnosticKind.MODEL_MISMATCH,
                rune_name="session",
                message=(
                    f"Session recorded model '{recorded_model}' differs from "
                    f"active model '{expected_model}'"
                ),
            )
        )

    # 2. Contemplation Level Compatibility
    recorded_contemplation = metadata.contemplation_level
    if recorded_contemplation is None and entries:
        for entry in entries:
            c = entry.payload.get("contemplationLevel") or entry.payload.get(
                "contemplation_level"
            )
            if isinstance(c, str) and c:
                recorded_contemplation = c
                break

    contemplation_mismatch: tuple[str, str] | None = None
    if (
        recorded_contemplation is not None
        and expected_contemplation is not None
        and recorded_contemplation != expected_contemplation
    ):
        contemplation_mismatch = (recorded_contemplation, expected_contemplation)
        diagnostics.append(
            Diagnostic(
                kind=DiagnosticKind.CONTEMPLATION_MISMATCH,
                rune_name="session",
                message=(
                    f"Session recorded contemplation level '{recorded_contemplation}' "
                    f"differs from active '{expected_contemplation}'"
                ),
            )
        )

    # 3. Spells Compatibility
    recorded_spells = list(metadata.spells)
    if not recorded_spells and entries:
        discovered_spells: set[str] = set()
        for entry in entries:
            spell_name = entry.payload.get("spell_name") or entry.payload.get("name")
            if isinstance(spell_name, str) and spell_name:
                discovered_spells.add(spell_name)
            content = entry.payload.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "spell_cast":
                        sc = block.get("spell_cast", {})
                        sc_name = sc.get("name")
                        if isinstance(sc_name, str) and sc_name:
                            discovered_spells.add(sc_name)
            tool_calls = entry.payload.get("tool_calls")
            if isinstance(tool_calls, list):
                for tc in tool_calls:
                    if isinstance(tc, dict) and "function" in tc:
                        fn_name = tc["function"].get("name")
                        if isinstance(fn_name, str) and fn_name:
                            discovered_spells.add(fn_name)
        recorded_spells = list(discovered_spells)

    missing_spells: list[str] = []
    extra_spells: list[str] = []
    if expected_spells is not None and recorded_spells:
        active_set = set(expected_spells)
        session_set = set(recorded_spells)
        missing_spells = [s for s in recorded_spells if s not in active_set]
        extra_spells = [s for s in expected_spells if s not in session_set]

        if missing_spells:
            diagnostics.append(
                Diagnostic(
                    kind=DiagnosticKind.MISSING_SPELL,
                    rune_name="session",
                    message=(
                        "Active configuration is missing required spells "
                        f"recorded in session: {', '.join(missing_spells)}"
                    ),
                )
            )

    compatible = (
        model_mismatch is None and contemplation_mismatch is None and not missing_spells
    )

    if not compatible:
        diagnostics.insert(
            0,
            Diagnostic(
                kind=DiagnosticKind.INCOMPATIBLE_SESSION,
                rune_name="session",
                message=(
                    f"Session '{tome_id}' is incompatible with current configuration"
                ),
            ),
        )

    return SessionCompatibilityReport(
        compatible=compatible,
        tome_id=tome_id,
        diagnostics=diagnostics,
        model_mismatch=model_mismatch,
        contemplation_mismatch=contemplation_mismatch,
        missing_spells=missing_spells,
        extra_spells=extra_spells,
    )


__all__ = [
    "SessionCompatibilityReport",
    "validate_session_compatibility",
]
