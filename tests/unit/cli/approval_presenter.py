"""Unit tests for the interactive CLI approval presenter.

Covers: automation modes, TTY detection, EOF / Ctrl-C fail-closed behavior,
the full option menu, persistent-grant second confirmations, inert rendering,
and closed-presenter denial.
"""

from __future__ import annotations

import asyncio
import io

import pytest

from mvge.cli.approval_presenter import CliApprovalPresenter
from mvge.cli.approval_types import (
    ApprovalDecision,
    ApprovalMode,
    ApprovalRequest,
)


class TtyStream(io.StringIO):
    """StringIO that reports itself as an interactive terminal."""

    def isatty(self) -> bool:  # noqa: D102
        return True


class ExplodingStream(io.StringIO):
    """Fails the test if the presenter tries to read from it."""

    def isatty(self) -> bool:  # noqa: D102
        return True

    def readline(self, *args: object) -> str:  # noqa: D102
        raise AssertionError("presenter must not prompt in this mode")


def _request(**overrides: object) -> ApprovalRequest:
    base: dict[str, object] = {
        "cast_id": "call_8b17",
        "spell_name": "write",
        "spell_identity": {
            "name": "write",
            "source_kind": "builtin",
            "source_id": "",
            "runner_origin": "false",
            "read_only": "false",
        },
        "arguments": {"path": "/workspace/mvgeos/notes.txt"},
        "argument_digest": "sha256:deadbeef",
        "project_root": "/workspace/mvgeos",
        "tome_id": "7f24c1",
        "agent_name": "coding_mvge",
    }
    base.update(overrides)
    return ApprovalRequest(**base)  # type: ignore[arg-type]


def _presenter(
    mode: ApprovalMode, script: str, tty: bool = True, explode_on_read: bool = False
) -> tuple[CliApprovalPresenter, io.StringIO]:
    if explode_on_read:
        stdin: io.StringIO = ExplodingStream(script)
    else:
        stdin = TtyStream(script) if tty else io.StringIO(script)
    stdout: io.StringIO = TtyStream() if tty else io.StringIO()
    return CliApprovalPresenter(mode=mode, stdin=stdin, stdout=stdout), stdout


def _decide(
    presenter: CliApprovalPresenter, request: ApprovalRequest
) -> ApprovalDecision:
    return asyncio.run(presenter.request_approval(request))


def test_deny_mode_denies_without_prompting() -> None:
    presenter, stdout = _presenter("deny", "1\n", explode_on_read=True)
    decision = _decide(presenter, _request())
    assert decision.outcome == "deny"
    assert decision.scope == "once"
    assert decision.reason_code == "user"
    assert decision.request_digest == "sha256:deadbeef"
    assert stdout.getvalue() == ""


def test_allow_all_mode_allows_once_without_prompting() -> None:
    presenter = CliApprovalPresenter(
        mode="allow-all", stdin=ExplodingStream(), stdout=TtyStream()
    )
    decision = _decide(presenter, _request())
    assert (decision.outcome, decision.scope) == ("allow", "once")
    assert decision.reason_code == "user"


def test_non_tty_denies_without_prompting() -> None:
    presenter, stdout = _presenter("prompt", "1\n", tty=False)
    decision = _decide(presenter, _request())
    assert decision.outcome == "deny"
    assert stdout.getvalue() == ""


def test_eof_denies() -> None:
    presenter, _ = _presenter("prompt", "")
    decision = _decide(presenter, _request())
    assert (decision.outcome, decision.scope) == ("deny", "once")


def test_keyboard_interrupt_denies_without_raising() -> None:
    class CtrlC(TtyStream):
        def readline(self, *args: object) -> str:
            raise KeyboardInterrupt

    presenter = CliApprovalPresenter(mode="prompt", stdin=CtrlC(), stdout=TtyStream())
    decision = _decide(presenter, _request())
    assert decision.outcome == "deny"


def test_allow_once_choice() -> None:
    presenter, _ = _presenter("prompt", "1\n")
    decision = _decide(presenter, _request())
    assert (decision.outcome, decision.scope) == ("allow", "once")


def test_deny_choice() -> None:
    presenter, _ = _presenter("prompt", "2\n")
    decision = _decide(presenter, _request())
    assert (decision.outcome, decision.scope) == ("deny", "once")


def test_always_allow_spell_needs_second_confirmation() -> None:
    presenter, stdout = _presenter("prompt", "3\nallow\n")
    decision = _decide(presenter, _request())
    assert (decision.outcome, decision.scope) == ("allow", "spell")
    assert "confirm" in stdout.getvalue().lower()


def test_always_allow_spell_wrong_word_returns_to_menu() -> None:
    presenter, stdout = _presenter("prompt", "3\nnope\n2\n")
    decision = _decide(presenter, _request())
    assert (decision.outcome, decision.scope) == ("deny", "once")
    assert "did not match" in stdout.getvalue()


