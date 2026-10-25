"""End-to-end proof: the engine's RuneRunner drives the CLI presenter.

Uses the real ``RuneRunner`` from ``mvge.runes`` and the real
``CliApprovalPresenter`` through the host binding adapter -- no slot
doubles. Proves the canonical contract holds: the engine invokes the
presenter as ``await presenter(request)``, honors the decision's digest
binding, and denies once the presenter is unbound.
"""

from __future__ import annotations

import asyncio
import io
from typing import Any

from mvge.cli.approval_binding import (
    bind_approval_presenter,
    unbind_approval_presenter,
)
from mvge.cli.approval_presenter import CliApprovalPresenter
from mvge.core.approval import (
    ApprovalOutcome,
    ApprovalReasonCode,
    ApprovalRequest,
    ApprovalScope,
    normalize_arguments,
)
from mvge.runes.rune_runner import RuneRunner


class TtyStream(io.StringIO):
    def isatty(self) -> bool:  # noqa: D102
        return True


def _request() -> ApprovalRequest:
    arguments, digest = normalize_arguments({"path": "/tmp/x.txt"})
    return ApprovalRequest(
        cast_id="call_8b17",
        spell_name="write",
        spell_identity={
            "name": "write",
            "source_kind": "builtin",
            "source_id": "",
            "runner_origin": "false",
            "read_only": "false",
        },
        arguments=arguments,
        argument_digest=digest,
        project_root="",
        tome_id="",
        agent_name="coding_mvge",
    )


def _presenter(script: str) -> tuple[CliApprovalPresenter, TtyStream]:
    stdout = TtyStream()
    presenter = CliApprovalPresenter(
        mode="prompt", stdin=TtyStream(script), stdout=stdout
    )
    return presenter, stdout


def _decide(runner: RuneRunner, request: ApprovalRequest) -> Any:
    return asyncio.run(runner.request_approval(request))


def test_engine_drives_cli_presenter_allow_once() -> None:
    runner = RuneRunner()
    presenter, _ = _presenter("1\n")
    assert bind_approval_presenter(runner, presenter) is True

    decision = _decide(runner, _request())

    assert decision.outcome is ApprovalOutcome.ALLOW
    assert decision.scope is ApprovalScope.ONCE
    assert decision.reason_code is ApprovalReasonCode.USER


def test_engine_honors_cli_presenter_deny() -> None:
    runner = RuneRunner()
    presenter, _ = _presenter("2\n")
    assert bind_approval_presenter(runner, presenter) is True

    decision = _decide(runner, _request())

    assert decision.outcome is ApprovalOutcome.DENY
    assert decision.reason_code is ApprovalReasonCode.USER


def test_engine_denies_after_presenter_unbound() -> None:
    runner = RuneRunner()
    presenter, _ = _presenter("1\n")
    assert bind_approval_presenter(runner, presenter) is True
    assert unbind_approval_presenter(runner, presenter) is True
    assert presenter.closed is True

    decision = _decide(runner, _request())

    # No presenter bound: the engine fails closed on its own.
    assert decision.outcome is ApprovalOutcome.DENY
    assert decision.reason_code is ApprovalReasonCode.FAILURE


def test_engine_denies_with_no_presenter_bound() -> None:
    runner = RuneRunner()
    decision = _decide(runner, _request())
    assert decision.outcome is ApprovalOutcome.DENY
