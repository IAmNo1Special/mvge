from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from mvge.cli.main import _run_print_mode
from mvge.core.channel import MvgeResponse
from mvge.core.errors import AuthenticationError, RateLimitError


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
