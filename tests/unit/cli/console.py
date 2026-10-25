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
