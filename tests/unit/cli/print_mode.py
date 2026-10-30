from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from mvge.cli.main import _run_print_mode
from mvge.core.channel import MvgeResponse
from mvge.core.errors import AuthenticationError, RateLimitError
from mvge.core.spells import SpellResultMessage
from mvge.runes.trust import WorkspaceTrustStatus

ERROR_TEXT = "Error: Validation error: Forbidden AST node: import os"


def _turn_agent(final_text: str, turn_entries: list[object]) -> MagicMock:
    """A fake agent whose run appends the turn's transcript entries."""

    async def _run(prompt: str) -> MvgeResponse:
        agent._state.invocations.extend(turn_entries)
        return MvgeResponse(
            stop_reason="stop",
            content=[{"type": "text", "text": final_text}],
        )

    agent = MagicMock()
    agent.workspace_trust_status = WorkspaceTrustStatus.TRUSTED
    agent._state = SimpleNamespace(invocations=[])
    agent.run = _run
    return agent


def _error_result() -> SpellResultMessage:
    return SpellResultMessage(
        spell_cast_id="c1",
        spell_name="execute_python",
        content=[{"type": "text", "text": ERROR_TEXT}],
        is_error=True,
    )


@pytest.mark.asyncio
async def test_run_print_mode_renders_unrecovered_spell_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A blocked turn prints the failure report without model help."""
    agent = _turn_agent("I can't run that code", [_error_result()])

    code = await _run_print_mode(agent, ["Use execute_python with import os"])

    assert code == 0
    captured = capsys.readouterr()
    assert "I can't run that code" in captured.out
    assert "Forbidden AST node: import os" in captured.out


@pytest.mark.asyncio
async def test_run_print_mode_does_not_duplicate_quoted_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When the model already quotes the error, print mode does not repeat it."""
    agent = _turn_agent(f"The call was rejected: {ERROR_TEXT}", [_error_result()])

    code = await _run_print_mode(agent, ["Use execute_python with import os"])

    assert code == 0
    captured = capsys.readouterr()
    assert captured.out.count("Forbidden AST node: import os") == 1


@pytest.mark.asyncio
async def test_run_print_mode_skips_error_after_recovery(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An error followed by a successful result is a recovery, not a blocked turn."""
    success = SpellResultMessage(
        spell_cast_id="c2",
        spell_name="execute_python",
        content=[{"type": "text", "text": "12.0"}],
        is_error=False,
    )
    agent = _turn_agent("12.0", [_error_result(), success])

    code = await _run_print_mode(agent, ["Try something, then compute"])

    assert code == 0
    captured = capsys.readouterr()
    assert "12.0" in captured.out
    assert "Forbidden AST node: import os" not in captured.out


@pytest.mark.asyncio
async def test_run_print_mode_renders_terminating_error_once(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A turn ending on the error result itself renders it once, not twice."""
    agent = MagicMock()
    agent.workspace_trust_status = WorkspaceTrustStatus.TRUSTED
    agent._state = SimpleNamespace(invocations=[])
    error = _error_result()
    error.terminate = True

    async def _run(prompt: str) -> SpellResultMessage:
        agent._state.invocations.append(error)
        return error

    agent.run = _run

    code = await _run_print_mode(agent, ["Use execute_python with import os"])

    assert code == 0
    captured = capsys.readouterr()
    assert captured.out.count("Forbidden AST node: import os") == 1


@pytest.mark.asyncio
async def test_run_print_mode_renders_response(
    capsys: pytest.CaptureFixture[str],
) -> None:
    agent = MagicMock()
    response = MvgeResponse(
        stop_reason="stop",
        content=[{"type": "text", "text": "Hello world response"}],
    )
    agent.run = AsyncMock(return_value=response)

    code = await _run_print_mode(agent, ["Hello"])

    assert code == 0
    agent.run.assert_called_once_with("Hello")
    captured = capsys.readouterr()
    assert "Hello world response" in captured.out
    assert captured.out.count("Hello world response") == 1


@pytest.mark.asyncio
async def test_run_print_mode_empty_prompts() -> None:
    agent = MagicMock()
    agent.run = AsyncMock()

    code = await _run_print_mode(agent, [])

    assert code == 0
    agent.run.assert_not_called()


@pytest.mark.asyncio
async def test_run_print_mode_rate_limit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    agent = MagicMock()
    agent.run = AsyncMock(
        side_effect=RateLimitError("Rate limit exceeded", retry_after=30.0)
    )

    code = await _run_print_mode(agent, ["Hello"])

    assert code == 1
    captured = capsys.readouterr()
    assert "Rate limited by the provider" in captured.out


@pytest.mark.asyncio
async def test_run_print_mode_auth_error(capsys: pytest.CaptureFixture[str]) -> None:
    agent = MagicMock()
    agent.run = AsyncMock(side_effect=AuthenticationError("401 Unauthorized"))

    code = await _run_print_mode(agent, ["Hello"])

    assert code == 1
    captured = capsys.readouterr()
    assert "Authentication failed (401)" in captured.out


@pytest.mark.asyncio
async def test_run_print_mode_generic_error(capsys: pytest.CaptureFixture[str]) -> None:
    agent = MagicMock()
    agent.run = AsyncMock(side_effect=RuntimeError("Unexpected connection error"))

    code = await _run_print_mode(agent, ["Hello"])

    assert code == 1
    captured = capsys.readouterr()
    assert "Error: Unexpected connection error" in captured.out
