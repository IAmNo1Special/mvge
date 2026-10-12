"""Unit tests for agent_factory module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from mvgeos_core.constants import DEFAULT_AGENT_NAME

from mvgeos_cli.agent_factory import (
    create_agent,
    default_agent_factory,
    validate_api_key,
)


def test_validate_api_key_valid() -> None:
    # OpenRouter
    validate_api_key("sk-or-12345")
    # Google
    validate_api_key("AIzaSy12345")
    # Ollama ignores missing or any api key
    validate_api_key("", model_id="ollama/llama3")
    validate_api_key("foo", model_id="ollama/llama3")
    # Google model prefix with any key
    validate_api_key("custom-key", model_id="google/gemini-pro")


def test_validate_api_key_invalid() -> None:
    with pytest.raises(ValueError, match="API key is required"):
        validate_api_key("")

    with pytest.raises(ValueError, match="Invalid API key format"):
        validate_api_key("invalid-key")


def test_default_agent_factory_hands_the_name_through_verbatim() -> None:
    """The CLI no longer answers "which Mvge" -- it forwards the name.

    A remap here was a second answerer to a question `Mvge` already owns, and
    it is what kept a marketplace artifact's slug in this package. `Mvge`
    resolves once, in its constructor, from the agents actually installed.
    """
    with (
        patch("mvgeos_cli.agent_factory.Path.is_dir", return_value=True),
        patch("mvgeos_cli.agent_factory.Mvge") as mock_mvge,
    ):
        default_agent_factory(name=DEFAULT_AGENT_NAME)
        mock_mvge.assert_called_once()
        assert mock_mvge.call_args.kwargs["name"] == DEFAULT_AGENT_NAME


def test_default_agent_factory_custom_name() -> None:
    with (
        patch("mvgeos_cli.agent_factory.Path.is_dir", return_value=True),
        patch("mvgeos_cli.agent_factory.Mvge") as mock_mvge,
    ):
        default_agent_factory(name="custom_mvge")
        mock_mvge.assert_called_once()
        assert mock_mvge.call_args.kwargs["name"] == "custom_mvge"


def test_default_agent_factory_when_dir_not_exists() -> None:
    with (
        patch("mvgeos_cli.agent_factory.Path.is_dir", return_value=False),
        patch("mvgeos_cli.agent_factory.Mvge") as mock_mvge,
    ):
        default_agent_factory(name=DEFAULT_AGENT_NAME)
        mock_mvge.assert_called_once()
        assert mock_mvge.call_args.kwargs["name"] == DEFAULT_AGENT_NAME


@pytest.mark.asyncio
async def test_create_agent_sequence_spells() -> None:
    mock_agent = MagicMock()
    mock_agent.initialize = MagicMock()

    # async initialize
    async def async_init() -> None:
        pass

    mock_agent.initialize = async_init

    factory = MagicMock(return_value=mock_agent)
    agent = await create_agent(
        spells=("read", "write"),
        agent_factory=factory,
        temperature=None,
        max_tokens=None,
        contemplation="",
    )
    assert agent == mock_agent
    factory.assert_called_once()
    assert factory.call_args.kwargs["spells"] == ["read", "write"]