def test_approve_all_session() -> None:
    presenter, stdout = _presenter("prompt", "4\n")
    decision = _decide(presenter, _request())
    assert (decision.outcome, decision.scope) == ("allow", "session")
    assert "session" in stdout.getvalue().lower()


def test_approve_all_project_needs_second_confirmation() -> None:
    presenter, _ = _presenter("prompt", "5\napprove\n")
    decision = _decide(presenter, _request())
    assert (decision.outcome, decision.scope) == ("allow", "project")


def test_more_options_always_deny_spell() -> None:
    presenter, _ = _presenter("prompt", "m\na\ndeny\n")
    decision = _decide(presenter, _request())
    assert (decision.outcome, decision.scope) == ("deny", "spell")


def test_more_options_back_returns_to_menu() -> None:
    presenter, _ = _presenter("prompt", "m\nb\n1\n")
    decision = _decide(presenter, _request())
    assert (decision.outcome, decision.scope) == ("allow", "once")


def test_closed_presenter_denies_even_in_allow_all() -> None:
    presenter, _ = _presenter("allow-all", "")
    presenter.close()
    decision = _decide(presenter, _request())
    assert decision.outcome == "deny"


def test_rendering_is_inert_plain_text() -> None:
    presenter, stdout = _presenter("prompt", "2\n")
    request = _request(arguments={"path": "[red]pwned[/red]"})
    _decide(presenter, request)
    output = stdout.getvalue()
    assert "[red]pwned[/red]" in output


def test_renders_engine_derived_spell_attribution() -> None:
    presenter, stdout = _presenter("prompt", "2\n")
    request = _request(
        spell_name="write",
        spell_identity={
            "name": "write",
            "source_kind": "rune",
            "source_id": "notes-rune",
            "runner_origin": "true",
            "read_only": "false",
        },
    )
    _decide(presenter, request)
    assert "write (rune: notes-rune)" in stdout.getvalue()


def test_engine_invokes_presenter_as_callable() -> None:
    # The engine's contract is ``await presenter(request)``.
    presenter, _ = _presenter("prompt", "1\n")
    decision = asyncio.run(presenter(_request()))
    assert (decision.outcome, decision.scope) == ("allow", "once")


def test_confirmation_states_scope_and_current_cast() -> None:
    presenter, stdout = _presenter("prompt", "3\nallow\n")
    _decide(presenter, _request())
    output = stdout.getvalue()
    assert "global (all projects)" in output
    assert "path: /workspace/mvgeos/notes.txt" in output


def test_confirmation_warns_on_unconstrained_mutating_spell() -> None:
    # read_only=false: "always allow" would authorize every future argument.
    presenter, stdout = _presenter("prompt", "3\nallow\n")
    _decide(presenter, _request())
    assert "every future argument" in stdout.getvalue().lower()


def test_confirmation_no_warning_for_read_only_spell() -> None:
    presenter, stdout = _presenter("prompt", "3\nallow\n")
    request = _request(
        spell_identity={
            "name": "read",
            "source_kind": "builtin",
            "source_id": "",
            "runner_origin": "false",
            "read_only": "true",
        },
    )
    _decide(presenter, request)
    assert "every future argument" not in stdout.getvalue().lower()


def test_repeated_invalid_input_still_allows_valid_decision() -> None:
    # Invalid entries never auto-deny: the prompt waits until the Summoner
    # decides or the run is cancelled.
    presenter, stdout = _presenter("prompt", "z\n" * 10 + "1\n")
    decision = _decide(presenter, _request())
    assert (decision.outcome, decision.scope) == ("allow", "once")
    assert "Unknown choice" in stdout.getvalue()


def test_invalid_input_in_more_options_still_decides() -> None:
    # Same guarantee inside the secondary menu: invalid entries re-prompt,
    # and a later valid path (always-deny + confirmation) still decides.
    presenter, _ = _presenter("prompt", "m\n" + "z\n" * 10 + "a\ndeny\n")
    decision = _decide(presenter, _request())
    assert (decision.outcome, decision.scope) == ("deny", "spell")


def test_invalid_mode_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="Unknown approval mode"):
        CliApprovalPresenter(mode="sometimes")  # type: ignore[arg-type]


def test_render_warns_on_unvalidated_arguments() -> None:
    presenter, stdout = _presenter("prompt", "2\n")
    _decide(presenter, _request(schema_validated=False))
    assert "unvalidated arguments" in stdout.getvalue()


def test_render_hides_warning_for_validated_cast() -> None:
    presenter, stdout = _presenter("prompt", "2\n")
    _decide(presenter, _request(schema_validated=True))
    assert "unvalidated arguments" not in stdout.getvalue()
