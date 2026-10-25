"""Unit tests for reading landed work back off a run transcript.

The shape under test is the one the loop actually produces: an ``MvgeResponse``
carrying ``spell_cast`` blocks, followed by the ``SpellResultMessage`` each
dispatched cast produced. Both halves are emitted before the turn that fails,
so a transcript taken after the failure is a complete record of what landed.
"""

from __future__ import annotations

from typing import Any

from mvge.core.channel import MvgeResponse, StopReason
from mvge.core.spells import MvgeSpell, SpellResultMessage
from mvge.core.worklog import landed_work


def _spell(name: str, *, read_only: bool = False) -> MvgeSpell:
    return MvgeSpell(
        name=name,
        description=name,
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        read_only=read_only,
    )


def _cast_turn(casts: list[tuple[str, str, dict[str, Any]]]) -> MvgeResponse:
    """Build the assistant turn that requested a batch of spell casts."""
    return MvgeResponse(
        stop_reason=StopReason.SPELL_USE,
        content=[
            {
                "type": "spell_cast",
                "spell_cast": {
                    "id": cast_id,
                    "name": name,
                    "arguments": arguments,
                },
            }
            for cast_id, name, arguments in casts
        ],
    )


def _result(cast_id: str, name: str, *, is_error: bool = False) -> SpellResultMessage:
    return SpellResultMessage(
        spell_cast_id=cast_id,
        spell_name=name,
        content=[{"type": "text", "text": "Wrote to hello.txt"}],
        is_error=is_error,
    )


def test_empty_transcript_reports_no_work() -> None:
    work = landed_work([])

    assert work.spells_run == 0
    assert work.spells_succeeded == 0
    assert work.spells_failed == 0
    assert work.mutated_paths == ()


def test_successful_write_is_reported_as_landed() -> None:
    """The measured failure: the file is on disk and the run still exits 1."""
    transcript = [
        _cast_turn([("call_1", "write", {"path": "hello.txt", "content": "hi"})]),
        _result("call_1", "write"),
    ]

    work = landed_work(transcript, [_spell("write")])

    assert work.spells_succeeded == 1
    assert work.spells_failed == 0
    assert work.mutated_paths == ("hello.txt",)


def test_failed_spell_is_counted_but_claims_no_file() -> None:
    transcript = [
        _cast_turn([("call_1", "write", {"path": "hello.txt"})]),
        _result("call_1", "write", is_error=True),
    ]

    work = landed_work(transcript, [_spell("write")])

    assert work.spells_run == 1
    assert work.spells_failed == 1
    assert work.mutated_paths == ()


def test_read_only_spell_never_reports_a_written_path() -> None:
    """A path the run only observed is not work the user can lose."""
    transcript = [
        _cast_turn([("call_1", "read", {"path": "src/app.py"})]),
        _result("call_1", "read"),
    ]

    work = landed_work(transcript, [_spell("read", read_only=True)])

    assert work.spells_succeeded == 1
    assert work.mutated_paths == ()


def test_repeated_writes_to_one_path_are_listed_once_in_order() -> None:
    transcript = [
        _cast_turn(
            [
                ("call_1", "write", {"path": "b.txt"}),
                ("call_2", "write", {"path": "a.txt"}),
            ]
        ),
        _result("call_1", "write"),
        _result("call_2", "write"),
        _cast_turn([("call_3", "write", {"path": "b.txt"})]),
        _result("call_3", "write"),
    ]

    work = landed_work(transcript, [_spell("write")])

    assert work.spells_succeeded == 3
    assert work.mutated_paths == ("b.txt", "a.txt")


def test_non_path_arguments_are_not_reported_as_files() -> None:
    """``bash`` mutates through its command line; the engine cannot say which
    file that was, so it does not claim one."""
    transcript = [
        _cast_turn([("call_1", "bash", {"command": "rm -rf build"})]),
        _result("call_1", "bash"),
    ]

    work = landed_work(transcript, [_spell("bash")])

    assert work.spells_succeeded == 1
    assert work.mutated_paths == ()


def test_blank_and_non_string_paths_are_ignored() -> None:
    transcript = [
        _cast_turn([("call_1", "write", {"path": "   "})]),
        _cast_turn([("call_2", "write", {"path": 17})]),
    ]

    work = landed_work(transcript, [_spell("write")])

    assert work.spells_succeeded == 0
    assert work.mutated_paths == ()


def test_spell_result_without_a_recorded_cast_still_counts() -> None:
    """A spell result with no matching cast block still ran."""
    transcript = [_result("call_orphan", "write")]

    work = landed_work(transcript, [_spell("write")])

    assert work.spells_succeeded == 1
    assert work.mutated_paths == ()
