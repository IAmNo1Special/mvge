from __future__ import annotations

import sys
from contextlib import suppress
from typing import Any, TextIO

import typer
from rich.console import Console

from mvge.cli.formatting import format_error

__all__ = [
    "clip_text",
    "configure_streams",
    "format_error",
    "get_console",
    "is_utf8_stream",
    "prompt_api_key",
]


def is_utf8_stream(stream: TextIO | None = None) -> bool:
    """Check if the target output stream uses UTF-8 encoding."""
    if stream is None:
        stream = sys.stdout
    encoding = getattr(stream, "encoding", None) or "utf-8"
    norm = encoding.lower().replace("-", "").replace("_", "")
    return norm in ("utf8", "utf")


def get_console(file: TextIO | None = None, **kwargs: Any) -> Console:
    """Create a Rich Console configured for the target stream encoding.

    If safe_box is not explicitly set, enables safe_box=True for non-UTF-8 streams
    so box drawing elements fallback cleanly to ASCII characters.
    """
    target_stream = file if file is not None else sys.stdout
    if "safe_box" not in kwargs and not is_utf8_stream(target_stream):
        kwargs["safe_box"] = True
    return Console(file=file, **kwargs)


def clip_text(value: str, max_width: int, ascii_only: bool = False) -> str:
    """Truncate ``value`` to ``max_width``, appending an ellipsis on overflow.

    Uses a Unicode ellipsis (…) under UTF-8 and an ASCII ``...`` under a
    non-UTF-8 stream so the marker itself never triggers ``?`` substitution.
    """
    if max_width <= 0 or len(value) <= max_width:
        return value
    if ascii_only:
        return value[: max(0, max_width - 3)] + "..."
    return value[: max(0, max_width - 1)] + "…"


def configure_streams() -> None:
    """Configure stdout and stderr streams safely."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            with suppress(Exception):
                stream.reconfigure(errors="replace")


def prompt_api_key(console: Console | None = None) -> str | None:
    """Prompt user interactively for OpenRouter API key."""
    if console is None:
        console = get_console()
    try:
        entered = typer.prompt("Enter OpenRouter API key", hide_input=True)
        return entered.strip() if entered else None
    except (KeyboardInterrupt, EOFError, typer.Abort):
        console.print("[red]Aborted.[/red]")
        return None
