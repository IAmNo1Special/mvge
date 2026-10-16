"""Unit tests for the partial-work report printed on a non-zero exit.

A run that fails after spells have written leaves the Summoner's work on disk
and the process exit non-zero. These tests pin the two reports that must come
out of that path -- name the files, or say plainly that nothing ran -- and the
silence a clean run keeps.

The agent's ``run`` appends to its own transcript before raising, because that
is the order the engine produces: the harness reduces each ``MESSAGE_END`` into
the state as the turn goes, and only then does the next turn's upstream error
raise.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from mvgeos_agent.harness._state import MvgeState
from mvgeos_core.channel import MvgeResponse, StopReason
from mvgeos_core.spells import MvgeSpell, SpellResultMessage

from mvgeos_cli.main import _run_print_mode

_SPELL_PARAMETERS = {
    "type": "object",
    "properties": {"path": {"type": "string"}},
    "required": ["path"],
}


def _cast(cast_id: str, name: str, arguments: dict[str, Any]) -> MvgeResponse:
    """The assistant turn that requested one spell cast."""
    return MvgeResponse(
        stop_reason=StopReason.SPELL_USE,
        content=[
            {
                "type": "spell_cast",
                "spell_cast": {"id": cast_id, "name": name, "arguments": arguments},
            }
        ],
    )


def _succeeded(cast_id: str, name: str, text: str) -> SpellResultMessage:
    return SpellResultMessage(
        spell_cast_id=cast_id,
        spell_name=name,
        content=[{"type": "text", "text": text}],
    )


def _wrote(path: str, index: int = 0) -> list[Any]:
    """One ``write`` cast that landed, as the transcript records it."""
    cast_id = f"call_{index}"
    return [
        _cast(cast_id, "write", {"path": path, "content": "hi"}),
        _succeeded(cast_id, "write", f"Wrote to {path}"),
    ]


def _agent(
    this_turn: list[Any],
    *,
    spell_names: list[str] = (),
    prior: list[Any] | None = None,
) -> MagicMock:
    """An agent whose failing turn appends ``this_turn`` to its transcript.

    ``prior`` stands in for a resumed Tome, whose casts are already in the
    transcript before this run starts.
    """
    agent = MagicMock()
    agent._state = MvgeState(
        invocations=list(prior or []),
        spells=[
            MvgeSpell(
                name=name,
                description=name,
                parameters=_SPELL_PARAMETERS,
                read_only=name == "read",
            )
            for name in spell_names
        ],
    )

    async def _run(_prompt: str) -> Any:
        agent._state.invocations.extend(this_turn)
        raise RuntimeError("provider overloaded")

    agent.run = AsyncMock(side_effect=_run)
    return agent


@pytest.mark.asyncio
async def test_failure_after_writes_names_the_files_already_on_disk(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The measured failure: hello.txt is on disk and the run still exits 1."""
    agent = _agent(_wrote("hello.txt"), spell_names=["write"])

    code = await _run_print_mode(agent, ["write hello.txt"])

    captured = capsys.readouterr()
    assert code == 1
    assert "hello.txt" in captured.out
    assert "already on disk" in captured.out


@pytest.mark.asyncio
async def test_failure_names_every_file_the_turn_wrote(
    capsys: pytest.CaptureFixture[str],
) -> None:
    agent = _agent(
        [*_wrote("hello.txt"), *_wrote("src/app.py", index=1)],
        spell_names=["write"],
    )

    await _run_print_mode(agent, ["write two files"])

    captured = capsys.readouterr()
    assert "hello.txt" in captured.out
    assert "src/app.py" in captured.out
    assert "2 spells completed" in captured.out


@pytest.mark.asyncio
async def test_failure_before_any_spell_says_nothing_was_written(
    capsys: pytest.CaptureFixture[str],
) -> None:
    agent = _agent([], spell_names=["write"])

    code = await _run_print_mode(agent, ["do the thing"])

    captured = capsys.readouterr()
    assert code == 1
    assert "No spells ran" in captured.out
    assert "nothing was written" in captured.out


@pytest.mark.asyncio
async def test_read_only_casts_are_not_reported_as_written_work(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The run only read a file. Claiming it as lost work would be a lie."""
    agent = _agent(
        [
            _cast("call_0", "read", {"path": "src/app.py"}),
            _succeeded("call_0", "read", "contents"),
        ],
        spell_names=["read"],
    )

    await _run_print_mode(agent, ["read the file"])

    captured = capsys.readouterr()
    assert "src/app.py" not in captured.out
    assert "None of them wrote a file" in captured.out


@pytest.mark.asyncio
async def test_failed_spell_is_reported_separately_from_landed_work(
    capsys: pytest.CaptureFixture[str],
) -> None:
    failed = SpellResultMessage(
        spell_cast_id="call_1",
        spell_name="write",
        content=[{"type": "text", "text": "permission denied"}],
        is_error=True,
    )
    agent = _agent([*_wrote("hello.txt"), failed], spell_names=["write"])

    await _run_print_mode(agent, ["write two files"])

    captured = capsys.readouterr()
    assert "1 spell completed" in captured.out
    assert "1 spell failed" in captured.out
    assert "hello.txt" in captured.out


@pytest.mark.asyncio
async def test_shell_command_claims_no_file_the_engine_cannot_name(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``bash`` mutates through its command line; the engine cannot say which
    file that was, so it does not claim one."""
    agent = _agent(
        [
            _cast("call_0", "bash", {"command": "rm -rf build"}),
            _succeeded("call_0", "bash", "done"),
        ],
        spell_names=["bash"],
    )

    await _run_print_mode(agent, ["clean up"])

    captured = capsys.readouterr()
    assert "build" not in captured.out
    assert "None of them wrote a file" in captured.out


@pytest.mark.asyncio
async def test_success_prints_no_work_report(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A clean run must not print a summary the Summoner did not ask for."""
    agent = MagicMock()
    agent.run = AsyncMock(
        return_value=MvgeResponse(
            stop_reason=StopReason.STOP,
            content=[{"type": "text", "text": "Wrote hello.txt"}],
        )
    )

    code = await _run_print_mode(agent, ["write hello.txt"])

    captured = capsys.readouterr()
    assert code == 0
    assert "Wrote hello.txt" in captured.out
    for marker in (
        "already on disk",
        "already done",
        "No spells ran",
        "completed",
        "Check these before retrying",
    ):
        assert marker not in captured.out


@pytest.mark.asyncio
async def test_agent_without_a_transcript_reports_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A custom agent factory may return an agent with no readable transcript.

    That is a missing accessor, not a crash: the error still prints, the report
    is skipped.
    """
    agent = MagicMock(spec=["run"])
    agent.run = AsyncMock(side_effect=RuntimeError("provider overloaded"))

    code = await _run_print_mode(agent, ["do the thing"])

    captured = capsys.readouterr()
    assert code == 1
    assert "Error: provider overloaded" in captured.out
    assert "No spells ran" not in captured.out


@pytest.mark.asyncio
async def test_resumed_session_work_is_not_reported_as_this_turn_s(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--resume`` replays the prior Tome's casts into the transcript.

    Reporting those as work the failing turn just completed would tell the
    Summoner to check files an earlier successful run already covered.
    """
    agent = _agent([], spell_names=["write"], prior=_wrote("from_last_session.txt"))

    code = await _run_print_mode(agent, ["do the thing"])

    captured = capsys.readouterr()
    assert code == 1
    assert "No spells ran" in captured.out
    assert "from_last_session.txt" not in captured.out
