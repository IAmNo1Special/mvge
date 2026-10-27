from __future__ import annotations

from unittest.mock import patch

import typer

from mvge.cli.console import prompt_api_key


def test_prompt_api_key_success() -> None:
    with patch("typer.prompt", return_value="  sk-or-v1-input-key  "):
        key = prompt_api_key()
        assert key == "sk-or-v1-input-key"


def test_prompt_api_key_keyboard_interrupt() -> None:
    with patch("typer.prompt", side_effect=KeyboardInterrupt):
        key = prompt_api_key()
        assert key is None


def test_prompt_api_key_typer_abort() -> None:
    with patch("typer.prompt", side_effect=typer.Abort):
        key = prompt_api_key()
        assert key is None


def test_prompt_api_key_eof_error() -> None:
    with patch("typer.prompt", side_effect=EOFError):
        key = prompt_api_key()
        assert key is None


def test_prompt_api_key_names_the_realm() -> None:
    """The prompt names the Realm whose key is being asked for."""
    seen: list[str] = []
    with patch("typer.prompt", side_effect=lambda *a, **k: seen.append(a[0]) or "k"):
        prompt_api_key(realm="opencode")
    assert seen == ["Enter opencode API key"]


def test_prompt_api_key_without_realm_names_no_realm() -> None:
    """Without a Realm the prompt stays generic, never a guessed Realm."""
    seen: list[str] = []
    with patch("typer.prompt", side_effect=lambda *a, **k: seen.append(a[0]) or "k"):
        prompt_api_key()
    assert seen == ["Enter API key"]
