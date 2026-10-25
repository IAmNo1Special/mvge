"""Read back the work a run completed, from the transcript it already wrote.

A Spell writes to disk the moment it executes, not when the turn ends. When an
upstream error raises after a turn of casts, the files are already on disk and
the run still exits non-zero. Nothing in the error path looked, so a Summoner
retrying without checking could overwrite or duplicate work they had already
done.

Everything needed to answer "what already landed" is already in the transcript:
the assistant turn records each ``spell_cast`` block with its arguments, and the
dispatcher appends the matching ``SpellResultMessage``. This module reads those
two halves and joins them, so reporting costs no new persistence.

Which paths count as *written* is the engine's call, not the spell's: a spell
declared ``read_only`` only observed its path, and claiming a Summoner might
lose a file they never touched would be worse than staying silent.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from mvge.core.channel import MvgeResponse
from mvge.core.events import ContentType
from mvge.core.invocations import MvgeInvocation
from mvge.core.spells import MvgeSpell, SpellResultMessage

#: Spell-cast argument names the engine reads as filesystem targets. ``path`` is
#: the MvgeOS convention; the rest are spellings seen in the wild. Anything else
#: -- a ``command`` string, say -- is left alone: the engine cannot say which
#: file a shell command touched, so it does not claim one.
_PATH_ARGUMENTS = frozenset({"path", "file_path", "filepath", "filename"})


@dataclass(frozen=True)
class LandedWork:
    """What a run completed before it stopped.

    ``mutated_paths`` is what the Summoner stands to lose by retrying blindly,
    deduplicated and in the order the transcript produced it.
    """

    spells_succeeded: int = 0
    spells_failed: int = 0
    mutated_paths: tuple[str, ...] = ()

    @property
    def spells_run(self) -> int:
        """Every spell the transcript shows a result for, succeeded or not."""
        return self.spells_succeeded + self.spells_failed


def _spell_casts(
    invocations: Sequence[MvgeInvocation],
) -> dict[str, dict[str, object]]:
    """Map every ``spell_cast`` id in the transcript to its recorded arguments."""
    casts: dict[str, dict[str, object]] = {}
    for invocation in invocations:
        if not isinstance(invocation, MvgeResponse):
            continue
        for item in invocation.content or []:
            if not isinstance(item, dict) or item.get("type") != ContentType.SPELL_CAST:
                continue
            cast = item.get("spell_cast")
            if not isinstance(cast, dict):
                continue
            cast_id = cast.get("id")
            if isinstance(cast_id, str) and cast_id:
                casts[cast_id] = cast
    return casts


def _written_paths(
    arguments: object,
    paths: list[str],
    seen: set[str],
) -> None:
    """Collect the filesystem targets of one spell cast, in argument order."""
    if not isinstance(arguments, dict):
        return
    for key, value in arguments.items():
        if key not in _PATH_ARGUMENTS or not isinstance(value, str):
            continue
        cleaned = value.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            paths.append(cleaned)


def landed_work(
    invocations: Sequence[MvgeInvocation],
    spells: Iterable[MvgeSpell] = (),
) -> LandedWork:
    """Summarise the work a run's transcript shows as already completed.

    ``spells`` is the spell table the run cast against, used to tell a spell
    that observed a path from one that changed it. A spell missing from the
    table is treated as mutating: over-reporting a path only costs the Summoner
    a glance before retrying, while under-reporting can lose their work.
    """
    casts = _spell_casts(invocations)
    read_only = {spell.name for spell in spells if spell.read_only}

    succeeded = 0
    failed = 0
    paths: list[str] = []
    seen: set[str] = set()

    for invocation in invocations:
        if not isinstance(invocation, SpellResultMessage):
            continue
        if invocation.is_error:
            failed += 1
            continue
        succeeded += 1
        if invocation.spell_name in read_only:
            continue
        cast = casts.get(invocation.spell_cast_id)
        _written_paths(cast.get("arguments") if cast else None, paths, seen)

    return LandedWork(
        spells_succeeded=succeeded,
        spells_failed=failed,
        mutated_paths=tuple(paths),
    )


__all__ = [
    "LandedWork",
    "landed_work",
]
