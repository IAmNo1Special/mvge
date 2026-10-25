"""Wiring tests: the CLI binds the approval presenter for exactly one run.

Covers the host-side contract (the rune itself is out of scope):

* print mode binds the presenter before ``_run_print_mode`` and unbinds
  after, even when the run raises
* the REPL binds after agent creation and unbinds on exit and on error
* ``--approval-mode=allow-all`` prints its warning before channeling
* omitting the flag claims no explicit automation override
* environment variables never grant approval
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mvge.cli.approval_binding import (
    bind_approval_presenter as real_bind,
)
from mvge.cli.approval_binding import (
    unbind_approval_presenter as real_unbind,
)
from mvge.cli.approval_presenter import CliApprovalPresenter
from mvge.cli.commands.repl import (
    NoConsoleScreenBufferError,
    run_repl,
)
from mvge.cli.main import _run_agent


class _FakeRunner:
    """Engine-owned runner slot stand-in with the canonical slot shape."""

    def __init__(self) -> None:
        self._presenter: Any = None

    def set_approval_presenter(self, presenter: Any) -> None:
        self._presenter = presenter

    def clear_approval_presenter(self) -> None:
        self._presenter = None

    def get_approval_presenter(self) -> Any:
        # Test-only introspection; the canonical engine exposes no getter.
        return self._presenter


def _print_mode_kwargs(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "incantation": "do the thing",
        "model_id": "ollama-model",
        "api_key": "test-key",
        "temperature": None,
        "max_tokens": None,
        "contemplation_level": None,
        "spells_enabled": ["bash"],
        "extension_dir": None,
        "resume": None,
        "provider_name": None,
        "tome_dir": None,
        "tui": False,
        "approval_mode": None,
    }
    kwargs.update(overrides)
    return kwargs


def _patched_run_agent_deps() -> Any:
    """Patch the collaborators _run_agent needs for a print-mode run."""
    mock_reg = MagicMock()
    mock_reg.resolve.side_effect = Exception("no realms")
    return (
        patch("mvge.cli.main.get_default_realm_registry", return_value=mock_reg),
        patch("mvge.cli.main._create_agent", new_callable=AsyncMock),
        patch("mvge.cli.main.validate_api_key"),
    )


@pytest.mark.asyncio
async def test_print_mode_binds_presenter_during_run_and_unbinds_after() -> None:
    runner = _FakeRunner()
    mock_agent = MagicMock()
    mock_agent.runner = runner
    mock_agent.close = AsyncMock()
    seen: dict[str, Any] = {}

    async def fake_print_mode(agent: Any, prompts: list[str]) -> int:
        seen["presenter"] = agent.runner.get_approval_presenter()
        return 0

    reg_patch, create_patch, key_patch = _patched_run_agent_deps()
    with (
        reg_patch,
        create_patch as mock_create,
        key_patch,
        patch("mvge.cli.main._run_print_mode", side_effect=fake_print_mode),
    ):
        mock_create.return_value = mock_agent
        code = await _run_agent(**_print_mode_kwargs(approval_mode="prompt"))

    assert code == 0
    presenter = seen["presenter"]
    assert isinstance(presenter, CliApprovalPresenter)
    assert presenter._mode == "prompt"
    assert runner.get_approval_presenter() is None


@pytest.mark.asyncio
async def test_print_mode_unbinds_presenter_when_run_raises() -> None:
    runner = _FakeRunner()
    mock_agent = MagicMock()
    mock_agent.runner = runner
    mock_agent.close = AsyncMock()

    reg_patch, create_patch, key_patch = _patched_run_agent_deps()
    with (
        reg_patch,
        create_patch as mock_create,
        key_patch,
        patch(
            "mvge.cli.main._run_print_mode",
            side_effect=RuntimeError("boom"),
        ),
    ):
        mock_create.return_value = mock_agent
        code = await _run_agent(**_print_mode_kwargs(approval_mode="prompt"))

    assert code == 1
    assert runner.get_approval_presenter() is None
    mock_agent.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_explicit_allow_all_prints_warning_before_channeling() -> None:
    runner = _FakeRunner()
    mock_agent = MagicMock()
    mock_agent.runner = runner
    mock_agent.close = AsyncMock()
    events: list[tuple[str, str]] = []

    def fake_print(*args: Any, **kwargs: Any) -> None:
        events.append(("print", str(args[0]) if args else ""))

    async def fake_print_mode(agent: Any, prompts: list[str]) -> int:
        events.append(("run", ""))
        return 0

    reg_patch, create_patch, key_patch = _patched_run_agent_deps()
    with (
        reg_patch,
        create_patch as mock_create,
        key_patch,
        patch("mvge.cli.main.console") as mock_console,
        patch("mvge.cli.main._run_print_mode", side_effect=fake_print_mode),
    ):
        mock_console.print.side_effect = fake_print
        mock_create.return_value = mock_agent
        code = await _run_agent(**_print_mode_kwargs(approval_mode="allow-all"))

    assert code == 0
    notice_idx = next(i for i, (kind, text) in enumerate(events) if "allow-all" in text)
    run_idx = next(i for i, (kind, _) in enumerate(events) if kind == "run")
    assert notice_idx < run_idx
    assert "WARNING: --approval-mode=allow-all" in events[notice_idx][1]


@pytest.mark.asyncio
async def test_omitted_approval_mode_prints_no_notice() -> None:
    runner = _FakeRunner()
    mock_agent = MagicMock()
    mock_agent.runner = runner
    mock_agent.close = AsyncMock()

    reg_patch, create_patch, key_patch = _patched_run_agent_deps()
    with (
        reg_patch,
        create_patch as mock_create,
        key_patch,
        patch("mvge.cli.main.console") as mock_console,
        patch("mvge.cli.main._run_print_mode", return_value=0),
    ):
        mock_create.return_value = mock_agent
        code = await _run_agent(**_print_mode_kwargs(approval_mode=None))

    assert code == 0
    printed = " ".join(str(c.args[0]) for c in mock_console.print.call_args_list)
    assert "approval" not in printed.lower()


@pytest.mark.asyncio
@patch.dict(
    os.environ,
    {
        "MVGE_APPROVAL_MODE": "allow-all",
        "APPROVAL_MODE": "allow-all",
        "MVGE_APPROVAL": "allow-all",
    },
)
async def test_environment_variables_never_grant_approval() -> None:
    runner = _FakeRunner()
    mock_agent = MagicMock()
    mock_agent.runner = runner
    mock_agent.close = AsyncMock()
    seen: dict[str, Any] = {}

    async def fake_print_mode(agent: Any, prompts: list[str]) -> int:
        seen["presenter"] = agent.runner.get_approval_presenter()
        return 0

    reg_patch, create_patch, key_patch = _patched_run_agent_deps()
    with (
        reg_patch,
        create_patch as mock_create,
        key_patch,
        patch("mvge.cli.main.console") as mock_console,
        patch("mvge.cli.main._run_print_mode", side_effect=fake_print_mode),
    ):
        mock_create.return_value = mock_agent
        code = await _run_agent(**_print_mode_kwargs(approval_mode=None))

    assert code == 0
    presenter = seen["presenter"]
    assert isinstance(presenter, CliApprovalPresenter)
    assert presenter._mode == "prompt"
    printed = " ".join(str(c.args[0]) for c in mock_console.print.call_args_list)
    assert "approval" not in printed.lower()


@pytest.mark.asyncio
async def test_print_mode_without_runner_warns_and_stays_fail_closed() -> None:
    """A custom agent_factory agent without the runner accessor is a missing
    slot: warn and stay fail-closed, not a crash."""

    class _RunnerlessAgent:
        def __init__(self) -> None:
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    agent = _RunnerlessAgent()
    reg_patch, create_patch, key_patch = _patched_run_agent_deps()
    with (
        reg_patch,
        create_patch as mock_create,
        key_patch,
        patch("mvge.cli.main.console") as mock_console,
        patch("mvge.cli.main._run_print_mode", return_value=0),
    ):
        mock_create.return_value = agent
        code = await _run_agent(**_print_mode_kwargs(approval_mode="prompt"))

    assert code == 0
    printed = " ".join(str(c.args[0]) for c in mock_console.print.call_args_list)
    assert "no approval slot" in printed
    assert agent.closed is True


@pytest.mark.asyncio
async def test_repl_without_runner_warns_and_exits_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_agent = _repl_agent(_FakeRunner())
    del mock_agent.runner  # custom agent without the engine runner accessor
    monkeypatch.setattr("builtins.input", lambda prompt="": "/quit")

    with (
        patch("mvge.cli.commands.repl._create_agent", return_value=mock_agent),
        patch(
            "mvge.cli.commands.repl.PromptSession",
            side_effect=NoConsoleScreenBufferError(),
        ),
        patch("mvge.cli.commands.repl.console") as mock_console,
    ):
        await run_repl(api_key="sk-or-test-key")

    printed = " ".join(
        str(c.args[0]) if c.args else "" for c in mock_console.print.call_args_list
    )
    assert "no approval slot" in printed
    mock_agent.close.assert_awaited_once()


def _repl_agent(runner: _FakeRunner) -> AsyncMock:
    mock_agent = AsyncMock()
    mock_agent.on = MagicMock(return_value=lambda: None)
    mock_agent.tome_id = "test-tome-id"
    mock_agent.runner = runner
    return mock_agent


@pytest.mark.asyncio
async def test_repl_binds_presenter_and_unbinds_on_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _FakeRunner()
    mock_agent = _repl_agent(runner)
    monkeypatch.setattr("builtins.input", lambda prompt="": "/quit")

    with (
        patch("mvge.cli.commands.repl._create_agent", return_value=mock_agent),
        patch(
            "mvge.cli.commands.repl.PromptSession",
            side_effect=NoConsoleScreenBufferError(),
        ),
        patch(
            "mvge.cli.commands.repl.bind_approval_presenter", wraps=real_bind
        ) as spy_bind,
        patch(
            "mvge.cli.commands.repl.unbind_approval_presenter", wraps=real_unbind
        ) as spy_unbind,
    ):
        await run_repl(api_key="sk-or-test-key")

    spy_bind.assert_called_once()
    bound = spy_bind.call_args[0][1]
    assert isinstance(bound, CliApprovalPresenter)
    spy_unbind.assert_called_once()
    assert spy_unbind.call_args[0][0] is runner
    assert isinstance(spy_unbind.call_args[0][1], CliApprovalPresenter)
    assert runner.get_approval_presenter() is None
    mock_agent.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_repl_unbinds_presenter_when_loop_raises() -> None:
    runner = _FakeRunner()
    mock_agent = _repl_agent(runner)

    with (
        patch("mvge.cli.commands.repl._create_agent", return_value=mock_agent),
        patch("mvge.cli.commands.repl.PromptSession") as mock_session_cls,
        patch(
            "mvge.cli.commands.repl._read_initial_prompt",
            side_effect=RuntimeError("boom"),
        ),
        patch(
            "mvge.cli.commands.repl.unbind_approval_presenter", wraps=real_unbind
        ) as spy_unbind,
    ):
        mock_session_cls.return_value = MagicMock()
        with pytest.raises(RuntimeError, match="boom"):
            await run_repl(api_key="sk-or-test-key")

    spy_unbind.assert_called_once()
    assert spy_unbind.call_args[0][0] is runner
    assert isinstance(spy_unbind.call_args[0][1], CliApprovalPresenter)
    assert runner.get_approval_presenter() is None
    mock_agent.close.assert_awaited_once()
