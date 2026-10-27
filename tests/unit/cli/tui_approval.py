"""Unit tests for TUI approval-mode binding.

The TUI binds the same host presenter slot as the REPL, but only for
modes that never prompt: allow-all and deny return immediately without
terminal I/O, so they cannot fight the full-screen app. Prompt mode
stays unbound, and the Approval Rune gate then denies, exactly as the
CLI note says. The rune's policy still runs first in all cases; these
tests only assert the host binding, not rune policy.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mvge.cli.approval_presenter import CliApprovalPresenter
from mvge.cli.approval_types import ApprovalRequest
from mvge.cli.commands.tui import run_tui


class _Runner:
    """Minimal engine runner approval slot."""

    def __init__(self) -> None:
        self.presenter: object | None = None

    def set_approval_presenter(self, presenter: object) -> None:
        self.presenter = presenter

    def clear_approval_presenter(self) -> None:
        self.presenter = None


def _request() -> ApprovalRequest:
    return ApprovalRequest(  # type: ignore[arg-type]
        cast_id="call_8b17",
        spell_name="write",
        spell_identity={
            "name": "write",
            "source_kind": "builtin",
            "source_id": "",
            "runner_origin": "false",
            "read_only": "false",
        },
        arguments={"path": "/workspace/mvge/notes.txt"},
        argument_digest="sha256:deadbeef",
        project_root="/workspace/mvge",
        tome_id="7f24c1",
        agent_name="coding_mvge",
    )


async def _decide(presenter: CliApprovalPresenter) -> str:
    decision = await presenter.request_approval(_request())
    return str(decision.outcome)


@pytest.mark.asyncio
@patch("mvge.cli.commands.tui._create_agent")
@patch("mvge.cli.commands.tui.TuiApp")
async def test_run_tui_allow_all_presenter_is_bound_during_run(
    mock_tui_app_cls: MagicMock,
    mock_create_agent: MagicMock,
) -> None:
    runner = _Runner()
    mock_agent = AsyncMock()
    mock_agent.on = MagicMock(return_value=lambda: None)
    mock_agent.environment = MagicMock(diagnostics=[])
    mock_agent.runner = runner
    mock_create_agent.return_value = mock_agent

    seen: list[object | None] = []
    outcomes: list[str] = []

    async def _run() -> None:
        seen.append(runner.presenter)
        if isinstance(runner.presenter, CliApprovalPresenter):
            outcomes.append(await _decide(runner.presenter))

    mock_app = MagicMock()
    mock_app.run = AsyncMock(side_effect=_run)
    mock_tui_app_cls.return_value = mock_app

    await run_tui(api_key="sk-or-test-key", approval_mode="allow-all")

    assert len(seen) == 1
    assert isinstance(seen[0], CliApprovalPresenter)
    assert outcomes == ["allow"]
    assert runner.presenter is None


@pytest.mark.asyncio
@patch("mvge.cli.commands.tui._create_agent")
@patch("mvge.cli.commands.tui.TuiApp")
async def test_run_tui_deny_binds_presenter_during_run(
    mock_tui_app_cls: MagicMock,
    mock_create_agent: MagicMock,
) -> None:
    runner = _Runner()
    mock_agent = AsyncMock()
    mock_agent.on = MagicMock(return_value=lambda: None)
    mock_agent.environment = MagicMock(diagnostics=[])
    mock_agent.runner = runner
    mock_create_agent.return_value = mock_agent

    seen: list[object | None] = []
    outcomes: list[str] = []

    async def _run() -> None:
        seen.append(runner.presenter)
        if isinstance(runner.presenter, CliApprovalPresenter):
            outcomes.append(await _decide(runner.presenter))

    mock_app = MagicMock()
    mock_app.run = AsyncMock(side_effect=_run)
    mock_tui_app_cls.return_value = mock_app

    await run_tui(api_key="sk-or-test-key", approval_mode="deny")

    assert isinstance(seen[0], CliApprovalPresenter)
    assert outcomes == ["deny"]
    assert runner.presenter is None


@pytest.mark.asyncio
@patch("mvge.cli.commands.tui._create_agent")
@patch("mvge.cli.commands.tui.TuiApp")
async def test_run_tui_prompt_does_not_bind(
    mock_tui_app_cls: MagicMock,
    mock_create_agent: MagicMock,
) -> None:
    runner = _Runner()
    mock_agent = AsyncMock()
    mock_agent.on = MagicMock(return_value=lambda: None)
    mock_agent.environment = MagicMock(diagnostics=[])
    mock_agent.runner = runner
    mock_create_agent.return_value = mock_agent

    seen: list[object | None] = []

    async def _run() -> None:
        seen.append(runner.presenter)

    mock_app = MagicMock()
    mock_app.run = AsyncMock(side_effect=_run)
    mock_tui_app_cls.return_value = mock_app

    await run_tui(api_key="sk-or-test-key")
    assert seen == [None]
    await run_tui(api_key="sk-or-test-key", approval_mode="prompt")
    assert seen == [None, None]


@pytest.mark.asyncio
@patch("mvge.cli.commands.tui._create_agent")
@patch("mvge.cli.commands.tui.TuiApp")
@patch("mvge.cli.commands.tui.CliApprovalPresenter")
async def test_run_tui_no_slot_warns_and_closes_presenter(
    mock_presenter_cls: MagicMock,
    mock_tui_app_cls: MagicMock,
    mock_create_agent: MagicMock,
) -> None:
    presenter = CliApprovalPresenter(mode="allow-all")
    mock_presenter_cls.return_value = presenter
    mock_agent = AsyncMock()
    mock_agent.on = MagicMock(return_value=lambda: None)
    mock_agent.environment = MagicMock(diagnostics=[])
    mock_agent.runner = None
    mock_create_agent.return_value = mock_agent

    mock_app = MagicMock()
    mock_app.run = AsyncMock()
    mock_tui_app_cls.return_value = mock_app

    await run_tui(api_key="sk-or-test-key", approval_mode="allow-all")

    assert presenter.closed
    warned = " ".join(str(call.args[0]) for call in mock_app._out.call_args_list)
    assert "approval presenter" in warned.lower()


@pytest.mark.asyncio
@patch("mvge.cli.commands.tui._create_agent")
@patch("mvge.cli.commands.tui.TuiApp")
async def test_run_tui_run_failure_unbinds_and_closes(
    mock_tui_app_cls: MagicMock,
    mock_create_agent: MagicMock,
) -> None:
    runner = _Runner()
    mock_agent = AsyncMock()
    mock_agent.on = MagicMock(return_value=lambda: None)
    mock_agent.environment = MagicMock(diagnostics=[])
    mock_agent.runner = runner
    mock_create_agent.return_value = mock_agent

    mock_app = MagicMock()
    mock_app.run = AsyncMock(side_effect=RuntimeError("boom"))
    mock_tui_app_cls.return_value = mock_app

    with pytest.raises(RuntimeError, match="boom"):
        await run_tui(api_key="sk-or-test-key", approval_mode="allow-all")

    assert runner.presenter is None
    mock_agent.close.assert_awaited_once()


@pytest.mark.asyncio
@patch("mvge.cli.commands.tui._create_agent")
@patch("mvge.cli.commands.tui.TuiApp")
async def test_run_tui_subscription_failure_unbinds_and_closes(
    mock_tui_app_cls: MagicMock,
    mock_create_agent: MagicMock,
) -> None:
    runner = _Runner()
    mock_agent = AsyncMock()
    calls = {"n": 0}

    def _on(*_args: object) -> object:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("subscribe boom")
        return lambda: None

    mock_agent.on = MagicMock(side_effect=_on)
    mock_agent.environment = MagicMock(diagnostics=[])
    mock_agent.runner = runner
    mock_create_agent.return_value = mock_agent

    mock_app = MagicMock()
    mock_app.run = AsyncMock()
    mock_tui_app_cls.return_value = mock_app

    with pytest.raises(RuntimeError, match="subscribe boom"):
        await run_tui(api_key="sk-or-test-key", approval_mode="allow-all")

    assert runner.presenter is None
    mock_agent.close.assert_awaited_once()
